"""Mission Control read API (HX-OPS-001 §11.5): GET-only JSON, one SSE stream, and the built dashboard.

Run: uv run uvicorn freight_exchange.ops.api:app --app-dir src --host 127.0.0.1 --port 8820
There is deliberately no POST/PUT/DELETE route: the dashboard observes the market and can never cancel, book,
approve, pay, retry or re-run anything. Optional access control: set OPS_DASHBOARD_TOKEN and open the dashboard
once with ?token=<value> (stored as an HttpOnly cookie).
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from freight_exchange.ops import analytics as an
from freight_exchange.ops import read_model as rm
from freight_exchange.ops.snapshots import INVENTORY_EVENTS, capture_capacity, capture_checkpoint
from freight_exchange.repository import get_repository

load_dotenv()
log = logging.getLogger("ops_api")
DIST = Path(__file__).resolve().parents[3] / "dashboard" / "dist"
_repo = None
_state = {"last_event_ts": None, "last_checkpoint": None, "last_capacity": None, "observer_ok": None,
          "started_at": datetime.now(timezone.utc)}


_repo_lock = asyncio.Lock()


async def pool():
    """One shared pool; the lock stops concurrent first requests from each opening (and leaking) a pool."""
    global _repo
    if _repo is None:
        async with _repo_lock:
            if _repo is None:
                _repo = await get_repository()
    return _repo.pool


def _authorized(request: Request) -> bool:
    expected = os.getenv("OPS_DASHBOARD_TOKEN", "")
    if not expected:
        return True
    given = request.headers.get("x-ops-token") or request.cookies.get("ops_token") or request.query_params.get("token") or ""
    return hmac.compare_digest(given, expected)


def guarded(handler):
    async def endpoint(request: Request):
        if not _authorized(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            body = await handler(request)
        except LookupError:
            return JSONResponse({"error": "not found"}, status_code=404)
        except ValueError:
            return JSONResponse({"error": "bad request"}, status_code=400)
        except Exception as exc:  # never leak raw exception text to the browser
            log.warning("ops read failed: %s", type(exc).__name__)
            return JSONResponse({"error": "read failed", "kind": type(exc).__name__}, status_code=500)
        if isinstance(body, Response):
            return body
        if rm.sanitize_check(body):  # last-line privacy guard
            log.error("blocked DTO with private fields: %s", rm.sanitize_check(body)[:3])
            return JSONResponse({"error": "redaction guard blocked this response"}, status_code=500)
        return JSONResponse(body, headers={"cache-control": "no-store"})
    return endpoint


def q(request: Request, key: str) -> str | None:
    v = request.query_params.get(key)
    return v or None


async def market_state(request: Request):
    async with (await pool()).connection() as conn:
        return await rm.projection(conn)


async def events(request: Request):
    after = q(request, "after")
    after_ts = datetime.fromisoformat(after.split("|")[0].replace("Z", "+00:00")) if after else rm.window(240)
    async with (await pool()).connection() as conn:
        return {"events": await rm.public_events(conn, after=after_ts, incident_id=q(request, "incident_id"),
                                                 limit=min(int(q(request, "limit") or 300), 2000))}


async def negotiations(request: Request):
    async with (await pool()).connection() as conn:
        return {"sessions": await rm.negotiations(conn, incident_id=q(request, "incident_id"),
                                                  request_id=q(request, "request_id"), session_id=q(request, "session_id"))}


async def incidents(request: Request):
    async with (await pool()).connection() as conn:
        return {"incidents": await rm.incidents(conn)}


async def incident(request: Request):
    async with (await pool()).connection() as conn:
        d = await rm.incident_detail(conn, request.path_params["recovery_id"])
    if d is None:
        raise LookupError
    return d


async def replay(request: Request):
    async with (await pool()).connection() as conn:
        return await an.replay_bundle(conn, incident_id=q(request, "incident_id"), start=q(request, "from"),
                                      end=q(request, "to"))


async def analytics_ep(request: Request):
    async with (await pool()).connection() as conn:
        return await an.analytics(conn, q(request, "metric") or "", start=q(request, "from"), end=q(request, "to"),
                                  grain=q(request, "bucket") or "1m", corridor=q(request, "corridor"),
                                  carrier=q(request, "carrier"), kind=q(request, "kind"))


async def decision(request: Request):
    async with (await pool()).connection() as conn:
        d = await an.decision(conn, request.path_params["kind"], request.path_params["ident"])
    if d is None:
        raise LookupError
    return d


async def provenance(request: Request):
    async with (await pool()).connection() as conn:
        return await an.provenance(conn)


async def health(request: Request):
    return {"ok": True, "observer": {k: (rm.iso(v) if isinstance(v, datetime) else v) for k, v in _state.items()},
            "sentry": bool(os.getenv("SENTRY_DSN"))}


async def stream(request: Request):
    """Read-only SSE. Resume with Last-Event-ID / ?cursor; a 3 s overlap window plus eventId de-duplication
    covers transactions that commit slightly out of timestamp order; a stale cursor asks for a resnapshot."""
    if not _authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    cursor = request.headers.get("last-event-id") or q(request, "cursor")
    since = datetime.fromisoformat(cursor.split("|")[0].replace("Z", "+00:00")) if cursor else datetime.now(timezone.utc)
    resnapshot = cursor is not None and datetime.now(timezone.utc) - since > timedelta(minutes=10)

    async def gen():
        nonlocal since
        seen: list[str] = []
        if resnapshot:
            yield "event: resnapshot\ndata: {}\n\n"
            since = datetime.now(timezone.utc)
        last_beat = datetime.now(timezone.utc)
        yield "retry: 3000\n\n"
        while True:
            if await request.is_disconnected():
                return
            try:
                async with (await pool()).connection() as conn:
                    evs = await rm.public_events(conn, after=since - timedelta(seconds=3), limit=500)
            except Exception as exc:
                log.warning("stream read failed: %s", type(exc).__name__)
                evs = []
            for e in evs:
                if e["eventId"] in seen:
                    continue
                seen.append(e["eventId"])
                yield f"id: {e['streamCursor']}\nevent: market\ndata: {json.dumps(e)}\n\n"
            if evs:
                since = max(since, datetime.fromisoformat(evs[-1]["eventTime"].replace("Z", "+00:00")))
            del seen[:-2000]
            if (datetime.now(timezone.utc) - last_beat).total_seconds() > 15:
                last_beat = datetime.now(timezone.utc)
                yield f": heartbeat {rm.iso(last_beat)}\n\n"
            await asyncio.sleep(1.0)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"cache-control": "no-store", "x-accel-buffering": "no"})


async def observer() -> None:
    """Capacity snapshots on inventory transitions + every minute; a checkpoint every minute when the market moved
    (or right after a reset). Writes only the observer tables."""
    p = await pool()
    last_ts, last_minute = None, None
    while True:
        try:
            async with p.connection() as conn:
                newest = (await (await conn.execute("SELECT max(ts) FROM market_events")).fetchone())[0]
                changed = []
                if last_ts and newest and newest > last_ts:
                    changed = [r[0] for r in await (await conn.execute(
                        "SELECT DISTINCT event_type FROM market_events WHERE ts > %s", (last_ts,))).fetchall()]
            reset = last_ts is not None and (newest is None or newest < last_ts)
            if any(t in INVENTORY_EVENTS for t in changed):
                await capture_capacity(p, next(t for t in changed if t in INVENTORY_EVENTS))
                _state["last_capacity"] = datetime.now(timezone.utc)
            now = datetime.now(timezone.utc)
            if last_minute is None or reset or (now - last_minute).total_seconds() >= 60:
                await capture_capacity(p, "interval" if not reset else "reset")
                if last_minute is None or reset or newest != _state.get("checkpoint_cursor"):
                    cp = await capture_checkpoint(p, "startup" if last_minute is None else "reset" if reset else "interval")
                    _state["last_checkpoint"], _state["checkpoint_cursor"] = now, cp["cursor"]
                last_minute = now
                _state["last_capacity"] = now
            last_ts = newest or last_ts if not reset else newest
            _state["last_event_ts"], _state["observer_ok"] = newest, True
        except Exception as exc:
            _state["observer_ok"] = False
            log.warning("observer cycle failed: %s", type(exc).__name__)
        await asyncio.sleep(2.0)


@contextlib.asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(observer()) if os.getenv("OPS_OBSERVER_ENABLED", "true") != "false" else None
    try:
        yield
    finally:
        if task:
            task.cancel()


async def spa(request: Request):
    if not _authorized(request):
        return Response("Unauthorized: open with ?token=…", status_code=401)
    index = DIST / "index.html"
    if not index.exists():
        return Response("Dashboard not built yet: cd dashboard && npm install && npm run build", status_code=503)
    resp = FileResponse(index, headers={"cache-control": "no-store"})
    token = request.query_params.get("token")
    if token and os.getenv("OPS_DASHBOARD_TOKEN"):
        resp.set_cookie("ops_token", token, httponly=True, samesite="strict")
    return resp


routes = [
    Route("/api/ops/health", guarded(health)),
    Route("/api/ops/market/state", guarded(market_state)),
    Route("/api/ops/events", guarded(events)),
    Route("/api/ops/events/stream", stream),
    Route("/api/ops/negotiations", guarded(negotiations)),
    Route("/api/ops/incidents", guarded(incidents)),
    Route("/api/ops/incidents/{recovery_id}", guarded(incident)),
    Route("/api/ops/replay", guarded(replay)),
    Route("/api/ops/analytics", guarded(analytics_ep)),
    Route("/api/ops/decisions/{kind}/{ident}", guarded(decision)),
    Route("/api/ops/provenance", guarded(provenance)),
]
if (DIST / "assets").exists():
    routes.append(Mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets"))
routes.append(Route("/{path:path}", spa))
app = Starlette(routes=routes, lifespan=lifespan)

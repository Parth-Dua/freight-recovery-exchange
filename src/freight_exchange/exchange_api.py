"""Internal Exchange API for the buyer gateway uAgent (loopback + shared token; never public).

Run: uv run uvicorn freight_exchange.exchange_api:app --app-dir src --host 127.0.0.1 --port 8900
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import logging
import os

from dotenv import load_dotenv
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from freight_exchange.gateway_service import GatewayService
from freight_exchange.gateway_store import ChatContext
from freight_exchange.repository import get_repository

load_dotenv()
REQUIRED = ("event_id", "sender_agent_address", "chat_session_id", "gateway_agent_address", "text")
_service: GatewayService | None = None


async def service() -> GatewayService:
    global _service
    if _service is None:
        _service = GatewayService(await get_repository())
    return _service


def authorized(request: Request) -> bool:
    expected = os.getenv("GATEWAY_BACKEND_TOKEN", "")
    return bool(expected) and hmac.compare_digest(request.headers.get("x-gateway-token", ""), expected)


def guarded(handler):
    async def endpoint(request: Request):
        if not authorized(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await handler(request)
    return endpoint


async def intent(request: Request):
    body = await request.json()
    if any(not isinstance(body.get(k), str) or not body.get(k) for k in REQUIRED):
        return JSONResponse({"error": "malformed envelope"}, status_code=400)
    ctx = ChatContext(event_id=body["event_id"], sender=body["sender_agent_address"], session=body["chat_session_id"],
                      gateway=body["gateway_agent_address"], gateway_wallet=body.get("gateway_wallet", ""),
                      text=body["text"][:8000])
    return JSONResponse(await (await service()).handle(ctx))


async def outbox(request: Request):
    gateway = request.query_params.get("gateway", "")
    return JSONResponse({"items": await (await service()).store.pending_outbox(gateway)})


async def outbox_ack(request: Request):
    body = await request.json()
    await (await service()).store.ack_outbox(int(request.path_params["outbox_id"]), body.get("status", "RETRY"),
                                             body.get("detail", ""))
    return JSONResponse({"ok": True})


async def payment_event(request: Request):
    return JSONResponse(await (await service()).payment_event(await request.json()))


async def health(request: Request):
    return JSONResponse({"ok": True})


async def payment_poller() -> None:
    """Stripe TEST has no webhook in mailbox mode: settle hosted-link payments and lapsed windows here."""
    from freight_exchange.fet_booking import payment_mode
    if payment_mode() != "stripe_test":
        return
    interval = float(os.getenv("PAYMENT_POLL_INTERVAL_S", "4"))
    while True:
        try:
            await (await service()).poll_payments()
        except Exception as exc:  # keep polling; one bad Stripe call must not stop settlement
            logging.getLogger("exchange_api").warning("payment poll failed: %s", type(exc).__name__)
        await asyncio.sleep(interval)


@contextlib.asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(payment_poller())
    try:
        yield
    finally:
        task.cancel()


app = Starlette(lifespan=lifespan, routes=[
    Route("/health", health),
    Route("/internal/chat/intent", guarded(intent), methods=["POST"]),
    Route("/internal/chat/outbox", guarded(outbox), methods=["GET"]),
    Route("/internal/chat/outbox/{outbox_id:int}/ack", guarded(outbox_ack), methods=["POST"]),
    Route("/internal/payment/event", guarded(payment_event), methods=["POST"]),
])

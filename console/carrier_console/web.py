"""Carrier operator console.

One page. A carrier operator sees their sailings and withdraws one, naming the
simulated advisory that prompted it. This is the demo's disruption moment: a judge
clicks the button and the booking they just made in ASI:One becomes DISRUPTED.

Run:  cd console && uv run uvicorn carrier_console.web:app --host 0.0.0.0 --port 8800
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from html import escape
from urllib.parse import urlencode
from uuid import uuid4

from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from carrier_console.config import console_port, load_env
from carrier_console.db import close_pools, get_pool
from carrier_console.queries import SailingRow, committed_bookings, list_sailings, recent_disruptions
from carrier_console.risk import RiskEvent, get_risk_event, load_risk_events
from carrier_console.withdrawal import WithdrawSailing, WithdrawalRejected, withdraw_sailing

STYLE = """
:root {
  --bg: #0e1116; --panel: #161b22; --line: #262d36; --ink: #e6edf3;
  --muted: #8b949e; --accent: #4493f8; --danger: #f85149; --ok: #3fb950;
  --warn: #d29922;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font: 15px/1.5 ui-sans-serif, -apple-system, "Segoe UI", sans-serif;
}
.wrap { max-width: 960px; margin: 0 auto; padding: 32px 20px 64px; }
header { border-bottom: 1px solid var(--line); padding-bottom: 16px; margin-bottom: 28px; }
h1 { font-size: 20px; margin: 0 0 4px; letter-spacing: -0.01em; }
.sub { color: var(--muted); font-size: 13px; margin: 0; }
h2 { font-size: 13px; text-transform: uppercase; letter-spacing: 0.08em;
     color: var(--muted); margin: 32px 0 12px; font-weight: 600; }
.card { background: var(--panel); border: 1px solid var(--line);
        border-radius: 10px; padding: 16px 18px; margin-bottom: 12px; }
.card.inactive { opacity: 0.55; }
.row { display: flex; justify-content: space-between; align-items: flex-start; gap: 20px; }
.id { font: 13px ui-monospace, SFMono-Regular, Menlo, monospace; color: var(--accent); }
.route { font-size: 15px; font-weight: 600; margin: 4px 0 2px; }
.meta { color: var(--muted); font-size: 13px; }
.chip { display: inline-block; font-size: 11px; font-weight: 600; padding: 3px 9px;
        border-radius: 999px; text-transform: uppercase; letter-spacing: 0.04em; }
.chip.active { background: rgba(63,185,80,.15); color: var(--ok); }
.chip.withdrawn { background: rgba(248,81,73,.15); color: var(--danger); }
.chip.other { background: rgba(139,148,158,.15); color: var(--muted); }
.count { font-size: 13px; color: var(--muted); }
.count b { color: var(--ink); }
form.withdraw { margin-top: 14px; padding-top: 14px; border-top: 1px solid var(--line);
                display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
select, button { font: inherit; border-radius: 7px; border: 1px solid var(--line); }
select { background: var(--bg); color: var(--ink); padding: 8px 10px; flex: 1; min-width: 260px; }
button { background: var(--danger); color: #fff; border-color: transparent;
         padding: 8px 18px; font-weight: 600; cursor: pointer; }
button:hover { filter: brightness(1.1); }
.banner { border-radius: 10px; padding: 14px 18px; margin-bottom: 24px;
          border: 1px solid; font-size: 14px; }
.banner.ok { background: rgba(63,185,80,.08); border-color: rgba(63,185,80,.4); }
.banner.warn { background: rgba(210,153,34,.08); border-color: rgba(210,153,34,.4); }
.banner.err { background: rgba(248,81,73,.08); border-color: rgba(248,81,73,.4); }
.banner b { display: block; margin-bottom: 4px; }
.log { font: 12px ui-monospace, Menlo, monospace; color: var(--muted);
       border-left: 2px solid var(--line); padding-left: 12px; margin-bottom: 8px; }
.note { color: var(--muted); font-size: 12px; margin-top: 24px; line-height: 1.6; }
.review h2 { margin-top: 0; }
.review .title { font-size: 20px; font-weight: 600; margin: 0 0 6px; }
.review .advisory { border-left: 2px solid var(--warn); padding: 4px 0 4px 12px; margin: 16px 0; }
.review ul { margin: 8px 0 0; padding-left: 18px; }
.review li { margin: 4px 0; }
.actions { display: flex; gap: 12px; align-items: center; margin-top: 20px; }
a.ghost { color: var(--muted); text-decoration: none; padding: 8px 14px;
          border: 1px solid var(--line); border-radius: 7px; }
a.ghost:hover { color: var(--ink); border-color: var(--muted); }
"""


def _chip(status: str) -> str:
    kind = {"ACTIVE": "active", "WITHDRAWN": "withdrawn"}.get(status, "other")
    return f'<span class="chip {kind}">{escape(status)}</span>'


def _sailing_card(sailing: SailingRow) -> str:
    options = "".join(
        f'<option value="{escape(event.risk_event_id)}">{escape(event.label)} — '
        f"{escape(event.description[:72])}…</option>"
        for event in load_risk_events()
    )
    form = ""
    if sailing.is_active:
        form = f"""
        <form class="withdraw" method="post" action="/withdraw/review">
          <input type="hidden" name="sailing_id" value="{escape(sailing.sailing_id)}">
          <input type="hidden" name="carrier_id" value="{escape(sailing.carrier_id)}">
          <select name="risk_event_id" required>{options}</select>
          <button type="submit">Withdraw sailing</button>
        </form>"""
    return f"""
    <div class="card{'' if sailing.is_active else ' inactive'}">
      <div class="row">
        <div>
          <div class="id">{escape(sailing.sailing_id)}</div>
          <div class="route">{escape(sailing.origin_port)} &rarr; {escape(sailing.destination_port)}</div>
          <div class="meta">{escape(sailing.carrier_name)} ·
            departs {sailing.departure_at:%d %b %H:%M} · arrives {sailing.arrival_at:%d %b %H:%M}</div>
        </div>
        <div style="text-align:right">
          {_chip(sailing.status)}
          <div class="count" style="margin-top:8px">
            <b>{sailing.sellable_slots}</b> sellable<br>
            <b>{sailing.committed_bookings}</b> committed
          </div>
        </div>
      </div>{form}
    </div>"""


def _review_body(sailing: SailingRow, risk_event: RiskEvent, bookings: list[tuple[str, str]]) -> str:
    """The confirmation step. Plain HTML on purpose: no browser popup, so it works in any
    browser, including embedded ones that block confirm() dialogs."""
    if bookings:
        affected = (
            f"<p>This will disrupt <b>{len(bookings)}</b> committed booking(s):</p><ul>"
            + "".join(f"<li><span class=\"id\">{escape(bid)}</span> · {escape(buyer)}</li>"
                      for bid, buyer in bookings)
            + "</ul>"
        )
    else:
        affected = "<p>No committed bookings are on this sailing, so nobody is disrupted.</p>"
    return f"""
  <div class="card review">
    <p class="title">Withdraw {escape(sailing.sailing_id)}?</p>
    <div class="meta">{escape(sailing.carrier_name)} · {escape(sailing.origin_port)} &rarr;
      {escape(sailing.destination_port)} · departs {sailing.departure_at:%d %b %H:%M}</div>
    <div class="advisory">
      <div class="meta">{escape(risk_event.label)} · {escape(risk_event.source_reference)}</div>
      <div>{escape(risk_event.description)}</div>
    </div>
    {affected}
    <p class="meta">Bookings on other sailings are not affected. Withdrawing twice changes nothing.</p>
    <form method="post" action="/withdraw" class="actions">
      <input type="hidden" name="sailing_id" value="{escape(sailing.sailing_id)}">
      <input type="hidden" name="carrier_id" value="{escape(sailing.carrier_id)}">
      <input type="hidden" name="risk_event_id" value="{escape(risk_event.risk_event_id)}">
      <button type="submit">Yes, withdraw {escape(sailing.sailing_id)}</button>
      <a class="ghost" href="/">Cancel</a>
    </form>
  </div>"""


def _shell(body: str) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Carrier Console</title><style>{STYLE}</style></head>
<body><div class="wrap">
  <header>
    <h1>Carrier Console</h1>
    <p class="sub">Operator view. Withdraw a sailing when an advisory makes it unsafe to run.</p>
  </header>
  {body}
</div></body></html>"""


def _back(**params: str) -> RedirectResponse:
    """Back to the console with a banner. Values are URL-encoded, so messages with spaces
    and colons survive the redirect."""
    return RedirectResponse("/?" + urlencode(params), status_code=303)


def _banner(params: dict[str, str]) -> str:
    if params.get("err"):
        return (
            f'<div class="banner err"><b>Withdrawal rejected</b>{escape(params["err"])}</div>'
        )
    if params.get("dup"):
        return (
            f'<div class="banner warn"><b>Already withdrawn</b>'
            f'{escape(params["dup"])} was withdrawn earlier. Nothing changed, '
            f"and no booking was disrupted twice.</div>"
        )
    if params.get("ok"):
        return f'<div class="banner ok"><b>Sailing withdrawn</b>{escape(params["ok"])}</div>'
    return ""


@asynccontextmanager
async def lifespan(_: FastAPI):
    load_env()
    await get_pool()
    yield
    await close_pools()


app = FastAPI(title="Carrier Console", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def index(ok: str = "", dup: str = "", err: str = "") -> HTMLResponse:
    pool = await get_pool()
    sailings = await list_sailings(pool)
    history = await recent_disruptions(pool)

    cards = "".join(_sailing_card(sailing) for sailing in sailings) or (
        '<div class="card"><div class="meta">No sailings. '
        "Run <code>uv run python scripts/db.py seed</code> from the repo root.</div></div>"
    )
    log = "".join(
        f'<div class="log">{row[5]:%H:%M:%S} · {escape(row[0])} · '
        f"{escape(row[2])} withdrawn by {escape(row[3] or 'unknown')} · {escape(row[4])}</div>"
        for row in history
    ) or '<div class="log">No disruptions recorded yet.</div>'

    return HTMLResponse(_shell(f"""
  {_banner({"ok": ok, "dup": dup, "err": err})}
  <h2>Sailings</h2>
  {cards}
  <h2>Disruption history</h2>
  {log}
  <p class="note">
    All advisories here are explicitly simulated fixtures. An advisory never withdraws a
    sailing on its own: a carrier operator has to authorize each withdrawal, which is what
    this page records. Withdrawing twice is a no-op.
  </p>"""))


@app.post("/withdraw/review", response_class=HTMLResponse, response_model=None)
async def review(
    sailing_id: str = Form(...),
    carrier_id: str = Form(...),
    risk_event_id: str = Form(...),
) -> HTMLResponse | RedirectResponse:
    pool = await get_pool()
    risk_event = get_risk_event(risk_event_id)
    sailing = next((s for s in await list_sailings(pool) if s.sailing_id == sailing_id), None)
    if risk_event is None or sailing is None:
        return _back(err=f"unknown sailing or advisory: {sailing_id}, {risk_event_id}")
    if sailing.carrier_id != carrier_id:
        return _back(err=f"CROSS_CARRIER_WITHDRAWAL: {carrier_id} does not own {sailing_id}")
    if not sailing.is_active:
        return _back(dup=sailing_id)
    return HTMLResponse(_shell(_review_body(sailing, risk_event, await committed_bookings(pool, sailing_id))))


@app.post("/withdraw")
async def withdraw(
    sailing_id: str = Form(...),
    carrier_id: str = Form(...),
    risk_event_id: str = Form(...),
) -> RedirectResponse:
    pool = await get_pool()
    risk_event = get_risk_event(risk_event_id)
    if risk_event is None:
        return _back(err=f"unknown risk event {risk_event_id}")

    command = WithdrawSailing(
        sailing_id=sailing_id,
        carrier_id=carrier_id,
        reason_risk_event_id=risk_event.risk_event_id,
        actor_id=f"operator:{carrier_id}",
        authorization_ref=risk_event.source_reference,
        idempotency_key=uuid4().hex,
        reason_text=risk_event.description,
    )
    try:
        result = await withdraw_sailing(pool, command)
    except WithdrawalRejected as rejection:
        return _back(err=f"{rejection.code.value}: {rejection.detail}")

    if result.already_withdrawn:
        return _back(dup=result.sailing_id)

    message = (
        f"{result.sailing_id} withdrawn as {result.event_id}. "
        f"{len(result.disrupted_booking_ids)} booking(s) disrupted"
        f"{': ' + ', '.join(result.disrupted_booking_ids) if result.disrupted_booking_ids else ''}. "
        f"{result.withdrawn_slot_count} sellable slot(s) withdrawn. "
        f"{result.untouched_booking_count} committed booking(s) elsewhere untouched."
    )
    return _back(ok=message)


def main() -> None:
    import uvicorn

    load_env()
    uvicorn.run(app, host="0.0.0.0", port=console_port())


if __name__ == "__main__":
    main()

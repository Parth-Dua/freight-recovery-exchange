"""Carrier operator console, on IBM's Carbon design system (see ui.py).

Pages, all scoped to one carrier:
  Sailings        what is scheduled, how full, what is at risk
  Sailing detail  the ship, its bookings, and the withdrawal review
  Pricing         the carrier agent's private settings (carrier_policies)
  Agent activity  every negotiation message the agent sent or received
  Risk            advisories and the sailings they touch

Run:  cd console && uv run uvicorn carrier_console.web:app --host 127.0.0.1 --port 8800
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from html import escape
from pathlib import Path
from urllib.parse import urlencode
from uuid import uuid4

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from carrier_console import market_view, ui
from carrier_console.config import console_port, load_env
from carrier_console.db import close_pools, get_pool
from carrier_console.market import load_snapshot, sample_snapshot
from carrier_console.preferences import (
    PRESETS,
    Preferences,
    PreferencesInvalid,
    StoredPolicy,
    VersionConflict,
    apply_preset,
    list_carriers,
    load_policy,
    preview,
    save_policy,
)
from carrier_console.queries import (
    AgentAction,
    SailingRow,
    agent_activity,
    committed_bookings,
    list_sailings,
    recent_disruptions,
    sailing_bookings,
)
from carrier_console.risk import RiskEvent, get_risk_event, load_risk_events
from carrier_console.withdrawal import WithdrawSailing, WithdrawalRejected, withdraw_sailing

STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    load_env()
    await get_pool()
    yield
    await close_pools()


app = FastAPI(title="Carrier Console", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


# --- shared --------------------------------------------------------------------------

def _status_tag(status: str) -> str:
    color = {"ACTIVE": "green", "WITHDRAWN": "red", "DISRUPTED": "red", "COMMITTED": "green",
             "RECOVERED": "teal", "PENDING_PAYMENT": "warm-gray"}.get(status, "gray")
    label = {"ACTIVE": "Scheduled"}.get(status, status.replace("_", " ").capitalize())
    return ui.tag(label, color)


def _booked(s: SailingRow) -> int:
    return s.booked_slots


def _at_risk(s: SailingRow, events: tuple[RiskEvent, ...]) -> RiskEvent | None:
    if not s.is_active:
        return None
    return next((e for e in events if s.corridor_id in e.affected_corridors and e.severity == "high"), None)


def _back(**params: str) -> RedirectResponse:
    """Back to the sailings page with a banner. Values are URL-encoded, so messages with spaces
    and colons survive the redirect."""
    return RedirectResponse("/?" + urlencode(params), status_code=303)


def _banner(params: dict[str, str]) -> str:
    if params.get("err"):
        return ui.notification("error", "Withdrawal rejected", escape(params["err"]))
    if params.get("dup"):
        return ui.notification("warning", "Already withdrawn",
                               f"{escape(params['dup'])} was withdrawn earlier. Nothing changed.")
    if params.get("ok"):
        return ui.notification("success", "Sailing withdrawn", escape(params["ok"]))
    return ""


async def _page(title: str, body: str, *, active: str, carrier_id: str) -> HTMLResponse:
    carriers = await list_carriers(await get_pool())
    return HTMLResponse(ui.shell(title, body, active=active, carrier_id=carrier_id, carriers=carriers))


async def _carrier_sailings(carrier_id: str) -> list[SailingRow]:
    return [s for s in await list_sailings(await get_pool()) if s.carrier_id == carrier_id]


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_model=None)
async def index(ok: str = "", dup: str = "", err: str = "") -> RedirectResponse:
    carriers = await list_carriers(await get_pool())
    first = carriers[0][0] if carriers else "atlas"
    query = urlencode({k: v for k, v in {"ok": ok, "dup": dup, "err": err}.items() if v})
    return RedirectResponse(f"/carrier/{first}/sailings" + (f"?{query}" if query else ""), status_code=303)


# --- Sailings -----------------------------------------------------------------------

def _hero(s: SailingRow, carrier_id: str) -> str:
    return f"""
<section class="row" style="align-items: flex-end;">
  <div class="grow">
    <p class="muted" style="margin: 0 0 8px;">Next sailing through the advisory zone</p>
    <div class="big mono">{escape(s.sailing_id)}</div>
    <p class="lede">{escape(s.origin_port)} → {escape(s.destination_port)} · departs <span class="mono">{s.departure_at:%d %b %H:%M}</span> ·
      {_booked(s)} of {s.total_slots} exchange slots booked · {s.committed_bookings} bookings</p>
    <div style="margin-top: 24px;">{ui.ship(_booked(s), s.total_slots, carrier_id, f"{_booked(s)} of {s.total_slots} exchange slots booked")}</div>
  </div>
  <div class="side" style="display: flex; flex-direction: column; gap: 16px;">
    {ui.notification("warning", "Hormuz advisory on this route", "Vessel security incident in the strait. Review before departure.")}
    <div style="display: flex; gap: 1px;">
      {ui.button_link(f"/carrier/{carrier_id}/sailings/{s.sailing_id}", "Review sailing", "primary")}
      {ui.button_link(f"/carrier/{carrier_id}/risk", "See advisory", "tertiary")}
    </div>
  </div>
</section>"""


@app.get("/carrier/{carrier_id}/sailings", response_class=HTMLResponse)
async def sailings(carrier_id: str, ok: str = "", dup: str = "", err: str = "") -> HTMLResponse:
    rows = await _carrier_sailings(carrier_id)
    events = load_risk_events()
    at_risk = [s for s in rows if _at_risk(s, events)]
    cid = escape(carrier_id)
    table_rows = [
        [
            f'<a class="cds--link mono" href="/carrier/{cid}/sailings/{escape(s.sailing_id)}">{escape(s.sailing_id)}</a>',
            f'<span class="mono">{escape(s.origin_port)} → {escape(s.destination_port)}</span>',
            f'<span class="mono">{s.departure_at:%d %b %H:%M}</span>',
            f'<span class="mono">{s.arrival_at:%d %b %H:%M}</span>',
            ui.containers(_booked(s), s.total_slots, carrier_id) + f' <span class="mono muted">{_booked(s)}/{s.total_slots}</span>',
            _status_tag(s.status) + (" " + ui.tag("Advisory", "magenta") if _at_risk(s, events) else ""),
        ]
        for s in rows
    ]
    body = f"""
<div class="stack">
  {_banner({"ok": ok, "dup": dup, "err": err})}
  <div><h1>Sailings</h1><p class="lede">{len(rows)} scheduled · {len(at_risk)} through an active advisory</p></div>
  {_hero(at_risk[0], carrier_id) if at_risk else ""}
  {ui.table("Schedule", "Exchange slots on each sailing. Colored blocks are booked.",
            ["Sailing", "Route", "Departs", "Arrives", "Slots", "Status"], table_rows,
            "No sailings. Run scripts/db.py seed from the repo root.")}
</div>"""
    return await _page("Sailings", body, active="sailings", carrier_id=carrier_id)


@app.get("/carrier/{carrier_id}/sailings/{sailing_id}", response_class=HTMLResponse)
async def sailing_detail(carrier_id: str, sailing_id: str) -> HTMLResponse:
    pool = await get_pool()
    s = next((r for r in await _carrier_sailings(carrier_id) if r.sailing_id == sailing_id), None)
    if s is None:
        return await _page("Not found", ui.notification("error", "Unknown sailing", escape(sailing_id)),
                           active="sailings", carrier_id=carrier_id)
    bookings = await sailing_bookings(pool, sailing_id)
    event = _at_risk(s, load_risk_events())
    rows = [[f'<span class="mono">{escape(b.booking_id)}</span>', escape(b.buyer_id), _status_tag(b.status),
             f'<span class="mono">{b.containers} × 20GP</span>', f'<span class="mono">{ui.money(b.total_price_minor)}</span>',
             f'<span class="mono muted">{b.created_at:%d %b %H:%M:%S}</span>'] for b in bookings]
    value = sum(b.total_price_minor for b in bookings if b.status == "COMMITTED")
    cid = escape(carrier_id)
    review = ""
    if s.is_active and event is not None:
        review = f"""
<form method="post" action="/withdraw/review" style="display: flex; gap: 1px;">
  <input type="hidden" name="sailing_id" value="{escape(s.sailing_id)}">
  <input type="hidden" name="carrier_id" value="{escape(s.carrier_id)}">
  <input type="hidden" name="risk_event_id" value="{escape(event.risk_event_id)}">
  {ui.button_link(f"/carrier/{carrier_id}/pricing", "Edit pricing", "secondary")}
  <button class="cds--btn cds--btn--danger" type="submit">Review withdrawal</button>
</form>"""
    body = f"""
<div class="stack">
  <p style="margin: 0;"><a class="cds--link" href="/carrier/{cid}/sailings">← Sailings</a></p>
  <div class="row" style="align-items: flex-end;">
    <div class="grow"><div class="big mono">{escape(s.sailing_id)}</div>
      <p class="lede">{escape(s.carrier_name)} · {escape(s.origin_port)} → {escape(s.destination_port)}</p></div>
    <div class="side">{_status_tag(s.status)}{" " + ui.tag("Hormuz advisory", "magenta") if event else ""}</div>
  </div>
  <div style="max-width: 960px;">{ui.ship(_booked(s), s.total_slots, carrier_id, f"{_booked(s)} of {s.total_slots} exchange slots booked")}</div>
  <dl class="facts" style="margin: 0;">
    <div><dt>Departs</dt><dd class="mono">{s.departure_at:%d %b %H:%M}</dd></div>
    <div><dt>Arrives</dt><dd class="mono">{s.arrival_at:%d %b %H:%M}</dd></div>
    <div><dt>Exchange slots</dt><dd>{ui.containers(_booked(s), s.total_slots, carrier_id)}</dd></div>
    <div><dt>Booked value</dt><dd class="mono">{ui.money(value)}</dd></div>
  </dl>
  {ui.table("Bookings", "Everyone holding space on this sailing.",
            ["Booking", "Buyer", "Status", "Containers", "Price", "Booked"], rows, "No bookings yet.")}
  {review}
</div>"""
    return await _page(sailing_id, body, active="sailings", carrier_id=carrier_id)


# --- Withdrawal -----------------------------------------------------------------------

def _review_body(sailing: SailingRow, risk_event: RiskEvent, bookings: list[tuple[str, str]]) -> str:
    """The confirmation step. Plain HTML on purpose: no browser popup, so it works in any
    browser, including embedded ones that block confirm() dialogs."""
    if bookings:
        affected = (
            f"<p>This will disrupt <b>{len(bookings)}</b> committed booking(s):</p><ul>"
            + "".join(f'<li class="mono">{escape(bid)} · {escape(buyer)}</li>' for bid, buyer in bookings)
            + "</ul>"
        )
    else:
        affected = "<p>No committed bookings are on this sailing, so nobody is disrupted.</p>"
    subtitle = escape(risk_event.description) + f' <span class="mono">{escape(risk_event.source_reference)}</span>'
    return f"""
<div class="stack" style="max-width: 720px;">
  <h1>Withdraw {escape(sailing.sailing_id)}?</h1>
  <p class="lede">{escape(sailing.carrier_name)} · {escape(sailing.origin_port)} → {escape(sailing.destination_port)} ·
    departs <span class="mono">{sailing.departure_at:%d %b %H:%M}</span></p>
  {ui.notification("warning", escape(risk_event.label), subtitle)}
  <div class="cds--tile">{affected}<p class="muted">The exchange starts looking for replacement space right away. Withdrawing twice changes nothing.</p></div>
  <form method="post" action="/withdraw" style="display: flex; gap: 1px;">
    <input type="hidden" name="sailing_id" value="{escape(sailing.sailing_id)}">
    <input type="hidden" name="carrier_id" value="{escape(sailing.carrier_id)}">
    <input type="hidden" name="risk_event_id" value="{escape(risk_event.risk_event_id)}">
    <a class="cds--btn cds--btn--secondary" href="/">Cancel</a>
    <button class="cds--btn cds--btn--danger" type="submit">Yes, withdraw {escape(sailing.sailing_id)}</button>
  </form>
</div>"""


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
    body = _review_body(sailing, risk_event, await committed_bookings(pool, sailing_id))
    return await _page(f"Withdraw {sailing_id}", body, active="sailings", carrier_id=carrier_id)


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

    disrupted = ", ".join(result.disrupted_booking_ids)
    message = (
        f"{result.sailing_id} withdrawn. {len(result.disrupted_booking_ids)} booking(s) disrupted"
        f"{': ' + disrupted if disrupted else ''}. The exchange is looking for replacement space."
    )
    return _back(ok=message)


# --- Risk -----------------------------------------------------------------------------

def _risk_section(event: RiskEvent, touched: list[SailingRow], carrier_id: str) -> str:
    cid = escape(carrier_id)
    rows = [[
        f'<a class="cds--link mono" href="/carrier/{cid}/sailings/{escape(s.sailing_id)}">{escape(s.sailing_id)}</a>',
        f'<span class="mono">{s.departure_at:%d %b %H:%M}</span>',
        ui.containers(_booked(s), s.total_slots, carrier_id),
        f"{s.committed_bookings}",
        f'<form method="post" action="/withdraw/review" style="margin: 0;">'
        f'<input type="hidden" name="sailing_id" value="{escape(s.sailing_id)}">'
        f'<input type="hidden" name="carrier_id" value="{cid}">'
        f'<input type="hidden" name="risk_event_id" value="{escape(event.risk_event_id)}">'
        f'<button class="cds--btn cds--btn--danger--ghost cds--btn--sm" type="submit">Review withdrawal</button></form>',
    ] for s in touched]
    return f"""
<section class="stack" style="gap: 24px;">
  <div>
    <p class="muted" style="margin: 0;"><span class="hz"></span> {escape(event.severity.upper())} · {escape(event.category.replace("_", " "))}</p>
    <h2 style="font-size: 1.75rem; font-weight: 400; margin: 8px 0 0;">{escape(event.region_code.replace("-", " ").title())}</h2>
    <p class="lede">{escape(event.description)}</p>
    <p class="mono muted" style="margin: 4px 0 0; font-size: .875rem;">{escape(event.source_reference)} · {escape(event.risk_event_id)}</p>
  </div>
  {ui.table("Your sailings in this area", "Advisories never cancel a sailing on their own.",
            ["Sailing", "Departs", "Slots", "Bookings", ""], rows, "None of your active sailings are affected.")}
</section>"""


@app.get("/carrier/{carrier_id}/risk", response_class=HTMLResponse)
async def risk(carrier_id: str) -> HTMLResponse:
    pool = await get_pool()
    rows = await _carrier_sailings(carrier_id)
    history = [h for h in await recent_disruptions(pool) if h[1] == carrier_id]
    sections = "".join(
        _risk_section(event, [s for s in rows if s.is_active and s.corridor_id in event.affected_corridors], carrier_id)
        for event in load_risk_events()
    )
    log = "".join(
        f'<tr><td class="mono">{h[5]:%d %b %H:%M:%S}</td><td class="mono">{escape(h[2])}</td>'
        f'<td>{escape(h[3] or "unknown")}</td><td>{escape(h[4])}</td></tr>' for h in history
    ) or '<tr><td colspan="4" class="muted">No withdrawals yet.</td></tr>'
    color = ui.CARRIER_COLORS.get(carrier_id, "#F26B1D")
    by_lane: dict[str, list[str]] = {}
    for s in rows:
        if s.is_active and s.corridor_id in ui.LANES:
            by_lane.setdefault(s.corridor_id, []).append(s.sailing_id)
    highlights = [{"label": ", ".join(ids), "color": color, "lane": lane, "dy": 0} for lane, ids in by_lane.items()]
    body = f"""
<div class="stack">
  <div><h1>Risk</h1><p class="lede">Advisories that touch your routes, and what each one would disrupt.</p></div>
  {ui.network_map(highlights, caption="Every route in the market in gray, yours in color, and both danger zones.")}
  {sections}
  <div class="cds--data-table-container"><div class="cds--data-table-header"><h2 class="cds--data-table-header__title">Withdrawal history</h2></div>
    <table class="cds--data-table cds--data-table--md"><thead><tr><th>Time</th><th>Sailing</th><th>By</th><th>Reason</th></tr></thead><tbody>{log}</tbody></table></div>
</div>"""
    return await _page("Risk", body, active="risk", carrier_id=carrier_id)


# --- Agent activity ---------------------------------------------------------------------

_ACTION = {
    ("carrier", "PROPOSE_OFFER"): "Quoted",
    ("buyer_agent", "COUNTER_OFFER"): "Buyer countered",
    ("carrier", "ACCEPT_COUNTER"): "Accepted counter",
    ("carrier", "REVISED_OFFER"): "Met part way",
    ("carrier", "REJECT_COUNTER"): "Held price",
}


def _activity_row(a: AgentAction, policy: dict) -> list[str]:
    unit = a.total_price_minor // a.quantity if a.total_price_minor and a.quantity else None
    floor = int(policy.get("min_price_per_unit_minor", 0))
    if a.sender_role == "buyer_agent":
        check = '<span class="muted">Incoming</span>'
    elif unit is None:
        check = '<span class="muted">n/a</span>'
    elif unit < floor:
        check = ui.tag(f"Below floor {ui.money(floor)}", "red")
    else:
        check = ui.tag("Above floor", "green")
    who = "Recovery" if a.kind == "recovery" else "Booking"
    action = _ACTION.get((a.sender_role, a.message_type), a.message_type.replace("_", " ").capitalize())
    price = f'<span class="mono">{ui.money(unit)}</span>' + (f' <span class="muted">× {a.quantity}</span>' if a.quantity else "")
    return [
        f'<span class="mono">{a.ts:%H:%M:%S}</span>',
        escape(action),
        f'<span class="mono">{escape(a.sailing_id or "n/a")}</span>',
        f'{who} · <span class="mono">{escape(a.buyer_agent_key[-6:])}</span>',
        price,
        f"Round {a.round}",
        check,
    ]


@app.get("/carrier/{carrier_id}/activity", response_class=HTMLResponse)
async def activity(carrier_id: str) -> HTMLResponse:
    pool = await get_pool()
    stored = await load_policy(pool, carrier_id)
    policy = stored.policy if stored else {}
    actions = await agent_activity(pool, carrier_id)
    rows = [_activity_row(a, policy) for a in actions]
    sent = [a for a in actions if a.sender_role == "carrier"]
    breaches = [r for r in rows if "Below floor" in r[-1]]
    alert = ui.notification("error", "Price sent below the floor",
                            "The agent's own floor check should make this impossible. Check its pricing.") if breaches else ""
    body = f"""
<div class="stack">
  <div class="row" style="align-items: flex-end; justify-content: space-between;">
    <div><h1>Agent activity</h1><p class="lede">Everything this carrier's agent said in negotiations, checked against its pricing settings.</p></div>
    <div>{ui.tag("Running", "green")} <span class="muted mono">settings v{stored.version if stored else "?"}</span></div>
  </div>
  <dl class="facts" style="margin: 0;">
    <div><dt>Messages shown</dt><dd class="mono">{len(actions)}</dd></div>
    <div><dt>Sent by the agent</dt><dd class="mono">{len(sent)}</dd></div>
    <div><dt>Below the floor</dt><dd class="mono">{len(breaches)}</dd></div>
  </dl>
  {alert}
  {ui.table("Negotiation log", "Newest first. Prices are per container.",
            ["Time", "Action", "Sailing", "Session", "Price", "Round", "Check"], rows,
            "No negotiations yet. They appear here as soon as a buyer asks for a quote.")}
</div>"""
    return await _page("Agent activity", body, active="activity", carrier_id=carrier_id)


# --- Pricing ----------------------------------------------------------------------------

_PREF_HELP = {
    "target_price": "Normal opening price for one 20ft container.",
    "min_price": "Your agent never offers or accepts less, and never reveals it.",
    "utilization_premium_pct": "How much the price rises as the ship fills.",
    "concession_empty_pct": "Most your agent will discount on a counteroffer when the ship is empty.",
    "concession_full_pct": "Most it will discount when the ship is nearly full.",
    "disruption_surge_pct": "Markup when bidding for cargo stranded by another carrier's cancellation.",
    "offer_ttl_minutes": "How long a quote stays valid.",
    "max_hold_minutes": "How long space is held while a buyer pays.",
    "payment_terms": "Shown to the buyer on the receipt.",
}

_ERROR_KEY = {
    "target_price_per_unit_minor": "target_price",
    "min_price_per_unit_minor": "min_price",
    "offer_ttl_seconds": "offer_ttl_minutes",
    "max_hold_seconds": "max_hold_minutes",
}

_GOAL_LABEL = {"fill_ship": "Fill the ship", "balanced": "Balanced", "protect_margin": "Protect margin"}


def _field(key: str, label: str, value: str, errors: dict[str, str], *, kind: str = "number") -> str:
    invalid = key in errors
    attrs = 'min="0" step="1"' if kind == "number" else 'maxlength="200"'
    wrapper = ' data-invalid="true"' if invalid else ""
    input_class = "cds--text-input cds--text-input--invalid" if invalid else "cds--text-input"
    below = (f'<div class="cds--form-requirement">{escape(errors[key])}</div>' if invalid
             else f'<div class="cds--form__helper-text">{escape(_PREF_HELP[key])}</div>')
    return f"""
<div class="cds--form-item cds--text-input-wrapper">
  <label for="{key}" class="cds--label">{label}</label>
  <div class="cds--text-input__field-outer-wrapper"><div class="cds--text-input__field-wrapper"{wrapper}>
    <input id="{key}" name="{key}" type="{kind}" {attrs} value="{escape(value)}" class="{input_class}" required>
  </div>{below}</div>
</div>"""


def _goal_switcher(current: str, errors: dict[str, str]) -> str:
    buttons = ""
    for goal in PRESETS:
        on = current == goal
        buttons += (
            f'<button type="submit" name="preset" value="{goal}" formnovalidate role="tab" aria-selected="{str(on).lower()}" '
            f'class="cds--content-switcher-btn{" cds--content-switcher--selected" if on else ""}">'
            f'<span class="cds--content-switcher__label">{_GOAL_LABEL[goal]}</span></button>'
        )
    below = (f'<div class="cds--form-requirement" style="display: block;">{escape(errors["business_goal"])}</div>'
             if "business_goal" in errors
             else '<div class="cds--form__helper-text">A goal fills in the percentages below. Adjust any of them, then save.</div>')
    return (f'<div><p class="cds--label">Business goal</p>'
            f'<div class="cds--content-switcher" role="tablist" aria-label="Business goal">{buttons}</div>{below}</div>')


def _prefs_form(stored: StoredPolicy, prefs: Preferences, errors: dict[str, str], banner: str) -> str:
    cid = escape(stored.carrier_id)
    selection = "".join(
        f'<option value="{v}"{" selected" if prefs.selection == v else ""}>{label}</option>'
        for v, label in (("earliest_arrival", "Earliest arrival"), ("earliest_departure", "Earliest departure"))
    )
    opening, lowest = preview(prefs)
    advanced_open = " open" if any(k in errors for k in ("utilization_premium_pct", "concession_empty_pct",
                                                          "concession_full_pct", "disruption_surge_pct")) else ""
    summary = (f"rise {prefs.utilization_premium_pct}% · discount {prefs.concession_empty_pct}% empty, "
               f"{prefs.concession_full_pct}% full · surge {prefs.disruption_surge_pct}%")
    return f"""
<div class="stack">
  {banner}
  <div><h1>Pricing agent</h1><p class="lede">How {escape(stored.display_name)}'s agent quotes, and how far it bends when a buyer pushes back.</p></div>
  <form method="post" action="/carrier/{cid}/pricing" class="row">
    <input type="hidden" name="expected_version" value="{stored.version}">
    <input type="hidden" name="business_goal" value="{escape(prefs.business_goal)}">
    <div class="grow stack" style="gap: 24px;">
      {_goal_switcher(prefs.business_goal, errors)}
      <div class="row" style="gap: 24px;">
        <div class="grow" style="flex-basis: 220px;">{_field("target_price", "Opening price per container (USD)", f"{prefs.target_price_per_unit_minor / 100:g}", errors)}</div>
        <div class="grow" style="flex-basis: 220px;">{_field("min_price", "Floor per container (USD)", f"{prefs.min_price_per_unit_minor / 100:g}", errors)}</div>
      </div>
      <details{advanced_open} class="cds--tile" style="padding: 16px;">
        <summary style="cursor: pointer;">Advanced pricing <span class="muted mono">· {summary}</span></summary>
        <div class="row" style="gap: 24px; margin-top: 16px;">
          <div class="grow" style="flex-basis: 220px;">{_field("utilization_premium_pct", "Price rise as the ship fills (%)", str(prefs.utilization_premium_pct), errors)}</div>
          <div class="grow" style="flex-basis: 220px;">{_field("disruption_surge_pct", "Crisis surge for stranded cargo (%)", str(prefs.disruption_surge_pct), errors)}</div>
          <div class="grow" style="flex-basis: 220px;">{_field("concession_empty_pct", "Max discount, empty ship (%)", str(prefs.concession_empty_pct), errors)}</div>
          <div class="grow" style="flex-basis: 220px;">{_field("concession_full_pct", "Max discount, full ship (%)", str(prefs.concession_full_pct), errors)}</div>
        </div>
      </details>
      <div class="row" style="gap: 24px;">
        <div class="grow" style="flex-basis: 160px;">{_field("offer_ttl_minutes", "Quote valid for (minutes)", f"{prefs.offer_ttl_seconds / 60:g}", errors)}</div>
        <div class="grow" style="flex-basis: 160px;">{_field("max_hold_minutes", "Hold space while paying (minutes)", f"{prefs.max_hold_seconds / 60:g}", errors)}</div>
        <div class="grow" style="flex-basis: 200px;"><div class="cds--form-item"><div class="cds--select">
          <label for="selection" class="cds--label">Offer first</label>
          <div class="cds--select-input__wrapper"><select id="selection" name="selection" class="cds--select-input">{selection}</select></div>
        </div></div></div>
      </div>
      {_field("payment_terms", "Payment terms", prefs.payment_terms, errors, kind="text")}
    </div>
    <aside class="side cds--tile stack" style="gap: 20px; padding: 24px;">
      <div><p class="cds--label" style="margin: 0;">Live quote preview</p>
        <p class="muted" style="margin: 4px 0 12px;">A 4-slot ship, half full</p>{ui.containers(2, 4, stored.carrier_id)}</div>
      <div><p class="muted" style="margin: 0;">Opens at</p><div class="big mono">{ui.money(opening)}</div></div>
      <div><p class="muted" style="margin: 0;">Accepts counters down to</p><div class="mono" style="font-size: 2rem;">{ui.money(lowest)}</div>
        <p class="muted" style="margin: 8px 0 0; font-size: .875rem;">Only your agent sees these limits.</p></div>
      <div style="display: flex; gap: 1px;"><a class="cds--btn cds--btn--secondary" href="/carrier/{cid}/pricing">Discard</a>
        <button type="submit" class="cds--btn cds--btn--primary">Save settings</button></div>
      <p class="mono muted" style="margin: 0; font-size: .75rem;">Version {stored.version} · saved {stored.updated_at:%d %b %H:%M:%S} · applies from the next quote</p>
    </aside>
  </form>
</div>"""


def _form_to_prefs(form: dict[str, str], current: Preferences) -> tuple[Preferences, dict[str, str]]:
    """Parse the posted form. Bad numbers become field errors instead of a 422."""
    errors: dict[str, str] = {}

    def whole(key: str, scale: int = 1) -> int:
        try:
            return round(float(form.get(key, "")) * scale)
        except ValueError:
            errors[key] = "Enter a number."
            return 0

    prefs = Preferences(
        business_goal=form.get("business_goal", current.business_goal),
        target_price_per_unit_minor=whole("target_price", 100),
        min_price_per_unit_minor=whole("min_price", 100),
        utilization_premium_pct=whole("utilization_premium_pct"),
        concession_empty_pct=whole("concession_empty_pct"),
        concession_full_pct=whole("concession_full_pct"),
        disruption_surge_pct=whole("disruption_surge_pct"),
        offer_ttl_seconds=whole("offer_ttl_minutes", 60),
        max_hold_seconds=whole("max_hold_minutes", 60),
        selection=form.get("selection", current.selection),
        payment_terms=form.get("payment_terms", ""),
    )
    return prefs, errors


async def _pricing_page(carrier_id: str, prefs: Preferences | None = None,
                        errors: dict[str, str] | None = None, banner: str = "") -> HTMLResponse:
    stored = await load_policy(await get_pool(), carrier_id)
    if stored is None:
        return await _page("Unknown carrier", ui.notification("error", "Unknown carrier", escape(carrier_id)),
                           active="pricing", carrier_id=carrier_id)
    body = _prefs_form(stored, prefs or stored.preferences(), errors or {}, banner)
    return await _page("Pricing", body, active="pricing", carrier_id=carrier_id)


@app.get("/carrier/{carrier_id}/preferences", response_model=None)
async def old_preferences(carrier_id: str) -> RedirectResponse:
    return RedirectResponse(f"/carrier/{carrier_id}/pricing", status_code=303)


@app.get("/carrier/{carrier_id}/pricing", response_class=HTMLResponse)
async def pricing(carrier_id: str, saved: str = "") -> HTMLResponse:
    banner = (ui.notification("success", "Saved", f"Version {escape(saved)} is live. Your agent uses it from its next quote.")
              if saved else "")
    return await _pricing_page(carrier_id, banner=banner)


@app.post("/carrier/{carrier_id}/pricing", response_class=HTMLResponse, response_model=None)
async def save_pricing(carrier_id: str, request: Request) -> HTMLResponse | RedirectResponse:
    pool = await get_pool()
    stored = await load_policy(pool, carrier_id)
    if stored is None:
        return await _pricing_page(carrier_id)
    form = {k: v for k, v in (await request.form()).items() if isinstance(v, str)}
    prefs, errors = _form_to_prefs(form, stored.preferences())

    if form.get("preset"):  # a goal button: fill the fields, do not save yet
        try:
            prefs = apply_preset(prefs if not errors else stored.preferences(), form["preset"])
        except PreferencesInvalid as invalid:
            errors.update(invalid.errors)
        banner = ui.notification("info", "Preset applied, not saved", "Review the numbers, then save.")
        return await _pricing_page(carrier_id, prefs, errors, banner)

    if errors:
        return await _pricing_page(carrier_id, prefs, errors)
    try:
        expected = int(form.get("expected_version", "0"))
        saved = await save_policy(pool, carrier_id, prefs, expected_version=expected,
                                  actor_id=f"operator:{carrier_id}")
    except PreferencesInvalid as invalid:
        shown = {_ERROR_KEY.get(k, k): v for k, v in invalid.errors.items()}
        return await _pricing_page(carrier_id, prefs, shown)
    except VersionConflict:
        banner = ui.notification("error", "Someone else saved first", "These are the latest settings. Make your change again.")
        return await _pricing_page(carrier_id, banner=banner)
    return RedirectResponse(f"/carrier/{carrier_id}/pricing?saved={saved.version}", status_code=303)


# --- Market view (read-only, for the demo) ---------------------------------------------------

def xray_allowed(sample: bool) -> bool:
    """X-ray reveals private limits. Always on for the labelled sample; for live data only when the
    console was started with MARKET_XRAY=1, so it cannot be left on by accident."""
    return sample or os.getenv("MARKET_XRAY") == "1"


@app.get("/market", response_class=HTMLResponse)
async def market(step: str = "negotiation", session: str = "", xray: str = "", sample: str = "",
                 run: str = "") -> HTMLResponse:
    use_sample = sample == "1"
    allowed = xray_allowed(use_sample)
    show_xray = allowed and xray == "1"
    params = {"step": step, "session": session, "xray": "1" if show_xray else "", "sample": sample, "run": run}

    def link(**change: str) -> str:
        merged = {**params, **change}
        return "/market?" + urlencode({k: v for k, v in merged.items() if v})

    snap = sample_snapshot() if use_sample else await load_snapshot(await get_pool(), run or None, xray=show_xray)
    if snap is None:
        body = ui.notification("info", "No recovery has run yet",
                               'Withdraw a sailing in the carrier console and this view fills in. '
                               '<a class="cds--link" href="/market?sample=1">See the sample story</a>.')
        return HTMLResponse(market_view.market_shell(body, snap=None, step=step, xray=False, xray_allowed=False,
                                                     params=params))
    if step == "disruption":
        body = market_view.disruption(snap)
    elif step == "allocation":
        body = market_view.allocation(snap)
    else:
        step = "negotiation"
        body = market_view.negotiation(snap, snap.session(session or None), show_xray, link)
    return HTMLResponse(market_view.market_shell(body, snap=snap, step=step, xray=show_xray, xray_allowed=allowed,
                                                 params=params))


def main() -> None:
    import uvicorn

    load_env()
    uvicorn.run(app, host="127.0.0.1", port=console_port())


if __name__ == "__main__":
    main()

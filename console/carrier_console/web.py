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

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from carrier_console.config import console_port, load_env
from carrier_console.db import close_pools, get_pool
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
nav.tabs { display: flex; gap: 16px; flex-wrap: wrap; margin-top: 10px; font-size: 13px; }
nav.tabs a { color: var(--muted); text-decoration: none; }
nav.tabs a:hover, nav.tabs a.on { color: var(--ink); }
form.prefs { display: grid; gap: 14px; }
form.prefs .field { display: grid; gap: 4px; }
form.prefs label { font-weight: 600; font-size: 14px; }
form.prefs input, form.prefs select { font: inherit; background: var(--bg); color: var(--ink);
  border: 1px solid var(--line); border-radius: 7px; padding: 8px 10px; max-width: 320px; }
form.prefs .help { color: var(--muted); font-size: 12px; }
form.prefs .error { color: var(--danger); font-size: 12px; }
form.prefs .goals { display: flex; gap: 10px; flex-wrap: wrap; }
form.prefs button.save { background: var(--accent); }
button.preset { background: var(--panel); color: var(--ink); border: 1px solid var(--line); font-weight: 500; }
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
      <div class="meta">{escape(risk_event.label)} · {escape(risk_event.source_reference)} (simulated)</div>
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


def _nav(current: str = "sailings", carrier_id: str = "") -> str:
    prefs_href = f"/carrier/{escape(carrier_id)}/preferences" if carrier_id else "/preferences"
    return (
        '<nav class="tabs">'
        f'<a href="/" class="{"on" if current == "sailings" else ""}">Sailings and cancellations</a>'
        f'<a href="{prefs_href}" class="{"on" if current == "preferences" else ""}">Pricing agent preferences</a>'
        "</nav>"
    )


def _shell(body: str, nav: str = "") -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Carrier Console</title><style>{STYLE}</style></head>
<body><div class="wrap">
  <header>
    <h1>Carrier Console</h1>
    <p class="sub">Operator view. Withdraw a sailing when an advisory makes it unsafe to run.</p>
    {nav or _nav()}
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


# --- Pricing agent preferences ---------------------------------------------------------

_PREF_HELP = {
    "target_price": "Normal opening price for one 20ft container.",
    "min_price": "Floor. Your agent never offers or accepts less, and never reveals it.",
    "utilization_premium_pct": "How much the price rises as the ship fills (full ship = this much more).",
    "concession_empty_pct": "Most your agent will discount on a counteroffer when the ship is empty.",
    "concession_full_pct": "Most it will discount when the ship is nearly full.",
    "disruption_surge_pct": "Markup when bidding for cargo stranded by another carrier's cancellation.",
    "offer_ttl_minutes": "How long a quote stays valid.",
    "max_hold_minutes": "How long space is held while a buyer pays.",
    "selection": "Which sailing your agent offers first.",
    "payment_terms": "Shown to the buyer on the receipt.",
}

_ERROR_KEY = {
    "target_price_per_unit_minor": "target_price",
    "min_price_per_unit_minor": "min_price",
    "offer_ttl_seconds": "offer_ttl_minutes",
    "max_hold_seconds": "max_hold_minutes",
}

_GOAL_LABEL = {"fill_ship": "Fill the ship", "balanced": "Balanced", "protect_margin": "Protect margin"}


def _money(minor: int) -> str:
    return f"${minor / 100:,.0f}"


def _prefs_form(stored: StoredPolicy, prefs: Preferences, errors: dict[str, str], banner: str) -> str:
    def err(key: str) -> str:
        return f'<span class="error">{escape(errors[key])}</span>' if key in errors else ""

    def number(key: str, label: str, value: int | float, unit: str) -> str:
        return f"""
    <div class="field">
      <label for="{key}">{label} ({unit})</label>
      <input id="{key}" name="{key}" type="number" min="0" step="1" value="{value:g}" required>
      <span class="help">{escape(_PREF_HELP[key])}</span>{err(key)}
    </div>"""

    selection = "".join(
        f'<option value="{v}"{" selected" if prefs.selection == v else ""}>{label}</option>'
        for v, label in (("earliest_arrival", "Earliest arrival"), ("earliest_departure", "Earliest departure"))
    )
    presets = "".join(
        f'<button type="submit" name="preset" value="{g}" class="preset" formnovalidate>{_GOAL_LABEL[g]}</button>'
        for g in PRESETS
    )
    opening, lowest = preview(prefs)
    cid = escape(stored.carrier_id)
    return f"""
  {banner}
  <h2>{escape(stored.display_name)} · pricing agent</h2>
  <div class="card">
    <form class="prefs" method="post" action="/carrier/{cid}/preferences">
      <input type="hidden" name="expected_version" value="{stored.version}">
      <input type="hidden" name="business_goal" value="{escape(prefs.business_goal)}">
      <div class="field">
        <label>Business goal: {_GOAL_LABEL.get(prefs.business_goal, prefs.business_goal)}</label>
        <div class="goals">{presets}</div>
        <span class="help">Picking a goal fills in the percentages below. Adjust any of them, then save.</span>
        {err("business_goal")}
      </div>
      {number("target_price", "Target price per container", prefs.target_price_per_unit_minor / 100, "USD")}
      {number("min_price", "Floor price per container", prefs.min_price_per_unit_minor / 100, "USD")}
      {number("utilization_premium_pct", "Price rise as the ship fills", prefs.utilization_premium_pct, "%")}
      {number("concession_empty_pct", "Max counteroffer discount, empty ship", prefs.concession_empty_pct, "%")}
      {number("concession_full_pct", "Max counteroffer discount, full ship", prefs.concession_full_pct, "%")}
      {number("disruption_surge_pct", "Crisis markup for stranded cargo", prefs.disruption_surge_pct, "%")}
      {number("offer_ttl_minutes", "Quote valid for", prefs.offer_ttl_seconds / 60, "minutes")}
      {number("max_hold_minutes", "Hold space while a buyer pays", prefs.max_hold_seconds / 60, "minutes")}
      <div class="field">
        <label for="selection">Offer first</label>
        <select id="selection" name="selection">{selection}</select>
        <span class="help">{escape(_PREF_HELP["selection"])}</span>{err("selection")}
      </div>
      <div class="field">
        <label for="payment_terms">Payment terms</label>
        <input id="payment_terms" name="payment_terms" type="text" maxlength="200"
               value="{escape(prefs.payment_terms)}" required style="max-width:100%">
        <span class="help">{escape(_PREF_HELP["payment_terms"])}</span>{err("payment_terms")}
      </div>
      <p class="meta">With these settings, a 4-slot ship that is half full opens at
        <b>{_money(opening)}</b> per container and accepts counters down to <b>{_money(lowest)}</b>.</p>
      <div class="actions">
        <button type="submit" class="save">Save preferences</button>
        <a class="ghost" href="/carrier/{cid}/preferences">Discard changes</a>
        <span class="meta">Version {stored.version} · saved {stored.updated_at:%d %b %H:%M}</span>
      </div>
    </form>
  </div>
  <p class="note">These numbers stay with {escape(stored.display_name)}'s agent. Buyers and the exchange
    never see them. The agent rereads them on every quote, so a save applies to its next offer.
    Re-seeding the demo market resets them to the fixture.</p>"""


def _carrier_switcher(carriers: list[tuple[str, str]], current: str) -> str:
    links = " · ".join(
        f'<a href="/carrier/{escape(cid)}/preferences" class="{"on" if cid == current else ""}">{escape(name)}</a>'
        for cid, name in carriers
    )
    return f'<nav class="tabs">{links}</nav>'


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


async def _prefs_page(carrier_id: str, prefs: Preferences | None = None,
                      errors: dict[str, str] | None = None, banner: str = "") -> HTMLResponse:
    pool = await get_pool()
    stored = await load_policy(pool, carrier_id)
    carriers = await list_carriers(pool)
    if stored is None:
        return HTMLResponse(_shell(f'<div class="banner err"><b>Unknown carrier</b>{escape(carrier_id)}</div>',
                                   _nav("preferences")), status_code=404)
    body = _carrier_switcher(carriers, carrier_id) + _prefs_form(
        stored, prefs or stored.preferences(), errors or {}, banner)
    return HTMLResponse(_shell(body, _nav("preferences", carrier_id)))


@app.get("/preferences", response_model=None)
async def preferences_home() -> RedirectResponse:
    carriers = await list_carriers(await get_pool())
    target = f"/carrier/{carriers[0][0]}/preferences" if carriers else "/"
    return RedirectResponse(target, status_code=303)


@app.get("/carrier/{carrier_id}/preferences", response_class=HTMLResponse)
async def preferences(carrier_id: str, saved: str = "") -> HTMLResponse:
    banner = (f'<div class="banner ok"><b>Saved</b>Version {escape(saved)} is live. '
              "Your agent uses it from its next quote.</div>") if saved else ""
    return await _prefs_page(carrier_id, banner=banner)


@app.post("/carrier/{carrier_id}/preferences", response_class=HTMLResponse, response_model=None)
async def save_preferences(carrier_id: str, request: Request) -> HTMLResponse | RedirectResponse:
    pool = await get_pool()
    stored = await load_policy(pool, carrier_id)
    if stored is None:
        return await _prefs_page(carrier_id)
    form = {k: v for k, v in (await request.form()).items() if isinstance(v, str)}
    prefs, errors = _form_to_prefs(form, stored.preferences())

    if form.get("preset"):  # a goal button: fill the fields, do not save yet
        try:
            prefs = apply_preset(prefs if not errors else stored.preferences(), form["preset"])
        except PreferencesInvalid as invalid:
            errors.update(invalid.errors)
        banner = ('<div class="banner warn"><b>Preset applied, not saved</b>'
                  "Review the numbers, then save.</div>")
        return await _prefs_page(carrier_id, prefs, errors, banner)

    if errors:
        return await _prefs_page(carrier_id, prefs, errors)
    try:
        expected = int(form.get("expected_version", "0"))
        saved = await save_policy(pool, carrier_id, prefs, expected_version=expected,
                                  actor_id=f"operator:{carrier_id}")
    except PreferencesInvalid as invalid:
        shown = {_ERROR_KEY.get(k, k): v for k, v in invalid.errors.items()}
        return await _prefs_page(carrier_id, prefs, shown)
    except VersionConflict:
        banner = ('<div class="banner err"><b>Someone else saved first</b>'
                  "These are the latest settings. Make your change again.</div>")
        return await _prefs_page(carrier_id, banner=banner)
    return RedirectResponse(f"/carrier/{carrier_id}/preferences?saved={saved.version}", status_code=303)


def main() -> None:
    import uvicorn

    load_env()
    uvicorn.run(app, host="0.0.0.0", port=console_port())


if __name__ == "__main__":
    main()

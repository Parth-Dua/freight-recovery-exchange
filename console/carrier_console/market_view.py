"""Market view pages: Disruption, Negotiation (with optional X-ray), Allocation.

Read-only. Rendered from a MarketSnapshot (market.py), so the same markup shows a live
recovery run or the labelled sample story.
"""

from __future__ import annotations

from html import escape
from urllib.parse import urlencode

from carrier_console import ui
from carrier_console.market import MarketSnapshot, Session

MAGENTA = "#E5399B"
BLUE = "#4589FF"
AMBER = "#F1C21B"
GRAY = "#6F6F6F"
INK = "#F4F4F4"

STEPS = (("disruption", "Disruption"), ("negotiation", "Negotiation"), ("allocation", "Allocation"))

MARKET_STYLE = """
.steps { display: flex; gap: 40px; border-bottom: 1px solid var(--cds-border-subtle-01); margin-bottom: 48px; flex-wrap: wrap; font-size: 1.0625rem; }
.steps a { padding: 12px 0; color: var(--cds-text-secondary); text-decoration: none; border-bottom: 2px solid transparent; }
.steps a.on { color: var(--cds-text-primary); border-bottom-color: var(--cds-border-interactive); }
.xray-bar { background: rgba(229,57,155,.12); border-bottom: 1px solid #E5399B; color: #FF7EB6; padding: 8px 32px;
            position: fixed; top: 48px; left: 0; right: 0; z-index: 5; font-size: .875rem; }
.msg { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 6px 16px; padding: 20px 0; border-bottom: 1px solid var(--cds-border-subtle-01); }
.msg .who { font-weight: 600; } .msg .text { grid-column: 1 / 3; color: var(--cds-text-secondary); }
.msg .price { grid-column: 1 / 3; font-size: 2rem; line-height: 1.2; }
.versus { display: flex; flex-wrap: wrap; gap: 1px; }
.versus > div { flex: 1 1 320px; padding: 32px; min-width: 0; }
.versus .n { font-size: clamp(3.5rem, 9vw, 6.5rem); line-height: 1; font-weight: 300; margin: 16px 0; }
.convo { display: flex; gap: 48px; flex-wrap: wrap; }
.convo > div { flex: 1 1 380px; min-width: 0; }
.stranded { display: inline-flex; flex-direction: column; align-items: center; gap: 8px; }
.stranded .box { position: relative; width: 104px; height: 56px; background-color: #F26B1D; border: 1px solid rgba(0,0,0,.5);
                 background-image: repeating-linear-gradient(90deg, rgba(0,0,0,.18) 0 2px, transparent 2px 7px);
                 display: flex; align-items: center; justify-content: center; }
.stranded .box b { background: #F4F4F4; color: #161616; font-size: 1.25rem; width: 32px; height: 32px;
                   display: flex; align-items: center; justify-content: center; }
.stranded .id { font-size: .8125rem; color: var(--cds-text-secondary); }
"""


def _money(minor: int | None) -> str:
    return ui.money(minor)


def market_shell(body: str, *, snap: MarketSnapshot | None, step: str, xray: bool, xray_allowed: bool,
                 params: dict[str, str]) -> str:
    def link(**change: str) -> str:
        merged = {**params, **change}
        return "/market?" + urlencode({k: v for k, v in merged.items() if v})

    steps = "".join(
        f'<a class="{"on" if key == step else ""}" href="{link(step=key)}"><span class="mono">{i}</span>&nbsp; {label}</a>'
        for i, (key, label) in enumerate(STEPS, 1)
    )
    toggle = ""
    if xray_allowed and step == "negotiation":
        toggle = (f'<a class="cds--header__menu-item" href="{link(xray="" if xray else "1")}" style="color: {MAGENTA if xray else "inherit"};">'
                  f'X-ray: {"on" if xray else "off"}</a>')
    sample = ui.tag("Sample story", "warm-gray") if snap and snap.sample else ""
    bar = ('<div class="xray-bar">X-ray on. These are private limits the agents never shared with each other. '
           'Demo only.</div>') if xray else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Market view · Freight exchange</title>
<link rel="stylesheet" href="{ui.CARBON_CSS}">
<link rel="stylesheet" href="{ui.FONTS}">
<style>{ui.STYLE}{MARKET_STYLE}</style></head>
<body class="cds--g100">
<header class="cds--header" aria-label="Freight exchange market view">
  <a class="cds--header__name" href="{link()}"><span class="cds--header__name--prefix">Freight exchange</span>&nbsp;Market view</a>
  <div class="cds--header__global">{sample}{toggle}<a class="cds--link" href="/">Carrier console</a><span class="muted hide-narrow" style="font-size: .875rem;">Demo view · read only</span></div>
</header>
{bar}
<main class="page" style="{"padding-top: 112px;" if xray else ""}"><nav class="steps" aria-label="Recovery steps">{steps}</nav>{body}</main>
</body></html>"""


def _label(snap: MarketSnapshot, booking_id: str) -> str:
    s = snap.booking(booking_id)
    return f"Buyer {s.label}" if s else booking_id


def disruption(snap: MarketSnapshot) -> str:
    containers = "".join(
        f'<span class="stranded"><span class="box"><b>{s.label}</b></span><span class="id mono">{escape(s.booking_id)}</span></span>'
        for s in snap.stranded for _ in range(s.quantity)
    )
    total = sum(s.quantity for s in snap.stranded)
    rows = [[f"Buyer {s.label}", f'<span class="mono">{escape(s.booking_id)}</span>', f"{s.quantity} × 20GP",
             f'<span class="mono">{s.due:%d %b %H:%M}</span>', "Yes" if s.must_deliver else "No"] for s in snap.stranded]
    pairs = []
    for s in snap.sessions:
        c = snap.carrier(s.carrier_id)
        pairs.append([_label(snap, s.booking_id), escape(c.name), f'<span class="mono">{escape(c.sailing_id or "")}</span>',
                      ui.tag("Paired", "teal")])
    for c in snap.carriers:
        if c.note and not any(s.carrier_id == c.carrier_id for s in snap.sessions):
            pairs.append(["Everyone", escape(c.name), "", ui.tag(c.note, "gray")])
    return f"""
<div class="stack">
  <div><div class="big mono">{escape(snap.sailing_id)} withdrawn</div>
    <p class="lede">{escape(snap.carrier(snap.withdrawn_by).name)} pulled the sailing at <span class="mono">{snap.withdrawn_at:%H:%M:%S}</span>
      over the Hormuz advisory. {total} containers have no ship.</p></div>
  <div class="row" style="align-items: flex-end;">
    <div class="grow">{ui.ship(0, snap.withdrawn_by, "exchange slots now empty")}
      <p class="cds--label" style="margin: 32px 0 12px;">Stranded containers</p>
      <div style="display: flex; gap: 16px; flex-wrap: wrap;">{containers}</div></div>
  </div>
  {ui.table("Stranded bookings", "Posted to the board in this order. Prices and limits stay private.",
            ["Buyer", "Booking", "Containers", "Due", "Must deliver"], rows, "Nobody was on this sailing.")}
  {ui.table("Negotiations opened", "A buyer is only paired with a carrier that has space and arrives before that buyer's deadline.",
            ["Buyer", "Carrier", "Sailing", ""], pairs, "No carrier could help.")}
</div>"""


def _line_state(snap: MarketSnapshot, s: Session) -> tuple[str, str, str]:
    """(color, dash, label) for a session line."""
    leg = snap.leg_for(s.booking_id)
    if leg and leg.carrier_id == s.carrier_id and leg.needs_approval and leg.status != "CONFIRMED":
        return AMBER, "6 4", "waiting on buyer"
    if s.status == "ACCEPTED":
        return INK, "", f"{_money(s.final_price_minor)} ✓"
    if s.status == "OPEN":
        return BLUE, "4 4", "bargaining"
    return GRAY, "4 4", "refused"


def _graph(snap: MarketSnapshot, selected: Session | None, xray: bool, link) -> str:
    """Buyers on the left, carriers on the right, one straight line per negotiation. Each line's
    label sits on its own dark plate near the carrier end, stacked so labels never overlap."""
    row_b, row_c = 190, 140
    buyers = {s.booking_id: 150 + i * row_b for i, s in enumerate(snap.stranded)}
    carriers = {c.carrier_id: 150 + j * row_c for j, c in enumerate(snap.carriers)}
    height = max([*buyers.values(), *carriers.values()]) + 120
    bx, cx = 230, 600  # line ends: right edge of buyer labels, left edge of carrier boxes
    lines, plates, notes = [], [], []
    per_carrier: dict[str, int] = {}
    for s in snap.sessions:
        if s.booking_id not in buyers or s.carrier_id not in carriers:
            continue
        ci = per_carrier[s.carrier_id] = per_carrier.get(s.carrier_id, -1) + 1
        y1, y2 = buyers[s.booking_id], carriers[s.carrier_id]
        color, dash, label = _line_state(snap, s)
        on = bool(selected and s.session_id == selected.session_id)
        stroke = BLUE if on else color
        lines.append(f'<a href="{link(session=s.session_id)}"><line x1="{bx}" y1="{y1}" x2="{cx}" y2="{y2}" stroke="{stroke}" '
                     f'stroke-width="{4 if on else 2}" stroke-dasharray="{dash}" stroke-linecap="round"></line>'
                     f'<line x1="{bx}" y1="{y1}" x2="{cx}" y2="{y2}" stroke="transparent" stroke-width="16"></line></a>')
        b = snap.booking(s.booking_id)
        text = f"{b.label if b else '?'} · {label}"
        width = 14 + len(text) * 8.4
        py = y2 - 34 - ci * 30
        plates.append(f'<a href="{link(session=s.session_id)}"><rect x="{cx - width - 6:.0f}" y="{py - 15}" width="{width:.0f}" height="24" '
                      f'fill="#161616" stroke="{stroke}" stroke-width="1"></rect>'
                      f'<text x="{cx - 13}" y="{py + 2}" text-anchor="end" fill="{stroke}" font-size="14" '
                      f'font-family="IBM Plex Mono">{escape(text)}</text></a>')
        floor = snap.floors_minor.get(s.carrier_id)
        if xray and b and floor and b.max_per_container_minor < floor:
            yb = buyers[s.booking_id] + 44
            notes.append(f'<rect x="20" y="{yb}" width="210" height="48" fill="#161616" stroke="{MAGENTA}"></rect>'
                         f'<text x="30" y="{yb + 20}" fill="{MAGENTA}" font-size="14">Outside limits, needs OK</text>'
                         f'<text x="30" y="{yb + 38}" fill="{MAGENTA}" font-size="13" font-family="IBM Plex Mono">'
                         f'{_money(b.max_per_container_minor)} max &lt; {_money(floor)} floor</text>')
    nodes = []
    for s in snap.stranded:
        y = buyers[s.booking_id]
        nodes.append(f'<rect x="20" y="{y - 20}" width="64" height="40" fill="#F26B1D"></rect>'
                     f'<rect x="20" y="{y - 20}" width="64" height="40" fill="url(#ribs)"></rect>'
                     f'<rect x="40" y="{y - 12}" width="24" height="24" fill="#F4F4F4"></rect>'
                     f'<text x="52" y="{y + 6}" text-anchor="middle" fill="#161616" font-size="16" font-weight="600">{s.label}</text>'
                     f'<text x="98" y="{y - 2}" fill="{INK}" font-size="18" font-weight="600">Buyer {s.label}</text>'
                     f'<text x="98" y="{y + 20}" fill="#A8A8A8" font-size="14">{s.quantity} × 20GP · by {s.due:%d %b}</text>')
    for c in snap.carriers:
        y = carriers[c.carrier_id]
        color = ui.CARRIER_COLORS.get(c.carrier_id, "#8D8D8D")
        sub = " · ".join(p for p in (c.sailing_id or "", f"arrives {c.arrival_at:%d %b}" if c.arrival_at else "", c.note) if p)
        nodes.append(f'<rect x="{cx + 8}" y="{y - 20}" width="64" height="40" fill="{color}"></rect>'
                     f'<rect x="{cx + 8}" y="{y - 20}" width="64" height="40" fill="url(#ribs)"></rect>'
                     f'<text x="{cx + 88}" y="{y - 2}" fill="{INK}" font-size="18" font-weight="600">{escape(c.name.split()[0])}</text>'
                     f'<text x="{cx + 88}" y="{y + 20}" fill="#A8A8A8" font-size="14">{escape(sub)}</text>')
    return f"""<svg viewBox="0 0 1000 {height}" role="img" aria-label="Straight lines from stranded buyers to the carriers negotiating with them" style="width: 100%; height: auto; font-family: 'IBM Plex Sans', sans-serif;">
  <defs><pattern id="ribs" width="7" height="7" patternUnits="userSpaceOnUse"><rect width="2" height="7" fill="rgba(0,0,0,.2)"></rect></pattern></defs>
  {"".join(lines)}{"".join(nodes)}{"".join(plates)}{"".join(notes)}
</svg>"""


def _price_track(snap: MarketSnapshot, s: Session) -> str:
    b = snap.booking(s.booking_id)
    floor, ceiling = snap.floors_minor.get(s.carrier_id), b.max_per_container_minor if b else None
    prices = [m.price_minor for m in s.messages if m.price_minor]
    if not prices or not floor or not ceiling:
        return ""
    lo = (min(prices + [floor, ceiling]) // 10_000 - 1) * 10_000
    hi = (max(prices + [floor, ceiling]) // 10_000 + 2) * 10_000

    def x(v: int) -> float:
        return 20 + (v - lo) / (hi - lo) * 480

    left, right = sorted((x(floor), x(ceiling)))
    dots, path = [], []
    for i, m in enumerate(mm for mm in s.messages if mm.price_minor):
        y = 44 + i * 34
        last = i == len(prices) - 1
        color = "#F1C21B" if last else (ui.CARRIER_COLORS.get(s.carrier_id, INK) if m.sender_role == "carrier" else "#F26B1D")
        path.append(f"{x(m.price_minor):.0f} {y}")
        dots.append(f'<circle cx="{x(m.price_minor):.0f}" cy="{y}" r="{7 if last else 6}" fill="{color}"></circle>'
                    f'<text x="{x(m.price_minor) + 12:.0f}" y="{y + 4}" fill="{color if last else INK}" font-size="12" font-family="IBM Plex Mono">'
                    f'{i + 1} · {m.price_minor / 100:,.0f}{" agreed" if last and s.status == "ACCEPTED" else ""}</text>')
    bottom = 44 + len(prices) * 34
    gap_low = (s.final_price_minor or prices[-1]) - floor
    gap_high = ceiling - (s.final_price_minor or prices[-1])
    fits = floor <= ceiling
    caption = (f"{escape(snap.carrier(s.carrier_id).name.split()[0])} could have gone {_money(gap_low)} lower. "
               f"Buyer {b.label} could have paid {_money(gap_high)} more. Neither knew.") if fits and gap_low >= 0 and gap_high >= 0 else (
               f"Buyer {b.label}'s limit is below the carrier's floor: no deal inside both limits, so the buyer is asked.")
    return f"""<div class="stack" style="gap: 8px; margin-top: 16px;">
  <p class="cds--label" style="color: {MAGENTA}; margin: 0;">What neither agent could see</p>
  <svg viewBox="0 0 520 {bottom + 40}" role="img" aria-label="Price track with the carrier's hidden floor and the buyer's hidden maximum" style="width: 100%; height: auto;">
    {f'<rect x="{left:.0f}" y="22" width="{right - left:.0f}" height="{bottom - 22}" fill="rgba(229,57,155,.12)"></rect>' if fits else ""}
    <line x1="20" y1="{bottom}" x2="500" y2="{bottom}" stroke="#525252"></line>
    <text x="20" y="{bottom + 18}" fill="#A8A8A8" font-size="11" font-family="IBM Plex Mono">{lo / 100:,.0f}</text>
    <text x="500" y="{bottom + 18}" fill="#A8A8A8" font-size="11" font-family="IBM Plex Mono" text-anchor="end">{hi / 100:,.0f}</text>
    <line x1="{x(floor):.0f}" y1="16" x2="{x(floor):.0f}" y2="{bottom}" stroke="{MAGENTA}" stroke-dasharray="4 3"></line>
    <line x1="{x(ceiling):.0f}" y1="16" x2="{x(ceiling):.0f}" y2="{bottom}" stroke="{MAGENTA}" stroke-dasharray="4 3"></line>
    <text x="{x(floor) + 4:.0f}" y="12" fill="{MAGENTA}" font-size="12">floor {_money(floor)}</text>
    <text x="{x(ceiling) - 4:.0f}" y="12" fill="{MAGENTA}" font-size="12" text-anchor="end">Buyer {b.label}'s max {_money(ceiling)}</text>
    <path d="M{" L".join(path)}" fill="none" stroke="#C6C6C6" stroke-width="1.5"></path>
    {"".join(dots)}
  </svg>
  <p style="margin: 0;">{caption}</p>
  <p class="muted" style="margin: 0; font-size: .875rem;">Buyer {b.label}'s max = {_money(b.original_price_minor // max(b.quantity, 1))} original
    + {_money(b.allowance_minor // max(b.quantity, 1))} pre-approved, per container.</p>
</div>"""


def negotiation(snap: MarketSnapshot, selected: Session | None, xray: bool, link) -> str:
    agreed = sum(s.status == "ACCEPTED" for s in snap.sessions)
    refused = sum(s.status in ("REJECTED", "EXHAUSTED", "EXPIRED") for s in snap.sessions)
    waiting = sum(1 for l in snap.legs if l.needs_approval and l.status != "CONFIRMED")
    convo = ""

    if selected:
        msgs = "".join(
            f'<div class="msg"><span class="who">{escape(snap.carrier(selected.carrier_id).name.split()[0] if m.sender_role == "carrier" else _label(snap, selected.booking_id))}\'s agent</span>'
            f'<span class="mono muted">{m.ts:%H:%M:%S}</span><span class="text">"{escape(m.text)}"</span>'
            f'<span class="price mono">{_money(m.price_minor) if m.price_minor else "no change"}</span></div>'
            for m in selected.messages
        )
        result = (f'<p>✓ Agreed at <span class="mono">{_money(selected.final_price_minor)}</span> per container.</p>'
                  if selected.status == "ACCEPTED" else f'<p class="muted">{escape(selected.reason or selected.status.lower())}</p>')
        convo = (f'<section><h2 style="font-size: 1.75rem; font-weight: 400; margin: 0;">{_label(snap, selected.booking_id)} ↔ '
                 f'{escape(snap.carrier(selected.carrier_id).name.split()[0])}</h2>'
                 f'<p class="muted" style="margin: 6px 0 16px;">Recovery negotiation <span class="mono">{escape(selected.session_id)}</span> · '
                 f'written by the two agents, nobody typed these</p>'
                 f'<div class="convo"><div>{msgs}{result}</div>'
                 f'{"<div>" + _price_track(snap, selected) + "</div>" if xray else ""}</div></section>')
    return f"""
<div class="stack">
  <div><h1>Agents are bargaining</h1><p class="lede">{len(snap.sessions)} sessions · {agreed} prices agreed · {refused} refused · {waiting} waiting on a buyer.
    Pick a line to read that negotiation.</p></div>
  <div style="max-width: 1100px;">{_graph(snap, selected, xray, link)}</div>
  {convo}
</div>"""


def allocation(snap: MarketSnapshot) -> str:
    total = len(snap.stranded)
    ours = snap.exchange_recovered
    base = snap.baseline_recovered
    rows = []
    for s in snap.stranded:
        leg = snap.leg_for(s.booking_id)
        if leg is None:
            rows.append([f"Buyer {s.label}", f"{s.quantity} × 20GP", "", "", ui.tag("Not recovered", "red")])
            continue
        c = snap.carrier(leg.carrier_id)
        if leg.status == "CONFIRMED":
            state = ui.tag("Confirmed", "green")
        elif leg.needs_approval:
            state = ui.tag("Asked in ASI:One", "warm-gray")
        else:
            state = ui.tag(leg.status.capitalize(), "teal")
        rows.append([f"Buyer {s.label}", ui.containers(leg.quantity, leg.quantity, leg.carrier_id),
                     f'{escape(c.name.split()[0])} <span class="mono">{escape(leg.sailing_id)}</span>',
                     f'<span class="mono">{_money(leg.price_minor)}</span> · arrives <span class="mono">{leg.arrival_at:%d %b}</span>', state])
    return f"""
<div class="stack">
  <div class="versus">
    <div class="cds--tile"><p class="cds--label">First come, first served</p><div class="n mono">{base if base is not None else "?"} / {total}</div>
      <p class="muted">Each buyer grabs the best space for itself, in the order they were posted.</p></div>
    <div style="background: var(--cds-button-primary); color: #fff;"><p class="cds--label" style="color: #fff;">Exchange, all at once</p>
      <div class="n mono">{ours} / {total}</div><p>Every stranded booking and every offer solved together, must-deliver first.</p></div>
  </div>
  {ui.table("Where each booking goes", "Prices per container. Only carrier-confirmed space counts as rebooked.",
            ["Buyer", "Containers", "New sailing", "Price", "Status"], rows, "No assignments yet.")}
</div>"""

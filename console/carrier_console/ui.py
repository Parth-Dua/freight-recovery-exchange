"""Page chrome for the carrier console, built on IBM's Carbon design system (g100 dark theme).

Carbon supplies the look of every standard part (header, tables, forms, buttons, tags,
notifications). The only custom pieces are ours: container blocks, the ship profile and the
advisory map. Pages pass already-escaped HTML fragments in.
"""

from __future__ import annotations

import re
from html import escape
from pathlib import Path

CARBON_CSS = "https://unpkg.com/@carbon/styles@1.60.0/css/styles.min.css"
FONTS = ("https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;600"
         "&family=IBM+Plex+Mono:wght@400&display=swap")

CARRIER_COLORS = {
    "atlas": "#F26B1D", "beacon": "#8B6CF0", "cove": "#1FAE6E",
    "meridian": "#3B6FE8", "dhow": "#C08A55",
}

STYLE = """
body { font-family: 'IBM Plex Sans', system-ui, sans-serif; background: var(--cds-background); font-size: 16px; line-height: 1.5; }
p, td, dd, li { overflow-wrap: anywhere; }
.page { max-width: 1240px; margin: 0 auto; padding: 104px 40px 96px; box-sizing: border-box; }
@media (max-width: 720px) { .page { padding: 88px 16px 64px; } }
.page h1 { font-size: clamp(1.75rem, 4vw, 2.5rem); line-height: 1.2; font-weight: 400; margin: 0; }
.lede { color: var(--cds-text-secondary); margin: 12px 0 0; max-width: 62ch; font-size: 1.125rem; line-height: 1.6; }
.mono { font-family: 'IBM Plex Mono', ui-monospace, monospace; font-variant-numeric: tabular-nums; }
.muted { color: var(--cds-text-secondary); }
.stack { display: flex; flex-direction: column; gap: 48px; }
.row { display: flex; gap: 48px; flex-wrap: wrap; }
.row > .grow { flex: 999 1 560px; min-width: 0; }
.row > .side { flex: 1 1 320px; min-width: 0; }
.facts { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 8px; }
.facts > div { background: var(--cds-layer-01); padding: 20px 24px; min-width: 0; }
.facts dt { font-size: .875rem; color: var(--cds-text-secondary); margin: 0 0 8px; }
.facts dd { margin: 0; font-size: 1.25rem; }
.big { font-size: clamp(2.25rem, 6vw, 4rem); line-height: 1.05; font-weight: 300; overflow-wrap: anywhere; }
.ct { display: inline-block; position: relative; width: 28px; height: 16px; box-sizing: border-box;
      border: 1px solid rgba(0,0,0,.45); vertical-align: middle;
      background-image: repeating-linear-gradient(90deg, rgba(0,0,0,.22) 0 2px, transparent 2px 6px); }
.ct::before, .ct::after { content: ""; position: absolute; top: 1px; width: 3px; height: 3px;
      background: rgba(0,0,0,.45); box-shadow: 0 10px 0 rgba(0,0,0,.45); }
.ct::before { left: 1px; } .ct::after { right: 1px; }
.ct.empty { background: none; border: 1px dashed var(--cds-border-strong-01); }
.ct.empty::before, .ct.empty::after { display: none; }
.cts { display: inline-flex; gap: 3px; }
.ship { margin: 0; }
.hz { display: inline-block; width: 16px; height: 10px; border: 1px solid #E5399B; vertical-align: middle;
      background: repeating-linear-gradient(45deg, #E5399B 0 2px, transparent 2px 5px); }
th { white-space: nowrap; }
.cds--data-table td { padding-top: 14px; padding-bottom: 14px; }
.cds--data-table-header { padding: 20px 24px 16px; }
.cds--data-table td { vertical-align: middle; }
.cds--header__global { align-items: center; padding-right: 16px; gap: 16px; min-width: 0; }
@media (max-width: 900px) { .hide-narrow { display: none; } }
.switcher { background: transparent; color: var(--cds-text-primary); border: 0;
            border-bottom: 1px solid var(--cds-border-strong-01); font: inherit; padding: 4px 8px; }
"""

NAV = (("sailings", "Sailings"), ("pricing", "Pricing"), ("activity", "Agent activity"), ("risk", "Risk"))


def shell(title: str, body: str, *, active: str, carrier_id: str, carriers: list[tuple[str, str]]) -> str:
    cid = escape(carrier_id)
    items = "".join(
        f'<li><a class="cds--header__menu-item" href="/carrier/{cid}/{key}"'
        f'{" aria-current=\"page\"" if key == active else ""}>'
        f'<span class="cds--text-truncate--end">{label}</span></a></li>'
        for key, label in NAV
    )
    options = "".join(
        f'<option value="{escape(c)}"{" selected" if c == carrier_id else ""}>{escape(name)}</option>'
        for c, name in carriers
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)} · Carrier console</title>
<link rel="stylesheet" href="{CARBON_CSS}">
<link rel="stylesheet" href="{FONTS}">
<style>{STYLE}</style></head>
<body class="cds--g100">
<header class="cds--header" aria-label="Carrier console">
  <a class="cds--header__name" href="/carrier/{cid}/sailings"><span class="cds--header__name--prefix">Freight exchange</span>&nbsp;Carrier console</a>
  <nav class="cds--header__nav" aria-label="Carrier console"><ul class="cds--header__menu-bar">{items}</ul></nav>
  <div class="cds--header__global">
    <label for="carrier" class="cds--visually-hidden">Carrier</label>
    <a class="cds--link" href="/market">Market view ↗</a>
    <select id="carrier" class="switcher" onchange="location.href='/carrier/'+this.value+'/{escape(active)}'">{options}</select>
  </div>
</header>
<main class="page">{body}</main>
</body></html>"""


def money(minor: int | None) -> str:
    return "n/a" if minor is None else f"${minor / 100:,.0f}"


def containers(filled: int, total: int, carrier_id: str) -> str:
    color = CARRIER_COLORS.get(carrier_id, "#8D8D8D")
    boxes = [f'<span class="ct" style="background-color: {color}"></span>'] * max(filled, 0)
    boxes += ['<span class="ct empty"></span>'] * max(total - filled, 0)
    return f'<span class="cts" aria-label="{filled} of {total} slots booked">{"".join(boxes)}</span>'


def tag(text: str, color: str) -> str:
    """Carbon tag. color: red, green, gray, magenta, purple, blue, cyan, teal, warm-gray."""
    return f'<span class="cds--tag cds--tag--{color}"><span class="cds--tag__label">{escape(text)}</span></span>'


def notification(kind: str, title: str, subtitle: str = "") -> str:
    """kind: error, warning, success, info."""
    return f"""<div class="cds--inline-notification cds--inline-notification--{kind} cds--inline-notification--low-contrast" role="status">
  <div class="cds--inline-notification__details"><div class="cds--inline-notification__text-wrapper">
    <div class="cds--inline-notification__title">{title}</div>
    <div class="cds--inline-notification__subtitle">{subtitle}</div>
  </div></div></div>"""


def table(title: str, description: str, headers: list[str], rows: list[list[str]], empty: str) -> str:
    head = "".join(f'<th scope="col"><div class="cds--table-header-label">{escape(h)}</div></th>' for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows) or (
        f'<tr><td colspan="{len(headers)}" class="muted">{escape(empty)}</td></tr>')
    return f"""<div class="cds--data-table-container">
  <div class="cds--data-table-header"><h2 class="cds--data-table-header__title">{escape(title)}</h2>
  <p class="cds--data-table-header__description">{escape(description)}</p></div>
  <div style="overflow-x: auto;"><table class="cds--data-table cds--data-table--lg"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>
</div>"""


def button_link(href: str, label: str, kind: str = "primary") -> str:
    return f'<a class="cds--btn cds--btn--{kind}" href="{escape(href)}">{escape(label)}</a>'


# --- ship profile -----------------------------------------------------------------------------

_SHIP_SVG = (Path(__file__).parent / "static" / "ship.svg").read_text()
_DECK = {"#3A3E44", "#30343A", "#464B51", "#373B41", "#1B1D20"}  # deck container fills in the drawing
_DECK_START, _DECK_END = 158.0, 1075.0  # x range of the deck containers, in the drawing's coordinates


def ship(booked: int, total: int, carrier_id: str, note: str) -> str:
    """Side profile of the sailing's ship (CC0 drawing, recolored). The deck is split into equal
    bays, one per exchange slot, from bow to stern; booked bays are filled in the carrier's color,
    so 1 of 4 booked fills a quarter of the ship."""
    color = CARRIER_COLORS.get(carrier_id, "#8D8D8D")
    bays = max(total, 1)
    width = (_DECK_END - _DECK_START) / bays

    def paint(match: re.Match) -> str:
        path = match.group(0)
        fill = re.search(r'fill="([^"]+)"', path)
        start = re.search(r'd="m([\d.]+),', path)
        if not fill or fill.group(1) not in _DECK or not start or float(start.group(1)) < _DECK_START:
            return path
        bay = min(int((float(start.group(1)) - _DECK_START) // width), bays - 1)
        new = color if bay < booked else "#2A2D31"
        return path.replace(f'fill="{fill.group(1)}"', f'fill="{new}"', 1)

    drawing = re.sub(r"<path[^>]*/>", paint, _SHIP_SVG)
    drawing = drawing.replace("<svg ", f'<svg role="img" aria-label="Ship with {booked} of {total} exchange slots booked" '
                                       'style="display: block; width: 100%; height: auto;" ', 1)
    return f"""<figure class="ship"><p class="mono" style="margin: 0 0 8px; font-size: .875rem; color: {color};">{escape(note)}</p>
{drawing}</figure>"""


# --- map ------------------------------------------------------------------------------------
# Coastlines plotted from real coordinates (x = (lon - 48) * 40, y = (31 - lat) * 40), simplified.

_ARABIA = ("M0 56 L16 84 L64 156 L88 188 L112 204 L124 196 L144 204 L144 252 L132 268 L184 272 L240 268 L268 248 "
           "L282 240 L296 228 L316 212 L324 196 L334 185 L338 198 L332 220 L334 240 L344 260 L350 268 L384 290 L424 296 "
           "L456 328 L472 340 L452 384 L424 416 L392 472 L352 512 L296 528 L240 564 L168 616 L80 640 L0 680 Z")
_NORTH = ("M0 0 L1200 0 L1200 916 L1180 916 L1136 884 L1112 840 L1080 760 L1060 700 L1032 624 L1008 560 L996 504 "
          "L994 476 L992 424 L984 380 L968 388 L912 412 L880 400 L840 348 L864 330 L888 320 L836 316 L816 296 L784 280 "
          "L760 248 L744 224 L640 228 L572 236 L504 228 L440 224 L392 216 L372 204 L356 164 L332 152 L292 176 L240 172 "
          "L184 144 L140 124 L112 84 L92 72 L20 40 L0 40 Z")
_HORMUZ = "M316 188 L336 170 L356 166 L366 192 L352 212 L336 204 Z"
_STRAIT = [(308, 210), (332, 178), (354, 196), (384, 236)]
_TO_MUMBAI = [(440, 276), (560, 336), (720, 404), (880, 456), (992, 480)]
LANES = {  # corridor id -> waypoints, from the demo market's corridors
    "AEJEA-INBOM": [(281, 239), *_STRAIT, *_TO_MUMBAI],
    "SADMM-INBOM": [(84, 183), (140, 172), (210, 184), (270, 202), *_STRAIT, *_TO_MUMBAI],
    "OMSOH-INBOM": [(349, 266), (392, 262), (446, 284), (560, 340), (720, 408), (880, 460), (992, 484)],
    "AEJEA-PKKHI": [(281, 239), *_STRAIT, (440, 262), (560, 254), (680, 252), (760, 248)],
    "AEJEA-INMUN": [(281, 239), *_STRAIT, (440, 270), (600, 300), (760, 318), (868, 330)],
}
_PORTS = {"AEJEA": (281, 239, -64, 24), "SADMM": (84, 183, -10, -14), "OMSOH": (349, 266, 10, 22),
          "PKKHI": (760, 248, 10, -12), "INMUN": (868, 330, 10, -10), "INBOM": (994, 482, 10, 24)}


def network_map(highlights: list[dict] | None = None, *, caption: str = "") -> str:
    """The whole demo network on one map: every route in gray, both danger zones, and the
    sailings that matter right now on top. Highlights run along a corridor (default Jebel Ali to
    Mumbai) and fan out after the strait: {"label", "color", "dash", "crossed", "dy", "lane"}."""
    base = "".join(
        f'<polyline points="{" ".join(f"{x},{y}" for x, y in pts)}" fill="none" stroke="#525252" stroke-width="1.5" '
        f'stroke-linejoin="round"></polyline>' for pts in LANES.values()
    )
    lines, tags, used = [], [], {}
    for r in highlights or []:
        dy, lane = r.get("dy", 0), LANES[r.get("lane", "AEJEA-INBOM")]
        pts = " ".join(f"{x},{y + dy * min(1.0, max(0.0, (i - 4) / 2)):.0f}" for i, (x, y) in enumerate(lane))
        lines.append(f'<polyline points="{pts}" fill="none" stroke="{r["color"]}" stroke-width="3.5" stroke-dasharray="{r.get("dash", "")}" '
                     f'stroke-linecap="round" stroke-linejoin="round"></polyline>')
        ax, ay = lane[int(len(lane) * 0.7)]
        stack = used[(ax, ay)] = used.get((ax, ay), -1) + 1
        ly = ay + dy + (26 if dy > 0 else -14) - (stack * 20 if dy == 0 else 0)
        tags.append(f'<text x="{ax}" y="{ly}" fill="{r["color"]}" font-size="16" text-anchor="middle" font-family="IBM Plex Mono" '
                    f'paint-order="stroke" stroke="#0E0F11" stroke-width="5">{escape(r["label"])}</text>')
        if r.get("crossed"):
            lines.append('<path d="M488 294 L512 318 M512 294 L488 318" stroke="#FA4D56" stroke-width="4" stroke-linecap="round"></path>')
    ports = "".join(
        f'<circle cx="{x}" cy="{y}" r="5" fill="#F4F4F4"></circle><text x="{x + dx}" y="{y + dy}" fill="#F4F4F4" font-size="15" '
        f'font-family="IBM Plex Mono" paint-order="stroke" stroke="#0E0F11" stroke-width="4">{code}</text>'
        for code, (x, y, dx, dy) in _PORTS.items()
    )
    return f"""<figure style="margin: 0;">
<svg viewBox="40 100 1020 470" role="img" aria-label="Map of every route in the demo market, the Strait of Hormuz security advisory and the Mumbai approach congestion zone" style="display: block; width: 100%; height: auto; background: #0E0F11;">
  <defs>
    <pattern id="hz-high" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="2.2" height="6" fill="#E5399B"></rect></pattern>
    <pattern id="hz-med" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="2" height="6" fill="#F1C21B"></rect></pattern>
  </defs>
  <rect x="0" y="0" width="1200" height="920" fill="#0E0F11"></rect>
  <path d="{_ARABIA}" fill="#1E2023" stroke="#4D5358"></path>
  <path d="{_NORTH}" fill="#1E2023" stroke="#4D5358"></path>
  <g fill="#6F6F6F" font-size="13" letter-spacing="3"><text x="560" y="150">IRAN</text><text x="640" y="200">PAKISTAN</text>
    <text x="300" y="420">OMAN</text><text x="1000" y="300">INDIA</text><text x="120" y="330">SAUDI ARABIA</text>
    <text x="560" y="520" font-style="italic" letter-spacing="1">Arabian Sea</text></g>
  {base}{"".join(lines)}
  <path d="{_HORMUZ}" fill="url(#hz-high)" stroke="#E5399B" stroke-width="1.5" opacity=".9"></path>
  <text x="372" y="160" fill="#FF7EB6" font-size="15" paint-order="stroke" stroke="#0E0F11" stroke-width="4">Hormuz · security advisory</text>
  <circle cx="994" cy="482" r="28" fill="url(#hz-med)" stroke="#F1C21B" stroke-width="1.5" opacity=".75"></circle>
  <text x="1028" y="530" fill="#F1C21B" font-size="14" text-anchor="end" paint-order="stroke" stroke="#0E0F11" stroke-width="4">Mumbai approach · congestion</text>
  {ports}{"".join(tags)}
</svg>{f'<figcaption class="muted" style="margin-top: 8px; font-size: .875rem;">{escape(caption)}</figcaption>' if caption else ""}</figure>"""

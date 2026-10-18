"""Page chrome for the carrier console, built on IBM's Carbon design system (g100 dark theme).

Carbon supplies the look of every standard part (header, tables, forms, buttons, tags,
notifications). The only custom pieces are ours: container blocks, the ship profile and the
advisory map. Pages pass already-escaped HTML fragments in.
"""

from __future__ import annotations

from html import escape

CARBON_CSS = "https://unpkg.com/@carbon/styles@1.60.0/css/styles.min.css"
FONTS = ("https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;600"
         "&family=IBM+Plex+Mono:wght@400&display=swap")

CARRIER_COLORS = {
    "atlas": "#F26B1D", "beacon": "#8B6CF0", "cove": "#1FAE6E",
    "meridian": "#3B6FE8", "dhow": "#C08A55",
}

STYLE = """
body { font-family: 'IBM Plex Sans', system-ui, sans-serif; background: var(--cds-background); }
.page { max-width: 1312px; margin: 0 auto; padding: 80px 32px 64px; box-sizing: border-box; }
.page h1 { font-size: 2rem; line-height: 2.5rem; font-weight: 400; margin: 0; }
.lede { color: var(--cds-text-secondary); margin: 8px 0 0; max-width: 60ch; }
.mono { font-family: 'IBM Plex Mono', ui-monospace, monospace; font-variant-numeric: tabular-nums; }
.muted { color: var(--cds-text-secondary); }
.stack { display: flex; flex-direction: column; gap: 32px; }
.row { display: flex; gap: 32px; flex-wrap: wrap; }
.row > .grow { flex: 999 1 560px; min-width: 0; }
.row > .side { flex: 1 1 320px; min-width: 0; }
.facts { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 1px;
         background: var(--cds-border-subtle-01); }
.facts > div { background: var(--cds-layer-01); padding: 16px; }
.facts dt { font-size: .75rem; color: var(--cds-text-secondary); margin: 0 0 4px; }
.facts dd { margin: 0; font-size: 1.25rem; }
.big { font-size: 3.5rem; line-height: 1; font-weight: 300; }
.ct { display: inline-block; position: relative; width: 28px; height: 16px; box-sizing: border-box;
      border: 1px solid rgba(0,0,0,.45); vertical-align: middle;
      background-image: repeating-linear-gradient(90deg, rgba(0,0,0,.22) 0 2px, transparent 2px 6px); }
.ct::before, .ct::after { content: ""; position: absolute; top: 1px; width: 3px; height: 3px;
      background: rgba(0,0,0,.45); box-shadow: 0 10px 0 rgba(0,0,0,.45); }
.ct::before { left: 1px; } .ct::after { right: 1px; }
.ct.empty { background: none; border: 1px dashed var(--cds-border-strong-01); }
.ct.empty::before, .ct.empty::after { display: none; }
.cts { display: inline-flex; gap: 3px; }
.ship { margin: 0; padding-top: 28px; }
.hull { position: relative; }
.hull img { display: block; width: 100%; height: auto; }
.hull .slot { position: absolute; width: 2.9%; height: 6.4%; box-sizing: border-box; border: 1px solid rgba(0,0,0,.5);
              background-image: repeating-linear-gradient(90deg, rgba(0,0,0,.22) 0 2px, transparent 2px 5px); }
.hull .mark { position: absolute; left: 64.1%; top: 49.6%; width: 6.9%; height: 14.6%; border: 2px solid #F26B1D; }
.hull .lead { position: absolute; left: 67.5%; top: -28px; bottom: 50.4%; border-left: 1px solid #F26B1D; }
.hull .note { position: absolute; left: calc(67.5% + 8px); top: -28px; font-size: .75rem; color: #F26B1D; white-space: nowrap; }
.hz { display: inline-block; width: 16px; height: 10px; border: 1px solid #E5399B; vertical-align: middle;
      background: repeating-linear-gradient(45deg, #E5399B 0 2px, transparent 2px 5px); }
td.mono, th { white-space: nowrap; }
.cds--data-table td { vertical-align: middle; }
.cds--header__global { align-items: center; padding-right: 16px; }
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
    <a class="cds--link" href="/market" style="margin-right: 24px;">Market view ↗</a>
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


# The ship drawing's four exchange slots (x%, y% of the image), filled bottom tier first.
_SLOTS = ((64.42, 57.0), (67.72, 57.0), (64.42, 50.4), (67.72, 50.4))


def ship(booked: int, carrier_id: str, note: str) -> str:
    """Side profile of the sailing's ship (CC0 drawing, recolored) with its exchange slots
    filled in to match the booking count."""
    color = CARRIER_COLORS.get(carrier_id, "#8D8D8D")
    filled = "".join(
        f'<span class="slot" style="left: {x}%; top: {y}%; background-color: {color};"></span>'
        for x, y in _SLOTS[:max(0, min(booked, len(_SLOTS)))]
    )
    return f"""<figure class="ship"><div class="hull">
  <img src="/static/ship.svg" alt="Container ship side profile. {booked} of its {len(_SLOTS)} exchange slots, mid-ship, are booked.">
  {filled}<span class="mark"></span><span class="lead"></span><span class="note mono">{escape(note)}</span>
</div></figure>"""

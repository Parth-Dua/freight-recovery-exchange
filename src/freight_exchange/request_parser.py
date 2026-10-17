"""Deterministic extraction of a cargo request from chat text (no LLM).

ASI:One may rephrase the user's words before forwarding them, so the parser
is tolerant of phrasing but never guesses ports: if origin/destination are not
both recognised, it reports what is missing instead of inventing a request.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone

from freight_exchange.repository import Port

NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "single": 1, "two": 2, "couple": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}

_NUM = r"\b(\d{1,2}|" + "|".join(NUMBER_WORDS) + r")"
_CONTAINER = re.compile(
    _NUM + r"\s*(?:x|×|\*)?\s*(?:(20|40)\s*(?:-?\s*(?:ft|foot|feet|'|’))?\s*(gp|dry|dv|hc|high[\s-]?cube|std|standard)?)"
    r"\s*(?:containers?|boxes|box|units?|cntrs?|teus?)?",
    re.IGNORECASE,
)
_QTY_ONLY = re.compile(_NUM + r"\s+(?:containers?|boxes|units?|cntrs?)\b", re.IGNORECASE)
_MONTH = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_DATE_PATTERNS = [
    re.compile(r"(\d{4})-(\d{2})-(\d{2})"),
    re.compile(_MONTH + r"\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?", re.IGNORECASE),
    re.compile(r"(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?" + _MONTH + r"(?:,?\s+(\d{4}))?", re.IGNORECASE),
]
_DEADLINE = re.compile(r"\b(by|before|no later than|arrive by|arriving by|deliver(?:ed)? by|deadline)\b", re.I)
_DEPART_AFTER = re.compile(r"\b(after|from|depart(?:ing|ure)?\s+(?:after|from|on or after)|leaving after)\b", re.I)
_BUDGET = [
    re.compile(r"\$\s?(\d[\d,]*(?:\.\d{1,2})?)\s*(k)?\b", re.IGNORECASE),
    re.compile(r"\b(\d[\d,]*(?:\.\d{1,2})?)\s*(k)?\s*(?:usd|dollars|us\$)\b", re.IGNORECASE),
    re.compile(r"\b(?:budget|max(?:imum)?|up to|under|cap)\D{0,12}(\d[\d,]*(?:\.\d{1,2})?)\s*(k)?\b", re.IGNORECASE),
]
_NO_SPLIT = re.compile(r"\b(don'?t|do not|no|not|without|never)\s+split|\bsame (vessel|sailing|ship)\b|\btogether\b", re.I)
_SPLIT_OK = re.compile(r"\b(can|may|ok to|okay to|allow(?:ed)? to|fine to)\s+split\b|\bsplit (is )?(ok|okay|fine|allowed)\b", re.I)


@dataclass
class ParsedRequest:
    origin: Port | None = None
    destination: Port | None = None
    quantity: int = 1
    quantity_assumed: bool = True
    container_type: str = "20GP"
    earliest_departure: datetime | None = None
    latest_arrival: datetime | None = None
    max_total_price_minor: int | None = None
    allow_split: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def missing(self) -> list[str]:
        out = []
        if self.origin is None:
            out.append("origin port")
        if self.destination is None:
            out.append("destination port")
        return out


def _find_ports(text: str, ports: list[Port]) -> list[tuple[int, Port]]:
    hits: list[tuple[int, Port]] = []
    lowered = text.lower()
    for port in ports:
        names = {port.port_code.lower(), port.name.lower(), *(a.lower() for a in port.aliases)}
        positions = [m.start() for n in names for m in re.finditer(rf"\b{re.escape(n)}\b", lowered)]
        if positions:
            hits.append((min(positions), port))
    return sorted(hits, key=lambda h: h[0])


def _assign_ports(text: str, hits: list[tuple[int, Port]]) -> tuple[Port | None, Port | None]:
    if not hits:
        return None, None
    lowered = text.lower()
    origin = dest = None
    for pos, port in hits:
        before = lowered[max(0, pos - 12):pos]
        if re.search(r"\b(from|ex|out of|leaving)\s+(the\s+)?(port of\s+)?$", before):
            origin = port
        elif re.search(r"\b(to|into|towards|for|destination)\s+(the\s+)?(port of\s+)?$", before):
            dest = port
    remaining = [p for _, p in hits if p not in (origin, dest)]
    if origin is None and remaining:
        origin = remaining.pop(0)
    if dest is None and remaining:
        dest = remaining.pop(0)
    return origin, dest


def _parse_date(match: re.Match, now: datetime) -> datetime | None:
    g = match.groups()
    try:
        if len(g) == 3 and g[0] and g[0].isdigit() and len(g[0]) == 4:
            year, month, day = int(g[0]), int(g[1]), int(g[2])
        elif g[0] and g[0].isdigit():
            day, month, year = int(g[0]), MONTHS[g[1][:3].lower()], int(g[2]) if g[2] else None
        else:
            month, day, year = MONTHS[g[0][:3].lower()], int(g[1]), int(g[2]) if g[2] else None
        if year is None:
            year = now.year
            candidate = datetime(year, month, day, tzinfo=timezone.utc)
            if candidate.date() < (now - timedelta(days=60)).date():
                year += 1
        return datetime(year, month, day, tzinfo=timezone.utc)
    except (ValueError, KeyError):
        return None


def _dates(text: str, now: datetime) -> tuple[datetime | None, datetime | None]:
    earliest = latest = None
    for pattern in _DATE_PATTERNS:
        for m in pattern.finditer(text):
            day = _parse_date(m, now)
            if day is None:
                continue
            before = text[max(0, m.start() - 30):m.start()]
            if _DEADLINE.search(before):
                latest = datetime.combine(day.date(), time(23, 59), tzinfo=timezone.utc)
            elif _DEPART_AFTER.search(before):
                earliest = day
            elif latest is None:
                latest = datetime.combine(day.date(), time(23, 59), tzinfo=timezone.utc)
    return earliest, latest


def _budget(text: str) -> int | None:
    for pattern in _BUDGET:
        m = pattern.search(text)
        if m:
            amount = float(m.group(1).replace(",", ""))
            if m.group(2):
                amount *= 1000
            if amount >= 100:  # ignore stray small numbers like quantities
                return round(amount * 100)
    return None


def _container(text: str) -> tuple[int | None, str | None]:
    for m in _CONTAINER.finditer(text):
        qty_word, size, kind = m.group(1), m.group(2), (m.group(3) or "").lower()
        if size is None:
            continue
        qty = int(qty_word) if qty_word.isdigit() else NUMBER_WORDS[qty_word.lower()]
        ctype = "40HC" if size == "40" and kind.startswith(("hc", "high")) else f"{size}GP"
        return qty, ctype
    m = _QTY_ONLY.search(text)
    if m:
        word = m.group(1)
        return (int(word) if word.isdigit() else NUMBER_WORDS[word.lower()]), None
    return None, None


def parse_request(text: str, ports: list[Port], now: datetime | None = None) -> ParsedRequest:
    now = now or datetime.now(timezone.utc)
    text = text[:2000]
    parsed = ParsedRequest()

    parsed.origin, parsed.destination = _assign_ports(text, _find_ports(text, ports))
    qty, ctype = _container(text)
    if qty is not None:
        parsed.quantity, parsed.quantity_assumed = qty, False
    else:
        parsed.notes.append("quantity not stated; assumed 1 container")
    if ctype:
        parsed.container_type = ctype
    elif not re.search(r"\b(20|40)\s*(gp|ft|foot|feet|'|hc)", text, re.I):
        parsed.notes.append("container type not stated; assumed 20GP")

    parsed.earliest_departure, parsed.latest_arrival = _dates(text, now)
    parsed.max_total_price_minor = _budget(text)
    if _SPLIT_OK.search(text):
        parsed.allow_split = True
    elif _NO_SPLIT.search(text):
        parsed.allow_split = False
    return parsed

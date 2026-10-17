"""Routing and reply rendering for the Exchange Agent (no LLM).

Routing is keyword-based so the visible reply always reflects code we control.
Data-backed replies (corridors, quotes) are rendered from Tiger rows and real
carrier replies passed in by the graph — never from preset text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from freight_exchange.carrier_client import CarrierOutcome
from freight_exchange.repository import Corridor, Port
from freight_exchange.request_parser import ParsedRequest

MAX_INPUT_CHARS = 2000

ROUTE_CAPABILITIES = "capabilities"
ROUTE_CORRIDORS = "get_supported_corridors"
ROUTE_QUOTES = "request_quotes"
ROUTE_BOOKING = "booking_not_implemented"
ROUTE_RECOVERY = "recovery_not_implemented"
ROUTE_UNSUPPORTED = "unsupported"

STATUS_LINE = (
    "Demo market: fictional carriers and simulated inventory. Quotes are indicative — "
    "booking, payment and recovery are not active yet."
)
EXAMPLE_PROMPT = '"Quote 2x20GP from Jebel Ali to Mumbai, arrive by Oct 23, budget $5,600, don\'t split"'

# Order matters: commitments are checked before quotes so "book 2 containers"
# is never silently treated as a booking.
_ROUTE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (ROUTE_BOOKING, re.compile(r"\b(book\w*|reserv\w*|pay\w*|checkout|purchase|buy|hold|confirm\w*)\b")),
    (ROUTE_RECOVERY, re.compile(r"\b(recover\w*|re-?rout\w*|disrupt\w*|withdraw\w*|rebook\w*)\b")),
    # strong quote signals beat corridor words ("rates on the Mumbai lane")
    (ROUTE_QUOTES, re.compile(
        r"\b(quot\w*|price\w*|pricing|rates?|cost\w*|how much|offers?|20gp|40gp|40hc|teus?)\b|"
        r"\b(\d{1,2}|a|an|one|two|three|four|five|six|seven|eight|nine|ten)\s*(x|×)?\s*"
        r"(\d{2}\s*-?\s*(gp|ft|foot|feet|hc|')|containers?|boxes)")),
    (ROUTE_CORRIDORS, re.compile(r"\b(corridors?|routes?|lanes?|ports?|trade\s*lanes?|catalog\w*|sailings?)\b")),
    # weak signals only after corridor words ("what shipping corridors…" is a corridor question)
    (ROUTE_QUOTES, re.compile(r"\b(ship|shipping|move|send|transport)\b.*\b(from|to)\b")),
    (ROUTE_CAPABILITIES, re.compile(
        r"\b(what\s+can\s+(you|your\b.*)\s+do|capabilit\w*|help|hi|hello|hey|"
        r"who\s+are\s+you|what\s+are\s+you|what\s+do\s+you\s+do|about)\b")),
]


@dataclass(frozen=True)
class Reply:
    route: str
    text: str


def classify(message: str) -> str:
    normalized = " ".join(message[:MAX_INPUT_CHARS].lower().split())
    if not normalized:
        return ROUTE_UNSUPPORTED
    for route, pattern in _ROUTE_PATTERNS:
        if pattern.search(normalized):
            return route
    return ROUTE_UNSUPPORTED


def static_reply(route: str) -> str:
    if route == ROUTE_CAPABILITIES:
        return (
            "I'm the Freight Recovery Exchange (Demo), an agent-native ocean-freight exchange. "
            "Ask me for quotes and I contact independent carrier agents in parallel, verify each "
            "carrier's signed offer and record everything in Tiger.\n"
            f"Try: {EXAMPLE_PROMPT}\n"
            "Coming next: negotiation, booking with test-mode payment, and joint recovery when a sailing is withdrawn."
        )
    if route == ROUTE_BOOKING:
        return (
            "Booking is not active yet, so nothing has been booked, held or charged. "
            f"I can get live carrier quotes now — try: {EXAMPLE_PROMPT}"
        )
    if route == ROUTE_RECOVERY:
        return "Shipment recovery is not active yet — no recovery action has been taken."
    return (
        "That's outside what I can do right now. I can list my corridors or fetch live carrier quotes — "
        f"try: {EXAMPLE_PROMPT}"
    )


def money(minor: int, currency: str = "USD") -> str:
    symbol = "$" if currency == "USD" else f"{currency} "
    return f"{symbol}{minor / 100:,.0f}" if minor % 100 == 0 else f"{symbol}{minor / 100:,.2f}"


def when(ts: datetime) -> str:
    return ts.strftime("%b %d %H:%M").replace(" 0", " ")


def short_address(address: str) -> str:
    return f"{address[:10]}…{address[-4:]}"


def port_label(port: Port) -> str:
    return f"{port.name} ({port.port_code})"


def corridors_reply(corridors: list[Corridor]) -> str:
    if not corridors:
        return "No active corridors are currently registered in Tiger."
    lines = ["Live corridors (from Tiger):"]
    for c in corridors:
        nxt = f", next departure {when(c.next_departure)} UTC" if c.next_departure else ""
        lines.append(
            f"- {port_label(c.origin)} → {port_label(c.destination)} · {'/'.join(c.container_types)} · "
            f"{c.carriers} carrier agents · {c.sailings} sailings{nxt}"
        )
    lines.append(f"Ask for quotes, e.g. {EXAMPLE_PROMPT}")
    return "\n".join(lines)


def missing_info_reply(parsed: ParsedRequest, corridors: list[Corridor]) -> str:
    lanes = "; ".join(f"{port_label(c.origin)} → {port_label(c.destination)}" for c in corridors) or "none"
    return (
        f"To get quotes I need the {' and '.join(parsed.missing)}. Corridors I serve: {lanes}.\n"
        f"Example: {EXAMPLE_PROMPT}"
    )


def invalid_request_reply(reason: str) -> str:
    return f"I couldn't send that to carriers: {reason}. Nothing was requested.\nExample: {EXAMPLE_PROMPT}"


def _request_summary(parsed: ParsedRequest) -> str:
    parts = [f"{parsed.quantity}× {parsed.container_type}",
             f"{port_label(parsed.origin)} → {port_label(parsed.destination)}"]
    if parsed.earliest_departure:
        parts.append(f"depart after {parsed.earliest_departure:%b %d}")
    if parsed.latest_arrival:
        parts.append(f"arrive by {parsed.latest_arrival:%b %d}")
    if parsed.max_total_price_minor:
        parts.append(f"budget {money(parsed.max_total_price_minor)}")
    parts.append("split allowed" if parsed.allow_split else "no split")
    return ", ".join(parts)


def _failure_text(o: CarrierOutcome) -> str:
    if o.kind == "reject":
        return o.reply.reason
    code = o.error_code.value if o.error_code else "ERROR"
    return f"{o.detail or 'error'} ({code})"


def quotes_reply(parsed: ParsedRequest, outcomes: list[CarrierOutcome], request_id: str) -> str:
    offers = sorted((o for o in outcomes if o.kind == "offer"),
                    key=lambda o: (o.offer.total_price_minor, o.offer.arrival_at))
    lines = [
        f"**Live quotes from {len(outcomes)} independent carrier agents** — {_request_summary(parsed)}.",
    ]
    if parsed.notes:
        lines.append("Assumed: " + "; ".join(parsed.notes) + ".")
    lines.append("")
    if offers:
        lines += [
            "| # | Carrier | Sailing | Departs → Arrives (UTC) | Qty | Total | Valid until (UTC) |",
            "|---|---|---|---|---|---|---|",
        ]
        for i, o in enumerate(offers, 1):
            f = o.offer
            lines.append(
                f"| {i} | {o.carrier.display_name} | {f.sailing_id} · {f.vessel_name} | "
                f"{when(f.departure_at)} → {when(f.arrival_at)} | {f.quantity} | "
                f"{money(f.total_price_minor, f.currency)} | {f.expires_at:%H:%M} |"
            )
    else:
        lines.append("No carrier could offer capacity that meets these constraints.")

    others = [o for o in outcomes if o.kind != "offer"]
    if others:
        lines.append("")
        for o in others:
            label = "Declined" if o.kind == "reject" else "No valid offer"
            lines.append(f"- {label} — {o.carrier.display_name}: {_failure_text(o)}")

    verified = [o for o in outcomes if o.signature_verified]
    lines += [
        "",
        f"✓ {len(verified)}/{len(outcomes)} replies carried a valid carrier signature, checked against the "
        "carrier agent registry in Tiger: "
        + ", ".join(f"{o.carrier.carrier_id} {short_address(o.carrier.agent_address)} ({o.latency_ms} ms)"
                    for o in outcomes) + ".",
        f"Recorded in Tiger as request {request_id}. Indicative quotes only — no capacity is held "
        "and nothing is booked or charged.",
    ]
    return "\n".join(lines)

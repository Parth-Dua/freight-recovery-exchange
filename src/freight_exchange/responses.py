"""Routing and reply rendering for the Exchange Agent (no LLM).

Routing is keyword-based so the visible reply always reflects code we control.
Data-backed replies (corridors, quotes) are rendered from Tiger rows and real
carrier replies passed in by the graph — never from preset text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from typing import TYPE_CHECKING

from freight_exchange.carrier_client import CarrierOutcome
from freight_exchange.repository import Corridor, Port
from freight_exchange.request_parser import ParsedRequest
from freight_exchange.selection import SELECT_INTENT

if TYPE_CHECKING:
    from freight_exchange.quotes import CarrierResult, QuoteRound

MAX_INPUT_CHARS = 2000

ROUTE_CAPABILITIES = "capabilities"
ROUTE_CORRIDORS = "get_supported_corridors"
ROUTE_QUOTES = "request_quotes"
ROUTE_SELECT = "select_offer"
ROUTE_REQUOTE = "requote"
ROUTE_BOOKING = "book"
ROUTE_PAID = "check_payment"
ROUTE_CANCEL = "cancel_booking"
ROUTE_RECOVERY = "disruption_status"
ROUTE_UNSUPPORTED = "unsupported"

STATUS_LINE = (
    "Demo market: fictional carriers and simulated inventory. Payments run in Stripe TEST mode only "
    "(no real money). Automatic recovery is not active yet."
)
EXAMPLE_PROMPT = '"Quote 2x20GP from Jebel Ali to Mumbai, arrive by Oct 23, budget $5,600, don\'t split"'

# Order matters: commitments are checked before quotes so "book 2 containers"
# is never silently treated as a booking.
_ROUTE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (ROUTE_REQUOTE, re.compile(r"\b(re-?quote|refresh( the)? (quotes?|offers?)|new quotes?|quote again|fresh quotes?)\b")),
    (ROUTE_PAID, re.compile(
        r"\b(i(?: have|'ve)? (?:just )?paid|paid|payment (?:is )?(?:done|complete|completed|made|went through|sent)|"
        r"check (?:my |the )?payment|did (?:my|the) payment|done paying|finished paying)\b")),
    (ROUTE_CANCEL, re.compile(r"\b(cancel(?!l)|release (?:the |my )?hold|don'?t book|stop (?:the )?booking)\b")),
    (ROUTE_SELECT, SELECT_INTENT),
    # an imperative "book …" is a booking command even if it mentions "auto-rebook"
    (ROUTE_BOOKING, re.compile(r"^\s*(?:please\s+|ok(?:ay)?,?\s+|yes,?\s+)?(?:book|reserve)\b")),
    # status questions beat booking words ("status of my booking")
    (ROUTE_RECOVERY, re.compile(
        r"\b(recover\w*|re-?rout\w*|disrupt\w*|withdraw\w*|rebook\w*|cancel+ed|my bookings?|"
        r"booking status|status of my|what happened to)\b")),
    (ROUTE_BOOKING, re.compile(r"\b(book\w*|reserv\w*|pay\w*|checkout|purchase|buy|hold|confirm\w*)\b")),
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


_MENTION = re.compile(r"(?<![\w.])@[\w.-]+")


def strip_mentions(message: str) -> str:
    """ASI:One forwards the @handle (e.g. "@freight-recovery-exc") inside the text; it is not intent."""
    return " ".join(_MENTION.sub(" ", message).split())


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


def _request_summary(cargo_or_parsed) -> str:
    c = cargo_or_parsed
    origin = port_label(c.origin) if isinstance(c, ParsedRequest) else c.origin_port
    dest = port_label(c.destination) if isinstance(c, ParsedRequest) else c.destination_port
    parts = [f"{c.quantity}× {c.container_type}", f"{origin} → {dest}"]
    if c.earliest_departure:
        parts.append(f"depart after {c.earliest_departure:%b %d}")
    if c.latest_arrival:
        parts.append(f"arrive by {c.latest_arrival:%b %d}")
    if c.max_total_price_minor:
        parts.append(f"budget {money(c.max_total_price_minor)}")
    parts.append("split allowed" if c.allow_split else "no split")
    return ", ".join(parts)


def _failure_text(o: CarrierOutcome) -> str:
    if o.kind == "reject":
        return o.reply.reason
    code = o.error_code.value if o.error_code else "ERROR"
    return f"{o.detail or 'error'} ({code})"


def _negotiation_cell(r: "CarrierResult") -> str:
    c = r.counter
    if c is None:
        return "—"
    proposed = money(c.counter.proposed_total_price_minor)
    if c.kind == "accept":
        return f"asked {proposed} → accepted ✓"
    if c.kind == "revise":
        return f"asked {proposed} → met at {money(c.new_offer.total_price_minor)}"
    if c.kind == "reject":
        return f"asked {proposed} → declined, opening stands"
    return f"counter failed ({c.error_code.value if c.error_code else 'error'}), opening stands"


def quotes_reply(summary_source, round_: "QuoteRound") -> str:
    results, ranked = round_.results, round_.ranked
    lines = [f"**Live offers from {len(results)} independent carrier agents** — {_request_summary(summary_source)}."]
    notes = getattr(summary_source, "notes", None)
    if notes:
        lines.append("Assumed: " + "; ".join(notes) + ".")
    lines.append("")
    if ranked:
        lines += [
            "| # | Carrier | Sailing | Departs → Arrives (UTC) | Opening | Negotiation (1 counter) | Final | Valid until (UTC) |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for i, r in enumerate(ranked, 1):
            f, o = r.final_offer, r.opening.offer
            lines.append(
                f"| {i} | {r.carrier.display_name} | {f.sailing_id} · {f.vessel_name} | "
                f"{when(f.departure_at)} → {when(f.arrival_at)} | {money(o.total_price_minor, o.currency)} | "
                f"{_negotiation_cell(r)} | **{money(f.total_price_minor, f.currency)}** | {f.expires_at:%H:%M} |"
            )
        lines += ["", "Reply **select 1** (or a carrier name, “cheapest”, “fastest”) to choose an offer."]
    else:
        lines.append("No carrier could offer capacity that meets these constraints (NO_FEASIBLE_OFFERS). "
                     "Try a later deadline, a higher budget or allowing a split.")

    others = [r.opening for r in results if r.final_offer is None]
    if others:
        lines.append("")
        for o in others:
            label = "Declined" if o.kind == "reject" else "No valid offer"
            lines.append(f"- {label} — {o.carrier.display_name}: {_failure_text(o)}")

    replies = [r.opening for r in results] + [r.counter for r in results if r.counter is not None]
    verified = sum(1 for x in replies if x.signature_verified)
    lines += [
        "",
        f"✓ {verified}/{len(replies)} carrier replies carried a valid signature, checked against the carrier "
        "registry in Tiger: " + ", ".join(
            f"{r.carrier.carrier_id} {short_address(r.carrier.agent_address)}" for r in results) + ".",
        f"Recorded in Tiger as request {round_.request_id}. Offers are indicative — nothing is held, booked or charged.",
    ]
    return "\n".join(lines)


def clarification_reply(parsed: ParsedRequest, corridors: list[Corridor]) -> str:
    lanes = "; ".join(f"{port_label(c.origin)} → {port_label(c.destination)} ({'/'.join(c.container_types)})"
                      for c in corridors) or "none"
    known = []
    if parsed.origin and parsed.destination:
        known.append(f"{port_label(parsed.origin)} → {port_label(parsed.destination)}")
    if not parsed.quantity_assumed:
        known.append(f"{parsed.quantity}× {parsed.container_type}")
    have = f" So far: {', '.join(known)}." if known else ""
    return (f"To get quotes I still need the {' and '.join(parsed.missing)}.{have} Just reply with it.\n"
            f"Corridors I serve: {lanes}. Example: {EXAMPLE_PROMPT}")


missing_info_reply = clarification_reply  # backwards-compatible name


def container_not_offered_reply(container_type: str, corridor: Corridor) -> str:
    return (f"{container_type} isn't offered on {port_label(corridor.origin)} → {port_label(corridor.destination)}; "
            f"available: {'/'.join(corridor.container_types)}. Nothing was requested. Example: {EXAMPLE_PROMPT}")


def invalid_request_reply(reason: str) -> str:
    return f"I couldn't send that to carriers: {reason}. Nothing was requested.\nExample: {EXAMPLE_PROMPT}"


def selected_reply(offer: dict, request_id: str, already: bool = False) -> str:
    head = "Already selected" if already else "Selected"
    return (
        f"{head}: option {offer['display_rank']} — {offer['display_name']}, sailing {offer['sailing_id']}, "
        f"{offer['quantity']}× {offer['container_type']}, departs {when(offer['departure_at'])} → arrives "
        f"{when(offer['arrival_at'])} UTC, **{money(offer['total_price_minor'], offer['currency'])}**, "
        f"valid until {offer['expires_at']:%H:%M} UTC (offer {offer['offer_id']}).\n"
        f"Saved in Tiger on request {request_id} as OFFER_SELECTED. This is **not a booking**: no capacity is held "
        "and nothing is charged. Booking with a carrier hold and test-mode payment comes next."
    )


def offer_expired_reply(rank: int | None) -> str:
    label = f"Option {rank}" if rank else "That offer"
    return f"{label} has expired (OFFER_EXPIRED), so it can't be selected. Say **requote** for fresh offers."


def selection_problem_reply(problem: str, offers: list[dict]) -> str:
    opts = "; ".join(f"{o['display_rank']}: {o['display_name']} {money(o['total_price_minor'], o['currency'])}"
                     for o in offers)
    return f"I couldn't match that to an offer — {problem}. Current options: {opts or 'none'}."


def no_quotes_yet_reply() -> str:
    return f"There are no offers in this conversation to select yet. Ask for quotes first, e.g. {EXAMPLE_PROMPT}"


def disruption_status_reply(withdrawals: list, bookings: list) -> str:
    """Real Tiger state: withdrawn sailings (public facts) + this buyer's own bookings."""
    lines = ["**Disruption status (live from Tiger)**"]
    if withdrawals:
        for w in withdrawals:
            lines.append(
                f"- Sailing **{w.sailing_id}** ({w.carrier_name}, was departing {when(w.departure_at)} UTC) "
                f"was **withdrawn** by the carrier at {when(w.withdrawn_at)} UTC — {w.reason}. "
                f"{w.disrupted_bookings} committed booking(s) disrupted."
            )
    else:
        lines.append("- No sailings have been withdrawn. All sailings are operating as scheduled.")
    lines.append("")
    if bookings:
        lines.append("Your bookings:")
        for b in bookings:
            flag = " ⚠️" if b.is_disrupted else ""
            extra = f", replaced by {b.replaced_by_booking_id}" if b.replaced_by_booking_id else ""
            since = f" since {when(b.disrupted_at)} UTC" if b.disrupted_at else ""
            lines.append(f"- {b.booking_id}: **{b.status}**{flag}{since} — {b.carrier_name}, sailing {b.sailing_id}{extra}")
    else:
        lines.append("You have no bookings on record yet.")
    if any(b.is_disrupted for b in bookings) or any(w.disrupted_bookings for w in withdrawals):
        lines.append("")
        lines.append("Automatic recovery is not active yet: disrupted bookings have **not** been rebooked, "
                     "and nothing has been charged or refunded.")
    return "\n".join(lines)


# ---- Phase 4: booking replies ---------------------------------------------------------
TEST_CARD = "4242 4242 4242 4242 (any future expiry, any CVC, any ZIP)"


def _allowance(minor: int) -> str:
    return (f"auto-rebook if the sailing is cancelled, up to {money(minor)} extra" if minor
            else "if the sailing is cancelled, any replacement that costs more needs your approval")


def booking_reply(step) -> str:
    i, o = step.intent or {}, step.offer or {}
    k = step.kind
    if k == "no_selection":
        return ("Select an offer first (e.g. **select 1** after a quote), then say **book**. Nothing was held or charged.\n"
                f"No quotes yet? Try: {EXAMPLE_PROMPT}")
    if k == "offer_expired":
        return "Your selected offer has expired, so I can't hold it. Say **requote** for fresh offers. Nothing was charged."
    if k == "hold_rejected":
        return (f"{o.get('display_name', 'The carrier')} couldn't hold that capacity ({step.detail}). "
                "Nothing was held or charged. Say **requote** for fresh offers.")
    if k == "checkout":
        return (
            f"**Hold placed** — {i['display_name']} is holding {i['quantity']}× {i['container_type']} on sailing "
            f"{i['sailing_id']} for you until **{i['hold_expires_at']:%H:%M} UTC**.\n"
            f"Pay in **Stripe TEST mode** (no real money): {step.checkout_url}\n"
            f"- Simulated ocean freight: {money(i['freight_minor'], i['currency'])}\n"
            f"- Booking service fee: {money(i['fee_minor'], i['currency'])}\n"
            f"- **Total: {money(i['freight_minor'] + i['fee_minor'], i['currency'])}**\n"
            f"Use Stripe's test card {TEST_CARD}. When you've paid, say **paid** — I'll verify it with Stripe "
            "before asking the carrier to confirm. Say **cancel** to release the hold."
        )
    if k == "pending":
        link = f" Checkout: {step.checkout_url}" if step.checkout_url else ""
        return (f"Stripe has **not** received a payment for this booking yet ({step.detail}).{link}\n"
                "Nothing is booked until Stripe confirms the payment. Say **paid** again once you've completed checkout.")
    if k == "failed":
        return (f"The checkout ended without payment ({step.detail}). The hold was released and nothing is booked "
                "or charged. Say **requote** to start again.")
    if k in ("reconciliation", "booking_pending"):
        return (f"⚠️ {step.detail or 'This booking needs manual reconciliation.'} It is **not** marked as booked, "
                "and no second charge will be made.")
    if k == "cancelled":
        return "Cancelled: the hold was released and the checkout closed. Nothing was booked or charged."
    if k == "nothing_to_cancel":
        status = i.get("status")
        return (f"There's no open checkout to cancel{f' (current status: {status})' if status else ''}. "
                "Confirmed bookings can't be cancelled from chat in this demo.")
    if k == "no_intent":
        return "There's no booking in progress in this conversation. Select an offer and say **book** first."
    if k == "confirmed":
        return (
            f"✅ **Booking {step.booking_id} confirmed** by {i['display_name']} — claim code **{step.claim_code}**\n"
            f"- Sailing {i['sailing_id']} · {i['quantity']}× {i['container_type']} · "
            f"{i['origin_port']} → {i['destination_port']} · departs {when(i['departure_at'])} → arrives "
            f"{when(i['arrival_at'])} UTC\n"
            f"- Paid in Stripe TEST mode: simulated freight {money(i['freight_minor'], i['currency'])} + booking fee "
            f"{money(i['fee_minor'], i['currency'])} = **{money(i['freight_minor'] + i['fee_minor'], i['currency'])}**\n"
            f"- Freight payment terms: {i.get('payment_terms') or 'per carrier terms'}\n"
            f"- Recovery: {_allowance(i.get('max_additional_cost_minor') or 0)}\n"
            "Keep the booking ID and claim code — ask “what happened to my booking?” any time."
        )
    return "Something unexpected happened with this booking; nothing new was charged."

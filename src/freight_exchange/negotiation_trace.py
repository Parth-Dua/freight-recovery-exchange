"""Negotiation transparency for buyers (spec UX-NEG-001): "your agent negotiated; here's what actually happened".

Read-only. Built from what Tiger already persists for every session: `negotiation_sessions` (authoritative
status and final offer), `negotiation_messages` (public terms only: quotes, counters, public rationale) and
`offers` (sailing, quantity, currency, validity). Nothing here is generated or invented: a missing step is
reported as missing, a price improvement is shown only against the same carrier's opening quote for the same
sailing, quantity and currency, and only when the final price is lower.

Privacy: every query is scoped to the viewing buyer BEFORE rows are read (sessions join the buyer's own
request or booking). The DTO has no field for a buyer budget, urgency, must-deliver flag, auto-rebook limit,
or any carrier floor, margin or policy; the buyer's counter rationale is mapped from allowlisted codes.

Speed: transcripts are rendered once, when the negotiation closes, and stored in `negotiation_trace_cards`;
opening one is a single owner-scoped read.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

SCHEMA_VERSION = "1.0"
MAX_STEPS = 12  # 1 opening + 2 x (counter + reply) + terminal fits easily; cap protects the 64 KB card limit

# Public rationale the buyer agent sent with a counter (negotiation_sessions.buyer_counter) -> buyer-facing reason.
REASONS = {
    "A comparable verified offer is lower.": "Another carrier quoted you less for comparable space",
    "We would book now at this price.": "Offered to book immediately at a lower price",
}
GENERIC_REASON = "Counter strategy applied"

OUTCOME = {  # negotiation_sessions.status -> badge
    "ACCEPTED": "Agreed",
    "EXHAUSTED": "Final quote",
    "REJECTED": "Declined",
    "EXPIRED": "Timed out",
    "OPEN": "In progress",
}


def enabled() -> bool:
    return os.getenv("NEGOTIATION_TRANSPARENCY_ENABLED", "true").lower() != "false"


@dataclass(frozen=True)
class TraceStep:
    sequence: int
    kind: str  # CARRIER_OFFER | BUYER_COUNTER | CARRIER_ACCEPTED | CARRIER_COUNTER | CARRIER_HELD | DECLINED | ...
    actor: str  # carrier | buyer_agent | exchange
    round: int
    price_minor: int | None
    at: str | None
    explanation: str | None = None


@dataclass(frozen=True)
class NegotiationTraceView:
    schema_version: str
    request_id: str | None
    session_id: str
    carrier_id: str
    carrier_display_name: str
    state: str  # authoritative negotiation_sessions.status
    outcome: str
    offer_id: str | None
    sailing_id: str | None
    quantity: int | None
    currency: str
    opening_price_minor: int | None
    final_price_minor: int | None
    comparable_terms: bool
    price_improvement_minor: int | None  # > 0 only for a real decrease on comparable terms
    steps: tuple[TraceStep, ...]
    quote_expires_at: str | None
    complete: bool  # False when the persisted trail is missing pieces (never filled in)
    rank: int | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["steps"] = [asdict(s) for s in self.steps]
        d["notes"] = list(self.notes)
        return d

    @property
    def improvement_pct(self) -> float | None:
        if not self.price_improvement_minor or not self.opening_price_minor:
            return None
        return round(self.price_improvement_minor * 100 / self.opening_price_minor, 1)


def _iso(ts) -> str | None:
    if ts is None:
        return None
    return ts.astimezone(timezone.utc).isoformat() if isinstance(ts, datetime) else str(ts)


def build_trace(session: dict, messages: list[dict], offers: dict[str, dict], carrier_name: str) -> NegotiationTraceView:
    """Pure: one session's persisted rows -> display DTO. `offers` holds the rows of offers this session referenced."""
    seen, ordered = set(), []
    # within a round the buyer's counter always precedes the carrier's reply (round 0 is the opening quote)
    for m in sorted(messages, key=lambda m: (int(m["round"]), m["sender_role"] == "carrier", m["ts"], m["message_id"])):
        if m["message_id"] in seen:  # replayed / duplicated persistence: count once
            continue
        seen.add(m["message_id"])
        ordered.append(m)

    steps: list[TraceStep] = []
    opening_offer_id, last_counter = None, None
    for m in ordered:
        terms = m.get("public_terms") or {}
        role, rnd, mtype = m["sender_role"], int(m["round"]), m["message_type"]
        price = terms.get("total_price_minor")
        if role == "carrier" and rnd == 0:
            opening_offer_id = terms.get("offer_id")
            steps.append(TraceStep(0, "CARRIER_OFFER", "carrier", 0, price, _iso(m["ts"])))
        elif role == "buyer_agent":
            last_counter = terms.get("proposed_total_price_minor")
            reason = REASONS.get(terms.get("public_rationale") or "", GENERIC_REASON)
            steps.append(TraceStep(0, "BUYER_COUNTER", "buyer_agent", rnd, last_counter, _iso(m["ts"]), reason))
        elif mtype == "ACCEPT_COUNTER":
            steps.append(TraceStep(0, "CARRIER_ACCEPTED", "carrier", rnd, price, _iso(m["ts"])))
        elif mtype == "REVISED_OFFER":
            steps.append(TraceStep(0, "CARRIER_COUNTER", "carrier", rnd, price, _iso(m["ts"])))
        elif mtype == "REJECT_COUNTER":
            steps.append(TraceStep(0, "CARRIER_HELD", "carrier", rnd, None, _iso(m["ts"])))
        else:  # carrier error / exchange-side failure: say so without internals
            steps.append(TraceStep(0, "NO_REPLY", "carrier", rnd, None, _iso(m["ts"])))

    status = session["status"]
    final_id = session.get("final_offer_id")
    final = offers.get(final_id) if final_id else None
    opening = offers.get(opening_offer_id) if opening_offer_id else None
    notes: list[str] = []
    complete = True
    if status == "REJECTED" and not steps:
        steps.append(TraceStep(0, "DECLINED", "carrier", 0, None, _iso(session.get("closed_at"))))
    elif status == "EXPIRED":
        steps.append(TraceStep(0, "TIMED_OUT", "exchange", int(session.get("round") or 0), None,
                               _iso(session.get("closed_at"))))
    elif status in ("ACCEPTED", "EXHAUSTED") and final is not None:
        if not steps or steps[-1].kind != "CARRIER_ACCEPTED":
            steps.append(TraceStep(0, "BUYER_ACCEPTED" if status == "ACCEPTED" else "FINAL_QUOTE", "buyer_agent",
                                   int(session.get("round") or 0), int(final["total_price_minor"]),
                                   _iso(session.get("closed_at"))))
    elif status == "OPEN":
        notes.append("Negotiation still in progress")
    if final is not None and opening is None:
        complete = False
        notes.append("Detailed negotiation history unavailable")
    if len(steps) > MAX_STEPS:  # keep the opening and the decisive tail; never drop the final result
        steps = steps[:1] + steps[-(MAX_STEPS - 1):]
        notes.append("Some intermediate steps are summarised")
    steps = [TraceStep(i + 1, s.kind, s.actor, s.round, s.price_minor, s.at, s.explanation) for i, s in enumerate(steps)]

    opening_price = int(opening["total_price_minor"]) if opening else None
    final_price = int(final["total_price_minor"]) if final else None
    comparable = bool(opening and final and opening["sailing_id"] == final["sailing_id"]
                      and int(opening["quantity"]) == int(final["quantity"]) and opening["currency"] == final["currency"])
    improvement = (opening_price - final_price) if comparable and final_price < opening_price else None
    return NegotiationTraceView(
        schema_version=SCHEMA_VERSION, request_id=session.get("request_id"), session_id=session["session_id"],
        carrier_id=session["carrier_id"], carrier_display_name=carrier_name, state=status,
        outcome=("Quote stands" if status == "REJECTED" and final is not None  # counter refused, offer kept
                 else OUTCOME.get(status, status.title())), offer_id=final_id if final else None,
        sailing_id=(final or opening or {}).get("sailing_id"), quantity=(final or opening or {}).get("quantity"),
        currency=(final or opening or {}).get("currency") or "USD", opening_price_minor=opening_price,
        final_price_minor=final_price, comparable_terms=comparable, price_improvement_minor=improvement,
        steps=tuple(steps), quote_expires_at=_iso(final["expires_at"]) if final else None, complete=complete,
        rank=final.get("display_rank") if final else None, notes=tuple(notes))


# ---- owner-scoped loaders --------------------------------------------------------------------
_SESSION_COLS = "s.session_id, s.carrier_id, s.request_id, s.booking_id, s.status, s.round, s.final_offer_id, " \
                "s.closed_at, k.display_name"


async def _rows(conn, sql: str, args: tuple) -> list[dict]:
    cur = await conn.execute(sql, args)
    names = [c.name for c in cur.description]
    return [dict(zip(names, r)) for r in await cur.fetchall()]


async def _assemble(conn, sessions: list[dict], request_ids: list[str]) -> list[NegotiationTraceView]:
    if not sessions:
        return []
    ids = [s["session_id"] for s in sessions]
    messages = await _rows(conn, """SELECT session_id, message_id, sender_role, round, message_type, public_terms, ts
                                    FROM negotiation_messages WHERE session_id = ANY(%s)""", (ids,))
    offer_ids = {s["final_offer_id"] for s in sessions if s["final_offer_id"]}
    offer_ids |= {(m["public_terms"] or {}).get("offer_id") for m in messages} - {None}
    offers = {o["offer_id"]: o for o in await _rows(
        conn, """SELECT offer_id, sailing_id, quantity, total_price_minor, currency, expires_at, display_rank
                 FROM offers WHERE offer_id = ANY(%s) AND request_id = ANY(%s)""", (list(offer_ids), request_ids))}
    by_session: dict[str, list[dict]] = {}
    for m in messages:
        by_session.setdefault(m["session_id"], []).append(m)
    return [build_trace(s, by_session.get(s["session_id"], []), offers, s["display_name"]) for s in sessions]


async def request_traces(pool, *, buyer_id: str, request_id: str) -> list[NegotiationTraceView]:
    """Every booking negotiation of this buyer's request. Another buyer's request returns nothing."""
    async with pool.connection() as conn:
        sessions = await _rows(conn, f"""
            SELECT {_SESSION_COLS} FROM negotiation_sessions s
            JOIN cargo_requests r ON r.request_id = s.request_id AND r.buyer_id = %s
            JOIN carriers k ON k.carrier_id = s.carrier_id
            WHERE s.kind = 'booking' AND s.request_id = %s ORDER BY k.display_name""", (buyer_id, request_id))
        return await _assemble(conn, sessions, [request_id])


async def recovery_traces(pool, *, buyer_id: str, recovery_id: str, booking_id: str) -> list[NegotiationTraceView]:
    """The recovery negotiations for this buyer's own displaced booking only (never other buyers')."""
    async with pool.connection() as conn:
        sessions = await _rows(conn, f"""
            SELECT {_SESSION_COLS} FROM negotiation_sessions s
            JOIN bookings b ON b.booking_id = s.booking_id AND b.buyer_id = %s
            JOIN carriers k ON k.carrier_id = s.carrier_id
            WHERE s.kind = 'recovery' AND s.recovery_id = %s AND s.booking_id = %s
            ORDER BY k.display_name, s.session_id""", (buyer_id, recovery_id, booking_id))
        request_ids = list({s["request_id"] for s in sessions if s["request_id"]})
        return await _assemble(conn, sessions, request_ids)


# ---- pre-rendered cards (written once at negotiation close; read by owner only) -------------------
async def save_card(pool, *, trace_key: str, buyer_id: str, kind: str, card: dict, trace: dict | None = None,
                    request_id: str | None = None, offer_id: str | None = None, session_id: str | None = None,
                    recovery_id: str | None = None, booking_id: str | None = None) -> None:
    from psycopg.types.json import Jsonb
    async with pool.connection() as conn:
        await conn.execute(
            """INSERT INTO negotiation_trace_cards (trace_key, buyer_id, kind, request_id, offer_id, session_id,
                   recovery_id, booking_id, card, trace)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (trace_key) DO UPDATE SET card = EXCLUDED.card, trace = EXCLUDED.trace""",
            (trace_key, buyer_id, kind, request_id, offer_id, session_id, recovery_id, booking_id, Jsonb(card),
             Jsonb(trace) if trace is not None else None))


async def load_cards(pool, *, buyer_id: str, kind: str, request_id: str | None = None,
                     recovery_id: str | None = None, booking_id: str | None = None) -> list[dict]:
    """Owner-scoped: the buyer filter is part of the query, so another buyer's IDs simply match nothing."""
    async with pool.connection() as conn:
        return await _rows(conn, """
            SELECT trace_key, offer_id, session_id, card, trace FROM negotiation_trace_cards
            WHERE buyer_id = %s AND kind = %s AND (%s::text IS NULL OR request_id = %s)
              AND (%s::text IS NULL OR recovery_id = %s) AND (%s::text IS NULL OR booking_id = %s)
            ORDER BY trace_key""", (buyer_id, kind, request_id, request_id, recovery_id, recovery_id,
                                    booking_id, booking_id))


async def load_offer_views(pool, *, buyer_id: str, view_card_id: str | None) -> list[dict]:
    """One owner-scoped read for a transcript click: every stored transcript of the request this buyer's view
    card belongs to, plus what decides whether its offer can still be chosen (carousel card state, expiry)."""
    async with pool.connection() as conn:
        return await _rows(conn, """
            WITH v AS (SELECT resource_id FROM card_instances WHERE buyer_id = %s AND purpose = 'negotiation_view'
                         AND (%s::text IS NULL OR card_instance_id = %s) ORDER BY created_at DESC LIMIT 1)
            SELECT t.request_id, t.offer_id, t.session_id, t.card, t.trace->>'carrier_display_name' AS carrier,
                   t.trace->>'carrier_id' AS carrier_id, cc.status AS carousel_status,
                   (o.expires_at > now()) AS offer_live
            FROM v JOIN negotiation_trace_cards t ON t.buyer_id = %s AND t.request_id = v.resource_id AND t.kind = 'offer'
            LEFT JOIN offers o ON o.offer_id = t.offer_id AND o.request_id = t.request_id
            LEFT JOIN negotiation_trace_cards c ON c.trace_key = 'carousel:' || t.request_id AND c.buyer_id = %s
            LEFT JOIN card_instances cc ON cc.card_instance_id = c.trace->>'carousel_card_id'
            ORDER BY t.trace_key""", (buyer_id, view_card_id, view_card_id, buyer_id, buyer_id))

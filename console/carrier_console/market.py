"""What the market view shows: one recovery run, read from Tiger, in one snapshot.

The view never calls an agent. Everything comes from tables the exchange already writes:
recovery_runs (stranded snapshot, clearing result, first-come baseline), negotiation_sessions
and negotiation_messages (who bargained with whom, and what they said), recovery_legs (what
was actually reserved or confirmed). X-ray adds each side's private limit: the carrier's floor
from carrier_policies and the buyer's maximum (original price plus pre-approved extra).

`sample_snapshot()` is the demo story with the same shape, clearly labelled, for designing the
screens without a live run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from psycopg_pool import AsyncConnectionPool


@dataclass(frozen=True)
class Stranded:
    label: str  # A, B, C in posting order
    booking_id: str
    quantity: int
    due: datetime
    must_deliver: bool
    original_price_minor: int
    allowance_minor: int

    @property
    def max_per_container_minor(self) -> int:
        return (self.original_price_minor + self.allowance_minor) // max(self.quantity, 1)


@dataclass(frozen=True)
class Message:
    ts: datetime
    sender_role: str  # carrier | buyer_agent
    kind: str  # PROPOSE_OFFER, COUNTER_OFFER, ACCEPT_COUNTER, REVISED_OFFER, REJECT_COUNTER
    price_minor: int | None  # per container
    text: str


@dataclass(frozen=True)
class Session:
    session_id: str
    booking_id: str
    carrier_id: str
    status: str  # OPEN, ACCEPTED, REJECTED, EXHAUSTED, EXPIRED
    final_price_minor: int | None  # per container
    reason: str
    messages: tuple[Message, ...] = ()


@dataclass(frozen=True)
class Leg:
    booking_id: str
    carrier_id: str
    sailing_id: str
    quantity: int
    price_minor: int  # per container
    arrival_at: datetime
    needs_approval: bool
    status: str  # PROPOSED, HELD, CONFIRMED, RELEASED, FAILED


@dataclass(frozen=True)
class Carrier:
    carrier_id: str
    name: str
    sailing_id: str | None = None
    arrival_at: datetime | None = None
    note: str = ""


@dataclass
class MarketSnapshot:
    recovery_id: str
    sailing_id: str
    withdrawn_by: str
    withdrawn_at: datetime
    status: str
    stranded: list[Stranded]
    carriers: list[Carrier]
    sessions: list[Session]
    legs: list[Leg]
    baseline_recovered: int | None
    floors_minor: dict[str, int] = field(default_factory=dict)  # X-ray only
    sample: bool = False

    def booking(self, booking_id: str) -> Stranded | None:
        return next((s for s in self.stranded if s.booking_id == booking_id), None)

    def carrier(self, carrier_id: str) -> Carrier:
        return next((c for c in self.carriers if c.carrier_id == carrier_id), Carrier(carrier_id, carrier_id.title()))

    def session(self, session_id: str | None) -> Session | None:
        if session_id:
            return next((s for s in self.sessions if s.session_id == session_id), None)
        def score(s: Session) -> tuple:
            b = self.booking(s.booking_id)
            return (s.status == "ACCEPTED", bool(b and b.must_deliver), len(s.messages))
        talked = [s for s in self.sessions if s.messages]
        return max(talked, key=score) if talked else None

    def leg_for(self, booking_id: str) -> Leg | None:
        live = [l for l in self.legs if l.booking_id == booking_id and l.status != "RELEASED"]
        return live[0] if live else None

    @property
    def exchange_recovered(self) -> int:
        return len({l.booking_id for l in self.legs if l.status in ("PROPOSED", "HELD", "CONFIRMED")})


# --- live ---------------------------------------------------------------------------------

def _per_container(total: int | None, quantity: int) -> int | None:
    return None if total is None else total // max(quantity, 1)


def _message_text(role: str, kind: str, terms: dict[str, Any]) -> str:
    if role == "buyer_agent":
        return terms.get("public_rationale") or "Can you do better on this price?"
    return {
        "PROPOSE_OFFER": "I can take this cargo.",
        "ACCEPT_COUNTER": "Accepted.",
        "REVISED_OFFER": "I can meet you part way.",
        "REJECT_COUNTER": "My offer stands.",
    }.get(kind, kind.replace("_", " ").capitalize())


async def load_snapshot(pool: AsyncConnectionPool, recovery_id: str | None = None,
                        *, xray: bool = False) -> MarketSnapshot | None:
    async with pool.connection() as conn:
        run = await (await conn.execute(
            """SELECT r.recovery_id, r.sailing_id, r.status, r.proposal, d.carrier_id, d.occurred_at
               FROM recovery_runs r JOIN disruption_events d ON d.event_id = r.withdrawal_event_id
               WHERE (%s::text IS NULL OR r.recovery_id = %s)
               ORDER BY r.created_at DESC LIMIT 1""", (recovery_id, recovery_id))).fetchone()
        if run is None:
            return None
        rid, sailing_id, status, proposal, withdrawn_by, withdrawn_at = run
        proposal = proposal or {}
        stranded_raw = sorted(proposal.get("stranded", []), key=lambda b: b["booking_id"])
        order = (proposal.get("proposal", {}).get("baseline") or {}).get("order") or [b["booking_id"] for b in stranded_raw]
        by_id = {b["booking_id"]: b for b in stranded_raw}
        stranded = [
            Stranded(label=chr(ord("A") + i), booking_id=bid, quantity=int(by_id[bid]["quantity"]),
                     due=datetime.fromisoformat(by_id[bid]["latest_arrival"]),
                     must_deliver=bool(by_id[bid].get("must_deliver", False)),
                     original_price_minor=int(by_id[bid].get("original_price_minor", 0)),
                     allowance_minor=int(by_id[bid].get("max_additional_cost_minor", 0)))
            for i, bid in enumerate(b for b in order if b in by_id)
        ]
        qty = {s.booking_id: s.quantity for s in stranded}

        session_rows = await (await conn.execute(
            """SELECT session_id, booking_id, carrier_id, status, final_total_minor, COALESCE(reason, '')
               FROM negotiation_sessions WHERE recovery_id = %s ORDER BY opened_at, session_id""", (rid,))).fetchall()
        message_rows = await (await conn.execute(
            """SELECT m.session_id, m.ts, m.sender_role, m.message_type, m.public_terms
               FROM negotiation_messages m JOIN negotiation_sessions s ON s.session_id = m.session_id
               WHERE s.recovery_id = %s ORDER BY m.ts""", (rid,))).fetchall()
        leg_rows = await (await conn.execute(
            """SELECT booking_id, carrier_id, sailing_id, quantity, quoted_price_minor, arrival_at, needs_approval, status
               FROM recovery_legs WHERE recovery_id = %s
                 AND attempt = (SELECT max(attempt) FROM recovery_legs WHERE recovery_id = %s)""", (rid, rid))).fetchall()
        carrier_rows = await (await conn.execute("SELECT carrier_id, display_name FROM carriers ORDER BY carrier_id")).fetchall()
        floors: dict[str, int] = {}
        if xray:
            for cid, policy in await (await conn.execute("SELECT carrier_id, policy FROM carrier_policies")).fetchall():
                floors[cid] = int((policy or {}).get("min_price_per_unit_minor", 0))

    messages: dict[str, list[Message]] = {}
    session_booking = {r[0]: r[1] for r in session_rows}
    for sid, ts, role, kind, terms in message_rows:
        terms = terms or {}
        total = terms.get("total_price_minor") or terms.get("proposed_total_price_minor")
        messages.setdefault(sid, []).append(Message(
            ts=ts, sender_role=role, kind=kind,
            price_minor=_per_container(int(total) if total else None, qty.get(session_booking.get(sid), 1)),
            text=_message_text(role, kind, terms)))
    sessions = [Session(session_id=sid, booking_id=bid, carrier_id=cid, status=st,
                        final_price_minor=_per_container(final, qty.get(bid, 1)), reason=reason,
                        messages=tuple(messages.get(sid, [])))
                for sid, bid, cid, st, final, reason in session_rows]
    legs = [Leg(booking_id=b, carrier_id=c, sailing_id=s, quantity=q, price_minor=p // max(q, 1),
                arrival_at=a, needs_approval=n, status=st) for b, c, s, q, p, a, n, st in leg_rows]
    names = {cid: name.replace(" (fictional)", "") for cid, name in carrier_rows}
    in_play = {s.carrier_id for s in sessions} | {l.carrier_id for l in legs}
    carriers = [Carrier(cid, names[cid]) for cid in names if cid in in_play]
    carriers += [Carrier(cid, names[cid], note="No offer") for cid in names if cid not in in_play and cid != withdrawn_by]
    baseline = (proposal.get("proposal", {}).get("baseline") or {}).get("recovered")
    return MarketSnapshot(recovery_id=rid, sailing_id=sailing_id or "", withdrawn_by=withdrawn_by,
                          withdrawn_at=withdrawn_at, status=status, stranded=stranded, carriers=carriers,
                          sessions=sessions, legs=legs, baseline_recovered=baseline, floors_minor=floors)


# --- sample: the demo story, same shape -----------------------------------------------------

def sample_snapshot() -> MarketSnapshot:
    t0 = datetime(2026, 10, 18, 9, 40, 16, tzinfo=timezone.utc)

    def at(minutes: float) -> datetime:
        return t0 + timedelta(minutes=minutes)

    due22, due25 = datetime(2026, 10, 22, 8, tzinfo=timezone.utc), datetime(2026, 10, 25, 8, tzinfo=timezone.utc)
    stranded = [
        Stranded("A", "BK-28C01D37", 2, due25, False, 1_040_000, 120_000),
        Stranded("B", "BK-0A14FFE2", 1, due22, True, 500_000, 80_000),
        Stranded("C", "BK-71BC02A9", 1, due22, False, 500_000, 0),
    ]
    b_beacon = (
        Message(at(33.9), "carrier", "PROPOSE_OFFER", 600_000, "I can take 1 container on BEA-201, arriving 22 Oct 04:00."),
        Message(at(36.4), "buyer_agent", "COUNTER_OFFER", 530_000, "Comparable offers are around $5,300. Can you meet it?"),
        Message(at(37.8), "carrier", "REVISED_OFFER", 550_000, "I can meet you at $5,500."),
    )
    sessions = [
        Session("S-0191", "BK-28C01D37", "beacon", "REJECTED", None, "carrier refused the counter",
                (Message(at(33.6), "carrier", "PROPOSE_OFFER", 600_000, "I can take 2 containers on BEA-201."),
                 Message(at(35.9), "buyer_agent", "COUNTER_OFFER", 520_000, "Cove arrives in time for less."),
                 Message(at(37.1), "carrier", "REJECT_COUNTER", None, "My offer stands."))),
        Session("S-0192", "BK-28C01D37", "cove", "ACCEPTED", 520_000, "carrier accepted the counter",
                (Message(at(33.7), "carrier", "PROPOSE_OFFER", 540_000, "I can take 2 containers on COV-301, arriving 24 Oct."),
                 Message(at(36.0), "buyer_agent", "COUNTER_OFFER", 520_000, "Can you do $5,200?"),
                 Message(at(36.9), "carrier", "ACCEPT_COUNTER", 520_000, "Accepted."))),
        Session("S-0194", "BK-28C01D37", "meridian", "ACCEPTED", 555_000, "buyer agent accepted this offer",
                (Message(at(34.0), "carrier", "PROPOSE_OFFER", 555_000, "I can take 2 containers on MER-411, arriving 25 Oct."),)),
        Session("S-0193", "BK-0A14FFE2", "beacon", "ACCEPTED", 550_000, "price agreed", b_beacon),
        Session("S-0195", "BK-71BC02A9", "beacon", "ACCEPTED", 550_000, "price agreed, above the buyer's limit",
                (Message(at(34.1), "carrier", "PROPOSE_OFFER", 600_000, "I can take 1 container on BEA-201."),
                 Message(at(36.5), "buyer_agent", "COUNTER_OFFER", 500_000, "Can you do $5,000?"),
                 Message(at(37.9), "carrier", "REVISED_OFFER", 550_000, "I can meet you at $5,500."))),
    ]
    carriers = [
        Carrier("beacon", "Beacon Maritime", "BEA-201", datetime(2026, 10, 22, 4, tzinfo=timezone.utc), "2 open slots"),
        Carrier("cove", "Cove Shipping", "COV-301", datetime(2026, 10, 24, 6, tzinfo=timezone.utc), "3 open slots"),
        Carrier("meridian", "Meridian Container Line", "MER-411", datetime(2026, 10, 25, 2, tzinfo=timezone.utc), "2 open slots"),
        Carrier("dhow", "Dhow Feeder Lines", note="No route to Mumbai"),
    ]
    legs = [
        Leg("BK-28C01D37", "cove", "COV-301", 2, 520_000, carriers[1].arrival_at, False, "CONFIRMED"),
        Leg("BK-0A14FFE2", "beacon", "BEA-201", 1, 550_000, carriers[0].arrival_at, False, "CONFIRMED"),
        Leg("BK-71BC02A9", "beacon", "BEA-201", 1, 550_000, carriers[0].arrival_at, True, "HELD"),
    ]
    return MarketSnapshot("rec_sample", "ATL-101", "atlas", t0, "AWAITING_APPROVAL", stranded, carriers, sessions,
                          legs, baseline_recovered=1,
                          floors_minor={"beacon": 510_000, "cove": 500_000, "meridian": 530_000}, sample=True)

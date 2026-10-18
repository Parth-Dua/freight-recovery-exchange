"""Two-sided negotiation for normal bookings (v2 phase 4).

pair -> one durable session per eligible carrier company -> signed REQUEST_OFFER (carrier prices from its
private policy) -> up to two buyer-agent counters per session (private buyer strategy) -> final offers.

The buyer agent is a logical, per-buyer isolated actor inside this backend (not a separate uAgent): its
private state (max price, priority, must-deliver, urgency) never leaves this module; carriers receive only
public cargo terms, the counter price and a short public rationale. Carrier replies are signed by the real
carrier uAgents. Prices are deterministic: no model chooses a price.
"""

from __future__ import annotations

import asyncio
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from freight_contracts import CargoSpec, ErrorCode, OfferTerms, RequestOffer
from psycopg.types.json import Jsonb

from freight_exchange.carrier_client import CarrierClient, CarrierOutcome, CarrierRef, CounterOutcome
from freight_exchange.observability import log_event
from freight_exchange.repository import Repository, _event

MAX_CARRIERS = 5
ROUNDS_MAX = 2
SESSION_SECONDS = 90
STEP_MINOR = 1000                      # counters in whole $10
VALUE_PER_DAY_MINOR = {"cheapest": 0, "balanced": 5000, "fastest": 20000}   # per container per day later
BASE_STEP, MIN_STEP, MAX_STEP = Decimal("0.06"), Decimal("0.015"), Decimal("0.08")


# ---- buyer agent (private, deterministic) ------------------------------------------------
@dataclass(frozen=True)
class BuyerPrivate:
    priority: str = "balanced"
    max_total_minor: int | None = None
    must_deliver: bool = False
    latest_arrival: datetime | None = None


@dataclass(frozen=True)
class Quote:
    total_minor: int
    arrival_at: datetime


@dataclass(frozen=True)
class Counter:
    amount_minor: int
    rationale: str


def adjusted_cost(q: Quote, fastest: datetime, priority: str, quantity: int) -> Decimal:
    days_later = Decimal(max(0.0, (q.arrival_at - fastest).total_seconds()) / 86400)
    return Decimal(q.total_minor) + VALUE_PER_DAY_MINOR.get(priority, 5000) * quantity * days_later


def urgency(private: BuyerPrivate, now: datetime) -> Decimal:
    """0 (relaxed) .. 1 (urgent): time left to the deadline over a one-week horizon, must-deliver adds 0.3."""
    u = Decimal(0)
    if private.latest_arrival:
        hours_left = Decimal(max(0.0, (private.latest_arrival - now).total_seconds()) / 3600)
        u = max(Decimal(0), min(Decimal(1), 1 - hours_left / 168))
    if private.must_deliver:
        u = min(Decimal(1), u + Decimal("0.3"))
    return u


def buyer_counter(own: Quote, rivals: list[Quote], private: BuyerPrivate, round_number: int, quantity: int,
                  now: datetime) -> Counter | None:
    """Ask for less using verified rival evidence; the more urgent the buyer, the gentler the ask.
    Never above the current offer or the private max, never reveals either; None = accept as is."""
    fastest = min([own.arrival_at] + [r.arrival_at for r in rivals])
    own_adj = adjusted_cost(own, fastest, private.priority, quantity)
    leverage = min([adjusted_cost(r, fastest, private.priority, quantity) for r in rivals], default=own_adj)
    step_frac = max(MIN_STEP, min(MAX_STEP, BASE_STEP * (1 - urgency(private, now))))
    if round_number >= 2:
        step_frac /= 2
    target = min(Decimal(own.total_minor), leverage - (own_adj - own.total_minor)) - own.total_minor * step_frac
    if private.max_total_minor is not None:
        target = min(target, Decimal(private.max_total_minor))
    amount = int(math.floor(target / STEP_MINOR) * STEP_MINOR)
    if amount <= 0 or amount >= own.total_minor:
        return None
    cheaper_rival = any(r.total_minor < own.total_minor for r in rivals)
    return Counter(amount, "A comparable verified offer is lower." if cheaper_rival
                   else "We would book now at this price.")


def rank_key(q: Quote, offer_id: str, fastest: datetime, private: BuyerPrivate, quantity: int):
    return (adjusted_cost(q, fastest, private.priority, quantity), q.arrival_at, offer_id)


# ---- pairing (public, deterministic) -------------------------------------------------------
@dataclass(frozen=True)
class Pair:
    carrier: CarrierRef
    eligible_sailings: int
    reason: str


PAIR_SQL = """
SELECT k.carrier_id, k.display_name, k.agent_address, k.endpoint_url, count(*) AS sailings
FROM carriers k
JOIN sailings s ON s.carrier_id = k.carrier_id AND s.status = 'ACTIVE'
JOIN LATERAL (SELECT count(*) AS free FROM capacity_slots c
              WHERE c.sailing_id = s.sailing_id AND c.container_type = %(ctype)s AND c.status = 'AVAILABLE') a ON true
WHERE k.active AND s.origin_port = %(origin)s AND s.destination_port = %(dest)s AND s.departure_at > now()
  AND (%(earliest)s::timestamptz IS NULL OR s.departure_at >= %(earliest)s::timestamptz)
  AND (%(latest)s::timestamptz IS NULL OR s.arrival_at <= %(latest)s::timestamptz)
  AND a.free >= %(need)s
GROUP BY k.carrier_id, k.display_name, k.agent_address, k.endpoint_url
ORDER BY k.carrier_id
LIMIT %(limit)s
"""


async def pair_candidates(repo: Repository, cargo: CargoSpec) -> list[Pair]:
    """Every active carrier with at least one ACTIVE sailing on the lane, inside the dates, with real free slots
    (all of them when the booking can't be split). Cargo-class acceptance is each carrier's private call."""
    async with repo.pool.connection() as conn:
        rows = await (await conn.execute(PAIR_SQL, {
            "ctype": cargo.container_type, "origin": cargo.origin_port, "dest": cargo.destination_port,
            "earliest": cargo.earliest_departure, "latest": cargo.latest_arrival,
            "need": 1 if cargo.allow_split else cargo.quantity, "limit": MAX_CARRIERS})).fetchall()
    return [Pair(CarrierRef(*r[:4]), r[4], f"{r[4]} eligible sailing(s) with space") for r in rows]


# ---- sessions ------------------------------------------------------------------------------
@dataclass
class Session:
    session_id: str
    carrier: CarrierRef
    request: RequestOffer
    status: str = "OPEN"
    round: int = 0
    current: OfferTerms | None = None
    trace: list[str] = field(default_factory=list)
    reason: str = ""
    booking_id: str | None = None  # recovery sessions: the displaced booking this session is for


@dataclass
class NegotiationResult:
    request_id: str
    sessions: list[Session]
    finals: list[Session]
    paired: int


class NegotiationService:
    def __init__(self, repo: Repository, client: CarrierClient, clock=lambda: datetime.now(timezone.utc)):
        self.repo = repo
        self.client = client
        self.clock = clock

    async def run(self, *, buyer_id: str, chat_session: str, request_id: str, cargo: CargoSpec,
                  private: BuyerPrivate, extra: dict | None = None) -> NegotiationResult:
        correlation = f"flow_{uuid4().hex[:12]}"
        await self.repo.create_request(cargo, request_id=request_id, correlation_id=correlation, buyer_id=buyer_id,
                                       requirements={"cargo": json.loads(cargo.json()), "channel": "gateway-card",
                                                     "negotiation": "two_sided_v2"}, session_id=chat_session)
        async with self.repo.pool.connection() as conn:
            await conn.execute(
                """UPDATE cargo_requests SET priority = %s, must_deliver = %s, max_total_price_minor = %s,
                       cargo_class = %s, recovery_allowance_minor = %s, gateway_agent_address = %s
                   WHERE request_id = %s""",
                (private.priority, private.must_deliver, private.max_total_minor, cargo.cargo_class,
                 (extra or {}).get("recovery_allowance_minor", 0), (extra or {}).get("gateway"), request_id))
        pairs = await pair_candidates(self.repo, cargo)
        now = self.clock()
        expires = now + timedelta(seconds=SESSION_SECONDS)
        sessions = [Session(f"neg_{uuid4().hex[:16]}", p.carrier,
                            RequestOffer(message_id=f"msg_{uuid4().hex}", correlation_id=correlation, request_id=request_id,
                                         negotiation_id="", sent_at=now, expires_at=now + timedelta(seconds=self.client.timeout_s),
                                         cargo=cargo)) for p in pairs]
        for s in sessions:
            s.request = s.request.copy(update={"negotiation_id": s.session_id})
            await self._open(s, buyer_id, request_id, expires)
        await self.negotiate_group(sessions, private, cargo.quantity, expires)

        finals = [s for s in sessions if s.current is not None and s.current.expires_at > self.clock()
                  and (private.max_total_minor is None or s.current.total_price_minor <= private.max_total_minor)]
        if finals:
            fastest = min(s.current.arrival_at for s in finals)
            finals.sort(key=lambda s: rank_key(Quote(s.current.total_price_minor, s.current.arrival_at),
                                               s.current.offer_id, fastest, private, cargo.quantity))
        await self.repo.set_display_ranks(request_id, [s.current.offer_id for s in finals])
        await self.repo.finish_request(request_id, correlation, len(finals))
        log_event("negotiation_done", request_id=request_id, paired=len(pairs), finals=len(finals),
                  sessions={s.carrier.carrier_id: s.status for s in sessions})
        return NegotiationResult(request_id, sessions, finals, len(pairs))

    async def negotiate_group(self, sessions: list[Session], private: BuyerPrivate, quantity: int,
                              deadline: datetime) -> None:
        """Opening offers in parallel, then up to two counter rounds; every session ends closed.
        Sessions in one group are rivals of each other (same buyer request / same displaced booking)."""
        exchange = self.client.identity.address

        async def opening(s: Session) -> None:
            out = await self.client.request_one(s.carrier, s.request,
                                                on_sent=lambda c, m: self.repo.record_sent(c, m, exchange))
            if out.offer is not None and await self.repo.sailing_owner(out.offer.sailing_id) != s.carrier.carrier_id:
                out.kind, out.error_code = "exchange_error", ErrorCode.OFFER_CONSTRAINT_VIOLATION
                out.detail = f"sailing {out.offer.sailing_id} is not owned by {s.carrier.carrier_id}"
            await self.repo.record_outcome(out, exchange)
            await self._opening_outcome(s, out)

        await asyncio.gather(*(opening(s) for s in sessions))
        for round_number in (1, 2):
            live = [s for s in sessions if s.status == "OPEN" and s.current is not None]
            if not live:
                break
            if self.clock() >= deadline:
                for s in live:
                    await self._close(s, "EXPIRED", "negotiation deadline reached", keep_offer=False)
                break
            await asyncio.gather(*(self._counter_round(s, sessions, private, round_number, quantity, exchange)
                                   for s in live))
        for s in sessions:
            if s.status == "OPEN":
                await self._close(s, "EXHAUSTED" if s.round >= ROUNDS_MAX else "ACCEPTED",
                                  "two counters used" if s.round >= ROUNDS_MAX else "buyer agent accepted this offer")

    async def _opening_outcome(self, s: Session, out: CarrierOutcome) -> None:
        if out.offer is not None:
            s.current = out.offer
            s.trace.append(f"opened {money(out.offer.total_price_minor)}")
            await self._message(s, "carrier", s.carrier.agent_address, out.offer.message_id, 0, "PROPOSE_OFFER",
                                {"offer_id": out.offer.offer_id, "sailing_id": out.offer.sailing_id,
                                 "total_price_minor": out.offer.total_price_minor,
                                 "arrival_at": out.offer.arrival_at.isoformat()}, "OFFER_SENT")
        else:
            s.trace.append("declined" if out.kind == "reject" else f"error {out.error_code}")
            await self._close(s, "REJECTED", out.detail or "carrier declined")

    async def _counter_round(self, s: Session, sessions: list[Session], private: BuyerPrivate, round_number: int,
                             quantity: int, exchange: str) -> None:
        now = self.clock()
        rivals = [Quote(o.current.total_price_minor, o.current.arrival_at) for o in sessions
                  if o is not s and o.current is not None and o.current.expires_at > now
                  and o.current.quantity == s.current.quantity]
        ask = buyer_counter(Quote(s.current.total_price_minor, s.current.arrival_at), rivals, private, round_number,
                            quantity, now)
        if ask is None:
            await self._close(s, "ACCEPTED", "buyer agent accepted this offer")
            return

        async def on_sent(carrier: CarrierRef, counter) -> None:
            await self.repo.record_counter_sent(carrier, counter, exchange)
            await self._message(s, "buyer_agent", exchange, counter.message_id, round_number, "COUNTER_OFFER",
                                {"offer_id": s.current.offer_id, "proposed_total_price_minor": ask.amount_minor,
                                 "public_rationale": ask.rationale}, "COUNTER_SENT")

        out: CounterOutcome = await self.client.counter_one(s.carrier, s.current, s.request, ask.amount_minor,
                                                            on_sent=on_sent, round_number=round_number,
                                                            rationale=ask.rationale)
        await self.repo.record_counter_outcome(out, exchange)
        if not await self._advance(s, round_number):
            return
        s.trace.append(f"countered {money(ask.amount_minor)}")
        if out.kind in ("accept", "revise"):
            s.current = out.new_offer
            s.trace.append(("accepted at " if out.kind == "accept" else "met at ") + money(s.current.total_price_minor))
            await self._message(s, "carrier", s.carrier.agent_address, out.reply.message_id, round_number,
                                out.reply.message_type, {"offer_id": s.current.offer_id,
                                                         "total_price_minor": s.current.total_price_minor},
                                "OFFER_ACCEPTED" if out.kind == "accept" else "OFFER_SENT")
            if out.kind == "accept":
                await self._close(s, "ACCEPTED", "carrier accepted the counter")
        else:
            s.trace.append("counter refused; their offer stands" if out.kind == "reject" else f"error {out.error_code}")
            await self._message(s, "carrier", s.carrier.agent_address,
                                out.reply.message_id if out.reply is not None else f"local_{uuid4().hex}", round_number,
                                getattr(out.reply, "message_type", "EXCHANGE_ERROR"),
                                {"offer_id": s.current.offer_id, "outcome": out.kind}, "OFFER_REJECTED")
            await self._close(s, "EXHAUSTED" if round_number >= ROUNDS_MAX else "REJECTED",
                              "carrier refused the counter; its previous offer stands")

    # ---- persistence (guarded transitions + public trace) ---------------------------------
    async def _open(self, s: Session, buyer_id: str, request_id: str, expires: datetime, *, kind: str = "booking",
                    recovery_id: str | None = None) -> None:
        async with self.repo.pool.connection() as conn, conn.transaction():
            await conn.execute(
                """INSERT INTO negotiation_sessions (session_id, buyer_agent_key, carrier_id, carrier_agent_address,
                       request_id, booking_id, recovery_id, kind, rounds_max, expires_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (s.session_id, f"buyer:{buyer_id}", s.carrier.carrier_id, s.carrier.agent_address, request_id,
                 s.booking_id, recovery_id, kind, ROUNDS_MAX, expires))
            await _event(conn, "NEGOTIATION_OPENED", "negotiation", s.session_id, correlation_id=s.session_id,
                         carrier_id=s.carrier.carrier_id,
                         payload={"kind": kind, "request_id": request_id, "booking_id": s.booking_id,
                                  "recovery_id": recovery_id, "rounds_max": ROUNDS_MAX})

    async def _advance(self, s: Session, round_number: int) -> bool:
        async with self.repo.pool.connection() as conn:
            row = await (await conn.execute(
                "UPDATE negotiation_sessions SET round = %s WHERE session_id = %s AND status = 'OPEN' AND round = %s "
                "AND expires_at > now() RETURNING session_id", (round_number, s.session_id, round_number - 1))).fetchone()
        if row is None:  # closed, expired or replayed: the round cannot advance
            s.status = "EXPIRED"
            return False
        s.round = round_number
        return True

    async def _close(self, s: Session, status: str, reason: str, *, keep_offer: bool = True) -> None:
        s.status, s.reason = status, reason
        if not keep_offer:  # an expired session never yields a final offer
            s.current = None
        async with self.repo.pool.connection() as conn, conn.transaction():
            await conn.execute(
                """UPDATE negotiation_sessions SET status = %s, reason = %s, closed_at = now(), final_offer_id = %s,
                       final_total_minor = %s WHERE session_id = %s AND status = 'OPEN'""",
                (status, reason, s.current.offer_id if s.current else None,
                 s.current.total_price_minor if s.current else None, s.session_id))
            await _event(conn, "NEGOTIATION_CLOSED", "negotiation", s.session_id, correlation_id=s.session_id,
                         carrier_id=s.carrier.carrier_id,
                         payload={"status": status, "rounds": s.round,
                                  "final_offer_id": s.current.offer_id if s.current else None,
                                  "final_total_minor": s.current.total_price_minor if s.current else None})

    async def _message(self, s: Session, role: str, address: str, message_id: str, round_number: int, mtype: str,
                       terms: dict, event: str) -> None:
        async with self.repo.pool.connection() as conn, conn.transaction():
            await conn.execute(
                """INSERT INTO negotiation_messages (session_id, message_id, sender_role, sender_address, round,
                       message_type, public_terms) VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (sender_address, message_id) DO NOTHING""",
                (s.session_id, message_id, role, address, round_number, mtype, Jsonb(terms)))
            await _event(conn, event, "negotiation", s.session_id, correlation_id=s.session_id,
                         carrier_id=s.carrier.carrier_id, payload={"round": round_number, **terms})


def money(minor: int) -> str:
    from freight_exchange.responses import money as fmt
    return fmt(minor)

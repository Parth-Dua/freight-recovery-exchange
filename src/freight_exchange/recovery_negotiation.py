"""Recovery negotiation (v2 phase 5): one session per displaced booking x eligible carrier, all in parallel.

`recovery_negotiation_timeout_seconds` (default 60) is an UPPER BOUND, not a wait: clearing starts as soon
as every session is closed, or at the deadline, whichever is first. Sessions still open at the deadline
expire without a final offer; late replies cannot change the frozen offer set. Carriers price recovery
requests with their private disruption surge; the buyer agent counters with the booking's private limits.
Only final, signed, unexpired offers reach the solver; a bid is never a hold.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from freight_contracts import CargoSpec, OfferTerms, RequestOffer
from freight_seams import DisruptedBooking, RecoveryOffer

from freight_exchange.negotiation_sessions import BuyerPrivate, NegotiationService, Session, pair_candidates
from freight_exchange.observability import log_event
from freight_exchange.repository import _event

NO_DEADLINE_YEAR = 2100


def recovery_timeout_seconds() -> int:
    return int(os.getenv("RECOVERY_NEGOTIATION_TIMEOUT_SECONDS", "60"))


@dataclass(frozen=True)
class NegotiatedSource:
    """Where a recovery offer came from: a real negotiated carrier offer for exactly one displaced booking."""

    offer: OfferTerms
    booking_id: str
    request_id: str
    session_id: str


@dataclass(frozen=True)
class NegotiationSummary:
    sessions: int
    finals: int
    expired: int
    elapsed_s: float
    closed_early: bool


class RecoveryNegotiator:
    def __init__(self, repo, client, clock=lambda: datetime.now(timezone.utc)):
        self.repo = repo
        self.client = client
        self.clock = clock

    async def post_board(self, recovery_id: str, stranded: list[DisruptedBooking]) -> None:
        """Public stranded-cargo board: lane, quantity, deadline, class, split. Never budgets, allowances,
        must-deliver or the buyer's identity."""
        async with self.repo.pool.connection() as conn, conn.transaction():
            for i, b in enumerate(sorted(stranded, key=lambda x: x.booking_id), 1):
                await _event(conn, "RECOVERY_BOARD_POSTED", "recovery_run", recovery_id, correlation_id=recovery_id,
                             payload={"job_ref": f"{recovery_id}-job{i}", "origin_port": b.origin_port,
                                      "destination_port": b.destination_port, "container_type": b.container_type,
                                      "quantity": b.quantity, "allow_split": b.allow_split,
                                      "latest_arrival_at": None if b.latest_arrival.year >= NO_DEADLINE_YEAR
                                      else b.latest_arrival.isoformat(), "source_kind": "SIMULATED_BUYER"})

    async def negotiate(self, recovery_id: str, stranded: list[DisruptedBooking], deadline: datetime):
        started = self.clock()
        service = NegotiationService(self.repo, self.client, clock=self.clock)
        facts = await self._facts([b.booking_id for b in stranded])
        groups = []
        for b in sorted(stranded, key=lambda x: x.booking_id):
            request_id, cargo_class, max_total = facts[b.booking_id]
            deadline_at = None if b.latest_arrival.year >= NO_DEADLINE_YEAR else b.latest_arrival
            cargo = CargoSpec(origin_port=b.origin_port, destination_port=b.destination_port, container_type="20GP",
                              quantity=b.quantity, latest_arrival=deadline_at, allow_split=b.allow_split,
                              currency=b.currency, cargo_class=cargo_class, max_total_price_minor=None)
            sessions = []
            for pair in await pair_candidates(self.repo, cargo):
                sid = f"neg_{uuid4().hex[:16]}"
                now = self.clock()
                s = Session(sid, pair.carrier, RequestOffer(
                    message_id=f"msg_{uuid4().hex}", correlation_id=recovery_id, request_id=request_id,
                    negotiation_id=sid, sent_at=now, expires_at=deadline,  # valid for the whole bounded window
                    cargo=cargo, kind="recovery"), booking_id=b.booking_id)
                await service._open(s, b.buyer_id, request_id, deadline, kind="recovery", recovery_id=recovery_id)
                sessions.append(s)
            private = BuyerPrivate(priority=b.priority, max_total_minor=max_total, must_deliver=b.must_deliver,
                                   latest_arrival=deadline_at)
            groups.append((b, sessions, private))

        tasks = [asyncio.create_task(service.negotiate_group(sessions, private, b.quantity, deadline))
                 for b, sessions, private in groups if sessions]
        remaining = max(0.0, (deadline - self.clock()).total_seconds())
        pending = set()
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=remaining)
        for task in pending:  # deadline hit: late messages can no longer change anything
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        expired = 0
        for _, sessions, _ in groups:
            for s in sessions:
                if s.status == "OPEN":
                    expired += 1
                    await service._close(s, "EXPIRED", "recovery negotiation deadline reached", keep_offer=False)

        now = self.clock()
        offers, sources = [], {}
        for b, sessions, _ in groups:
            for s in sessions:
                o = s.current
                if o is None or s.status == "EXPIRED" or o.expires_at <= now:
                    continue
                offers.append(RecoveryOffer(offer_id=o.offer_id, carrier_id=s.carrier.carrier_id, sailing_id=o.sailing_id,
                                            capacity_units=o.quantity, total_price_minor=o.total_price_minor,
                                            currency=o.currency, arrival_at=o.arrival_at, expires_at=o.expires_at,
                                            eligible_booking_ids=(b.booking_id,)))
                sources[o.offer_id] = NegotiatedSource(o, b.booking_id, s.request.request_id, s.session_id)
        summary = NegotiationSummary(sessions=sum(len(g[1]) for g in groups), finals=len(offers), expired=expired,
                                     elapsed_s=round((now - started).total_seconds(), 1),
                                     closed_early=not pending and now < deadline)
        log_event("recovery_negotiation_done", recovery_id=recovery_id, **summary.__dict__)
        return offers, sources, summary

    async def _facts(self, booking_ids: list[str]) -> dict[str, tuple[str, str, int | None]]:
        async with self.repo.pool.connection() as conn:
            rows = await (await conn.execute(
                """SELECT b.booking_id, b.request_id, r.cargo_class, r.max_total_price_minor
                   FROM bookings b JOIN cargo_requests r ON r.request_id = b.request_id
                   WHERE b.booking_id = ANY(%s)""", (booking_ids,))).fetchall()
        return {r[0]: (r[1], r[2], r[3]) for r in rows}

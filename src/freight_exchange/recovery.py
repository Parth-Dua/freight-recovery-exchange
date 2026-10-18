"""Recovery orchestration: RecoverySeam (Parth), withdrawal -> confirmed replacement bookings.

    claim run (one per withdrawal) -> stranded bookings (DisruptionSeam, Tisya) -> tell buyers
    -> one-shot REQUEST_RECOVERY_CAPACITY to every carrier (recovery_client.py, phase 6)
    -> message checks -> AllocationSeam.allocate (Tisya: claim checks + OR-Tools) -> PROPOSED
    -> holds on every assignment, in a fixed order (phase 7)
         a hold fails on the first attempt: release them all, ask the carriers again, re-solve once
    -> inside the buyer's allowance: confirm; outside: ApprovalSeam (Tisya), confirm only if APPROVED
    -> confirmed: replacement booking (replaces_booking_id), original SUPERSEDED -> tell buyers

A proposal is never a booking: only carrier-confirmed legs are reported as recovered. Runs
waiting on a buyer or on an unanswered confirmation are finished later by `resume`. Duplicate
triggers for the same withdrawal return the existing run (recovery_runs.withdrawal_event_id is
UNIQUE). Multi-carrier commitments are not atomic: every step is idempotent and recorded per leg.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from typing import Callable, Protocol

from freight_contracts import RecoveryCapacityTerms
from freight_seams import (
    AllocationSeam, ApprovalSeam, DisruptedBooking, DisruptionSeam, RecoveryAssignment, RecoveryOutcome,
    RecoveryProposal,
)
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from uuid import uuid4

from freight_exchange.carrier_client import CarrierRef
from freight_exchange.observability import log_event
from freight_exchange.recovery_client import (
    RecoveryClient, RecoveryReplyOutcome, SailingFacts, check_offers, recovery_items,
)
from freight_exchange.repository import Repository, _event

HOLD_TTL_S = 2400  # covers the buyer approval window (30 min) with room to confirm
MAX_ATTEMPTS = 2   # the first solve plus at most one fresh re-solve after a failed hold
# Transport-level failures leave a confirmation's result unknown; anything else is a definite answer.
UNKNOWN_RESULT = {"CARRIER_TIMEOUT", "CARRIER_UNREACHABLE", "MALFORMED_REPLY", "INTERNAL_ERROR"}


class NoWithdrawal(LookupError):
    """Recovery starts only from a carrier-authorized SAILING_WITHDRAWN event, never a risk alert."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _jsonable(value) -> dict:
    return json.loads(json.dumps(value, default=lambda v: v.isoformat() if isinstance(v, datetime) else str(v)))


_REPLY_STATUS = {"offers": "ACCEPTED_FOR_REVIEW", "reject": "DECLINED",
                 "carrier_error": "CARRIER_ERROR", "exchange_error": "REJECTED_BY_EXCHANGE"}


@dataclass
class Leg:
    """One proposed assignment of (part of) a booking to one recovery offer."""

    booking_id: str
    recovery_offer_id: str
    offer_id: str  # per-booking offers row the hold and the replacement booking reference
    carrier_id: str
    sailing_id: str
    quantity: int
    quoted_price_minor: int
    currency: str
    arrival_at: datetime
    needs_approval: bool = False
    status: str = "PROPOSED"
    hold_id: str | None = None
    hold_expires_at: datetime | None = None
    approval_id: str | None = None
    replacement_booking_id: str | None = None
    detail: str | None = None

    @property
    def key(self) -> tuple[str, str, str]:  # deterministic hold order
        return self.carrier_id, self.sailing_id, self.booking_id


@dataclass(frozen=True)
class BookingFacts:
    booking_id: str
    buyer_id: str
    request_id: str
    claim_code_hash: str | None


class RecoveryNotifier(Protocol):
    """Buyer messages (Tisya's asi/recovery_notify.py). Must never raise."""

    async def cancelled(self, *, withdrawal_event_id: str, sailing_id: str, stranded: list[DisruptedBooking]) -> None: ...

    async def update(self, *, recovery_run_id: str, stranded: list[DisruptedBooking], proposal: RecoveryProposal,
                     outcome: RecoveryOutcome | None) -> None: ...


class RecoveryStore(Repository):
    """Tiger reads and writes for recovery runs. Every state change appends a market event."""

    async def withdrawal_event(self, sailing_id: str) -> str | None:
        async with self.pool.connection() as conn:
            row = await (await conn.execute(
                """SELECT event_id FROM disruption_events
                   WHERE sailing_id = %s AND event_type = 'SAILING_WITHDRAWN'
                   ORDER BY occurred_at DESC LIMIT 1""", (sailing_id,))).fetchone()
        return row[0] if row else None

    async def claim_run(self, *, event_id: str, sailing_id: str, triggered_at: datetime) -> tuple[str, bool]:
        """Return (recovery_id, created). Only the first trigger for a withdrawal creates a run."""
        recovery_id = f"rec_{uuid4().hex[:12]}"
        async with self.pool.connection() as conn, conn.transaction():
            row = await (await conn.execute(
                """INSERT INTO recovery_runs (recovery_id, withdrawal_event_id, status, sailing_id, correlation_id)
                   VALUES (%s, %s, 'CREATED', %s, %s)
                   ON CONFLICT (withdrawal_event_id) DO NOTHING RETURNING recovery_id""",
                (recovery_id, event_id, sailing_id, recovery_id))).fetchone()
            if row is None:
                existing = await (await conn.execute(
                    "SELECT recovery_id FROM recovery_runs WHERE withdrawal_event_id = %s", (event_id,))).fetchone()
                return existing[0], False
            await _event(conn, "RECOVERY_RUN_CREATED", "recovery_run", recovery_id, correlation_id=recovery_id,
                         sailing_id=sailing_id, payload={"withdrawal_event_id": event_id,
                                                         "triggered_at": triggered_at.isoformat()})
        return recovery_id, True

    async def set_status(self, recovery_id: str, status: str, detail: str | None = None) -> None:
        async with self.pool.connection() as conn, conn.transaction():
            await conn.execute(
                "UPDATE recovery_runs SET status = %s, detail = COALESCE(%s, detail), updated_at = now() "
                "WHERE recovery_id = %s", (status, detail, recovery_id))
            await _event(conn, f"RECOVERY_{status}", "recovery_run", recovery_id, correlation_id=recovery_id,
                         payload={"detail": detail} if detail else {})

    async def set_attempts(self, recovery_id: str, attempts: int) -> None:
        async with self.pool.connection() as conn:
            await conn.execute("UPDATE recovery_runs SET attempts = %s, updated_at = now() WHERE recovery_id = %s",
                               (attempts, recovery_id))

    async def sailing_facts(self, sailing_ids: list[str]) -> dict[str, SailingFacts]:
        if not sailing_ids:
            return {}
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(
                """SELECT sailing_id, carrier_id, origin_port, destination_port, departure_at, arrival_at, status
                   FROM sailings WHERE sailing_id = ANY(%s)""", (sailing_ids,))).fetchall()
        return {r[0]: SailingFacts(*r) for r in rows}

    async def booking_facts(self, booking_ids: list[str]) -> dict[str, BookingFacts]:
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(
                "SELECT booking_id, buyer_id, request_id, claim_code_hash FROM bookings WHERE booking_id = ANY(%s)",
                (booking_ids,))).fetchall()
        return {r[0]: BookingFacts(*r) for r in rows}

    async def record_reply(self, outcome: RecoveryReplyOutcome, exchange_address: str) -> None:
        """Audit the reply and every offer in it (valid or not), in one transaction."""
        req, carrier, reply = outcome.request, outcome.carrier, outcome.reply
        payload = json.loads(reply.json()) if reply is not None else {}
        payload["_exchange"] = {"error_code": outcome.error_code.value if outcome.error_code else None,
                                "detail": outcome.detail, "signature_verified": outcome.signature_verified}
        async with self.pool.connection() as conn, conn.transaction():
            await conn.execute(
                """INSERT INTO agent_messages (message_id, correlation_id, request_id, negotiation_id, in_reply_to,
                       direction, message_type, carrier_id, sender_address, recipient_address, status,
                       latency_ms, payload, ts)
                   VALUES (%s, %s, NULL, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (message_id) DO NOTHING""",
                (reply.message_id if reply is not None else f"local_{uuid4().hex}", req.correlation_id,
                 req.negotiation_id, req.message_id, "INBOUND" if reply is not None else "LOCAL",
                 reply.message_type if reply is not None else "EXCHANGE_ERROR", carrier.carrier_id,
                 carrier.agent_address if reply is not None else exchange_address, exchange_address,
                 _REPLY_STATUS[outcome.kind], outcome.latency_ms, Jsonb(payload), outcome.received_at))
            for check in outcome.checks:
                t = check.terms
                await conn.execute(
                    """INSERT INTO recovery_offers (offer_id, recovery_id, message_id, carrier_id, sailing_id,
                           container_type, capacity_units, total_price_minor, currency, departure_at, arrival_at,
                           expires_at, eligible_booking_ids, status, rejection_code, rejection_detail,
                           carrier_signature, received_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (offer_id) DO NOTHING""",
                    (t.offer_id, req.recovery_run_id, reply.message_id, carrier.carrier_id, t.sailing_id,
                     t.container_type, t.capacity_units, t.total_price_minor, t.currency, t.departure_at,
                     t.arrival_at, t.expires_at, list(t.eligible_booking_ids), "VALID" if check.ok else "REJECTED",
                     check.error_code.value if check.error_code else None, check.detail or None, reply.signature,
                     outcome.received_at))
                await _event(conn, "RECOVERY_OFFER_SUBMITTED" if check.ok else "RECOVERY_OFFER_REJECTED",
                             "recovery_offer", t.offer_id, correlation_id=req.correlation_id,
                             carrier_id=carrier.carrier_id, sailing_id=t.sailing_id,
                             corridor_id=f"{t.origin_port}-{t.destination_port}",
                             payload={"capacity_units": t.capacity_units, "total_price_minor": t.total_price_minor,
                                      "unit_price_minor": t.total_price_minor // t.capacity_units,
                                      "eligible": list(t.eligible_booking_ids),
                                      "error_code": check.error_code.value if check.error_code else None})
            if not outcome.checks:
                await _event(conn, "RECOVERY_OFFER_DECLINED" if outcome.kind == "reject" else "CARRIER_REPLY_ERROR",
                             "recovery_run", req.recovery_run_id, correlation_id=req.correlation_id,
                             carrier_id=carrier.carrier_id,
                             payload={"reason": getattr(reply, "reason_code", None) and reply.reason_code.value,
                                      "error_code": outcome.error_code.value if outcome.error_code else None,
                                      "latency_ms": outcome.latency_ms})

    async def save_snapshot(self, recovery_id: str, *, status: str, snapshot: dict, input_version: str | None,
                            event_payload: dict) -> None:
        async with self.pool.connection() as conn, conn.transaction():
            await conn.execute(
                """UPDATE recovery_runs SET status = %s, input_version = COALESCE(%s, input_version), proposal = %s,
                          updated_at = now() WHERE recovery_id = %s""",
                (status, input_version, Jsonb(_jsonable(snapshot)), recovery_id))
            await _event(conn, f"RECOVERY_{status}", "recovery_run", recovery_id, correlation_id=recovery_id,
                         payload=_jsonable(event_payload))

    async def load_run(self, recovery_id: str) -> tuple[str, dict | None, str | None] | None:
        async with self.pool.connection() as conn:
            row = await (await conn.execute(
                "SELECT status, proposal, detail FROM recovery_runs WHERE recovery_id = %s",
                (recovery_id,))).fetchone()
        return (row[0], row[1], row[2]) if row else None

    async def run_attempts(self, recovery_id: str) -> int:
        async with self.pool.connection() as conn:
            row = await (await conn.execute("SELECT attempts FROM recovery_runs WHERE recovery_id = %s",
                                            (recovery_id,))).fetchone()
        return row[0]

    async def runs_to_resume(self) -> list[str]:
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(
                """SELECT recovery_id FROM recovery_runs
                   WHERE status IN ('AWAITING_APPROVAL', 'RECONCILIATION_REQUIRED') ORDER BY created_at""")).fetchall()
        return [r[0] for r in rows]

    # ---- phase 7: legs, holds, confirmations, lineage ----------------------------
    async def create_legs(self, recovery_id: str, attempt: int, legs: list[Leg],
                          sources: dict[str, tuple[RecoveryReplyOutcome, RecoveryCapacityTerms]],
                          facts: dict[str, BookingFacts]) -> None:
        """Per leg: a per-booking `offers` row (the replacement booking's offer) and a PROPOSED leg."""
        async with self.pool.connection() as conn, conn.transaction():
            for leg in legs:
                reply, terms = sources[leg.recovery_offer_id]
                fact = facts[leg.booking_id]
                await conn.execute(
                    """INSERT INTO offers (offer_id, request_id, negotiation_id, correlation_id, message_id, carrier_id,
                           sailing_id, container_type, quantity, total_price_minor, currency, departure_at, arrival_at,
                           expires_at, round, status, carrier_signature, terms, source_kind, created_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, 'ACCEPTED', %s, %s, 'simulated', %s)
                       ON CONFLICT (offer_id) DO NOTHING""",
                    (leg.offer_id, fact.request_id, recovery_id, recovery_id,
                     f"{reply.reply.message_id}.{leg.booking_id}", leg.carrier_id, leg.sailing_id, terms.container_type,
                     leg.quantity, leg.quoted_price_minor, leg.currency, terms.departure_at, terms.arrival_at,
                     terms.expires_at, reply.reply.signature,
                     f"Recovery replacement from one-shot offer {terms.offer_id}; booked only once the carrier confirms.",
                     reply.reply.sent_at))
                await conn.execute(
                    """INSERT INTO recovery_legs (recovery_id, attempt, booking_id, recovery_offer_id, offer_id,
                           carrier_id, sailing_id, quantity, quoted_price_minor, currency, arrival_at, needs_approval,
                           status)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'PROPOSED')
                       ON CONFLICT DO NOTHING""",
                    (recovery_id, attempt, leg.booking_id, leg.recovery_offer_id, leg.offer_id, leg.carrier_id,
                     leg.sailing_id, leg.quantity, leg.quoted_price_minor, leg.currency, leg.arrival_at,
                     leg.needs_approval))

    async def update_leg(self, recovery_id: str, attempt: int, leg: Leg) -> None:
        async with self.pool.connection() as conn, conn.transaction():
            await conn.execute(
                """UPDATE recovery_legs SET status = %s, hold_id = %s, hold_expires_at = %s, approval_id = %s,
                          replacement_booking_id = %s, detail = %s, updated_at = now()
                   WHERE recovery_id = %s AND attempt = %s AND booking_id = %s AND recovery_offer_id = %s""",
                (leg.status, leg.hold_id, leg.hold_expires_at, leg.approval_id, leg.replacement_booking_id,
                 leg.detail, recovery_id, attempt, leg.booking_id, leg.recovery_offer_id))
            await _event(conn, f"RECOVERY_LEG_{leg.status}", "recovery_leg", f"{leg.booking_id}@{leg.sailing_id}",
                         correlation_id=recovery_id, carrier_id=leg.carrier_id, sailing_id=leg.sailing_id,
                         payload={"booking_id": leg.booking_id, "attempt": attempt, "hold_id": leg.hold_id,
                                  "replacement_booking_id": leg.replacement_booking_id, "detail": leg.detail})

    async def legs(self, recovery_id: str, attempt: int) -> list[Leg]:
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(
                """SELECT booking_id, recovery_offer_id, offer_id, carrier_id, sailing_id, quantity, quoted_price_minor,
                          currency, arrival_at, needs_approval, status, hold_id, hold_expires_at, approval_id,
                          replacement_booking_id, detail
                   FROM recovery_legs WHERE recovery_id = %s AND attempt = %s""", (recovery_id, attempt))).fetchall()
        return sorted((Leg(*r) for r in rows), key=lambda leg: leg.key)

    async def supersede(self, recovery_id: str, old_booking_id: str, replacement_ids: list[str]) -> None:
        """The original stays in Tiger as SUPERSEDED, linked to its confirmed replacement(s)."""
        async with self.pool.connection() as conn, conn.transaction():
            changed = await conn.execute(
                "UPDATE bookings SET status = 'SUPERSEDED' WHERE booking_id = %s AND status = 'DISRUPTED'",
                (old_booking_id,))
            await self._assignment(conn, recovery_id, old_booking_id, "RECOVERED", replacement_ids[0])
            # The original no longer claims its slots; on a withdrawn sailing they can never be sold again.
            slots = [r[0] for r in await (await conn.execute(
                "UPDATE booking_slots SET status = 'RELEASED' WHERE booking_id = %s AND status = 'COMMITTED' "
                "RETURNING slot_id", (old_booking_id,))).fetchall()]
            if slots:
                await conn.execute(
                    """UPDATE capacity_slots c SET status = 'WITHDRAWN', version = version + 1
                       FROM sailings s WHERE c.sailing_id = s.sailing_id AND s.status = 'WITHDRAWN'
                         AND c.slot_id = ANY(%s) AND c.status = 'COMMITTED'""", (slots,))
            if changed.rowcount:  # resumed runs re-check; record the change only once
                await _event(conn, "BOOKING_SUPERSEDED", "booking", old_booking_id, correlation_id=recovery_id,
                             payload={"replacement_booking_ids": replacement_ids})

    async def set_assignment(self, recovery_id: str, old_booking_id: str, status: str) -> None:
        async with self.pool.connection() as conn, conn.transaction():
            await self._assignment(conn, recovery_id, old_booking_id, status, None)

    @staticmethod
    async def _assignment(conn, recovery_id: str, old_booking_id: str, status: str, replacement: str | None) -> None:
        await conn.execute(
            """INSERT INTO recovery_assignments (recovery_id, old_booking_id, replacement_booking_id, status)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (recovery_id, old_booking_id)
               DO UPDATE SET status = EXCLUDED.status,
                             replacement_booking_id = COALESCE(EXCLUDED.replacement_booking_id,
                                                               recovery_assignments.replacement_booking_id)""",
            (recovery_id, old_booking_id, replacement, status))


# ---- snapshots: what the run decided and what it actually committed -------------------

def stranded_from_snapshot(snapshot: dict) -> list[DisruptedBooking]:
    return [DisruptedBooking(**{**b, "latest_arrival": datetime.fromisoformat(b["latest_arrival"])})
            for b in snapshot.get("stranded", [])]


def proposal_from_snapshot(snapshot: dict) -> RecoveryProposal:
    p = snapshot["proposal"]
    return RecoveryProposal(
        recovery_run_id=p["recovery_run_id"],
        assignments=tuple(RecoveryAssignment(**{**a, "arrival_at": datetime.fromisoformat(a["arrival_at"])})
                          for a in p["assignments"]),
        unrecovered_booking_ids=tuple(p["unrecovered_booking_ids"]),
        needs_approval_booking_ids=tuple(p["needs_approval_booking_ids"]),
        explanations=dict(p["explanations"]), verified_capacity=dict(p["verified_capacity"]),
        input_version=p["input_version"])


def outcome_from_snapshot(recovery_id: str, status: str, snapshot: dict | None, detail: str | None) -> RecoveryOutcome:
    """PROPOSED shows the plan; after phase 7 starts, only carrier-confirmed legs are assignments."""
    if not snapshot:
        return RecoveryOutcome(recovery_id, status, diagnostics={"detail": detail} if detail else {})
    proposal = proposal_from_snapshot(snapshot)
    diagnostics = {k: str(v) for k, v in snapshot.get("summary", {}).items()}
    diagnostics["needs_approval"] = ",".join(proposal.needs_approval_booking_ids)
    diagnostics["input_version"] = proposal.input_version
    for booking_id, text in proposal.explanations.items():
        diagnostics[f"explain:{booking_id}"] = text
    committed = snapshot.get("committed")
    if committed is None:
        return RecoveryOutcome(recovery_id, status, assignments=proposal.assignments,
                               unrecovered_booking_ids=proposal.unrecovered_booking_ids, diagnostics=diagnostics)
    assignments = tuple(RecoveryAssignment(**{**a, "arrival_at": datetime.fromisoformat(a["arrival_at"])})
                        for a in committed["assignments"])
    diagnostics.update({k: str(v) for k, v in committed.get("diagnostics", {}).items()})
    return RecoveryOutcome(recovery_id, status, assignments=assignments,
                           unrecovered_booking_ids=tuple(committed["unrecovered_booking_ids"]), diagnostics=diagnostics)


def legs_from_proposal(proposal: RecoveryProposal,
                       sources: dict[str, tuple[RecoveryReplyOutcome, RecoveryCapacityTerms]]) -> list[Leg]:
    needs = set(proposal.needs_approval_booking_ids)
    legs = []
    for a in proposal.assignments:
        _, terms = sources[a.offer_id]
        legs.append(Leg(booking_id=a.booking_id, recovery_offer_id=a.offer_id, offer_id=f"{a.offer_id}.{a.booking_id}",
                        carrier_id=a.carrier_id, sailing_id=a.sailing_id, quantity=a.quantity,
                        quoted_price_minor=a.quoted_price_minor, currency=terms.currency, arrival_at=a.arrival_at,
                        needs_approval=a.booking_id in needs))
    return sorted(legs, key=lambda leg: leg.key)


class AcpRecoverySeam:
    """RecoverySeam: withdrawal -> proposal (phase 6) -> holds, approval, confirmation (phase 7)."""

    def __init__(self, store: RecoveryStore, client: RecoveryClient, disruption: DisruptionSeam,
                 allocation: AllocationSeam, holds=None, approval: ApprovalSeam | None = None,
                 notifier: RecoveryNotifier | None = None, clock: Callable[[], datetime] = utcnow) -> None:
        self.store = store
        self.client = client
        self.disruption = disruption
        self.allocation = allocation
        self.holds = holds  # AcpHoldClient; None stops at PROPOSED (phase 6 behaviour)
        self.approval = approval
        self.notifier = notifier
        self.clock = clock

    # ---- RecoverySeam ------------------------------------------------------------
    async def recover(self, *, sailing_id: str, triggered_at: datetime) -> RecoveryOutcome:
        event_id = await self.store.withdrawal_event(sailing_id)
        if event_id is None:
            raise NoWithdrawal(f"{sailing_id} has no carrier-authorized withdrawal")
        recovery_id, created = await self.store.claim_run(event_id=event_id, sailing_id=sailing_id,
                                                          triggered_at=triggered_at)
        if not created:
            log_event("recovery_duplicate_trigger", recovery_id=recovery_id, sailing_id=sailing_id)
            return await self.outcome(recovery_id)
        try:
            stranded = await self.disruption.disrupted_bookings(sailing_id)
            if not stranded:
                proposal = RecoveryProposal(recovery_run_id=recovery_id)
                summary = {"stranded": 0, "detail": "no displaced bookings on this sailing"}
                await self.store.save_snapshot(recovery_id, status="COMPLETED", input_version=None,
                                               snapshot={"stranded": [], "proposal": asdict(proposal), "summary": summary},
                                               event_payload=summary)
                return await self.outcome(recovery_id)
            if self.notifier:
                await self.notifier.cancelled(withdrawal_event_id=event_id, sailing_id=sailing_id, stranded=stranded)
            proposal, sources = await self._propose(recovery_id, sailing_id, stranded, attempt=1)
            if self.holds is None:
                return await self.outcome(recovery_id)
            return await self._commit(recovery_id, sailing_id, stranded, proposal, sources, attempt=1)
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"[:500]
            await self.store.set_status(recovery_id, "FAILED", detail)
            log_event("recovery_failed", logging.ERROR, recovery_id=recovery_id, error_type=type(exc).__name__)
            return RecoveryOutcome(recovery_id, "FAILED", diagnostics={"detail": detail})

    async def outcome(self, recovery_run_id: str) -> RecoveryOutcome | None:
        run = await self.store.load_run(recovery_run_id)
        return None if run is None else outcome_from_snapshot(recovery_run_id, *run)

    async def resume(self, recovery_run_id: str) -> RecoveryOutcome | None:
        """Finish a run waiting on buyer approval or on an unanswered confirmation. Idempotent."""
        run = await self.store.load_run(recovery_run_id)
        if run is None or run[0] not in ("AWAITING_APPROVAL", "RECONCILIATION_REQUIRED") or not run[1]:
            return None if run is None else outcome_from_snapshot(recovery_run_id, *run)
        snapshot = run[1]
        attempt = await self.store.run_attempts(recovery_run_id)
        return await self._settle(recovery_run_id, stranded_from_snapshot(snapshot),
                                  proposal_from_snapshot(snapshot), attempt, snapshot)

    # ---- phase 6: one-shot offers -> proposal ---------------------------------------
    async def _propose(self, recovery_id: str, sailing_id: str, stranded: list[DisruptedBooking], *,
                       attempt: int) -> tuple[RecoveryProposal, dict]:
        await self.store.set_status(recovery_id, "OFFERS_REQUESTED")
        carriers = await self.store.active_carriers()
        exchange = self.client.identity.address

        async def on_sent(carrier: CarrierRef, message) -> None:
            await self.store.record_acp_message(message=message, carrier=carrier, direction="OUTBOUND", status="SENT")

        replies = await self.client.request_capacity(
            carriers, recovery_run_id=recovery_id, withdrawn_sailing_id=sailing_id,
            items=recovery_items(stranded), correlation_id=recovery_id, on_sent=on_sent)
        offered = sorted({t.sailing_id for r in replies if r.kind == "offers" for t in r.reply.offers})
        facts = await self.store.sailing_facts(offered)
        now = self.clock()
        for r in replies:
            r.checks = check_offers(r, facts, now)
        await asyncio.gather(*(self.store.record_reply(r, exchange) for r in replies))
        for r in replies:
            log_event("recovery_carrier_outcome", recovery_id=recovery_id, carrier_id=r.carrier.carrier_id,
                      kind=r.kind, error_code=r.error_code.value if r.error_code else None, attempt=attempt,
                      offers_valid=sum(c.ok for c in r.checks), offers_rejected=sum(not c.ok for c in r.checks),
                      latency_ms=r.latency_ms)

        sources = {c.terms.offer_id: (r, c.terms) for r in replies for c in r.checks if c.ok}
        offers = [o for r in replies for o in r.valid_offers()]
        proposal = await self.allocation.allocate(recovery_run_id=recovery_id, stranded=stranded, offers=offers)
        summary = {
            "stranded": len(stranded), "carriers_asked": len(carriers), "attempt": attempt,
            "offers_valid": len(offers), "offers_rejected": sum(not c.ok for r in replies for c in r.checks),
            "recovered": proposal.recovered_count, "unrecovered": len(proposal.unrecovered_booking_ids),
        }
        await self.store.save_snapshot(
            recovery_id, status="PROPOSED", input_version=proposal.input_version or None,
            snapshot={"stranded": [asdict(b) for b in stranded], "proposal": asdict(proposal), "summary": summary},
            event_payload={"input_version": proposal.input_version, **summary})
        log_event("recovery_proposed", recovery_id=recovery_id, sailing_id=sailing_id, **summary)
        return proposal, sources

    # ---- phase 7: holds -> approval -> confirmation ---------------------------------
    async def _commit(self, recovery_id: str, sailing_id: str, stranded: list[DisruptedBooking],
                      proposal: RecoveryProposal, sources: dict, *, attempt: int) -> RecoveryOutcome:
        facts = await self.store.booking_facts([b.booking_id for b in stranded])
        legs = legs_from_proposal(proposal, sources)
        await self.store.create_legs(recovery_id, attempt, legs, sources, facts)
        await self.store.set_status(recovery_id, "HOLDING")
        carriers = {c.carrier_id: c for c in await self.store.active_carriers()}

        for leg in legs:  # fixed order (carrier, sailing, booking): two runs never hold in opposite orders
            fact = facts[leg.booking_id]
            result = await self.holds.request_hold(
                carriers[leg.carrier_id], offer_id=leg.offer_id, request_id=fact.request_id, buyer_id=fact.buyer_id,
                quantity=leg.quantity, idempotency_key=f"rh:{recovery_id}:{attempt}:{leg.booking_id}:{leg.recovery_offer_id}",
                ttl_seconds=HOLD_TTL_S, negotiation_id=recovery_id, correlation_id=recovery_id,
                recovery_offer_id=leg.recovery_offer_id)
            if result.ok:
                leg.status, leg.hold_id, leg.hold_expires_at = "HELD", result.hold_id, result.expires_at
            else:
                leg.status, leg.detail = "FAILED", f"hold refused: {result.code}"
            await self.store.update_leg(recovery_id, attempt, leg)

        failed = {leg.booking_id for leg in legs if leg.status == "FAILED"}
        if failed and attempt < MAX_ATTEMPTS:
            # spec 07: release the partial holds and re-solve once from fresh carrier offers.
            await self._release(recovery_id, attempt, [leg for leg in legs if leg.status == "HELD"], carriers,
                                reason="released for re-solve after a failed hold")
            log_event("recovery_resolve", recovery_id=recovery_id, failed=sorted(failed))
            await self.store.set_attempts(recovery_id, attempt + 1)
            stranded = await self.disruption.disrupted_bookings(sailing_id)
            proposal, sources = await self._propose(recovery_id, sailing_id, stranded, attempt=attempt + 1)
            return await self._commit(recovery_id, sailing_id, stranded, proposal, sources, attempt=attempt + 1)
        # Final attempt: a booking with any failed leg is not recovered; free its other legs.
        await self._release(recovery_id, attempt,
                            [leg for leg in legs if leg.booking_id in failed and leg.status == "HELD"], carriers,
                            reason="another part of this booking could not be held")

        if self.approval is not None:
            for booking_id in sorted({leg.booking_id for leg in legs if leg.needs_approval and leg.status == "HELD"}):
                approval_id = await self.approval.request_approval(
                    proposal=proposal, booking_id=booking_id, buyer_id=facts[booking_id].buyer_id)
                for leg in legs:
                    if leg.booking_id == booking_id:
                        leg.approval_id = approval_id
                        await self.store.update_leg(recovery_id, attempt, leg)
        if self.notifier:  # approval requests for held bookings only; nothing is booked yet
            held_approvals = tuple(sorted({leg.booking_id for leg in legs if leg.needs_approval and leg.status == "HELD"}))
            await self.notifier.update(recovery_run_id=recovery_id, stranded=stranded, outcome=None,
                                       proposal=replace(proposal, needs_approval_booking_ids=held_approvals))
        snapshot = (await self.store.load_run(recovery_id))[1]
        return await self._settle(recovery_id, stranded, proposal, attempt, snapshot)

    async def _settle(self, recovery_id: str, stranded: list[DisruptedBooking], proposal: RecoveryProposal,
                      attempt: int, snapshot: dict) -> RecoveryOutcome:
        """Confirm what may be confirmed, release what was declined or expired, then report truthfully."""
        legs = await self.store.legs(recovery_id, attempt)
        carriers = {c.carrier_id: c for c in await self.store.active_carriers()}
        facts = await self.store.booking_facts([b.booking_id for b in stranded])
        now = self.clock()
        by_booking: dict[str, list[Leg]] = {}
        for leg in legs:
            by_booking.setdefault(leg.booking_id, []).append(leg)

        waiting, unknown, declined = [], [], []
        for booking_id, booking_legs in sorted(by_booking.items()):
            if any(leg.status in ("FAILED", "RELEASED") for leg in booking_legs):
                continue
            if all(leg.status == "CONFIRMED" for leg in booking_legs):
                continue
            if any(leg.needs_approval for leg in booking_legs):
                decision = await self._decision(booking_legs, now)
                if decision == "PENDING":
                    waiting.append(booking_id)
                    continue
                if decision != "APPROVED":
                    declined.append(booking_id)
                    await self._release(recovery_id, attempt, [leg for leg in booking_legs if leg.status == "HELD"],
                                        carriers, reason=f"buyer approval {decision.lower()}")
                    continue
            if not await self._confirm(recovery_id, attempt, booking_legs, facts[booking_id], carriers):
                unknown.append(booking_id)

        legs = await self.store.legs(recovery_id, attempt)
        confirmed = [leg for leg in legs if leg.status == "CONFIRMED"]
        recovered = {b for b, ls in by_booking.items() if all(leg.status == "CONFIRMED" for leg in ls)}
        for booking_id in sorted(recovered):
            await self.store.supersede(recovery_id, booking_id,
                                       [leg.replacement_booking_id for leg in legs
                                        if leg.booking_id == booking_id and leg.replacement_booking_id])
        pending = set(waiting) | set(unknown)
        unrecovered = [b.booking_id for b in stranded if b.booking_id not in recovered | pending]
        for booking_id in unrecovered:
            await self.store.set_assignment(recovery_id, booking_id,
                                            "DECLINED" if booking_id in declined else "UNRECOVERED")
        for booking_id in waiting:
            await self.store.set_assignment(recovery_id, booking_id, "AWAITING_APPROVAL")

        status = ("RECONCILIATION_REQUIRED" if unknown else "AWAITING_APPROVAL" if waiting
                  else "COMPLETED" if not unrecovered else "PARTIAL")
        assignments = tuple(RecoveryAssignment(booking_id=leg.booking_id, offer_id=leg.recovery_offer_id,
                                               sailing_id=leg.sailing_id, carrier_id=leg.carrier_id,
                                               quantity=leg.quantity, quoted_price_minor=leg.quoted_price_minor,
                                               arrival_at=leg.arrival_at)
                            for leg in confirmed if leg.booking_id in recovered)
        diagnostics = {f"replacement:{leg.booking_id}": leg.replacement_booking_id
                       for leg in confirmed if leg.booking_id in recovered}
        diagnostics.update({"awaiting_approval": ",".join(waiting), "reconciliation": ",".join(unknown),
                            "declined": ",".join(declined), "attempts": attempt})
        for leg in legs:
            if leg.booking_id in unrecovered and leg.detail:
                diagnostics.setdefault(leg.booking_id, f"Replacement not secured: {leg.detail}.")
        committed = {"assignments": [asdict(a) for a in assignments], "unrecovered_booking_ids": unrecovered,
                     "diagnostics": diagnostics}
        await self.store.save_snapshot(
            recovery_id, status=status, input_version=None, snapshot={**snapshot, "committed": committed},
            event_payload={"recovered": len(recovered), "unrecovered": len(unrecovered), "waiting": len(waiting),
                           "reconciliation": len(unknown), "attempt": attempt})
        outcome = outcome_from_snapshot(recovery_id, status, {**snapshot, "committed": _jsonable(committed)}, None)
        if self.notifier:  # REBOOKED for carrier-confirmed bookings, NOT_RECOVERED for the rest; never twice
            await self.notifier.update(recovery_run_id=recovery_id, stranded=stranded, proposal=proposal,
                                       outcome=outcome)
        log_event("recovery_settled", recovery_id=recovery_id, status=status, recovered=len(recovered),
                  unrecovered=len(unrecovered), waiting=len(waiting), reconciliation=len(unknown))
        return outcome

    async def _decision(self, booking_legs: list[Leg], now: datetime) -> str:
        """APPROVED | DECLINED | EXPIRED | PENDING. Nothing is confirmed without APPROVED, and a hold that
        lapses while waiting ends the wait."""
        approval_id = next((leg.approval_id for leg in booking_legs if leg.approval_id), None)
        status = await self.approval.approval_status(approval_id) if self.approval and approval_id else "PENDING"
        if status == "PENDING" and any(leg.hold_expires_at and leg.hold_expires_at <= now for leg in booking_legs):
            return "EXPIRED"
        return status

    async def _confirm(self, recovery_id: str, attempt: int, booking_legs: list[Leg], fact: BookingFacts,
                       carriers: dict[str, CarrierRef]) -> bool:
        """Confirm every held leg of one booking. False if any result is unknown (retry on resume)."""
        settled = True
        for leg in booking_legs:
            if leg.status != "HELD":
                continue
            result = await self.holds.confirm_booking(
                carriers[leg.carrier_id], hold_id=leg.hold_id, payment_reference=f"recovery:{recovery_id}",
                idempotency_key=f"rc:{recovery_id}:{attempt}:{leg.booking_id}:{leg.recovery_offer_id}",
                intent_id=recovery_id, claim_code_hash=fact.claim_code_hash or "", request_id=fact.request_id,
                negotiation_id=recovery_id, correlation_id=recovery_id, replaces_booking_id=leg.booking_id)
            if result.ok:
                leg.status, leg.replacement_booking_id, leg.detail = "CONFIRMED", result.booking_id, None
            elif result.code in UNKNOWN_RESULT:
                leg.detail = f"confirmation unanswered: {result.code}"
                settled = False
            else:
                leg.status, leg.detail = "FAILED", f"carrier refused confirmation: {result.code}"
            await self.store.update_leg(recovery_id, attempt, leg)
        if any(leg.status == "FAILED" for leg in booking_legs):
            await self._release(recovery_id, attempt, [leg for leg in booking_legs if leg.status == "HELD"], carriers,
                                reason="another part of this booking could not be confirmed")
        return settled

    async def _release(self, recovery_id: str, attempt: int, legs: list[Leg], carriers: dict[str, CarrierRef],
                       *, reason: str) -> None:
        for leg in legs:
            result = await self.holds.release_hold(carriers[leg.carrier_id], hold_id=leg.hold_id,
                                                   negotiation_id=recovery_id, correlation_id=recovery_id)
            # If the release is unanswered the hold still lapses at hold_expires_at; never leave it un-noted.
            leg.status = "RELEASED"
            leg.detail = reason if result.ok else f"{reason}; release unanswered ({result.code}), hold lapses on expiry"
            await self.store.update_leg(recovery_id, attempt, leg)


class AsiRecoveryNotifier:
    """Adapter to Tisya's buyer messages (asi/recovery_notify.py). Those calls never raise."""

    def __init__(self, pool: AsyncConnectionPool, agent_uri: str) -> None:
        self.pool = pool
        self.agent_uri = agent_uri

    async def cancelled(self, *, withdrawal_event_id, sailing_id, stranded) -> None:
        from freight_exchange.asi.recovery_notify import notify_cancellation
        await notify_cancellation(self.pool, self.agent_uri, withdrawal_event_id, sailing_id, stranded)

    async def update(self, *, recovery_run_id, stranded, proposal, outcome) -> None:
        from freight_exchange.asi.recovery_notify import notify_recovery
        await notify_recovery(self.pool, self.agent_uri, recovery_run_id, stranded, proposal, outcome)


def real_approval_seam(pool: AsyncConnectionPool) -> ApprovalSeam | None:
    """Tisya's Tiger-backed ApprovalSeam (asi/approvals.py). None only if it is unavailable, which keeps
    every over-allowance booking waiting until its hold lapses (never confirmed without approval)."""
    try:
        from freight_exchange.asi.approvals import TigerApprovalSeam, TigerApprovalStore
    except ImportError:
        return None
    return TigerApprovalSeam(TigerApprovalStore(pool))


def _notifier(pool: AsyncConnectionPool, agent_uri: str | None):
    """Gateway cards for buyers who chat through the gateway; legacy text for older chats."""
    if not agent_uri:
        return None
    from freight_exchange.gateway_notify import GatewayRecoveryNotifier
    return GatewayRecoveryNotifier(pool, legacy=AsiRecoveryNotifier(pool, agent_uri))


async def build_recovery_seam(identity, pool: AsyncConnectionPool, *, agent_uri: str | None = None,
                              commit: bool = True) -> AcpRecoverySeam:
    """Production wiring: Tisya's Tiger-backed DisruptionSeam, OR-Tools AllocationSeam and ApprovalSeam,
    ACP holds/confirmations, and buyer notifications when `agent_uri` is given."""
    from freight_allocation import SolverAllocationSeam, TigerCapacityReader
    from freight_disruption import TigerDisruptionSeam

    from freight_exchange.hold_client import AcpHoldClient

    store = RecoveryStore(pool)

    async def audit(**kw) -> None:
        await store.record_acp_message(**kw)

    return AcpRecoverySeam(
        store=store,
        client=RecoveryClient(identity),
        disruption=TigerDisruptionSeam(pool),
        allocation=SolverAllocationSeam(TigerCapacityReader(pool)),
        holds=AcpHoldClient(identity, audit=audit) if commit else None,
        approval=real_approval_seam(pool),
        notifier=_notifier(pool, agent_uri),
    )

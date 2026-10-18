"""Recovery orchestration, phase 6 half of RecoverySeam (Parth): withdrawal -> proposal.

    claim run (one per withdrawal) -> stranded bookings (DisruptionSeam, Tisya)
    -> one-shot REQUEST_RECOVERY_CAPACITY to every carrier (recovery_client.py)
    -> message checks -> AllocationSeam.allocate (Tisya: claim checks + OR-Tools)
    -> persist offers, input version and proposal -> RecoveryOutcome(status="PROPOSED")

A proposal is never a booking. Holds, approval and confirmation are phase 7 and start from
a PROPOSED run. Duplicate triggers for the same withdrawal return the existing run, because
recovery_runs.withdrawal_event_id is UNIQUE.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Callable
from uuid import uuid4

from freight_seams import (
    AllocationSeam, DisruptedBooking, DisruptionSeam, RecoveryAssignment, RecoveryOutcome, RecoveryProposal,
)
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from freight_exchange.carrier_client import CarrierRef
from freight_exchange.observability import log_event
from freight_exchange.recovery_client import (
    RecoveryClient, RecoveryReplyOutcome, SailingFacts, check_offers, recovery_items,
)
from freight_exchange.repository import Repository, _event


class NoWithdrawal(LookupError):
    """Recovery starts only from a carrier-authorized SAILING_WITHDRAWN event, never a risk alert."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _jsonable(value) -> dict:
    return json.loads(json.dumps(value, default=lambda v: v.isoformat() if isinstance(v, datetime) else str(v)))


_REPLY_STATUS = {"offers": "ACCEPTED_FOR_REVIEW", "reject": "DECLINED",
                 "carrier_error": "CARRIER_ERROR", "exchange_error": "REJECTED_BY_EXCHANGE"}


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

    async def sailing_facts(self, sailing_ids: list[str]) -> dict[str, SailingFacts]:
        if not sailing_ids:
            return {}
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(
                """SELECT sailing_id, carrier_id, origin_port, destination_port, departure_at, arrival_at, status
                   FROM sailings WHERE sailing_id = ANY(%s)""", (sailing_ids,))).fetchall()
        return {r[0]: SailingFacts(*r) for r in rows}

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

    async def save_proposal(self, recovery_id: str, *, status: str, stranded: list[DisruptedBooking],
                            proposal: RecoveryProposal, summary: dict) -> None:
        snapshot = _jsonable({"stranded": [asdict(b) for b in stranded], "proposal": asdict(proposal),
                              "summary": summary})
        async with self.pool.connection() as conn, conn.transaction():
            await conn.execute(
                """UPDATE recovery_runs SET status = %s, input_version = %s, proposal = %s, updated_at = now()
                   WHERE recovery_id = %s""", (status, proposal.input_version or None, Jsonb(snapshot), recovery_id))
            await _event(conn, f"RECOVERY_{status}", "recovery_run", recovery_id, correlation_id=recovery_id,
                         payload={"input_version": proposal.input_version, **summary})

    async def load_run(self, recovery_id: str) -> tuple[str, dict | None, str | None] | None:
        async with self.pool.connection() as conn:
            row = await (await conn.execute(
                "SELECT status, proposal, detail FROM recovery_runs WHERE recovery_id = %s",
                (recovery_id,))).fetchone()
        return (row[0], row[1], row[2]) if row else None


def outcome_from_snapshot(recovery_id: str, status: str, snapshot: dict | None, detail: str | None) -> RecoveryOutcome:
    if not snapshot:
        return RecoveryOutcome(recovery_id, status, diagnostics={"detail": detail} if detail else {})
    proposal = snapshot["proposal"]
    assignments = tuple(
        RecoveryAssignment(**{**a, "arrival_at": datetime.fromisoformat(a["arrival_at"])})
        for a in proposal["assignments"])
    diagnostics = {k: str(v) for k, v in snapshot.get("summary", {}).items()}
    diagnostics["needs_approval"] = ",".join(proposal["needs_approval_booking_ids"])
    diagnostics["input_version"] = proposal["input_version"]
    for booking_id, text in proposal["explanations"].items():
        diagnostics[f"explain:{booking_id}"] = text
    return RecoveryOutcome(recovery_id, status, assignments=assignments,
                           unrecovered_booking_ids=tuple(proposal["unrecovered_booking_ids"]), diagnostics=diagnostics)


class AcpRecoverySeam:
    """RecoverySeam, phase 6: produces and persists a validated proposal from real ACP offers.

    `recover` ends at PROPOSED. Phase 7 extends the same run through holds, approval and
    confirmation; until then nothing here holds or books capacity.
    """

    def __init__(self, store: RecoveryStore, client: RecoveryClient, disruption: DisruptionSeam,
                 allocation: AllocationSeam, clock: Callable[[], datetime] = utcnow) -> None:
        self.store = store
        self.client = client
        self.disruption = disruption
        self.allocation = allocation
        self.clock = clock

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
            return await self._propose(recovery_id, sailing_id)
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"[:500]
            await self.store.set_status(recovery_id, "FAILED", detail)
            log_event("recovery_failed", logging.ERROR, recovery_id=recovery_id, error_type=type(exc).__name__)
            return RecoveryOutcome(recovery_id, "FAILED", diagnostics={"detail": detail})

    async def outcome(self, recovery_run_id: str) -> RecoveryOutcome | None:
        run = await self.store.load_run(recovery_run_id)
        return None if run is None else outcome_from_snapshot(recovery_run_id, *run)

    async def _propose(self, recovery_id: str, sailing_id: str) -> RecoveryOutcome:
        stranded = await self.disruption.disrupted_bookings(sailing_id)
        if not stranded:
            proposal = RecoveryProposal(recovery_run_id=recovery_id)
            summary = {"stranded": 0, "detail": "no displaced bookings on this sailing"}
            await self.store.save_proposal(recovery_id, status="COMPLETED", stranded=[], proposal=proposal,
                                           summary=summary)
            return outcome_from_snapshot(recovery_id, "COMPLETED", _jsonable(
                {"proposal": asdict(proposal), "summary": summary}), None)

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
                      kind=r.kind, error_code=r.error_code.value if r.error_code else None,
                      offers_valid=sum(c.ok for c in r.checks), offers_rejected=sum(not c.ok for c in r.checks),
                      latency_ms=r.latency_ms)

        offers = [o for r in replies for o in r.valid_offers()]
        proposal = await self.allocation.allocate(recovery_run_id=recovery_id, stranded=stranded, offers=offers)
        summary = {
            "stranded": len(stranded), "carriers_asked": len(carriers),
            "offers_valid": len(offers), "offers_rejected": sum(not c.ok for r in replies for c in r.checks),
            "recovered": proposal.recovered_count, "unrecovered": len(proposal.unrecovered_booking_ids),
        }
        await self.store.save_proposal(recovery_id, status="PROPOSED", stranded=stranded, proposal=proposal,
                                       summary=summary)
        log_event("recovery_proposed", recovery_id=recovery_id, sailing_id=sailing_id, **summary)
        return await self.outcome(recovery_id)


async def build_recovery_seam(identity, pool: AsyncConnectionPool) -> AcpRecoverySeam:
    """Production wiring: Tisya's Tiger-backed DisruptionSeam and OR-Tools AllocationSeam."""
    from freight_allocation import SolverAllocationSeam, TigerCapacityReader
    from freight_disruption import TigerDisruptionSeam

    return AcpRecoverySeam(
        store=RecoveryStore(pool),
        client=RecoveryClient(identity),
        disruption=TigerDisruptionSeam(pool),
        allocation=SolverAllocationSeam(TigerCapacityReader(pool)),
    )

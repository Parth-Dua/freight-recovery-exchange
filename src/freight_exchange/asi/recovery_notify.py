"""Tell each stranded buyer what is happening, from the exchange's recovery loop.

Two calls for Parth's recovery code, both safe to retry:

    attempts = await notify_cancellation(pool, agent_uri, withdrawal_event_id, sailing_id, stranded)
    ... ask carriers, allocate, maybe wait for approvals, confirm ...
    attempts = await notify_recovery(pool, agent_uri, recovery_run_id, stranded, proposal, outcome)

`stranded` is TigerDisruptionSeam.disrupted_bookings(sailing_id). `proposal` is
AllocationSeam.allocate(...). `outcome` is the CONFIRMED RecoveryOutcome, or None before
anything is confirmed. Which message each buyer gets:

  outcome given:   confirmed assignment -> REBOOKED; listed unrecovered -> NOT_RECOVERED
  proposal only:   needs approval -> NEEDS_APPROVAL; unrecovered -> NOT_RECOVERED;
                   anything else waits silently for confirmation (a plan is not a booking)

Every buyer is handled on their own: one failure never stops the others, and nothing
here raises. Each sent message is recorded as a BUYER_NOTIFIED market event keyed by
(booking, message kind, withdrawal or recovery run), and a retry skips anything already
sent. Recovery runs are claimed through recovery_runs (UNIQUE withdrawal_event_id), so
two loops never notify the same withdrawal at once.

Runs in the exchange process only: the agent's acknowledgements and buyer replies come
back to the exchange's mailbox, so only the exchange's logs can account for them.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from freight_exchange.asi import messages as m
from freight_exchange.asi.notify import Notified, buyer_address_for_booking, notify_buyer
from freight_exchange.observability import log_event
from freight_seams import DisruptedBooking, RecoveryOutcome, RecoveryProposal

SENT = "SENT"
SKIPPED_UNREACHABLE = "SKIPPED_UNREACHABLE"
SKIPPED_ALREADY_SENT = "SKIPPED_ALREADY_SENT"
FAILED = "FAILED"


@dataclass(frozen=True)
class NotifyAttempt:
    booking_id: str
    kind: str
    status: str
    detail: str = ""


CONTEXT_SQL = """
SELECT s.sailing_id, c.display_name, o.name, d.name, s.departure_at,
       (SELECT reason FROM disruption_events
        WHERE sailing_id = s.sailing_id AND event_type = 'SAILING_WITHDRAWN'
        ORDER BY occurred_at DESC LIMIT 1)
FROM sailings s
JOIN carriers c ON c.carrier_id = s.carrier_id
JOIN ports o ON o.port_code = s.origin_port
JOIN ports d ON d.port_code = s.destination_port
WHERE s.sailing_id = %s
"""

ALREADY_SENT_SQL = """
SELECT 1 FROM market_events
WHERE event_type = 'BUYER_NOTIFIED' AND entity_type = 'booking' AND entity_id = %s
  AND payload->>'dedupe_key' = %s
LIMIT 1
"""

RECORD_SQL = """
INSERT INTO market_events (ts, event_id, event_type, entity_type, entity_id, correlation_id,
                           carrier_id, sailing_id, corridor_id, source_kind, payload)
VALUES (now(), %s, 'BUYER_NOTIFIED', 'booking', %s, %s, %s, %s, %s, 'simulated', %s)
"""


async def cancellation_context(pool: AsyncConnectionPool, sailing_id: str) -> m.CancellationContext | None:
    async with pool.connection() as conn:
        row = await (await conn.execute(CONTEXT_SQL, (sailing_id,))).fetchone()
    return m.CancellationContext(*row) if row else None


async def carrier_names(pool: AsyncConnectionPool) -> dict[str, str]:
    async with pool.connection() as conn:
        rows = await (await conn.execute("SELECT carrier_id, display_name FROM carriers")).fetchall()
    return {row[0]: row[1] for row in rows}


async def _already_sent(pool: AsyncConnectionPool, booking_id: str, key: str) -> bool:
    async with pool.connection() as conn:
        return await (await conn.execute(ALREADY_SENT_SQL, (booking_id, key))).fetchone() is not None


async def _record_sent(
    pool: AsyncConnectionPool, booking: DisruptedBooking, kind: str, key: str, sent: Notified
) -> None:
    payload = {
        "kind": kind,
        "dedupe_key": key,
        "msg_id": str(sent.msg_id),
        "session_id": str(sent.session_id) if sent.session_id else None,
    }
    async with pool.connection() as conn, conn.transaction():
        await conn.execute(
            RECORD_SQL,
            (uuid4(), booking.booking_id, key, booking.carrier_id, booking.sailing_id,
             f"{booking.origin_port}-{booking.destination_port}", Jsonb(payload)),
        )


async def _deliver(
    pool: AsyncConnectionPool, agent_uri: str, booking: DisruptedBooking, kind: str, run_key: str, text: str
) -> NotifyAttempt:
    key = f"{kind}:{run_key}"
    try:
        if await _already_sent(pool, booking.booking_id, key):
            return NotifyAttempt(booking.booking_id, kind, SKIPPED_ALREADY_SENT)
        async with pool.connection() as conn:
            address = await buyer_address_for_booking(conn, booking.booking_id)
        if address is None:
            return NotifyAttempt(booking.booking_id, kind, SKIPPED_UNREACHABLE,
                                 "buyer is not an ASI:One address or has no stored conversation")
        sent = await notify_buyer(agent_uri, address, text)
        await _record_sent(pool, booking, kind, key, sent)
        return NotifyAttempt(booking.booking_id, kind, SENT, str(sent.msg_id))
    except Exception as error:  # one buyer's failure must never stop the rest
        return NotifyAttempt(booking.booking_id, kind, FAILED, type(error).__name__)


def _log(attempt: NotifyAttempt, run_key: str) -> None:
    # Never log message text (observability.py rule); ids and outcome only.
    log_event("buyer_notification", booking_id=attempt.booking_id, kind=attempt.kind,
              status=attempt.status, run=run_key, detail=attempt.detail)


async def notify_cancellation(
    pool: AsyncConnectionPool,
    agent_uri: str,
    withdrawal_event_id: str,
    sailing_id: str,
    stranded: list[DisruptedBooking],
) -> list[NotifyAttempt]:
    context = await cancellation_context(pool, sailing_id)
    attempts = []
    for booking in sorted(stranded, key=lambda b: b.booking_id):
        if context is None:
            attempt = NotifyAttempt(booking.booking_id, m.CANCELLED, FAILED, f"unknown sailing {sailing_id}")
        else:
            attempt = await _deliver(pool, agent_uri, booking, m.CANCELLED, withdrawal_event_id,
                                     m.cancellation_notice(booking, context))
        _log(attempt, withdrawal_event_id)
        attempts.append(attempt)
    return attempts


def plan_recovery_messages(
    stranded: list[DisruptedBooking],
    proposal: RecoveryProposal,
    outcome: RecoveryOutcome | None,
    names: dict[str, str] | None = None,
) -> list[tuple[DisruptedBooking, str, str]]:
    """(booking, kind, text) for every buyer who should hear something now. Pure."""
    planned = []
    for booking in sorted(stranded, key=lambda b: b.booking_id):
        bid = booking.booking_id
        if outcome is not None:
            confirmed = [a for a in outcome.assignments if a.booking_id == bid]
            if confirmed:
                planned.append((booking, m.REBOOKED, m.rebooked_message(booking, confirmed, names)))
            elif bid in outcome.unrecovered_booking_ids:
                reason = outcome.diagnostics.get(bid) or proposal.explanations.get(bid)
                planned.append((booking, m.NOT_RECOVERED, m.not_recovered_message(booking, reason)))
            continue
        if bid in proposal.needs_approval_booking_ids:
            proposed = [a for a in proposal.assignments if a.booking_id == bid]
            planned.append((booking, m.NEEDS_APPROVAL, m.needs_approval_message(booking, proposed, names)))
        elif bid in proposal.unrecovered_booking_ids:
            planned.append((booking, m.NOT_RECOVERED,
                            m.not_recovered_message(booking, proposal.explanations.get(bid))))
    return planned


async def notify_recovery(
    pool: AsyncConnectionPool,
    agent_uri: str,
    recovery_run_id: str,
    stranded: list[DisruptedBooking],
    proposal: RecoveryProposal,
    outcome: RecoveryOutcome | None = None,
) -> list[NotifyAttempt]:
    try:
        names = await carrier_names(pool)
    except Exception:
        names = {}
    attempts = []
    for booking, kind, text in plan_recovery_messages(stranded, proposal, outcome, names):
        attempt = await _deliver(pool, agent_uri, booking, kind, recovery_run_id, text)
        _log(attempt, recovery_run_id)
        attempts.append(attempt)
    return attempts

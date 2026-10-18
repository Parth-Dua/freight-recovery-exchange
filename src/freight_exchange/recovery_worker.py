"""Recovery trigger (spec 07 WP1): picks up every carrier-authorized withdrawal and finishes waiting runs.

Each tick: every withdrawal with no recovery run yet (DisruptionSeam.pending_withdrawals) -> recover,
then every run waiting on a buyer or an unanswered confirmation -> resume. Both are idempotent, so a
crash or a second worker can never recover one withdrawal twice (recovery_runs is UNIQUE per
withdrawal). The worker signs carrier messages and buyer notifications with the Exchange identity but
never reads the Agentverse mailbox, so it runs beside the Exchange, not instead of it.
"""

from __future__ import annotations

import asyncio
import logging

from freight_seams import RecoveryOutcome

from freight_exchange.observability import log_event
from freight_exchange.recovery import AcpRecoverySeam, utcnow


async def tick(seam: AcpRecoverySeam) -> list[RecoveryOutcome]:
    outcomes = []
    for pending in await seam.disruption.pending_withdrawals():
        outcomes.append(await seam.recover(sailing_id=pending.sailing_id, triggered_at=utcnow()))
    for recovery_id in await seam.store.runs_to_resume():
        outcome = await seam.resume(recovery_id)
        if outcome is not None:
            outcomes.append(outcome)
    return outcomes


async def run_forever(seam: AcpRecoverySeam, interval_s: float = 10.0) -> None:
    while True:
        try:
            for outcome in await tick(seam):
                log_event("recovery_worker_outcome", recovery_id=outcome.recovery_run_id, status=outcome.status,
                          recovered=len({a.booking_id for a in outcome.assignments}),
                          unrecovered=len(outcome.unrecovered_booking_ids))
        except Exception as exc:  # keep polling; each run records its own failure
            log_event("recovery_worker_error", logging.ERROR, error_type=type(exc).__name__)
        await asyncio.sleep(interval_s)

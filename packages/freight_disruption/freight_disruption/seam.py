"""The real DisruptionSeam, backed by Tiger. Owner: Tisya."""

from __future__ import annotations

from uuid import uuid4

from psycopg_pool import AsyncConnectionPool

from freight_seams import DisruptedBooking, WithdrawalSummary
from freight_disruption.stranded import PendingWithdrawal, pending_withdrawals, stranded_bookings
from freight_disruption.withdrawal import WithdrawSailing, withdraw_sailing


class TigerDisruptionSeam:
    """Implements freight_seams.DisruptionSeam.

    `withdraw` is the same transaction the carrier console runs. The console passes the
    full advisory text itself; callers of this seam only have the risk event id, so the
    reason records that.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def withdraw(
        self, *, sailing_id: str, carrier_id: str, risk_event_id: str, actor_id: str
    ) -> WithdrawalSummary:
        result = await withdraw_sailing(
            self._pool,
            WithdrawSailing(
                sailing_id=sailing_id,
                carrier_id=carrier_id,
                reason_risk_event_id=risk_event_id,
                actor_id=actor_id,
                authorization_ref=f"seam:{actor_id}",
                idempotency_key=uuid4().hex,
                reason_text=f"Withdrawn via DisruptionSeam for risk event {risk_event_id}",
            ),
        )
        return WithdrawalSummary(
            event_id=result.event_id,
            sailing_id=result.sailing_id,
            carrier_id=result.carrier_id,
            already_withdrawn=result.already_withdrawn,
            disrupted_booking_ids=result.disrupted_booking_ids,
            untouched_booking_count=result.untouched_booking_count,
        )

    async def disrupted_bookings(self, sailing_id: str) -> list[DisruptedBooking]:
        return await stranded_bookings(self._pool, sailing_id)

    async def pending_withdrawals(self) -> list[PendingWithdrawal]:
        """Not part of the Protocol: the work queue for whichever recovery trigger we pick."""
        return await pending_withdrawals(self._pool)

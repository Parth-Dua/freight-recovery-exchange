"""Who is stranded by a withdrawn sailing, in the shape the recovery solver consumes.

Reads only. Bookings stay DISRUPTED until Parth's phase 7 confirms a replacement and
supersedes them, so calling this again after a partial recovery returns only the
bookings still waiting, which is exactly what a re-solve needs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from psycopg_pool import AsyncConnectionPool

from freight_seams import DisruptedBooking

# A request with no stated deadline accepts any arrival. DisruptedBooking needs a real,
# timezone-aware datetime, so "no deadline" is a date far enough away never to bind.
NO_DEADLINE = datetime(2100, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class StrandedRow:
    """One row of the stranded-bookings query, before conversion."""

    booking_id: str
    buyer_id: str
    carrier_id: str
    sailing_id: str
    container_type: str
    quantity: int
    origin_port: str
    destination_port: str
    latest_arrival: datetime | None
    allow_split: bool
    total_price_minor: int
    currency: str
    max_additional_cost_minor: int
    must_deliver: bool = False
    priority: str = "balanced"


@dataclass(frozen=True)
class PendingWithdrawal:
    """A withdrawal that has no recovery run yet: the recovery trigger's work queue."""

    event_id: str
    carrier_id: str
    sailing_id: str
    occurred_at: datetime


def to_disrupted_booking(row: StrandedRow) -> DisruptedBooking:
    return DisruptedBooking(
        booking_id=row.booking_id,
        buyer_id=row.buyer_id,
        carrier_id=row.carrier_id,
        sailing_id=row.sailing_id,
        container_type=row.container_type,
        quantity=row.quantity,
        origin_port=row.origin_port,
        destination_port=row.destination_port,
        latest_arrival=row.latest_arrival or NO_DEADLINE,
        allow_split=row.allow_split,
        original_price_minor=row.total_price_minor,
        currency=row.currency,
        max_additional_cost_minor=row.max_additional_cost_minor,
        must_deliver=row.must_deliver,
        priority=row.priority,
    )


STRANDED_SQL = """
SELECT b.booking_id, b.buyer_id, b.carrier_id, b.sailing_id,
       o.container_type,
       COALESCE(NULLIF(bs.slots, 0), o.quantity) AS quantity,
       s.origin_port, s.destination_port,
       r.latest_arrival, r.allow_split,
       b.total_price_minor, b.currency,
       b.max_additional_cost_minor,
       (b.must_deliver OR r.must_deliver), r.priority
FROM bookings b
JOIN offers o ON o.offer_id = b.offer_id
JOIN cargo_requests r ON r.request_id = b.request_id
JOIN sailings s ON s.sailing_id = b.sailing_id
LEFT JOIN (
    SELECT booking_id, count(*) AS slots
    FROM booking_slots
    WHERE status = 'COMMITTED'
    GROUP BY booking_id
) bs ON bs.booking_id = b.booking_id
WHERE b.sailing_id = %s AND b.status = 'DISRUPTED'
ORDER BY b.booking_id
"""

PENDING_SQL = """
SELECT d.event_id, d.carrier_id, d.sailing_id, d.occurred_at
FROM disruption_events d
LEFT JOIN recovery_runs rr ON rr.withdrawal_event_id = d.event_id
WHERE d.event_type = 'SAILING_WITHDRAWN' AND rr.withdrawal_event_id IS NULL
ORDER BY d.occurred_at, d.event_id
"""


async def stranded_bookings(pool: AsyncConnectionPool, sailing_id: str) -> list[DisruptedBooking]:
    async with pool.connection() as conn:
        rows = await (await conn.execute(STRANDED_SQL, (sailing_id,))).fetchall()
    return [to_disrupted_booking(StrandedRow(*row)) for row in rows]


async def pending_withdrawals(pool: AsyncConnectionPool) -> list[PendingWithdrawal]:
    """Withdrawals with no recovery_runs row yet. recovery_runs.withdrawal_event_id is
    UNIQUE, so a poller that inserts the run before working on it can never recover the
    same withdrawal twice."""
    async with pool.connection() as conn:
        rows = await (await conn.execute(PENDING_SQL)).fetchall()
    return [PendingWithdrawal(*row) for row in rows]

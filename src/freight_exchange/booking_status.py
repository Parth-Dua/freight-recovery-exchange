"""Real `BookingSeam` (freight-seams) backed by Tiger — Parth's lane.

`bookings_for_buyer` is real now (read model for ASI:One). `commit_booking` lands in
Phase 4 with carrier holds and test-mode payment; until then it refuses loudly rather
than pretending. Consumers that need commits today keep using `FakeBookingSeam`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from freight_seams import BookingStatusView
from psycopg_pool import AsyncConnectionPool


@dataclass(frozen=True)
class SailingWithdrawal:
    """Public fact: a carrier withdrew one of its sailings (no buyer details)."""

    sailing_id: str
    carrier_name: str
    withdrawn_at: datetime
    reason: str
    disrupted_bookings: int
    departure_at: datetime


class TigerBookingSeam:
    def __init__(self, pool: AsyncConnectionPool):
        self.pool = pool

    async def commit_booking(self, *, buyer_id: str, sailing_id: str, container_type: str, quantity: int,
                             total_price_minor: int, currency: str) -> str:
        raise NotImplementedError("commit_booking lands in Phase 4 (carrier hold + test-mode payment)")

    async def bookings_for_buyer(self, buyer_id: str) -> list[BookingStatusView]:
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(
                """SELECT b.booking_id, b.buyer_id, b.status, b.sailing_id, k.display_name,
                          (SELECT max(e.ts) FROM market_events e
                            WHERE e.event_type = 'BOOKING_DISRUPTED' AND e.entity_id = b.booking_id),
                          (SELECT ra.replacement_booking_id FROM recovery_assignments ra
                            WHERE ra.old_booking_id = b.booking_id AND ra.replacement_booking_id IS NOT NULL
                            LIMIT 1)
                   FROM bookings b JOIN carriers k ON k.carrier_id = b.carrier_id
                   WHERE b.buyer_id = %s ORDER BY b.created_at DESC""", (buyer_id,))).fetchall()
        return [BookingStatusView(booking_id=r[0], buyer_id=r[1], status=r[2], sailing_id=r[3], carrier_name=r[4],
                                  disrupted_at=r[5], replaced_by_booking_id=r[6]) for r in rows]

    async def recent_withdrawals(self, limit: int = 5) -> list[SailingWithdrawal]:
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(
                """SELECT d.sailing_id, k.display_name, d.occurred_at,
                          coalesce(d.reason_risk_event_id || ': ', '') || d.reason,
                          (SELECT count(*) FROM bookings b WHERE b.sailing_id = d.sailing_id AND b.status = 'DISRUPTED'),
                          s.departure_at
                   FROM disruption_events d
                   JOIN carriers k ON k.carrier_id = d.carrier_id
                   JOIN sailings s ON s.sailing_id = d.sailing_id
                   WHERE d.event_type = 'SAILING_WITHDRAWN'
                   ORDER BY d.occurred_at DESC LIMIT %s""", (limit,))).fetchall()
        return [SailingWithdrawal(*r) for r in rows]

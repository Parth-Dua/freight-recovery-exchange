"""Read-only views the console renders. No writes happen here."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from psycopg_pool import AsyncConnectionPool


@dataclass(frozen=True)
class SailingRow:
    sailing_id: str
    carrier_id: str
    carrier_name: str
    corridor_id: str
    origin_port: str
    destination_port: str
    departure_at: datetime
    arrival_at: datetime
    status: str
    sellable_slots: int
    committed_bookings: int

    @property
    def is_active(self) -> bool:
        return self.status == "ACTIVE"


async def list_sailings(pool: AsyncConnectionPool) -> list[SailingRow]:
    async with pool.connection() as conn:
        rows = await (
            await conn.execute(
                """SELECT s.sailing_id, s.carrier_id, c.display_name, s.corridor_id,
                          s.origin_port, s.destination_port, s.departure_at, s.arrival_at, s.status,
                          count(DISTINCT cs.slot_id) FILTER (WHERE cs.status = 'AVAILABLE'),
                          count(DISTINCT b.booking_id) FILTER (WHERE b.status = 'COMMITTED')
                   FROM sailings s
                   JOIN carriers c ON c.carrier_id = s.carrier_id
                   LEFT JOIN capacity_slots cs ON cs.sailing_id = s.sailing_id
                   LEFT JOIN bookings b ON b.sailing_id = s.sailing_id
                   GROUP BY s.sailing_id, s.carrier_id, c.display_name, s.corridor_id,
                            s.origin_port, s.destination_port, s.departure_at, s.arrival_at, s.status
                   ORDER BY s.status, s.departure_at, s.sailing_id"""
            )
        ).fetchall()
    return [SailingRow(*row) for row in rows]


async def recent_disruptions(pool: AsyncConnectionPool, limit: int = 8) -> list[tuple]:
    async with pool.connection() as conn:
        rows = await (
            await conn.execute(
                """SELECT event_id, carrier_id, sailing_id, authorized_by, reason, occurred_at
                   FROM disruption_events
                   ORDER BY occurred_at DESC
                   LIMIT %s""",
                (limit,),
            )
        ).fetchall()
    return list(rows)


async def committed_bookings(pool: AsyncConnectionPool, sailing_id: str) -> list[tuple[str, str]]:
    """(booking_id, buyer_id) for every COMMITTED booking on one sailing."""
    async with pool.connection() as conn:
        rows = await (
            await conn.execute(
                """SELECT booking_id, buyer_id
                   FROM bookings
                   WHERE sailing_id = %s AND status = 'COMMITTED'
                   ORDER BY booking_id""",
                (sailing_id,),
            )
        ).fetchall()
    return [(row[0], row[1]) for row in rows]

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
    total_slots: int = 0
    booked_slots: int = 0

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
                          count(DISTINCT b.booking_id) FILTER (WHERE b.status = 'COMMITTED'),
                          count(DISTINCT cs.slot_id),
                          count(DISTINCT cs.slot_id) FILTER (WHERE cs.status IN ('COMMITTED', 'HELD'))
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


@dataclass(frozen=True)
class BookingRow:
    booking_id: str
    buyer_id: str
    status: str
    containers: int
    total_price_minor: int
    created_at: datetime


async def sailing_bookings(pool: AsyncConnectionPool, sailing_id: str) -> list[BookingRow]:
    async with pool.connection() as conn:
        rows = await (
            await conn.execute(
                """SELECT b.booking_id, b.buyer_id, b.status,
                          GREATEST(count(bs.slot_id), 1), b.total_price_minor, b.created_at
                   FROM bookings b
                   LEFT JOIN booking_slots bs ON bs.booking_id = b.booking_id
                   WHERE b.sailing_id = %s AND b.status <> 'CANCELLED'
                   GROUP BY b.booking_id
                   ORDER BY b.created_at""",
                (sailing_id,),
            )
        ).fetchall()
    return [BookingRow(*row) for row in rows]


@dataclass(frozen=True)
class AgentAction:
    ts: datetime
    kind: str
    sender_role: str
    message_type: str
    round: int
    sailing_id: str | None
    buyer_agent_key: str
    total_price_minor: int | None
    quantity: int | None
    session_status: str


ACTIVITY_SQL = """
SELECT m.ts, s.kind, m.sender_role, m.message_type, m.round,
       COALESCE(m.public_terms->>'sailing_id', o.sailing_id),
       s.buyer_agent_key,
       COALESCE((m.public_terms->>'total_price_minor')::bigint,
                (m.public_terms->>'proposed_total_price_minor')::bigint),
       o.quantity,
       s.status
FROM negotiation_messages m
JOIN negotiation_sessions s ON s.session_id = m.session_id
LEFT JOIN offers o ON o.offer_id = m.public_terms->>'offer_id'
WHERE s.carrier_id = %s
ORDER BY m.ts DESC
LIMIT %s
"""


async def agent_activity(pool: AsyncConnectionPool, carrier_id: str, limit: int = 60) -> list[AgentAction]:
    """Every negotiation message to or from this carrier's agent, newest first."""
    async with pool.connection() as conn:
        rows = await (await conn.execute(ACTIVITY_SQL, (carrier_id, limit))).fetchall()
    return [AgentAction(*row) for row in rows]

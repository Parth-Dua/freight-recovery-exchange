"""Carrier-scoped Tiger reads. Every query is filtered by this carrier's own id."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from freight_contracts import CargoSpec, OfferTerms
from psycopg_pool import AsyncConnectionPool

from carrier_agent.policy import Policy, SailingInventory


@dataclass(frozen=True)
class LoggedOffer:
    offer_id: str
    negotiation_id: str
    request_id: str
    sailing_id: str
    container_type: str
    quantity: int
    total_price_minor: int
    currency: str
    round: int
    expires_at: datetime
    counters_used: int


_SAILING_SQL = """
    SELECT s.sailing_id, v.name, s.origin_port, s.destination_port, s.departure_at, s.arrival_at,
           count(c.slot_id) AS capacity,
           count(c.slot_id) FILTER (WHERE c.status = 'AVAILABLE') AS available
    FROM sailings s
    JOIN vessels v ON v.vessel_id = s.vessel_id AND v.carrier_id = s.carrier_id
    JOIN capacity_slots c ON c.sailing_id = s.sailing_id AND c.container_type = %s
    WHERE s.carrier_id = %s AND s.status = 'ACTIVE' {extra}
    GROUP BY s.sailing_id, v.name
    ORDER BY s.departure_at"""


class CarrierInventory:
    def __init__(self, pool: AsyncConnectionPool, carrier_id: str):
        self._pool = pool
        self.carrier_id = carrier_id

    async def registered_address(self) -> str | None:
        async with self._pool.connection() as conn:
            row = await (await conn.execute(
                "SELECT agent_address FROM carriers WHERE carrier_id = %s AND active", (self.carrier_id,)
            )).fetchone()
        return row[0] if row else None

    async def policy(self) -> Policy:
        async with self._pool.connection() as conn:
            row = await (await conn.execute(
                "SELECT policy FROM carrier_policies WHERE carrier_id = %s", (self.carrier_id,)
            )).fetchone()
        if row is None:
            raise LookupError(f"no policy for carrier {self.carrier_id}")
        return Policy.from_json(row[0])

    async def sailings_for(self, cargo: CargoSpec) -> list[SailingInventory]:
        sql = _SAILING_SQL.format(extra="AND s.origin_port = %s AND s.destination_port = %s")
        async with self._pool.connection() as conn:
            rows = await (await conn.execute(
                sql, (cargo.container_type, self.carrier_id, cargo.origin_port, cargo.destination_port))).fetchall()
        return [_sailing(r) for r in rows]

    async def sailing(self, sailing_id: str, container_type: str) -> SailingInventory | None:
        sql = _SAILING_SQL.format(extra="AND s.sailing_id = %s")
        async with self._pool.connection() as conn:
            row = await (await conn.execute(sql, (container_type, self.carrier_id, sailing_id))).fetchone()
        return _sailing(row) if row else None

    # ---- carrier-owned offer log (never written by the Exchange) ----------
    async def log_offer(self, offer: OfferTerms, *, negotiation_id: str, request_id: str) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                """INSERT INTO carrier_offer_log (offer_id, carrier_id, negotiation_id, request_id, sailing_id,
                       container_type, quantity, total_price_minor, currency, round, expires_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (offer.offer_id, self.carrier_id, negotiation_id, request_id, offer.sailing_id,
                 offer.container_type, offer.quantity, offer.total_price_minor, offer.currency,
                 offer.round, offer.expires_at),
            )

    async def logged_offer(self, offer_id: str) -> LoggedOffer | None:
        async with self._pool.connection() as conn:
            row = await (await conn.execute(
                """SELECT offer_id, negotiation_id, request_id, sailing_id, container_type, quantity,
                          total_price_minor, currency, round, expires_at, counters_used
                   FROM carrier_offer_log WHERE offer_id = %s AND carrier_id = %s""",
                (offer_id, self.carrier_id))).fetchone()
        return LoggedOffer(*row) if row else None

    async def claim_counter(self, offer_id: str) -> bool:
        """Atomically use the single counter allowed on this offer. False if already used."""
        async with self._pool.connection() as conn:
            row = await (await conn.execute(
                """UPDATE carrier_offer_log SET counters_used = counters_used + 1
                   WHERE offer_id = %s AND carrier_id = %s AND counters_used = 0
                   RETURNING offer_id""",
                (offer_id, self.carrier_id))).fetchone()
        return row is not None


def _sailing(r) -> SailingInventory:
    return SailingInventory(
        sailing_id=r[0], vessel_name=r[1], origin_port=r[2], destination_port=r[3],
        departure_at=r[4], arrival_at=r[5], capacity_units=r[6], available_units=r[7],
    )

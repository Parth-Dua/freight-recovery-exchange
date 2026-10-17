"""Carrier-scoped Tiger reads. Every query is filtered by this carrier's own id."""

from __future__ import annotations

from freight_contracts import CargoSpec
from psycopg_pool import AsyncConnectionPool

from carrier_agent.policy import Policy, SailingInventory


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
        async with self._pool.connection() as conn:
            rows = await (await conn.execute(
                """SELECT s.sailing_id, v.name, s.origin_port, s.destination_port, s.departure_at, s.arrival_at,
                          count(c.slot_id) AS capacity,
                          count(c.slot_id) FILTER (WHERE c.status = 'AVAILABLE') AS available
                   FROM sailings s
                   JOIN vessels v ON v.vessel_id = s.vessel_id AND v.carrier_id = s.carrier_id
                   JOIN capacity_slots c ON c.sailing_id = s.sailing_id AND c.container_type = %s
                   WHERE s.carrier_id = %s AND s.status = 'ACTIVE'
                     AND s.origin_port = %s AND s.destination_port = %s
                   GROUP BY s.sailing_id, v.name
                   ORDER BY s.departure_at""",
                (cargo.container_type, self.carrier_id, cargo.origin_port, cargo.destination_port),
            )).fetchall()
        return [
            SailingInventory(
                sailing_id=r[0], vessel_name=r[1], origin_port=r[2], destination_port=r[3],
                departure_at=r[4], arrival_at=r[5], capacity_units=r[6], available_units=r[7],
            )
            for r in rows
        ]

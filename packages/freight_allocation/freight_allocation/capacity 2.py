"""Where the sellable-slot counts for claim checks come from.

`TigerCapacityReader` is the real source. The other two exist for tests and for
running the allocation before a database is available; neither is evidence.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from psycopg_pool import AsyncConnectionPool


@runtime_checkable
class CapacityReader(Protocol):
    async def sellable_slots(self, sailing_ids: list[str], container_type: str) -> dict[str, int]:
        """Sellable slots per sailing: AVAILABLE slots on ACTIVE sailings only."""
        ...


class StaticCapacity:
    """Fixed counts, for tests."""

    def __init__(self, counts: dict[str, int]) -> None:
        self._counts = dict(counts)

    async def sellable_slots(self, sailing_ids: list[str], container_type: str) -> dict[str, int]:
        return {sailing_id: self._counts.get(sailing_id, 0) for sailing_id in sailing_ids}


class TigerCapacityReader:
    """Real counts from Tiger.

    Counts AVAILABLE capacity_slots only, so slots already HELD or COMMITTED (including
    by other recoveries running at the same time) are never offered twice. A WITHDRAWN
    or otherwise inactive sailing counts as 0.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def sellable_slots(self, sailing_ids: list[str], container_type: str) -> dict[str, int]:
        counts = {sailing_id: 0 for sailing_id in sailing_ids}
        if not sailing_ids:
            return counts
        async with self._pool.connection() as conn:
            rows = await (
                await conn.execute(
                    """SELECT cs.sailing_id, count(*)
                       FROM capacity_slots cs
                       JOIN sailings s ON s.sailing_id = cs.sailing_id
                       WHERE cs.sailing_id = ANY(%s)
                         AND cs.container_type = %s
                         AND cs.status = 'AVAILABLE'
                         AND s.status = 'ACTIVE'
                       GROUP BY cs.sailing_id""",
                    (sailing_ids, container_type),
                )
            ).fetchall()
        for sailing_id, count in rows:
            counts[sailing_id] = int(count)
        return counts

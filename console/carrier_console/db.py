"""Tiger access for the console.

Mirrors the exchange's conventions on purpose (see src/freight_exchange/repository.py):
every state change writes its market_events row inside the same transaction, and all
Part 1 rows carry source_kind = 'simulated'.
"""

from __future__ import annotations

import asyncio

from psycopg_pool import AsyncConnectionPool

from carrier_console.config import database_url
from freight_disruption.events import SOURCE, emit_event  # noqa: F401  (re-exported)


_pools: dict[int, AsyncConnectionPool] = {}


async def get_pool() -> AsyncConnectionPool:
    """One pool per event loop, matching the exchange's pattern."""
    loop_id = id(asyncio.get_running_loop())
    pool = _pools.get(loop_id)
    if pool is None:
        pool = AsyncConnectionPool(
            database_url(),
            min_size=1,
            max_size=4,
            open=False,
            kwargs={"connect_timeout": 15},
            check=AsyncConnectionPool.check_connection,
        )
        await pool.open(wait=True, timeout=20)
        _pools[loop_id] = pool
    return pool


async def close_pools() -> None:
    for pool in list(_pools.values()):
        await pool.close()
    _pools.clear()

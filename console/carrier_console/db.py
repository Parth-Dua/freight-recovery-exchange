"""Tiger access for the console.

Mirrors the exchange's conventions on purpose (see src/freight_exchange/repository.py):
every state change writes its market_events row inside the same transaction, and all
Part 1 rows carry source_kind = 'simulated'.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from carrier_console.config import database_url

SOURCE = "simulated"

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


async def emit_event(
    conn: AsyncConnection,
    event_type: str,
    entity_type: str,
    entity_id: str,
    *,
    correlation_id: str | None = None,
    carrier_id: str | None = None,
    sailing_id: str | None = None,
    corridor_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    """Append one row to the market_events hypertable. Call inside a transaction."""
    await conn.execute(
        """INSERT INTO market_events (ts, event_id, event_type, entity_type, entity_id,
                                      correlation_id, carrier_id, sailing_id, corridor_id,
                                      source_kind, payload)
           VALUES (now(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            uuid4(),
            event_type,
            entity_type,
            entity_id,
            correlation_id,
            carrier_id,
            sailing_id,
            corridor_id,
            SOURCE,
            Jsonb(payload or {}),
        ),
    )

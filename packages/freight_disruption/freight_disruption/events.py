"""market_events writer shared by every disruption write.

Same convention as the exchange (src/freight_exchange/repository.py): every state change
writes its market_events row inside the same transaction, and Part 1 rows carry
source_kind = 'simulated'.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

SOURCE = "simulated"


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

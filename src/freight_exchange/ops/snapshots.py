"""The observer's only writes: per-sailing capacity snapshots and Time Machine checkpoints.

Both are derived from consistent reads of the business ledger (REPEATABLE READ), never the other way round.
Capacity snapshots are taken every minute and whenever an inventory-changing event appears (booking committed,
hold granted/released, sailing withdrawn, booking superseded), so history shows the exact moment of change.
A checkpoint stores the sanitized projection plus `cursor_ts`: every market event with ts <= cursor_ts is
already reflected in it; replay folds only later events.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from freight_exchange.ops.read_model import PROJECTION_SCHEMA, projection, sanitize_check

INVENTORY_EVENTS = {"BOOKING_COMMITTED", "HOLD_CREATED", "HOLD_RELEASED", "HOLD_EXPIRED", "SAILING_WITHDRAWN",
                    "BOOKING_SUPERSEDED", "BOOKING_DISRUPTED"}


async def capture_capacity(pool, trigger: str) -> int:
    async with pool.connection() as conn, conn.transaction():
        await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        p = await projection(conn)
        now = datetime.now(timezone.utc)
        rows = [(now, s["sailingId"], s["carrierId"], s["corridorId"], s["status"], s["totalUnits"],
                 s["committedUnits"], s["heldUnits"], s["availableUnits"], trigger) for s in p["sailings"]]
        async with conn.cursor() as cur:
            await cur.executemany(
                """INSERT INTO sailing_capacity_snapshots (ts, sailing_id, carrier_id, corridor_id, status, total_units,
                       committed_units, held_units, available_units, trigger)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""", rows)
    return len(rows)


async def capture_checkpoint(pool, reason: str) -> dict:
    async with pool.connection() as conn, conn.transaction():
        await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        p = await projection(conn)
        leaks = sanitize_check(p)
        if leaks:  # never persist a projection that would ship private fields to a browser
            raise RuntimeError(f"projection contains private fields: {leaks[:3]}")
        cursor = (await (await conn.execute("SELECT max(ts) FROM market_events")).fetchone())[0]
        snapshot_id = f"cp_{uuid4().hex[:16]}"
        await conn.execute(
            """INSERT INTO market_state_checkpoints (snapshot_id, captured_at, cursor_ts, projection_schema, reason,
                   projection) VALUES (%s, now(), %s, %s, %s, %s)""",
            (snapshot_id, cursor, PROJECTION_SCHEMA, reason, json.dumps(p)))
    return {"snapshotId": snapshot_id, "cursor": cursor}


async def latest_checkpoint_cursor(pool):
    async with pool.connection() as conn:
        row = await (await conn.execute(
            "SELECT cursor_ts, captured_at FROM market_state_checkpoints ORDER BY captured_at DESC LIMIT 1")).fetchone()
    return row

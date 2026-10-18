"""Live Tiger tests. Skipped unless TIGER_DATABASE_URL is set.

They withdraw a real sailing, so run them only against your own dev database
(`cd console && uv run python scripts/dev_db.py reset` first, which preseeds Buyers B and C).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[3] / ".env")
except ImportError:
    pass

pytestmark = pytest.mark.skipif(
    not os.getenv("TIGER_DATABASE_URL"), reason="TIGER_DATABASE_URL not set; needs live Tiger"
)


@pytest.fixture
async def pool():
    from psycopg_pool import AsyncConnectionPool

    pool = AsyncConnectionPool(os.environ["TIGER_DATABASE_URL"], min_size=1, max_size=2, open=False)
    await pool.open(wait=True, timeout=20)
    yield pool
    await pool.close()


async def _active_sailing_with_bookings(pool) -> tuple[str, str] | None:
    async with pool.connection() as conn:
        row = await (
            await conn.execute(
                """SELECT s.sailing_id, s.carrier_id
                   FROM sailings s JOIN bookings b ON b.sailing_id = s.sailing_id
                   WHERE s.status = 'ACTIVE' AND b.status = 'COMMITTED'
                   ORDER BY s.departure_at LIMIT 1"""
            )
        ).fetchone()
    return (row[0], row[1]) if row else None


async def test_withdraw_then_read_who_is_stranded(pool) -> None:
    from freight_disruption import TigerDisruptionSeam

    target = await _active_sailing_with_bookings(pool)
    if target is None:
        pytest.skip("no ACTIVE sailing with a COMMITTED booking; run dev_db.py reset")
    sailing_id, carrier_id = target
    seam = TigerDisruptionSeam(pool)

    summary = await seam.withdraw(
        sailing_id=sailing_id, carrier_id=carrier_id, risk_event_id="risk_sim_0001", actor_id="pytest"
    )
    assert summary.already_withdrawn is False
    assert summary.disrupted_booking_ids

    stranded = await seam.disrupted_bookings(sailing_id)
    assert {b.booking_id for b in stranded} == set(summary.disrupted_booking_ids)
    for booking in stranded:
        assert booking.sailing_id == sailing_id
        assert booking.quantity >= 1
        assert booking.original_price_minor > 0
        assert booking.latest_arrival.tzinfo is not None

    pending = await seam.pending_withdrawals()
    assert summary.event_id in {p.event_id for p in pending}

    async with pool.connection() as conn:
        row = await (
            await conn.execute(
                """SELECT reason_risk_event_id, authorization_ref, idempotency_key
                   FROM disruption_events WHERE event_id = %s""",
                (summary.event_id,),
            )
        ).fetchone()
    assert row[0] == "risk_sim_0001"
    assert row[1] == "seam:pytest"
    assert row[2]

    again = await seam.withdraw(
        sailing_id=sailing_id, carrier_id=carrier_id, risk_event_id="risk_sim_0001", actor_id="pytest"
    )
    assert again.already_withdrawn is True
    assert again.event_id == summary.event_id


async def test_a_sailing_nobody_withdrew_strands_nobody(pool) -> None:
    from freight_disruption import TigerDisruptionSeam

    assert await TigerDisruptionSeam(pool).disrupted_bookings("NO-SUCH-SAILING") == []

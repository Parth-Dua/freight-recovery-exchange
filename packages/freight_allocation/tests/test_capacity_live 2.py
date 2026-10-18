"""Live Tiger read for claim checks. Skipped unless TIGER_DATABASE_URL is set.

Read-only: it never changes the database, so it is safe on a shared Tiger.
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
    not os.getenv("TIGER_DATABASE_URL"), reason="TIGER_DATABASE_URL not set; this suite needs live Tiger"
)


async def test_tiger_reader_counts_sellable_slots() -> None:
    from psycopg_pool import AsyncConnectionPool

    from freight_allocation import TigerCapacityReader

    pool = AsyncConnectionPool(os.environ["TIGER_DATABASE_URL"], min_size=1, max_size=2, open=False)
    await pool.open(wait=True, timeout=20)
    try:
        counts = await TigerCapacityReader(pool).sellable_slots(["BEA-201", "COV-301", "NO-SUCH-SAILING"], "20GP")
    finally:
        await pool.close()
    assert set(counts) == {"BEA-201", "COV-301", "NO-SUCH-SAILING"}
    assert counts["NO-SUCH-SAILING"] == 0
    assert all(isinstance(v, int) and v >= 0 for v in counts.values())

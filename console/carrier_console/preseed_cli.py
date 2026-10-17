"""Seed the demo market's background commitments.

    cd console && uv run python -m carrier_console.preseed_cli

Creates Buyer B on the sailing that will be withdrawn, and Buyer C on a different
sailing so the demo can show an untouched booking. Buyer A is intentionally absent:
PRD 16.1 requires Buyer A to be booked live through ASI:One.
"""

from __future__ import annotations

import asyncio
import sys

from carrier_console.config import load_env
from carrier_console.db import close_pools, get_pool
from carrier_console.preseed import PreseedRefused, preseed_booking
from carrier_console.queries import list_sailings


async def run() -> int:
    load_env()
    pool = await get_pool()
    try:
        sailings = [s for s in await list_sailings(pool) if s.is_active]
        if len(sailings) < 2:
            print("Need at least two ACTIVE sailings. Seed the market from the repo root:")
            print("  uv run python scripts/db.py migrate && uv run python scripts/db.py seed")
            return 1

        target, elsewhere = sailings[0], sailings[-1]
        plan = [("buyer_b", target, 1), ("buyer_c", elsewhere, 1)]

        for buyer_id, sailing, quantity in plan:
            try:
                result = await preseed_booking(
                    pool, buyer_id=buyer_id, sailing_id=sailing.sailing_id, quantity=quantity
                )
            except PreseedRefused as refused:
                print(f"  skipped {buyer_id}: {refused}")
                continue
            print(
                f"  {result.booking_id}  {result.buyer_id:>8}  {result.sailing_id}  "
                f"{len(result.slot_ids)} slot(s)  {result.total_price_minor} minor"
            )

        print("\nBuyer A is not preseeded on purpose. Book it live in ASI:One.")
        return 0
    finally:
        await close_pools()


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))

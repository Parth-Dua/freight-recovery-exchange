"""Fill your LOCAL dev database with believable demo activity for screen recordings.

    cd console && uv run python scripts/dev_db.py reset && uv run python scripts/dev_demo_fill.py

Adds bookings across carriers (so ships and slot blocks show cargo) and a day of Atlas
negotiation history (so Agent activity has rows). Every row is simulated: bookings go
through the same preseed path the demo already uses, and negotiation rows carry a
"demo:" buyer key. Refuses to run against anything but a database on this machine.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from carrier_console.config import load_env  # noqa: E402
from carrier_console.db import close_pools, get_pool  # noqa: E402
from carrier_console.preseed import PreseedRefused, preseed_booking  # noqa: E402

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}

# (buyer, sailing, containers, price per container in cents)
BOOKINGS = [
    ("buyer_7f2", "ATL-101", 1, 269_000),
    ("buyer_3a9", "ATL-101", 1, 261_000),
    ("buyer_e58", "ATL-102", 2, 264_000),
    ("buyer_c41", "ATL-103", 1, 255_000),
    ("buyer_9d0", "ATL-151", 2, 266_000),
    ("buyer_b17", "BEA-201", 1, 283_000),
    ("buyer_a62", "COV-301", 1, 248_000),
]

# Atlas negotiations for the bookings above: (buyer, minutes ago, opening, counter, final, outcome)
ATLAS_TALKS = [
    ("buyer_7f2", 142, 273_000, 258_000, 269_000, "REVISED_OFFER"),
    ("buyer_3a9", 96, 268_000, 261_000, 261_000, "ACCEPT_COUNTER"),
    ("buyer_e58", 71, 271_000, 255_000, 264_000, "REVISED_OFFER"),
    ("buyer_c41", 38, 262_000, 250_000, 255_000, "REVISED_OFFER"),
    ("buyer_9d0", 17, 270_000, 240_000, None, "REJECT_COUNTER"),
]


def require_local() -> None:
    host = urlparse(os.getenv("TIGER_DATABASE_URL", "")).hostname
    if host not in LOCAL_HOSTS:
        sys.exit(f"Refusing: TIGER_DATABASE_URL points at {host!r}, not this machine.")


async def fill() -> int:
    load_env()
    require_local()
    pool = await get_pool()
    try:
        made: dict[str, tuple[str, str, int]] = {}  # buyer -> (request_id, offer_id, quantity)
        for buyer, sailing, qty, price in BOOKINGS:
            try:
                r = await preseed_booking(pool, buyer_id=buyer, sailing_id=sailing, quantity=qty, unit_price_minor=price)
            except PreseedRefused as refused:
                print(f"  skipped {buyer} on {sailing}: {refused}")
                continue
            async with pool.connection() as conn:
                req, offer = await (await conn.execute(
                    "SELECT request_id, offer_id FROM bookings WHERE booking_id = %s", (r.booking_id,))).fetchone()
            made[buyer] = (req, offer, qty)
            print(f"  {r.booking_id}  {buyer}  {sailing}  {qty} × 20GP")

        async with pool.connection() as conn, conn.transaction():
            atlas_addr = (await (await conn.execute(
                "SELECT agent_address FROM carriers WHERE carrier_id = 'atlas'")).fetchone())[0]
            for buyer, ago, opening, counter, final, outcome in ATLAS_TALKS:
                if buyer not in made:
                    continue
                req, offer, qty = made[buyer]
                sid = f"neg_demo_{uuid4().hex[:10]}"
                buyer_key = f"demo:{buyer}"
                t0 = timedelta(minutes=ago)
                status = "REJECTED" if final is None else "ACCEPTED"
                await conn.execute(
                    """INSERT INTO negotiation_sessions (session_id, buyer_agent_key, carrier_id, carrier_agent_address,
                           request_id, kind, round, rounds_max, status, opened_at, expires_at, closed_at,
                           final_offer_id, final_total_minor, reason)
                       VALUES (%s, %s, 'atlas', %s, %s, 'booking', 1, 2, %s, now() - %s, now() - %s + interval '10 min',
                               now() - %s + interval '41 s', %s, %s, %s)""",
                    (sid, buyer_key, atlas_addr, req, status, t0, t0, t0, offer if final else None,
                     final * qty if final else None,
                     "carrier refused the counter" if final is None else "price agreed"))
                steps = [("carrier", atlas_addr, 0, "PROPOSE_OFFER", {"offer_id": offer, "total_price_minor": opening * qty}, 0),
                         ("buyer_agent", buyer_key, 1, "COUNTER_OFFER",
                          {"offer_id": offer, "proposed_total_price_minor": counter * qty,
                           "public_rationale": "Comparable offers are lower. Can you meet this?"}, 17),
                         ("carrier", atlas_addr, 1, outcome,
                          {"offer_id": offer, **({"total_price_minor": final * qty} if final else {"outcome": "reject"})}, 38)]
                for role, addr, rnd, kind, terms, secs in steps:
                    await conn.execute(
                        """INSERT INTO negotiation_messages (session_id, message_id, sender_role, sender_address, round,
                               message_type, public_terms, ts)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, now() - %s + %s)""",
                        (sid, f"msg_demo_{uuid4().hex[:12]}", role, addr, rnd, kind, json.dumps(terms), t0,
                         timedelta(seconds=secs)))
        print(f"  {len([t for t in ATLAS_TALKS if t[0] in made])} Atlas negotiations added")
        return 0
    finally:
        await close_pools()


if __name__ == "__main__":
    sys.exit(asyncio.run(fill()))

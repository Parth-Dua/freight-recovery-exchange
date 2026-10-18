"""LIVE Tiger tests for Phase 7 carrier-side recovery holds and confirmations.

A throwaway replacement sailing (TST-…) with 4 free slots, a one-shot recovery offer of 2 units,
and two buyers whose original bookings on another throwaway sailing are DISRUPTED. Everything is
deleted afterwards. Skipped without TIGER_DATABASE_URL (then: no evidence).
"""

import asyncio
import os
from pathlib import Path
from uuid import uuid4

import pytest
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
pytestmark = pytest.mark.skipif(not os.getenv("TIGER_DATABASE_URL"), reason="needs live Tiger")

from psycopg_pool import AsyncConnectionPool  # noqa: E402

from carrier_agent.holds import confirm_booking, request_hold  # noqa: E402

CARRIER = "atlas"
UNIT = 270000


async def _pool():
    pool = AsyncConnectionPool(os.environ["TIGER_DATABASE_URL"], min_size=1, max_size=6, open=False,
                               kwargs={"connect_timeout": 15})
    await pool.open(wait=True, timeout=20)
    return pool


async def _sailing(c, sailing, slots, status="ACTIVE"):
    await c.execute(
        """INSERT INTO sailings (sailing_id, carrier_id, vessel_id, origin_port, destination_port, corridor_id,
               departure_at, arrival_at, status, source_kind)
           VALUES (%s, %s, 'atlas-meridian', 'AEJEA', 'INBOM', 'AEJEA-INBOM', now() + interval '5 days',
                   now() + interval '7 days', %s, 'simulated')""", (sailing, CARRIER, status))
    for n in range(1, slots + 1):
        await c.execute("""INSERT INTO capacity_slots (slot_id, sailing_id, slot_no, container_type, status, source_kind)
                           VALUES (%s, %s, %s, '20GP', 'AVAILABLE', 'simulated')""", (f"{sailing}-{n}", sailing, n))


async def _setup(pool, buyers=3):
    tag = uuid4().hex[:8]
    ids = {"tag": tag, "old": f"TST-{tag}-OLD", "new": f"TST-{tag}-NEW", "run": f"rec_tst_{tag}",
           "rof": f"rof_tst_{tag}", "requests": [], "bookings": [], "offers": []}
    async with pool.connection() as c, c.transaction():
        await _sailing(c, ids["old"], 0, status="WITHDRAWN")
        await _sailing(c, ids["new"], 4)
        await c.execute(
            """INSERT INTO carrier_offer_log (offer_id, carrier_id, negotiation_id, request_id, sailing_id, container_type,
                   quantity, total_price_minor, currency, round, expires_at, offer_kind)
               VALUES (%s, %s, %s, %s, %s, '20GP', 2, %s, 'USD', 1, now() + interval '10 minutes', 'recovery')""",
            (ids["rof"], CARRIER, ids["run"], ids["run"], ids["new"], 2 * UNIT))
        for i in range(buyers):
            req, old_offer, new_offer, booking = (f"req_tst_{tag}_{i}", f"off_tst_{tag}_{i}", f"{ids['rof']}.BK{i}",
                                                  f"BK-TST-{tag}-{i}")
            await c.execute(
                """INSERT INTO cargo_requests (request_id, buyer_id, correlation_id, origin_port, destination_port,
                       container_type, quantity, currency, status, source_kind)
                   VALUES (%s, %s, %s, 'AEJEA', 'INBOM', '20GP', 1, 'USD', 'BOOKED', 'simulated')""",
                (req, f"buyer_tst_{i}", f"flow_tst_{tag}"))
            for offer, sailing, price in ((old_offer, ids["old"], 250000), (new_offer, ids["new"], UNIT)):
                await c.execute(
                    """INSERT INTO offers (offer_id, request_id, negotiation_id, correlation_id, message_id, carrier_id,
                           sailing_id, container_type, quantity, total_price_minor, currency, departure_at, arrival_at,
                           expires_at, status, carrier_signature, terms, source_kind, created_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, '20GP', 1, %s, 'USD', now() + interval '5 days',
                               now() + interval '7 days', now() + interval '10 minutes', 'ACCEPTED', 'test', 'test',
                               'simulated', now())""",
                    (offer, req, ids["run"], ids["run"], f"msg_{offer}", CARRIER, sailing, price))
            await c.execute(
                """INSERT INTO bookings (booking_id, buyer_id, request_id, offer_id, carrier_id, sailing_id, status,
                       total_price_minor, currency, confirmation_key, source_kind)
                   VALUES (%s, %s, %s, %s, %s, %s, 'DISRUPTED', 250000, 'USD', %s, 'simulated')""",
                (booking, f"buyer_tst_{i}", req, old_offer, CARRIER, ids["old"], f"key_{booking}"))
            ids["requests"].append(req)
            ids["offers"] += [old_offer, new_offer]
            ids["bookings"].append(booking)
    return ids


async def _cleanup(pool, ids):
    sailings = [ids["old"], ids["new"]]
    async with pool.connection() as c, c.transaction():
        await c.execute("DELETE FROM booking_slots WHERE booking_id IN (SELECT booking_id FROM bookings WHERE sailing_id = ANY(%s))", (sailings,))
        await c.execute("DELETE FROM bookings WHERE sailing_id = %s", (ids["new"],))
        await c.execute("DELETE FROM bookings WHERE sailing_id = %s", (ids["old"],))
        await c.execute("DELETE FROM hold_slots WHERE hold_id IN (SELECT hold_id FROM holds WHERE sailing_id = ANY(%s))", (sailings,))
        await c.execute("DELETE FROM holds WHERE sailing_id = ANY(%s)", (sailings,))
        await c.execute("DELETE FROM carrier_offer_log WHERE sailing_id = ANY(%s)", (sailings,))
        await c.execute("DELETE FROM offers WHERE sailing_id = ANY(%s)", (sailings,))
        await c.execute("DELETE FROM cargo_requests WHERE request_id = ANY(%s)", (ids["requests"],))
        await c.execute("DELETE FROM capacity_slots WHERE sailing_id = ANY(%s)", (sailings,))
        await c.execute("DELETE FROM market_events WHERE sailing_id = ANY(%s) OR correlation_id = %s", (sailings, ids["run"]))
        await c.execute("DELETE FROM sailings WHERE sailing_id = ANY(%s)", (sailings,))


def run(scenario):
    async def main():
        pool = await _pool()
        ids = await _setup(pool)
        try:
            return await scenario(pool, ids)
        finally:
            await _cleanup(pool, ids)
            await pool.close()
    return asyncio.run(main())


def hold(pool, ids, i, *, run_id=None, key=None, offer=None):
    return request_hold(pool, CARRIER, offer_id=offer or f"{ids['rof']}.BK{i}", request_id=ids["requests"][i],
                        buyer_id=f"buyer_tst_{i}", quantity=1, idempotency_key=key or f"rh:{ids['tag']}:{i}",
                        ttl_seconds=600, recovery_offer_id=ids["rof"], run_id=run_id or ids["run"])


def test_holds_never_exceed_the_offered_units_even_with_free_slots_and_concurrency():
    async def scenario(pool, ids):
        outcomes = await asyncio.gather(*(hold(pool, ids, i) for i in range(3)))   # 3 buyers race for 2 units
        assert sorted(o.ok for o in outcomes) == [False, True, True]
        refused = next(o for o in outcomes if not o.ok)
        assert refused.code == "INSUFFICIENT_CAPACITY" and "offered" in refused.detail
        async with pool.connection() as c:
            free = (await (await c.execute("SELECT count(*) FROM capacity_slots WHERE sailing_id = %s AND status = 'AVAILABLE'",
                                           (ids["new"],))).fetchone())[0]
        assert free == 2   # the ship still has space; the carrier only offered 2
    run(scenario)


def test_wrong_run_or_price_is_refused():
    async def scenario(pool, ids):
        assert (await hold(pool, ids, 0, run_id="rec_other")).code == "OFFER_NOT_FOUND"
        async with pool.connection() as c:
            await c.execute("UPDATE offers SET total_price_minor = 1 WHERE offer_id = %s", (f"{ids['rof']}.BK1",))
        assert (await hold(pool, ids, 1)).code == "HOLD_TERMS_MISMATCH"
    run(scenario)


def test_confirm_links_the_replacement_and_only_for_the_same_buyers_disrupted_booking():
    async def scenario(pool, ids):
        held = await hold(pool, ids, 0)
        assert held.ok
        common = dict(hold_id=held.hold_id, payment_reference=f"recovery:{ids['run']}", intent_id=ids["run"],
                      claim_code_hash="", payment_terms="test")
        wrong = await confirm_booking(pool, CARRIER, idempotency_key=f"rc:{ids['tag']}:x",
                                      replaces_booking_id=ids["bookings"][1], **common)
        assert wrong.code == "REPLACES_MISMATCH"
        ok = await confirm_booking(pool, CARRIER, idempotency_key=f"rc:{ids['tag']}:0",
                                   replaces_booking_id=ids["bookings"][0], **common)
        assert ok.ok
        async with pool.connection() as c:
            row = await (await c.execute("SELECT status, replaces_booking_id, total_price_minor FROM bookings WHERE booking_id = %s",
                                         (ok.booking_id,))).fetchone()
        assert row == ("COMMITTED", ids["bookings"][0], UNIT)
    run(scenario)

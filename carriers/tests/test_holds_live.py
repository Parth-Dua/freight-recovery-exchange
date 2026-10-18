"""LIVE Tiger tests for carrier hold/confirm/release (spec 04 AT-04-01/02/04 inventory side).

Each test builds an isolated throwaway sailing (TST-…) with its own requests/offers and
deletes everything afterwards. Skipped without TIGER_DATABASE_URL (then: no evidence).
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

from carrier_agent.holds import confirm_booking, release_hold, request_hold  # noqa: E402

CARRIER = "atlas"


async def _pool():
    pool = AsyncConnectionPool(os.environ["TIGER_DATABASE_URL"], min_size=1, max_size=6, open=False,
                               kwargs={"connect_timeout": 15})
    await pool.open(wait=True, timeout=20)
    return pool


async def _setup(pool, slots: int, offers: int, quantity: int = 1):
    tag = uuid4().hex[:8]
    sailing = f"TST-{tag}"
    ids = {"sailing": sailing, "requests": [], "offers": []}
    async with pool.connection() as c, c.transaction():
        await c.execute(
            """INSERT INTO sailings (sailing_id, carrier_id, vessel_id, origin_port, destination_port, corridor_id,
                   departure_at, arrival_at, status, source_kind)
               VALUES (%s, %s, 'atlas-meridian', 'AEJEA', 'INBOM', 'AEJEA-INBOM', now() + interval '5 days',
                       now() + interval '7 days', 'ACTIVE', 'simulated')""", (sailing, CARRIER))
        for n in range(1, slots + 1):
            await c.execute("""INSERT INTO capacity_slots (slot_id, sailing_id, slot_no, container_type, status, source_kind)
                               VALUES (%s, %s, %s, '20GP', 'AVAILABLE', 'simulated')""", (f"{sailing}-{n}", sailing, n))
        for i in range(offers):
            req, off = f"req_tst_{tag}_{i}", f"off_tst_{tag}_{i}"
            await c.execute(
                """INSERT INTO cargo_requests (request_id, buyer_id, correlation_id, origin_port, destination_port,
                       container_type, quantity, currency, status, source_kind)
                   VALUES (%s, %s, %s, 'AEJEA', 'INBOM', '20GP', %s, 'USD', 'OFFER_SELECTED', 'simulated')""",
                (req, f"buyer_tst_{i}", f"flow_tst_{tag}", quantity))
            await c.execute(
                """INSERT INTO offers (offer_id, request_id, negotiation_id, correlation_id, message_id, carrier_id,
                       sailing_id, container_type, quantity, total_price_minor, currency, departure_at, arrival_at,
                       expires_at, status, carrier_signature, terms, source_kind, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, '20GP', %s, 260000, 'USD', now() + interval '5 days',
                           now() + interval '7 days', now() + interval '10 minutes', 'SELECTED', 'test', 'test',
                           'simulated', now())""",
                (off, req, f"neg_tst_{tag}_{i}", f"flow_tst_{tag}", f"msg_tst_{tag}_{i}", CARRIER, sailing, quantity))
            await c.execute(
                """INSERT INTO carrier_offer_log (offer_id, carrier_id, negotiation_id, request_id, sailing_id,
                       container_type, quantity, total_price_minor, currency, round, expires_at)
                   VALUES (%s, %s, %s, %s, %s, '20GP', %s, 260000, 'USD', 1, now() + interval '10 minutes')""",
                (off, CARRIER, f"neg_tst_{tag}_{i}", req, sailing, quantity))
            ids["requests"].append(req)
            ids["offers"].append(off)
    return ids


async def _cleanup(pool, ids):
    s = ids["sailing"]
    async with pool.connection() as c, c.transaction():
        await c.execute("DELETE FROM booking_slots WHERE booking_id IN (SELECT booking_id FROM bookings WHERE sailing_id = %s)", (s,))
        await c.execute("DELETE FROM bookings WHERE sailing_id = %s", (s,))
        await c.execute("DELETE FROM hold_slots WHERE hold_id IN (SELECT hold_id FROM holds WHERE sailing_id = %s)", (s,))
        await c.execute("DELETE FROM holds WHERE sailing_id = %s", (s,))
        await c.execute("DELETE FROM carrier_offer_log WHERE sailing_id = %s", (s,))
        await c.execute("DELETE FROM offers WHERE sailing_id = %s", (s,))
        await c.execute("DELETE FROM cargo_requests WHERE request_id = ANY(%s)", (ids["requests"],))
        await c.execute("DELETE FROM capacity_slots WHERE sailing_id = %s", (s,))
        await c.execute("DELETE FROM market_events WHERE sailing_id = %s", (s,))
        await c.execute("DELETE FROM sailings WHERE sailing_id = %s", (s,))


async def _slot_states(pool, sailing):
    async with pool.connection() as c:
        return sorted(r[0] for r in await (await c.execute(
            "SELECT status FROM capacity_slots WHERE sailing_id = %s", (sailing,))).fetchall())


def run(scenario, *, slots=1, offers=1, quantity=1):
    async def main():
        pool = await _pool()
        ids = await _setup(pool, slots, offers, quantity)
        try:
            return await scenario(pool, ids)
        finally:
            await _cleanup(pool, ids)
            await pool.close()
    return asyncio.run(main())


def hold(pool, ids, i=0, key=None, ttl=1800, quantity=1):
    return request_hold(pool, CARRIER, offer_id=ids["offers"][i], request_id=ids["requests"][i],
                        buyer_id=f"buyer_tst_{i}", quantity=quantity, idempotency_key=key or uuid4().hex,
                        ttl_seconds=ttl)


def test_at_04_01_two_concurrent_holds_cannot_oversell_the_last_slot():
    async def scenario(pool, ids):
        a, b = await asyncio.gather(hold(pool, ids, 0), hold(pool, ids, 1))
        assert sorted([a.ok, b.ok]) == [False, True]
        loser = a if not a.ok else b
        assert loser.code == "INSUFFICIENT_CAPACITY"
        assert await _slot_states(pool, ids["sailing"]) == ["HELD"]
    run(scenario, slots=1, offers=2)


def test_many_concurrent_holds_never_exceed_capacity():
    async def scenario(pool, ids):
        results = await asyncio.gather(*(hold(pool, ids, i) for i in range(5)))
        assert sum(r.ok for r in results) == 2
        assert await _slot_states(pool, ids["sailing"]) == ["HELD", "HELD"]
    run(scenario, slots=2, offers=5)


def test_at_04_02_hold_and_confirm_are_idempotent():
    async def scenario(pool, ids):
        first = await hold(pool, ids, key="k-hold")
        again = await hold(pool, ids, key="k-hold")
        assert first.ok and again.ok and again.replay and again.hold_id == first.hold_id
        b1 = await confirm_booking(pool, CARRIER, hold_id=first.hold_id, payment_reference="cs_test_x",
                                   idempotency_key="k-confirm", intent_id="intent_x", claim_code_hash="h",
                                   payment_terms="prepaid")
        b2 = await confirm_booking(pool, CARRIER, hold_id=first.hold_id, payment_reference="cs_test_x",
                                   idempotency_key="k-confirm", intent_id="intent_x", claim_code_hash="h",
                                   payment_terms="prepaid")
        assert b1.ok and b2.ok and b2.replay and b2.booking_id == b1.booking_id
        assert await _slot_states(pool, ids["sailing"]) == ["COMMITTED"]
        third = await hold(pool, ids, key="k-other")  # nothing left to hold
        assert not third.ok
    run(scenario)


def test_at_04_04_expired_hold_cannot_confirm_and_frees_capacity():
    async def scenario(pool, ids):
        h = await hold(pool, ids, ttl=1)
        assert h.ok
        await asyncio.sleep(2.5)
        c = await confirm_booking(pool, CARRIER, hold_id=h.hold_id, payment_reference="cs_test_late",
                                  idempotency_key="k-late", intent_id="intent_late", claim_code_hash="h",
                                  payment_terms="prepaid")
        assert not c.ok and c.code == "HOLD_EXPIRED"
        assert await _slot_states(pool, ids["sailing"]) == ["AVAILABLE"]
    run(scenario)


def test_release_is_idempotent_and_returns_capacity():
    async def scenario(pool, ids):
        h = await hold(pool, ids)
        r1 = await release_hold(pool, CARRIER, hold_id=h.hold_id)
        r2 = await release_hold(pool, CARRIER, hold_id=h.hold_id)
        assert r1.ok and r1.code == "HOLD_RELEASED" and r2.ok and r2.replay
        assert await _slot_states(pool, ids["sailing"]) == ["AVAILABLE"]
        c = await confirm_booking(pool, CARRIER, hold_id=h.hold_id, payment_reference="p", idempotency_key="k-r",
                                  intent_id="i", claim_code_hash="h", payment_terms="prepaid")
        assert not c.ok and c.code == "HOLD_RELEASED"  # INV-3
    run(scenario)


def test_withdrawn_sailing_cannot_be_held_or_confirmed():
    async def scenario(pool, ids):
        h = await hold(pool, ids, 0)
        async with pool.connection() as c:
            await c.execute("UPDATE sailings SET status = 'WITHDRAWN' WHERE sailing_id = %s", (ids["sailing"],))
        again = await hold(pool, ids, 1)
        assert not again.ok and again.code == "SAILING_NOT_ACTIVE"
        c = await confirm_booking(pool, CARRIER, hold_id=h.hold_id, payment_reference="p", idempotency_key="k-w",
                                  intent_id="i", claim_code_hash="h", payment_terms="prepaid")
        assert not c.ok and c.code == "SAILING_NOT_ACTIVE"
    run(scenario, slots=2, offers=2)


def test_superseded_or_wrong_quantity_offers_cannot_be_held():
    async def scenario(pool, ids):
        async with pool.connection() as c:
            await c.execute(
                """INSERT INTO carrier_offer_log (offer_id, carrier_id, negotiation_id, request_id, sailing_id,
                       container_type, quantity, total_price_minor, currency, round, expires_at)
                   SELECT offer_id || '_r2', carrier_id, negotiation_id, request_id, sailing_id, container_type,
                          quantity, 250000, currency, 2, expires_at FROM carrier_offer_log WHERE offer_id = %s""",
                (ids["offers"][0],))
        old = await hold(pool, ids, 0)
        assert not old.ok and old.code == "OFFER_SUPERSEDED"
        wrong = await hold(pool, ids, 1, quantity=2)
        assert not wrong.ok and wrong.code == "QUANTITY_MISMATCH"
    run(scenario, slots=2, offers=2)

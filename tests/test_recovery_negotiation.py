"""R02 (unit, controlled timing): the recovery deadline is an upper bound, not a wait."""

import asyncio
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from freight_seams import demo_displaced_bookings

import freight_exchange.recovery_negotiation as rn
from freight_exchange.carrier_client import CarrierRef
from freight_exchange.negotiation_sessions import NegotiationService, Pair

NOW = datetime.now(timezone.utc)
FAST, SLOW = demo_displaced_bookings()   # BK-1001 answers quickly, BK-1002's only carrier never answers
FAST = replace(FAST, latest_arrival=NOW + timedelta(days=4))
SLOW = replace(SLOW, latest_arrival=NOW + timedelta(days=4))


class Offer:
    def __init__(self, offer_id):
        self.offer_id, self.quantity, self.total_price_minor, self.currency = offer_id, 2, 540000, "USD"
        self.sailing_id, self.carrier_id = "BEA-201", "beacon"
        self.arrival_at, self.expires_at = NOW + timedelta(days=3), NOW + timedelta(hours=1)


@pytest.fixture
def harness(monkeypatch):
    closed = []

    async def pairs(repo, cargo):
        return [Pair(CarrierRef("beacon", "Beacon", "agent1qbeacon", None), 1, "")]

    async def no_op_open(self, s, *a, **k):
        return None

    async def close(self, s, status, reason, keep_offer=True):
        s.status = status
        if not keep_offer:
            s.current = None
        closed.append((s.booking_id, status))

    async def group(self, sessions, private, quantity, deadline):
        if sessions[0].booking_id == SLOW.booking_id and harness_state["slow"]:
            await asyncio.sleep(30)            # a silent carrier: never answers
        for s in sessions:
            s.current, s.status = Offer(f"off_{s.booking_id}"), "ACCEPTED"

    harness_state = {"slow": True}
    monkeypatch.setattr(rn, "pair_candidates", pairs)
    monkeypatch.setattr(NegotiationService, "_open", no_op_open)
    monkeypatch.setattr(NegotiationService, "_close", close)
    monkeypatch.setattr(NegotiationService, "negotiate_group", group)

    class Client:
        timeout_s = 8
        identity = type("I", (), {"address": "agent1qexchange"})()

    negotiator = rn.RecoveryNegotiator(repo=None, client=Client())

    async def facts(ids):
        return {i: (f"req_{i}", "general", None) for i in ids}
    negotiator._facts = facts
    return negotiator, closed, harness_state


def run(coro):
    return asyncio.run(coro)


def test_silent_carrier_expires_at_the_deadline_and_the_rest_still_clear(harness):
    negotiator, closed, _ = harness
    started = time.monotonic()
    offers, sources, summary = run(negotiator.negotiate("rec_1", [FAST, SLOW], datetime.now(timezone.utc) + timedelta(seconds=0.5)))
    assert time.monotonic() - started < 3                       # bounded by the deadline, not by the silent carrier
    assert [o.eligible_booking_ids for o in offers] == [(FAST.booking_id,)]   # late session contributes nothing
    assert (SLOW.booking_id, "EXPIRED") in closed and summary.expired == 1 and not summary.closed_early


def test_all_sessions_done_early_means_no_waiting(harness):
    negotiator, _, state = harness
    state["slow"] = False
    started = time.monotonic()
    offers, _, summary = run(negotiator.negotiate("rec_1", [FAST, SLOW], datetime.now(timezone.utc) + timedelta(seconds=60)))
    assert time.monotonic() - started < 2 and summary.closed_early and len(offers) == 2

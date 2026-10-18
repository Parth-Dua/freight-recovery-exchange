"""Spec 05 WP6 read path: disruption status in ASI:One (unit tests against fakes)."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from freight_seams import BookingSeam, BookingStatusView

from freight_exchange import responses as r
from freight_exchange.booking_status import SailingWithdrawal, TigerBookingSeam

NOW = datetime(2026, 10, 19, 15, 40, tzinfo=timezone.utc)
WITHDRAWAL = SailingWithdrawal("ATL-101", "Atlas Lines (fictional)", NOW, "risk_sim_0001: simulated advisory",
                               2, NOW + timedelta(days=1))
DISRUPTED = BookingStatusView("BK-1002", "agent1qbuyer", "DISRUPTED", "ATL-101", "Atlas Lines (fictional)",
                              disrupted_at=NOW)


def test_tiger_seam_satisfies_the_booking_seam_protocol():
    assert isinstance(TigerBookingSeam(pool=None), BookingSeam)


def test_commit_booking_refuses_until_phase_4():
    with pytest.raises(NotImplementedError, match="Phase 4"):
        asyncio.run(TigerBookingSeam(pool=None).commit_booking(
            buyer_id="b", sailing_id="ATL-101", container_type="20GP", quantity=1,
            total_price_minor=1, currency="USD"))


def test_reply_shows_withdrawal_disrupted_booking_and_no_false_recovery():
    text = r.disruption_status_reply([WITHDRAWAL], [DISRUPTED])
    assert "Sailing **ATL-101** (Atlas Lines (fictional)" in text and "**withdrawn**" in text
    assert "risk_sim_0001" in text and "2 committed booking(s) disrupted" in text
    assert "BK-1002: **DISRUPTED** ⚠️" in text
    assert "have **not** been rebooked" in text


def test_reply_when_nothing_is_withdrawn():
    text = r.disruption_status_reply([], [])
    assert "No sailings have been withdrawn" in text and "no bookings on record yet" in text
    assert "rebooked" not in text


@pytest.mark.parametrize("message", [
    "@freight-recovery-exc my sailing was withdrawn, what happens now?",
    "is there any disruption?", "what's the status of my booking", "what happened to ATL-101",
    "can you recover my cargo",
])
def test_disruption_questions_route_to_status(message):
    assert r.classify(r.strip_mentions(message)) == r.ROUTE_RECOVERY


def test_graph_status_node_reads_the_booking_seam(monkeypatch):
    import agent as module

    class FakeSeam:
        async def recent_withdrawals(self):
            return [WITHDRAWAL]

        async def bookings_for_buyer(self, buyer_id):
            assert buyer_id == "agent1qbuyer"
            return [DISRUPTED]

    async def fake_seam():
        return FakeSeam()

    async def no_services():
        raise RuntimeError("dedupe offline")  # status must still answer

    monkeypatch.setattr(module, "get_booking_seam", fake_seam)
    monkeypatch.setattr(module, "get_services", no_services)
    state = asyncio.run(module.agent.ainvoke(
        {"messages": [{"role": "human", "content": "@freight-recovery-exc was my sailing withdrawn?"}]},
        config={"metadata": {"agentverse_sender": "agent1qbuyer"}}))
    text = state["messages"][-1].content
    assert "BK-1002: **DISRUPTED**" in text and "ATL-101" in text

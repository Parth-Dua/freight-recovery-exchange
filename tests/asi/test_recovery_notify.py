"""Recovery notifications: who hears what, and delivery that survives retries and failures.
No database and nothing sent: the DB and send calls are replaced."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import UUID, uuid4

from freight_seams import DisruptedBooking, RecoveryAssignment, RecoveryOutcome, RecoveryProposal
from freight_exchange.asi import messages as m
from freight_exchange.asi import recovery_notify as rn
from freight_exchange.asi.notify import BuyerAddress, Notified

UTC = timezone.utc


def _booking(bid: str, qty: int = 1) -> DisruptedBooking:
    return DisruptedBooking(
        booking_id=bid, buyer_id="agent1q" + bid, carrier_id="atlas", sailing_id="ATL-101",
        container_type="20GP", quantity=qty, origin_port="AEJEA", destination_port="INBOM",
        latest_arrival=datetime(2026, 10, 24, 8, 0, tzinfo=UTC), allow_split=False,
        original_price_minor=262_500 * qty, currency="USD",
    )


def _assign(bid: str, qty: int = 1) -> RecoveryAssignment:
    return RecoveryAssignment(bid, "ROF-1", "BEA-201", "beacon", qty, 270_000 * qty,
                              datetime(2026, 10, 23, 4, 0, tzinfo=UTC))


A, B, C = _booking("BK-A", 2), _booking("BK-B"), _booking("BK-C")
PROPOSAL = RecoveryProposal(
    recovery_run_id="rec_1",
    assignments=(_assign("BK-A", 2), _assign("BK-B")),
    unrecovered_booking_ids=("BK-C",),
    needs_approval_booking_ids=("BK-A",),
    explanations={"BK-C": "No replacement sailing arrives by your deadline."},
)


def test_before_confirmation_only_approvals_and_failures_are_sent() -> None:
    kinds = {b.booking_id: kind for b, kind, _ in rn.plan_recovery_messages([A, B, C], PROPOSAL, None)}
    assert kinds == {"BK-A": m.NEEDS_APPROVAL, "BK-C": m.NOT_RECOVERED}  # BK-B waits: a plan is not a booking


def test_after_confirmation_rebooked_and_unrecovered_are_sent() -> None:
    outcome = RecoveryOutcome(recovery_run_id="rec_1", status="PARTIAL",
                              assignments=(_assign("BK-A", 2),), unrecovered_booking_ids=("BK-C",))
    planned = rn.plan_recovery_messages([A, B, C], PROPOSAL, outcome)
    kinds = {b.booking_id: kind for b, kind, _ in planned}
    assert kinds == {"BK-A": m.REBOOKED, "BK-C": m.NOT_RECOVERED}
    assert "No replacement sailing arrives by your deadline." in dict((b.booking_id, t) for b, _, t in planned)["BK-C"]


class _FakePool:
    @asynccontextmanager
    async def connection(self):
        yield None


def _wire(monkeypatch, *, already=(), unreachable=(), explode=()):
    sent, recorded = [], []

    async def fake_already(pool, booking_id, key):
        return booking_id in already

    async def fake_address(conn, booking_id):
        return None if booking_id in unreachable else BuyerAddress("agent1q" + booking_id, uuid4())

    async def fake_notify(uri, address, text, **_):
        if address.agent_address.endswith(tuple(explode)):
            raise ConnectionError("agentverse unreachable")
        sent.append((address.agent_address, text))
        return Notified(uuid4(), address.agent_address, address.session_id, datetime.now(UTC))

    async def fake_record(pool, booking, kind, key, notified):
        recorded.append((booking.booking_id, key))

    async def fake_context(pool, sailing_id):
        return m.CancellationContext(sailing_id, "Atlas Lines (fictional)", "Jebel Ali", "Mumbai",
                                     datetime(2026, 10, 20, 12, 0, tzinfo=UTC), "Simulated incident.")

    monkeypatch.setattr(rn, "_already_sent", fake_already)
    monkeypatch.setattr(rn, "buyer_address_for_booking", fake_address)
    monkeypatch.setattr(rn, "notify_buyer", fake_notify)
    monkeypatch.setattr(rn, "_record_sent", fake_record)
    monkeypatch.setattr(rn, "cancellation_context", fake_context)
    return sent, recorded


def test_cancellation_goes_to_every_reachable_buyer_and_is_recorded(monkeypatch) -> None:
    sent, recorded = _wire(monkeypatch, unreachable=("BK-C",))
    attempts = asyncio.run(rn.notify_cancellation(_FakePool(), "uri", "evt_1", "ATL-101", [A, B, C]))
    assert [(a.booking_id, a.status) for a in attempts] == [
        ("BK-A", rn.SENT), ("BK-B", rn.SENT), ("BK-C", rn.SKIPPED_UNREACHABLE)]
    assert recorded == [("BK-A", "CANCELLED:evt_1"), ("BK-B", "CANCELLED:evt_1")]
    assert all("has been cancelled by Atlas Lines (fictional)" in text for _, text in sent)


def test_a_retry_does_not_message_anyone_twice(monkeypatch) -> None:
    sent, _ = _wire(monkeypatch, already=("BK-A", "BK-B"))
    attempts = asyncio.run(rn.notify_cancellation(_FakePool(), "uri", "evt_1", "ATL-101", [A, B]))
    assert {a.status for a in attempts} == {rn.SKIPPED_ALREADY_SENT}
    assert sent == []


def test_one_buyer_failing_never_stops_the_others(monkeypatch) -> None:
    sent, _ = _wire(monkeypatch, explode=("BK-A",))
    attempts = asyncio.run(rn.notify_cancellation(_FakePool(), "uri", "evt_1", "ATL-101", [A, B]))
    assert [(a.booking_id, a.status, a.detail) for a in attempts] == [
        ("BK-A", rn.FAILED, "ConnectionError"), ("BK-B", rn.SENT, attempts[1].detail)]
    assert len(sent) == 1


def test_recovery_notifications_use_the_recovery_run_as_their_key(monkeypatch) -> None:
    _, recorded = _wire(monkeypatch)

    async def no_names(pool):
        return {}

    monkeypatch.setattr(rn, "carrier_names", no_names)
    attempts = asyncio.run(rn.notify_recovery(_FakePool(), "uri", "rec_1", [A, B, C], PROPOSAL))
    assert [(a.booking_id, a.kind) for a in attempts] == [("BK-A", m.NEEDS_APPROVAL), ("BK-C", m.NOT_RECOVERED)]
    assert recorded == [("BK-A", "NEEDS_APPROVAL:rec_1"), ("BK-C", "NOT_RECOVERED:rec_1")]

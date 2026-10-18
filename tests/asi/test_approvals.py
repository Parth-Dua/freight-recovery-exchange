"""Buyer approval: reading replies and the rules around deciding. In-memory store, no DB."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from freight_seams import ApprovalSeam, RecoveryAssignment, RecoveryProposal
from freight_exchange.asi import approvals as ap

UTC = timezone.utc
BUYER, OTHER = "agent1qbuyer", "agent1qsomeoneelse"


def _proposal(run="rec_1", bookings=("BK-A",)) -> RecoveryProposal:
    return RecoveryProposal(
        recovery_run_id=run,
        assignments=tuple(RecoveryAssignment(b, "ROF-1", "BEA-201", "beacon", 1, 270_000,
                                             datetime(2026, 10, 23, 4, 0, tzinfo=UTC)) for b in bookings),
        needs_approval_booking_ids=tuple(bookings),
    )


def _setup(*bookings, buyer=BUYER):
    store = ap.MemoryApprovalStore()
    seam = ap.TigerApprovalSeam(store)
    ids = [asyncio.run(seam.request_approval(proposal=_proposal(bookings=bookings), booking_id=b, buyer_id=buyer))
           for b in bookings]
    return store, seam, ids


def _reply(store, text, buyer=BUYER, now=None):
    return asyncio.run(ap.handle_approval_reply(store, buyer, text, now))


@pytest.mark.parametrize("text, decision, booking", [
    ("approve BK-6439ce5fcd51", ap.APPROVED, "BK-6439ce5fcd51"),
    ("@freight-recovery-exc Approve bk-123", ap.APPROVED, "bk-123"),
    ("I approve the rebooking for BK-1", ap.APPROVED, "BK-1"),
    ("accepted", ap.APPROVED, None),
    ("decline BK-1", ap.DECLINED, "BK-1"),
    ("Declined.", ap.DECLINED, None),
    ("reject it", ap.DECLINED, None),
    ("don't approve BK-1", ap.DECLINED, "BK-1"),
    ("approve and decline BK-1", None, "BK-1"),
    ('{"action": "approve", "booking_id": "BK-9"}', ap.APPROVED, "BK-9"),
])
def test_replies_are_read(text, decision, booking) -> None:
    assert ap.parse_approval_reply(text) == ap.ApprovalReply(decision, booking)


@pytest.mark.parametrize("text", ["book it", "what's my status?", "cancel", "yes", "quote 2x20GP", "", "  "])
def test_ordinary_chat_is_not_an_approval(text) -> None:
    assert ap.parse_approval_reply(text) is None
    assert not ap.is_approval_reply(text)


def test_it_implements_the_seam() -> None:
    assert isinstance(ap.TigerApprovalSeam(ap.MemoryApprovalStore()), ApprovalSeam)


def test_requests_are_idempotent() -> None:
    store, seam, ids = _setup("BK-A")
    again = asyncio.run(seam.request_approval(proposal=_proposal(), booking_id="BK-A", buyer_id=BUYER))
    assert again == ids[0] == "apr_rec_1_BK-A"
    assert len(store.requests) == 1
    assert asyncio.run(seam.approval_status(again)) == ap.PENDING


def test_buyer_approves() -> None:
    store, seam, ids = _setup("BK-A")
    result = _reply(store, "approve BK-A")
    assert result.status == ap.APPROVED
    assert result.text.startswith("Approved. I'm confirming BEA-201 with Beacon for booking BK-A")
    assert "Nothing is final until the carrier confirms." in result.text
    assert asyncio.run(seam.approval_status(ids[0])) == ap.APPROVED


def test_buyer_declines() -> None:
    store, seam, ids = _setup("BK-A")
    result = _reply(store, "decline BK-A")
    assert result.status == ap.DECLINED and "nothing new was booked or charged" in result.text
    assert asyncio.run(seam.approval_status(ids[0])) == ap.DECLINED


def test_the_first_decision_wins() -> None:
    store, seam, ids = _setup("BK-A")
    _reply(store, "approve BK-A")
    second = _reply(store, "decline BK-A")
    assert second.status == ap.APPROVED and "already approved" in second.text
    assert asyncio.run(seam.approval_status(ids[0])) == ap.APPROVED


def test_only_the_buyer_it_was_asked_of_can_decide() -> None:
    store, seam, ids = _setup("BK-A")
    result = _reply(store, "approve BK-A", buyer=OTHER)
    assert result.status is None and "can't find" in result.text
    assert asyncio.run(seam.approval_status(ids[0])) == ap.PENDING


def test_expired_requests_cannot_be_approved() -> None:
    store, seam, ids = _setup("BK-A")
    later = ap.utcnow() + ap.APPROVAL_TTL + timedelta(seconds=1)
    result = _reply(store, "approve BK-A", now=later)
    assert result.status == ap.EXPIRED and "nothing was booked" in result.text
    assert store.decisions == {}


def test_one_waiting_approval_needs_no_booking_number() -> None:
    store, _, _ = _setup("BK-A")
    assert _reply(store, "approve").status == ap.APPROVED


def test_two_waiting_approvals_ask_which_one() -> None:
    store, _, _ = _setup("BK-A", "BK-B")
    result = _reply(store, "approve")
    assert result.status is None and result.booking_ids_waiting == ("BK-A", "BK-B")
    assert store.decisions == {}


def test_unknown_booking_and_nothing_waiting() -> None:
    store, _, _ = _setup("BK-A")
    assert "can't find" in _reply(store, "approve BK-ZZZ").text
    assert _reply(ap.MemoryApprovalStore(), "approve").text == "There's nothing waiting for your approval right now."


def test_a_reply_saying_both_asks_again_and_decides_nothing() -> None:
    store, _, _ = _setup("BK-A")
    result = _reply(store, "approve and decline BK-A")
    assert "both approve and decline" in result.text and store.decisions == {}


def test_not_an_approval_reply_is_left_for_other_routes() -> None:
    store, _, _ = _setup("BK-A")
    assert _reply(store, "book it") is None


def test_a_racing_decision_is_reported_not_overwritten() -> None:
    class RaceStore(ap.MemoryApprovalStore):
        async def record_decision(self, approval_id, decision, decided_by):
            self.decisions.setdefault(approval_id, ap.DECLINED)  # the other reply landed first
            return await super().record_decision(approval_id, decision, decided_by)

    store = RaceStore()
    asyncio.run(ap.TigerApprovalSeam(store).request_approval(proposal=_proposal(), booking_id="BK-A", buyer_id=BUYER))
    result = _reply(store, "approve BK-A")
    assert result.status == ap.DECLINED and "already declined" in result.text

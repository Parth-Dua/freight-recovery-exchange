"""Unit tests. Fakes only, so none of this is integration evidence."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from freight_seams import (
    BookingSeam,
    DisruptedBooking,
    DisruptionSeam,
    FakeBookingSeam,
    FakeDisruptionSeam,
    FakeRecoverySeam,
    RecoverySeam,
    demo_displaced_bookings,
)

NOW = datetime(2026, 10, 17, 18, 0, tzinfo=timezone.utc)


def test_fakes_satisfy_their_protocols() -> None:
    assert isinstance(FakeBookingSeam(), BookingSeam)
    assert isinstance(FakeDisruptionSeam(), DisruptionSeam)
    assert isinstance(FakeRecoverySeam(), RecoverySeam)


def test_demo_fixture_matches_spec_06_work_package_5() -> None:
    """Atlas displaces Buyer A with 2 unsplittable and Buyer B with 1."""
    displaced = demo_displaced_bookings()
    assert [b.quantity for b in displaced] == [2, 1]
    assert displaced[0].allow_split is False
    assert {b.sailing_id for b in displaced} == {"ATL-101"}
    assert {b.carrier_id for b in displaced} == {"atlas"}
    assert "buyer_c" not in {b.buyer_id for b in displaced}


def test_disrupted_booking_rejects_bad_input() -> None:
    base = demo_displaced_bookings()[0].__dict__
    with pytest.raises(ValueError, match="quantity"):
        DisruptedBooking(**{**base, "quantity": 0})
    with pytest.raises(ValueError, match="price"):
        DisruptedBooking(**{**base, "original_price_minor": 0})
    with pytest.raises(ValueError, match="timezone-aware"):
        DisruptedBooking(**{**base, "latest_arrival": datetime(2026, 10, 23, 8, 0)})


async def test_nothing_is_disrupted_before_a_withdrawal() -> None:
    seam = FakeDisruptionSeam()
    assert await seam.disrupted_bookings("ATL-101") == []


async def test_withdrawal_is_idempotent_in_the_fake_too() -> None:
    seam = FakeDisruptionSeam()
    first = await seam.withdraw(
        sailing_id="ATL-101", carrier_id="atlas", risk_event_id="risk_sim_0001", actor_id="op"
    )
    second = await seam.withdraw(
        sailing_id="ATL-101", carrier_id="atlas", risk_event_id="risk_sim_0001", actor_id="op"
    )
    assert first.already_withdrawn is False
    assert len(first.disrupted_booking_ids) == 2
    assert second.already_withdrawn is True
    assert second.disrupted_booking_ids == ()


async def test_recovery_fake_defaults_to_an_honest_partial() -> None:
    disruption = FakeDisruptionSeam()
    await disruption.withdraw(
        sailing_id="ATL-101", carrier_id="atlas", risk_event_id="risk_sim_0001", actor_id="op"
    )
    outcome = await FakeRecoverySeam(disruption).recover(sailing_id="ATL-101", triggered_at=NOW)

    assert outcome.status == "PARTIAL"
    assert outcome.fully_recovered is False
    assert outcome.unrecovered_booking_ids == ("BK-1002",)


async def test_booking_fake_round_trips_a_commitment() -> None:
    seam = FakeBookingSeam()
    booking_id = await seam.commit_booking(
        buyer_id="buyer_a",
        sailing_id="ATL-101",
        container_type="20GP",
        quantity=2,
        total_price_minor=525_000,
        currency="USD",
    )
    views = await seam.bookings_for_buyer("buyer_a")
    assert [v.booking_id for v in views] == [booking_id]
    assert views[0].status == "COMMITTED"

    seam.mark_disrupted(booking_id, NOW)
    assert (await seam.bookings_for_buyer("buyer_a"))[0].is_disrupted


def test_fake_card_seam_satisfies_the_protocol() -> None:
    from freight_seams import CardSeam, FakeCardSeam

    assert isinstance(FakeCardSeam(), CardSeam)


def test_card_spec_rejects_an_unknown_kind() -> None:
    from freight_seams import CardSpec

    with pytest.raises(ValueError, match="unknown card_kind"):
        CardSpec("bogus", {}, "narration")


def test_card_spec_requires_narration() -> None:
    """Narration is the fallback when ASI:One cannot render the card."""
    from freight_seams import CardSpec

    with pytest.raises(ValueError, match="narration is required"):
        CardSpec("form", {"fields": []}, "   ")


def test_text_only_fallback_still_answers_the_question() -> None:
    """If the card spike fails, every method keeps working and returns real prose."""
    from freight_seams import FakeCardSeam, RecoveryOutcome

    seam = FakeCardSeam()
    outcome = RecoveryOutcome(recovery_run_id="rec_1", status="PARTIAL",
                              unrecovered_booking_ids=("BK-1002",))
    for spec in (
        seam.build_cargo_form([]),
        seam.build_offer_selection([{"offer_id": "OFR-1"}]),
        seam.build_approval_review(outcome),
        seam.build_recovery_summary(outcome),
    ):
        assert spec.narration.strip()


def test_card_response_parses_both_shapes() -> None:
    from freight_seams import FakeCardSeam

    seam = FakeCardSeam()
    assert seam.parse_card_response("") is None
    assert seam.parse_card_response('{"action":"select","rank":1}').action == "submit"


def test_new_fakes_satisfy_their_protocols() -> None:
    from freight_seams import AllocationSeam, ApprovalSeam, FakeAllocationSeam, FakeApprovalSeam

    assert isinstance(FakeAllocationSeam(), AllocationSeam)
    assert isinstance(FakeApprovalSeam(), ApprovalSeam)


async def test_fake_allocation_matches_spec_06_baseline() -> None:
    """AT-06-01 shape: A (2, no split) to Beacon, B (1) to Cove. Fake, so unit test only."""
    from freight_seams import FakeAllocationSeam, demo_recovery_offers

    disruption = FakeDisruptionSeam()
    await disruption.withdraw(
        sailing_id="ATL-101", carrier_id="atlas", risk_event_id="risk_sim_0001", actor_id="op"
    )
    proposal = await FakeAllocationSeam().allocate(
        recovery_run_id="rec_1",
        stranded=await disruption.disrupted_bookings("ATL-101"),
        offers=demo_recovery_offers(),
    )
    placed = {a.booking_id: a.sailing_id for a in proposal.assignments}
    assert placed == {"BK-1001": "BEA-201", "BK-1002": "COV-301"}
    assert proposal.unrecovered_booking_ids == ()
    assert proposal.recovered_count == 2


async def test_fake_allocation_reports_an_honest_partial() -> None:
    """AT-06-02 shape: remove Cove and Buyer B must be reported unrecovered."""
    from freight_seams import FakeAllocationSeam, demo_recovery_offers

    beacon_only = [o for o in demo_recovery_offers() if o.carrier_id == "beacon"]
    proposal = await FakeAllocationSeam().allocate(
        recovery_run_id="rec_2", stranded=demo_displaced_bookings(), offers=beacon_only
    )
    assert proposal.unrecovered_booking_ids == ("BK-1002",)
    assert "BK-1002" in proposal.explanations


async def test_over_limit_price_needs_approval() -> None:
    from dataclasses import replace

    from freight_seams import FakeAllocationSeam, demo_recovery_offers

    pricey = [replace(o, total_price_minor=o.total_price_minor * 2) for o in demo_recovery_offers()]
    proposal = await FakeAllocationSeam().allocate(
        recovery_run_id="rec_3", stranded=demo_displaced_bookings(), offers=pricey
    )
    assert set(proposal.needs_approval_booking_ids) == {"BK-1001", "BK-1002"}


async def test_fake_approval_returns_the_configured_decision() -> None:
    from freight_seams import FakeApprovalSeam, RecoveryProposal

    seam = FakeApprovalSeam(decision="DECLINED")
    approval_id = await seam.request_approval(
        proposal=RecoveryProposal(recovery_run_id="rec_4"), booking_id="BK-1001", buyer_id="buyer_a"
    )
    assert await seam.approval_status(approval_id) == "DECLINED"
    assert await seam.approval_status("apr_unknown") == "EXPIRED"


def test_recovery_offer_rejects_bad_input() -> None:
    from dataclasses import replace

    from freight_seams import demo_recovery_offers

    offer = demo_recovery_offers()[0]
    with pytest.raises(ValueError, match="capacity_units"):
        replace(offer, capacity_units=0)
    with pytest.raises(ValueError, match="timezone-aware"):
        replace(offer, arrival_at=datetime(2026, 10, 22, 20, 0))

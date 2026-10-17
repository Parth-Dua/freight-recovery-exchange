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

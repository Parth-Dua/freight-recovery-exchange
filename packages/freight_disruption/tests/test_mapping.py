"""Unit tests: row conversion and the seam's shape. No database."""

from __future__ import annotations

from datetime import datetime, timezone

from freight_seams import DisruptionSeam
from freight_disruption import (
    NO_DEADLINE,
    UNSTORED_RECOVERY_ALLOWANCE_MINOR,
    StrandedRow,
    TigerDisruptionSeam,
    to_disrupted_booking,
)
from freight_disruption.stranded import PENDING_SQL, STRANDED_SQL


def _row(**changes) -> StrandedRow:
    base = dict(
        booking_id="BK-1",
        buyer_id="buyer_b",
        carrier_id="atlas",
        sailing_id="ATL-101",
        container_type="20GP",
        quantity=2,
        origin_port="AEJEA",
        destination_port="INBOM",
        latest_arrival=datetime(2026, 10, 24, 8, 0, tzinfo=timezone.utc),
        allow_split=False,
        total_price_minor=525_000,
        currency="USD",
    )
    base.update(changes)
    return StrandedRow(**base)


def test_row_becomes_solver_input() -> None:
    booking = to_disrupted_booking(_row())
    assert booking.booking_id == "BK-1"
    assert booking.quantity == 2
    assert booking.allow_split is False
    assert booking.original_price_minor == 525_000
    assert booking.latest_arrival == datetime(2026, 10, 24, 8, 0, tzinfo=timezone.utc)


def test_no_stated_deadline_never_binds() -> None:
    assert to_disrupted_booking(_row(latest_arrival=None)).latest_arrival == NO_DEADLINE


def test_unstored_allowance_means_approval_for_any_extra_cost() -> None:
    """PRD D5 is not stored yet; zero sends every pricier replacement to the buyer."""
    assert UNSTORED_RECOVERY_ALLOWANCE_MINOR == 0
    assert to_disrupted_booking(_row()).max_additional_cost_minor == 0


def test_it_implements_the_seam() -> None:
    assert isinstance(TigerDisruptionSeam(pool=None), DisruptionSeam)


def test_only_still_disrupted_bookings_on_that_sailing_are_read() -> None:
    assert "b.sailing_id = %s AND b.status = 'DISRUPTED'" in STRANDED_SQL


def test_pending_means_no_recovery_run_yet() -> None:
    assert "rr.withdrawal_event_id IS NULL" in PENDING_SQL
    assert "SAILING_WITHDRAWN" in PENDING_SQL

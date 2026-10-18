"""Unit tests: row conversion and the seam's shape. No database."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from freight_seams import DisruptionSeam
from freight_disruption import (
    NO_DEADLINE,
    StrandedRow,
    TigerDisruptionSeam,
    to_disrupted_booking,
)
from freight_disruption.seam import BUYER_SAFE_REASON
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
        max_additional_cost_minor=0,
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


def test_stored_allowance_reaches_the_solver() -> None:
    """PRD D5: the extra cost the buyer pre-approved at booking time (migration 005)."""
    assert to_disrupted_booking(_row(max_additional_cost_minor=35_000)).max_additional_cost_minor == 35_000
    assert "b.max_additional_cost_minor" in STRANDED_SQL


def test_zero_allowance_stays_zero() -> None:
    """The column defaults to 0: any pricier replacement goes to the buyer for approval."""
    assert to_disrupted_booking(_row()).max_additional_cost_minor == 0


def test_it_implements_the_seam() -> None:
    assert isinstance(TigerDisruptionSeam(pool=None), DisruptionSeam)


def test_only_still_disrupted_bookings_on_that_sailing_are_read() -> None:
    assert "b.sailing_id = %s AND b.status = 'DISRUPTED'" in STRANDED_SQL


def test_pending_means_no_recovery_run_yet() -> None:
    assert "rr.withdrawal_event_id IS NULL" in PENDING_SQL
    assert "SAILING_WITHDRAWN" in PENDING_SQL


class _Result:
    event_id = "evt_1"
    sailing_id = "ATL-101"
    carrier_id = "atlas"
    already_withdrawn = False
    disrupted_booking_ids = ()
    untouched_booking_count = 0


@pytest.fixture
def sent(monkeypatch) -> list:
    commands: list = []

    async def fake_withdraw(pool, cmd):
        commands.append(cmd)
        return _Result()

    monkeypatch.setattr("freight_disruption.seam.withdraw_sailing", fake_withdraw)
    return commands


async def test_buyers_never_see_internal_wording(sent) -> None:
    await TigerDisruptionSeam(pool=None).withdraw(
        sailing_id="ATL-101", carrier_id="atlas", risk_event_id="risk_sim_0001", actor_id="recovery"
    )
    (cmd,) = sent
    assert cmd.reason_text == BUYER_SAFE_REASON
    assert "risk_sim" not in cmd.reason_text and "Seam" not in cmd.reason_text
    assert cmd.reason_risk_event_id == "risk_sim_0001"


async def test_caller_reason_is_kept(sent) -> None:
    await TigerDisruptionSeam(pool=None).withdraw(
        sailing_id="ATL-101",
        carrier_id="atlas",
        risk_event_id="risk_sim_0001",
        actor_id="recovery",
        reason_text="  Security incident reported in the transit corridor.  ",
    )
    assert sent[0].reason_text == "Security incident reported in the transit corridor."

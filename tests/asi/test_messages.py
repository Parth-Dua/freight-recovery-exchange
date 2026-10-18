"""Buyer-facing recovery messages: wording rules that keep them honest."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from freight_seams import DisruptedBooking, RecoveryAssignment
from freight_exchange.asi import messages as m

UTC = timezone.utc
NAMES = {"beacon": "Beacon Maritime (fictional)", "cove": "Cove Shipping (fictional)"}


def _booking(**changes) -> DisruptedBooking:
    base = dict(
        booking_id="BK-1001", buyer_id="agent1qbuyer", carrier_id="atlas", sailing_id="ATL-101",
        container_type="20GP", quantity=2, origin_port="AEJEA", destination_port="INBOM",
        latest_arrival=datetime(2026, 10, 23, 23, 59, tzinfo=UTC), allow_split=False,
        original_price_minor=525_000, currency="USD", max_additional_cost_minor=0,
    )
    base.update(changes)
    return DisruptedBooking(**base)


def _assignment(sailing="BEA-201", carrier="beacon", qty=2, price=540_000, day=23, hour=4):
    return RecoveryAssignment("BK-1001", f"ROF-{sailing}", sailing, carrier, qty, price,
                              datetime(2026, 10, day, hour, 0, tzinfo=UTC))


CONTEXT = m.CancellationContext(
    "ATL-101", "Atlas Lines (fictional)", "Jebel Ali", "Mumbai",
    datetime(2026, 10, 20, 12, 0, tzinfo=UTC),
    "Simulated security incident reported in the transit corridor. Operators are reviewing routing.",
)


def test_cancellation_notice_says_what_happened_and_what_happens_next() -> None:
    text = m.cancellation_notice(_booking(), CONTEXT)
    assert "ATL-101" in text and "Jebel Ali to Mumbai" in text and "Atlas Lines (fictional)" in text
    assert "Reason given: Simulated security incident reported in the transit corridor." in text
    assert "Operators are reviewing" not in text  # first sentence only
    assert "BK-1001 (2 x 20GP)" in text
    assert "Your deadline is 23 Oct 23:59 UTC." in text
    assert "Nothing new will be booked or charged without telling you first." in text


def test_no_stated_deadline_is_not_mentioned() -> None:
    text = m.cancellation_notice(_booking(latest_arrival=datetime(2100, 1, 1, tzinfo=UTC)), CONTEXT)
    assert "deadline" not in text


def test_approval_request_never_claims_a_booking() -> None:
    text = m.needs_approval_message(_booking(), [_assignment()], NAMES)
    assert "BEA-201 with Beacon Maritime (fictional)" in text
    assert "USD 5,400.00" in text and "USD 150.00 more" in text and "USD 5,250.00" in text
    assert "Nothing is booked yet." in text
    assert 'Reply "approve BK-1001"' in text and '"decline BK-1001"' in text
    assert "rebooked" not in text.lower() and "moved to" not in text.lower()


def test_approval_wording_is_honest_about_an_unstored_allowance() -> None:
    assert "You haven't approved any extra cost" in m.needs_approval_message(_booking(), [_assignment()], NAMES)
    stored = m.needs_approval_message(_booking(max_additional_cost_minor=10_000), [_assignment()], NAMES)
    assert "You agreed in advance to up to USD 100.00 extra" in stored


def test_rebooked_needs_confirmed_assignments_and_uses_carrier_names() -> None:
    text = m.rebooked_message(_booking(), [_assignment()], NAMES)
    assert text.startswith("You're rebooked.")
    assert "Beacon Maritime (fictional)" in text and "The carrier has confirmed the space." in text
    with pytest.raises(ValueError):
        m.rebooked_message(_booking(), [], NAMES)


def test_split_rebooking_lists_every_sailing() -> None:
    text = m.rebooked_message(
        _booking(allow_split=True),
        [_assignment("BEA-201", "beacon", 1, 270_000, 23, 4), _assignment("COV-301", "cove", 1, 270_000, 23, 18)],
        NAMES,
    )
    assert "split across sailings as you allowed" in text
    assert "1 on BEA-201 (Beacon Maritime (fictional))" in text and "1 on COV-301 (Cove Shipping (fictional))" in text
    assert "all arriving by 23 Oct 18:00 UTC" in text


def test_unknown_carrier_falls_back_to_a_readable_name() -> None:
    assert "with Dolphin," in m.rebooked_message(_booking(), [_assignment(carrier="dolphin")], {})


def test_not_recovered_is_honest_and_promises_nothing() -> None:
    text = m.not_recovered_message(_booking(), "No single replacement sailing has 2 slots free.")
    assert "couldn't find replacement space" in text
    assert "No single replacement sailing has 2 slots free." in text
    assert "Nothing new was booked or charged." in text
    assert "refund" not in text.lower()


def test_no_buyer_message_uses_an_em_dash() -> None:
    texts = [
        m.cancellation_notice(_booking(), CONTEXT),
        m.needs_approval_message(_booking(), [_assignment()], NAMES),
        m.rebooked_message(_booking(), [_assignment()], NAMES),
        m.not_recovered_message(_booking(), "reason"),
    ]
    assert all("—" not in t for t in texts)


def test_first_sentence() -> None:
    assert m.first_sentence("One. Two.") == "One."
    assert m.first_sentence("No full stop") == "No full stop"
    assert m.first_sentence("   ") is None and m.first_sentence(None) is None

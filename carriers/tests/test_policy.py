from datetime import datetime, timedelta, timezone

import pytest
from freight_contracts import CargoSpec, RejectReason

from carrier_agent.policy import Decline, Policy, Quote, SailingInventory, decide, unit_price_minor

NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)
POLICY = Policy("USD", 230000, 260000, 10, 600, "earliest_arrival")


def sailing(sid, dep_days, arr_days, cap=4, avail=4):
    return SailingInventory(sid, "Vessel", "AEJEA", "INBOM", NOW + timedelta(days=dep_days),
                            NOW + timedelta(days=arr_days), cap, avail)


def cargo(**kw):
    base = dict(origin_port="AEJEA", destination_port="INBOM", container_type="20GP", quantity=2)
    return CargoSpec(**{**base, **kw})


def test_picks_earliest_arrival_with_full_quantity_at_target_price():
    out = decide(cargo(), [sailing("B", 2, 5), sailing("A", 1, 3)], POLICY, NOW)
    assert isinstance(out, Quote)
    assert out.sailing.sailing_id == "A" and out.quantity == 2 and out.total_price_minor == 2 * 260000


def test_earliest_departure_selection():
    policy = Policy("USD", 230000, 260000, 10, 600, "earliest_departure")
    out = decide(cargo(), [sailing("A", 2, 3), sailing("B", 1, 6)], policy, NOW)
    assert out.sailing.sailing_id == "B"


def test_utilization_premium_raises_price():
    assert unit_price_minor(POLICY, sailing("A", 1, 3, cap=4, avail=2)) == 260000 + 13000


def test_deadline_excludes_late_sailings():
    out = decide(cargo(latest_arrival=NOW + timedelta(days=2)), [sailing("A", 1, 3)], POLICY, NOW)
    assert out == Decline(RejectReason.NO_ELIGIBLE_SAILING, "no sailing fits the requested dates")


def test_departed_sailings_are_not_offered():
    out = decide(cargo(), [sailing("A", -1, 1)], POLICY, NOW)
    assert isinstance(out, Decline) and out.reason_code is RejectReason.NO_ELIGIBLE_SAILING


def test_insufficient_capacity_without_split():
    out = decide(cargo(quantity=3), [sailing("A", 1, 3, cap=2, avail=2)], POLICY, NOW)
    assert isinstance(out, Decline) and out.reason_code is RejectReason.INSUFFICIENT_CAPACITY


def test_split_allowed_offers_partial_quantity():
    out = decide(cargo(quantity=3, allow_split=True), [sailing("A", 1, 3, cap=2, avail=2)], POLICY, NOW)
    assert isinstance(out, Quote) and out.quantity == 2


def test_budget_between_floor_and_target_is_met():
    out = decide(cargo(max_total_price_minor=500000), [sailing("A", 1, 3)], POLICY, NOW)
    assert isinstance(out, Quote) and out.total_price_minor == 500000


def test_budget_below_floor_declines_without_revealing_floor():
    out = decide(cargo(max_total_price_minor=400000), [sailing("A", 1, 3)], POLICY, NOW)
    assert isinstance(out, Decline) and out.reason_code is RejectReason.BUDGET_BELOW_POLICY
    assert "230" not in out.reason


@pytest.mark.parametrize("kw, reason", [
    ({"container_type": "40HC"}, RejectReason.UNSUPPORTED_CONTAINER_TYPE),
    ({"origin_port": "CNSHA", "destination_port": "USLAX"}, RejectReason.UNSUPPORTED_CORRIDOR),
])
def test_unsupported_requests(kw, reason):
    out = decide(cargo(**kw), [sailing("A", 1, 3)], POLICY, NOW)
    assert isinstance(out, Decline) and out.reason_code is reason

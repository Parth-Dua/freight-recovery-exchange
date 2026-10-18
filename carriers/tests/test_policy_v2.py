"""v2 private carrier pricing (unit): utilization, concessions, surcharges, bulk, surge, two rounds."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from carrier_agent.policy import (
    Policy, PolicyConfigError, SailingInventory, concession_fraction, decide_counter, opening_unit_minor,
)

NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)
BASE = {"currency": "USD", "min_price_per_unit_minor": 230000, "target_price_per_unit_minor": 260000,
        "utilization_premium_pct": 10, "offer_ttl_seconds": 600, "selection": "earliest_arrival",
        "concession_empty_pct": 12, "concession_full_pct": 2, "disruption_surge_pct": 15,
        "cargo_surcharge_pct": {"general": 0, "high_value": 8, "hazardous": "reject"},
        "bulk_discount_pct": 5, "bulk_discount_min_units": 3}
POLICY = Policy.from_json(BASE)


def ship(available, capacity=10):
    return SailingInventory("S1", "V", "AEJEA", "INBOM", NOW + timedelta(days=1), NOW + timedelta(days=3),
                            capacity, available)


def test_fuller_ships_open_higher():
    prices = [opening_unit_minor(POLICY, ship(a)) for a in (10, 5, 0)]   # 0%, 50%, 100% utilized
    assert prices == sorted(prices) and prices[0] == 260000 and prices[-1] == 286000


def test_empty_ships_concede_more_than_full_ones():
    assert concession_fraction(POLICY, Decimal(0)) == Decimal("0.12")
    assert concession_fraction(POLICY, Decimal(1)) == Decimal("0.02")


def test_bulk_and_class_surcharge_applied_once_in_order():
    unit = opening_unit_minor(POLICY, ship(10), quantity=3, cargo_class="high_value")
    assert unit == round(260000 * Decimal("1.08") * Decimal("0.95"))
    assert opening_unit_minor(POLICY, ship(10), cargo_class="hazardous") is None


def test_surge_only_for_recovery_and_floor_never_breached():
    assert opening_unit_minor(POLICY, ship(10), recovery=True) == 299000
    cheap = Policy.from_json({**BASE, "target_price_per_unit_minor": 230000, "bulk_discount_pct": 50})
    assert opening_unit_minor(cheap, ship(10), quantity=5) == 230000


def test_target_below_floor_is_refused():
    with pytest.raises(PolicyConfigError):
        Policy.from_json({**BASE, "target_price_per_unit_minor": 200000})


def test_two_rounds_never_compound_the_concession():
    opening = 520000                     # full ship: 2% concession -> limit 509600, rounded up to $10 -> 510000
    first = decide_counter(opening, 2, 480000, POLICY, util=Decimal(1))
    assert first.kind == "revise" and first.total_price_minor == 510000
    second = decide_counter(opening, 2, 495000, POLICY, current_total=first.total_price_minor, util=Decimal(1))
    assert second.kind == "reject" and "best price" in second.reason   # no further concession
    assert decide_counter(opening, 2, 440000, POLICY, util=Decimal(0)).kind == "reject"   # below the floor


def test_full_ship_concedes_little():
    d = decide_counter(560000, 2, 500000, POLICY, util=Decimal(1))
    assert d.kind == "revise" and d.total_price_minor == 549000           # 2% off 560000, rounded up to $10

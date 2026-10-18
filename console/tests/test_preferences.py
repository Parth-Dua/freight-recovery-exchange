"""Unit tests for the pricing-agent preferences. No database."""

from __future__ import annotations

from dataclasses import replace

import pytest

from carrier_console.preferences import (
    PRESETS,
    PreferencesInvalid,
    apply_preset,
    changed_fields,
    from_policy,
    merged_policy,
    preview,
    validate,
)
from carrier_console.web import _form_to_prefs

ATLAS = {
    "currency": "USD",
    "min_price_per_unit_minor": 230000,
    "target_price_per_unit_minor": 260000,
    "utilization_premium_pct": 10,
    "offer_ttl_seconds": 600,
    "selection": "earliest_arrival",
    "concession_pct": 10,
    "payment_terms": "Freight prepaid to Atlas before departure (simulated)",
    "max_hold_seconds": 3600,
}


def test_todays_policy_reads_with_one_concession_at_both_ends() -> None:
    prefs = from_policy(ATLAS)
    assert prefs.concession_empty_pct == prefs.concession_full_pct == 10
    assert prefs.business_goal == "balanced"
    assert prefs.disruption_surge_pct == 0
    validate(prefs)


def test_preset_sets_only_its_percentages() -> None:
    prefs = apply_preset(from_policy(ATLAS), "fill_ship")
    assert prefs.business_goal == "fill_ship"
    for key, value in PRESETS["fill_ship"].items():
        assert getattr(prefs, key) == value
    assert prefs.target_price_per_unit_minor == 260000
    assert prefs.payment_terms == ATLAS["payment_terms"]


def test_every_preset_is_valid() -> None:
    for goal in PRESETS:
        validate(apply_preset(from_policy(ATLAS), goal))


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"target_price_per_unit_minor": 200000}, "target_price_per_unit_minor"),
        ({"min_price_per_unit_minor": 0}, "min_price_per_unit_minor"),
        ({"concession_full_pct": 20, "concession_empty_pct": 5}, "concession_full_pct"),
        ({"utilization_premium_pct": -1}, "utilization_premium_pct"),
        ({"disruption_surge_pct": 101}, "disruption_surge_pct"),
        ({"offer_ttl_seconds": 10}, "offer_ttl_seconds"),
        ({"selection": "cheapest"}, "selection"),
        ({"payment_terms": "   "}, "payment_terms"),
        ({"business_goal": "yolo"}, "business_goal"),
    ],
)
def test_invalid_settings_name_the_field(change: dict, field: str) -> None:
    with pytest.raises(PreferencesInvalid) as caught:
        validate(replace(from_policy(ATLAS), **change))
    assert field in caught.value.errors


def test_merge_keeps_keys_we_do_not_edit_and_a_legacy_concession() -> None:
    prefs = replace(from_policy(ATLAS), concession_empty_pct=12, concession_full_pct=2)
    policy = merged_policy({**ATLAS, "added_by_parth": True}, prefs)
    assert policy["currency"] == "USD"
    assert policy["added_by_parth"] is True
    assert policy["concession_pct"] == 7  # half-full ship, for carrier code that reads one value


def test_changed_fields_lists_names_only() -> None:
    after = merged_policy(ATLAS, replace(from_policy(ATLAS), disruption_surge_pct=15))
    changed = changed_fields(ATLAS, after)
    assert "disruption_surge_pct" in changed
    assert "min_price_per_unit_minor" not in changed


def test_preview_matches_the_design_doc_formula() -> None:
    prefs = replace(from_policy(ATLAS), concession_empty_pct=12, concession_full_pct=2)
    opening, lowest = preview(prefs)  # 4 slots, 2 left: half full
    assert opening == 273000  # 2,600 × (1 + 10% × 0.5)
    assert lowest == 253890  # 2,730 × (1 - 7%)


def test_preview_never_goes_below_the_floor() -> None:
    prefs = replace(from_policy(ATLAS), concession_empty_pct=50, concession_full_pct=50)
    assert preview(prefs)[1] == ATLAS["min_price_per_unit_minor"]


def test_form_uses_dollars_and_minutes() -> None:
    form = {
        "business_goal": "balanced",
        "target_price": "2650",
        "min_price": "2300",
        "utilization_premium_pct": "10",
        "concession_empty_pct": "8",
        "concession_full_pct": "2",
        "disruption_surge_pct": "15",
        "offer_ttl_minutes": "10",
        "max_hold_minutes": "60",
        "selection": "earliest_arrival",
        "payment_terms": "Prepaid",
    }
    prefs, errors = _form_to_prefs(form, from_policy(ATLAS))
    assert errors == {}
    assert prefs.target_price_per_unit_minor == 265000
    assert prefs.offer_ttl_seconds == 600
    validate(prefs)


def test_form_turns_bad_numbers_into_field_errors() -> None:
    _, errors = _form_to_prefs({"target_price": "lots"}, from_policy(ATLAS))
    assert errors["target_price"] == "Enter a number."

"""Gateway intent parsing and payment amounts (unit). Not ASI:One evidence."""

from decimal import Decimal

import pytest

from freight_exchange.gateway_service import SHIP_INTENT, parse_action, to_atestfet


@pytest.mark.parametrize("raw, action, extra", [
    ('{"action":"submit_cargo_request","quantity":2}', "submit_cargo_request", {"quantity": 2}),
    ('@hormuz-exchange {"selection":{"action":"select_offer","offer_id":"o1"}}', "select_offer", {"offer_id": "o1"}),
    ('{"values":{"quantity":"3","origin_port":"AEJEA"},"action":"submit_cargo_request"}', "submit_cargo_request",
     {"quantity": "3", "origin_port": "AEJEA"}),
])
def test_direct_card_json_is_normalized(raw, action, extra):
    parsed = parse_action(raw)
    assert parsed["action"] == action and all(parsed[k] == v for k, v in extra.items())


@pytest.mark.parametrize("raw", ["I want the second offer", '{"no_action": 1}', "{broken json", "[1,2]"])
def test_prose_and_junk_are_not_actions(raw):
    assert parse_action(raw) is None


def test_ship_intent():
    assert SHIP_INTENT.search("I need to ship cargo") and SHIP_INTENT.search("ship cargo")
    assert not SHIP_INTENT.search("What corridors do you support?")


def test_fet_conversion_is_exact():
    assert to_atestfet(Decimal("0.1")) == 10**17
    with pytest.raises(ValueError):
        to_atestfet(Decimal("0.0000000000000000001"))


from datetime import datetime, timedelta, timezone  # noqa: E402

from freight_exchange.gateway_service import FormError, GatewayService  # noqa: E402

PORTS = {"AEJEA", "INBOM"}
SOON = (datetime.now(timezone.utc) + timedelta(days=4)).isoformat()


def form(**kw):
    base = {"origin_port": "AEJEA", "destination_port": "INBOM", "quantity": "2", "latest_arrival": SOON,
            "allow_split": "false", "cargo_class": "general", "priority": "balanced", "must_deliver": "on",
            "max_total_price": "5600", "recovery_allowance": "250"}
    base.update(kw)
    return GatewayService.validate_form(None, base, PORTS)


def test_valid_form_splits_public_and_private_fields():
    f = form()
    assert (f["quantity"], f["must_deliver"], f["max_total_price_minor"], f["recovery_allowance_minor"]) == (2, True, 560000, 25000)


@pytest.mark.parametrize("bad", [
    {"origin_port": "CNSHA"}, {"destination_port": "AEJEA"}, {"quantity": "-1"}, {"quantity": "abc"},
    {"latest_arrival": "2020-01-01T00:00:00+00:00"}, {"latest_arrival": ""}, {"cargo_class": "hazardous"},
    {"priority": "yolo"}, {"max_total_price": "-5"},
])
def test_invalid_forms_are_rejected_before_any_carrier_call(bad):
    with pytest.raises(FormError):
        form(**bad)

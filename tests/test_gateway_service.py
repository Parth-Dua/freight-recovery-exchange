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

from datetime import datetime, timezone

import pytest

from freight_exchange.repository import Port
from freight_exchange.request_parser import parse_request

PORTS = [Port("AEJEA", "Jebel Ali", ("jebel ali", "dubai", "aejea")),
         Port("INBOM", "Mumbai", ("mumbai", "bombay", "nhava sheva", "inbom"))]
NOW = datetime(2026, 10, 17, 20, tzinfo=timezone.utc)


def p(text):
    return parse_request(text, PORTS, NOW)


def test_prd_demo_prompt():
    r = p("Move two 20-foot containers from Jebel Ali to Mumbai by October 23. Don't split them. "
          "Maximum simulated freight budget $5,600.")
    assert (r.origin.port_code, r.destination.port_code, r.quantity, r.container_type) == ("AEJEA", "INBOM", 2, "20GP")
    assert r.latest_arrival == datetime(2026, 10, 23, 23, 59, tzinfo=timezone.utc)
    assert r.max_total_price_minor == 560000 and r.allow_split is False and not r.missing


def test_codes_and_compact_container():
    r = p("Get quotes for 2x20GP AEJEA to INBOM")
    assert (r.origin.port_code, r.destination.port_code, r.quantity, r.container_type) == ("AEJEA", "INBOM", 2, "20GP")


def test_from_to_order_window_budget_and_split():
    r = p("I need to ship 3 containers to Mumbai from Dubai, departing after Oct 20, "
          "arrive before 2026-10-27, max 9000 USD, can split")
    assert (r.origin.port_code, r.destination.port_code, r.quantity) == ("AEJEA", "INBOM", 3)
    assert r.earliest_departure == datetime(2026, 10, 20, tzinfo=timezone.utc)
    assert r.latest_arrival.date().isoformat() == "2026-10-27"
    assert r.max_total_price_minor == 900000 and r.allow_split is True


def test_asi_one_style_rephrasing_and_high_cube():
    r = p("The user is requesting freight quotes for shipping 1 40ft high cube container from "
          "Jebel Ali to Mumbai with delivery by 25th Oct.")
    assert (r.quantity, r.container_type, r.latest_arrival.day) == (1, "40HC", 25)


def test_unknown_ports_are_reported_not_guessed():
    r = p("quote shanghai to LA please")
    assert r.origin is None and r.destination is None and r.missing == ["origin port", "destination port"]
    assert "assumed 1 container" in r.notes[0]


@pytest.mark.parametrize("text", ["2 containers by Oct 23 to Mumbai from Jebel Ali", "up to Oct 23 Mumbai Jebel Ali"])
def test_small_numbers_are_not_budgets(text):
    assert p(text).max_total_price_minor is None

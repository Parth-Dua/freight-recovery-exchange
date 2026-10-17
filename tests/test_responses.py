from datetime import datetime, timedelta, timezone

import pytest
from freight_contracts import ErrorCode, ProposeOffer, RejectOffer, RejectReason

from freight_exchange import responses as r
from freight_exchange.carrier_client import CarrierOutcome, CarrierRef
from freight_exchange.repository import Corridor, Port
from freight_exchange.request_parser import ParsedRequest

AEJEA = Port("AEJEA", "Jebel Ali", ("jebel ali",))
INBOM = Port("INBOM", "Mumbai", ("mumbai",))
NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)


@pytest.mark.parametrize(("message", "route"), [
    ("What can you do?", r.ROUTE_CAPABILITIES),
    ("What can your freight agent do?", r.ROUTE_CAPABILITIES),
    ("What shipping corridors can you show?", r.ROUTE_CORRIDORS),
    ("What corridors do you currently support?", r.ROUTE_CORRIDORS),
    ("Can you book two containers tomorrow?", r.ROUTE_BOOKING),
    ("Move two 20-foot containers from Jebel Ali to Mumbai by October 23", r.ROUTE_QUOTES),
    ("Get quotes for 2x20GP AEJEA to INBOM", r.ROUTE_QUOTES),
    ("How much does it cost to ship to Mumbai?", r.ROUTE_QUOTES),
    ("What are the rates on the Mumbai lane?", r.ROUTE_QUOTES),
    ("send cargo from Jebel Ali to Mumbai", r.ROUTE_QUOTES),
    ("My sailing was withdrawn, can you recover my cargo?", r.ROUTE_RECOVERY),
    ("asdf qwerty zxcv", r.ROUTE_UNSUPPORTED),
    ("", r.ROUTE_UNSUPPORTED),
    ("   \n\t ", r.ROUTE_UNSUPPORTED),
])
def test_classify(message, route):
    assert r.classify(message) == route


def test_booking_is_declined_without_inventing_a_booking():  # Phase 1 T03 still holds
    text = r.static_reply(r.ROUTE_BOOKING).lower()
    assert "not active" in text and "nothing has been booked" in text


@pytest.mark.parametrize("message", ["🚢" * 10_000, "\x00\x01", "x" * 100_000, '{"json": 1}'])
def test_unusual_input_is_safe(message):
    assert r.classify(message) == r.ROUTE_UNSUPPORTED


def test_corridors_reply_renders_tiger_rows():
    text = r.corridors_reply([Corridor(AEJEA, INBOM, ("20GP",), 3, 6, NOW + timedelta(hours=12))])
    assert "Jebel Ali (AEJEA) → Mumbai (INBOM)" in text and "3 carrier agents" in text and "6 sailings" in text


def _outcome(kind, carrier_id="atlas", **kw):
    carrier = CarrierRef(carrier_id, carrier_id.title(), "agent1q" + "x" * 50 + "abcd", None)
    base = dict(message_id="m2", correlation_id="c", request_id="req_1", negotiation_id="n", sent_at=NOW,
                carrier_id=carrier_id, carrier_address=carrier.agent_address, in_reply_to="m1", signature="s")
    if kind == "offer":
        reply = ProposeOffer(**base, offer_id="o", sailing_id="ATL-101", vessel_name="Atlas Meridian",
                             origin_port="AEJEA", destination_port="INBOM", container_type="20GP", quantity=2,
                             total_price_minor=kw.get("price", 520000), currency="USD",
                             departure_at=NOW + timedelta(days=1), arrival_at=NOW + timedelta(days=3),
                             expires_at=NOW + timedelta(minutes=10))
        return CarrierOutcome(carrier, None, "offer", 120, "almanac", reply=reply, signature_verified=True)
    if kind == "reject":
        reply = RejectOffer(**base, reason_code=RejectReason.INSUFFICIENT_CAPACITY, reason="not enough capacity")
        return CarrierOutcome(carrier, None, "reject", 90, "almanac", reply=reply, signature_verified=True)
    return CarrierOutcome(carrier, None, "exchange_error", 8000, "none",
                          error_code=ErrorCode.CARRIER_TIMEOUT, detail="no reply within 8s")


def test_quotes_reply_lists_offers_cheapest_first_with_declines_and_errors():
    parsed = ParsedRequest(origin=AEJEA, destination=INBOM, quantity=2, quantity_assumed=False,
                           latest_arrival=NOW + timedelta(days=4), max_total_price_minor=560000)
    outcomes = [_outcome("offer", "atlas", price=540000), _outcome("offer", "beacon", price=510000),
                _outcome("reject", "cove"), _outcome("error", "delta")]
    text = r.quotes_reply(parsed, outcomes, "req_1")
    assert text.index("Beacon") < text.index("| 2 | Atlas")
    assert "$5,100" in text and "$5,400" in text
    assert "Declined — Cove: not enough capacity" in text
    assert "No valid offer — Delta: no reply within 8s (CARRIER_TIMEOUT)" in text
    assert "3/4 replies carried a valid carrier signature" in text
    assert "request req_1" in text and "nothing is booked" in text


def test_missing_ports_reply_lists_corridors():
    text = r.missing_info_reply(ParsedRequest(), [Corridor(AEJEA, INBOM, ("20GP",), 3, 6, None)])
    assert "origin port and destination port" in text and "Jebel Ali (AEJEA) → Mumbai (INBOM)" in text

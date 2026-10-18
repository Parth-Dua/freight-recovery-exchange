from datetime import datetime, timedelta, timezone

import pytest
from freight_contracts import ErrorCode, ProposeOffer, RejectOffer, RejectReason

from freight_exchange import responses as r
from freight_exchange.carrier_client import CarrierOutcome, CarrierRef, CounterOutcome
from freight_exchange.quotes import CarrierResult, QuoteRound, rank_results
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


def test_booking_without_selection_invents_nothing():  # Phase 1 T03 spirit: never invent a booking
    from freight_exchange.booking import BookingStep
    text = r.booking_reply(BookingStep("no_selection")).lower()
    assert "select an offer first" in text and "nothing was held or charged" in text


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


def test_quotes_reply_shows_opening_negotiation_and_final_ranked():
    from freight_contracts import AcceptCounter, CounterOffer
    parsed = ParsedRequest(origin=AEJEA, destination=INBOM, quantity=2, quantity_assumed=False,
                           latest_arrival=NOW + timedelta(days=4), max_total_price_minor=560000)
    atlas, beacon = _outcome("offer", "atlas", price=540000), _outcome("offer", "beacon", price=510000)
    counter = CounterOffer(message_id="m3", correlation_id="c", request_id="req_1", negotiation_id="n", sent_at=NOW,
                           offer_id="o", proposed_total_price_minor=484000, expires_at=NOW + timedelta(seconds=30))
    accepted = AcceptCounter(**{**atlas.reply.dict(exclude={"message_type"}), "offer_id": "o2",
                                "total_price_minor": 484000, "supersedes_offer_id": "o", "round": 2})
    results = [CarrierResult(atlas, CounterOutcome(atlas.carrier, counter, atlas.reply, "accept", 90, reply=accepted,
                                                   signature_verified=True)),
               CarrierResult(beacon), CarrierResult(_outcome("reject", "cove")), CarrierResult(_outcome("error", "delta"))]
    text = r.quotes_reply(parsed, QuoteRound("req_1", "c", None, results, rank_results(results)))
    assert text.index("| 1 | Atlas") < text.index("| 2 | Beacon")          # $4,840 final beats $5,100
    assert "asked $4,840 → accepted ✓" in text and "**$4,840**" in text and "$5,400" in text
    assert "Declined — Cove: not enough capacity" in text
    assert "No valid offer — Delta: no reply within 8s (CARRIER_TIMEOUT)" in text
    assert "4/5 carrier replies carried a valid signature" in text
    assert "select 1" in text and "nothing is held, booked or charged" in text


@pytest.mark.parametrize(("message", "route"), [
    ("select 2", r.ROUTE_SELECT), ("go with Atlas", r.ROUTE_SELECT), ("I'll take the cheapest one", r.ROUTE_SELECT),
    ("book option 1", r.ROUTE_SELECT), ("2", r.ROUTE_SELECT), ("requote", r.ROUTE_REQUOTE),
    ("refresh the offers", r.ROUTE_REQUOTE), ("Can you book two containers tomorrow?", r.ROUTE_BOOKING),
])
def test_phase3_routes(message, route):
    assert r.classify(message) == route


def test_missing_ports_reply_lists_corridors():
    text = r.missing_info_reply(ParsedRequest(), [Corridor(AEJEA, INBOM, ("20GP",), 3, 6, None)])
    assert "origin port and destination port" in text and "Jebel Ali (AEJEA) → Mumbai (INBOM)" in text


@pytest.mark.parametrize(("message", "route"), [
    # ASI:One forwards the @handle; "recovery" in our own handle must not trigger the recovery route
    ("@freight-recovery-exc Quote 2x20GP from Jebel Ali to Mumbai", r.ROUTE_QUOTES),
    ("@freight-recovery-exc What corridors do you support?", r.ROUTE_CORRIDORS),
    ("@freight-recovery-exc my sailing was withdrawn, recover it", r.ROUTE_RECOVERY),
])
def test_mentions_are_ignored_for_routing(message, route):
    assert r.classify(r.strip_mentions(message)) == route


def test_strip_mentions_keeps_emails_and_prices():
    assert r.strip_mentions("@freight-recovery-exc  ping test@example.com $5,600") == "ping test@example.com $5,600"

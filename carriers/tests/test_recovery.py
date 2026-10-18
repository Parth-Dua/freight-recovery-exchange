"""Phase 6 carrier side (unit): one-shot recovery offers from private policy, never countered."""

from datetime import datetime, timedelta, timezone

from freight_contracts import (
    CounterOffer, ErrorCode, RecoveryCapacityOffers, RecoveryItem, RequestRecoveryCapacity, parse_carrier_reply,
    payload_digest, verify_model,
)
from freight_contracts.messages import RejectReason
from uagents_core.identity import Identity

from carrier_agent.agent import build_recovery_reply, counter_precheck
from carrier_agent.inventory import LoggedOffer
from carrier_agent.policy import Decline, Policy, SailingInventory, decide_recovery, unit_price_minor

NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)
EXCHANGE = Identity.generate().address
CARRIER = Identity.generate()
POLICY = Policy("USD", 230000, 260000, 10, 600, "earliest_arrival")


def sailing(sid, *, dep=1, arr=3, cap=4, avail=4, lane=("AEJEA", "INBOM")):
    return SailingInventory(sid, "Vessel", *lane, NOW + timedelta(days=dep), NOW + timedelta(days=arr), cap, avail)


def item(bid, qty, *, deadline=4, split=False, lane=("AEJEA", "INBOM")):
    return RecoveryItem(booking_id=bid, container_type="20GP", quantity=qty, origin_port=lane[0],
                        destination_port=lane[1], latest_arrival=NOW + timedelta(days=deadline), allow_split=split)


def decide(items, sailings, withdrawn="ATL-101"):
    return decide_recovery(items, {"20GP": sailings}, POLICY, NOW, withdrawn_sailing_id=withdrawn)


def test_offers_every_sailing_that_fits_priced_by_policy():
    s1, s2 = sailing("BEA-201", arr=3, avail=2), sailing("BEA-202", arr=5, avail=4)
    quotes = decide([item("A", 2), item("B", 1, deadline=6)], [s2, s1])
    assert [q.sailing.sailing_id for q in quotes] == ["BEA-201", "BEA-202"]   # earliest arrival first
    first, second = quotes
    assert first.eligible_booking_ids == ("A", "B") and first.capacity_units == 2
    assert first.total_price_minor == unit_price_minor(POLICY, s1) * 2
    assert second.eligible_booking_ids == ("B",) and second.capacity_units == 1   # A's deadline is day 4


def test_never_offers_withdrawn_departed_or_full_sailings():
    sailings = [sailing("ATL-101"), sailing("ATL-099", dep=-1), sailing("ATL-102", avail=0)]
    out = decide([item("A", 2)], sailings)
    assert isinstance(out, Decline) and out.reason_code is RejectReason.NO_ELIGIBLE_SAILING


def test_no_split_booking_needs_room_for_all_of_it_split_booking_does_not():
    small = sailing("COV-301", avail=1)
    assert isinstance(decide([item("A", 2)], [small]), Decline)
    quotes = decide([item("A", 2, split=True)], [small])
    assert quotes[0].capacity_units == 1 and quotes[0].eligible_booking_ids == ("A",)


def test_other_corridor_is_declined_as_unsupported():
    out = decide([item("A", 1)], [sailing("MER-451", lane=("SADMM", "INBOM"))])
    assert isinstance(out, Decline) and out.reason_code is RejectReason.UNSUPPORTED_CORRIDOR


def request(**kw):
    return RequestRecoveryCapacity(
        message_id="m1", correlation_id="rec_1", request_id="", negotiation_id="rec_1", sent_at=NOW,
        expires_at=kw.pop("expires_at", NOW + timedelta(seconds=30)), recovery_run_id="rec_1",
        withdrawn_sailing_id="ATL-101", request_items=kw.pop("items", [item("A", 2)]), **kw)


def reply(msg, sender=EXCHANGE):
    def decide_fn(items, at):
        result = decide(items, [sailing("BEA-201")])
        return result if isinstance(result, Decline) else (result, POLICY)
    return build_recovery_reply(msg, sender=sender, carrier_id="beacon", carrier_address=CARRIER.address,
                                allowed_requesters={EXCHANGE}, decide_fn=decide_fn, now=NOW)


def test_reply_is_correlated_signed_and_final():
    kind, fields = reply(request())
    assert kind == "offers"
    msg = RecoveryCapacityOffers(**fields)
    msg.signature = CARRIER.sign_digest(payload_digest(msg))
    parsed = parse_carrier_reply(msg.json())
    assert verify_model(parsed, CARRIER.address)
    assert (parsed.in_reply_to, parsed.negotiation_id, parsed.recovery_run_id) == ("m1", "rec_1", "rec_1")
    assert parsed.offers[0].sailing_id == "BEA-201" and parsed.offers[0].expires_at == NOW + timedelta(seconds=600)


def test_unauthorized_expired_and_past_deadline_are_typed_errors():
    assert reply(request(), sender=Identity.generate().address)[1]["error_code"] is ErrorCode.UNAUTHORIZED_REQUESTER
    assert reply(request(expires_at=NOW))[1]["error_code"] is ErrorCode.REQUEST_EXPIRED
    kind, fields = reply(request(items=[item("A", 1, deadline=-1)]))
    assert kind == "error" and fields["error_code"] is ErrorCode.INVALID_CARGO_REQUEST


def test_recovery_offers_cannot_be_countered():
    logged = LoggedOffer("rof_beacon_1", "rec_1", "rec_1", "BEA-201", "20GP", 2, 540000, "USD", 1,
                         NOW + timedelta(minutes=10), 0, offer_kind="recovery")
    counter = CounterOffer(message_id="m2", correlation_id="rec_1", request_id="rec_1", negotiation_id="rec_1",
                           sent_at=NOW, offer_id="rof_beacon_1", proposed_total_price_minor=500000,
                           expires_at=NOW + timedelta(seconds=30))
    kind, extra = counter_precheck(counter, sender=EXCHANGE, allowed_requesters={EXCHANGE}, logged=logged, now=NOW)
    assert kind == "reject" and extra["reason_code"] is RejectReason.COUNTER_LIMIT_REACHED

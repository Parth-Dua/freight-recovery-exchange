from datetime import datetime, timedelta, timezone

from freight_contracts import (
    CargoSpec, ErrorCode, ProposeOffer, RequestOffer, parse_carrier_reply, payload_digest, verify_model,
)
from uagents_core.identity import Identity

from carrier_agent.agent import build_reply
from carrier_agent.policy import Decline, Policy, Quote, SailingInventory

NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)
EXCHANGE = Identity.generate().address
CARRIER = Identity.generate()
POLICY = Policy("USD", 230000, 260000, 10, 600, "earliest_arrival")
SAILING = SailingInventory("ATL-101", "Atlas Meridian", "AEJEA", "INBOM",
                           NOW + timedelta(days=1), NOW + timedelta(days=3), 4, 4)


def request(**kw):
    cargo = CargoSpec(origin_port="AEJEA", destination_port="INBOM", container_type="20GP", quantity=2,
                      latest_arrival=kw.pop("latest_arrival", NOW + timedelta(days=5)))
    return RequestOffer(message_id="m1", correlation_id="c1", request_id="r1", negotiation_id="n1",
                        sent_at=NOW, expires_at=kw.pop("expires_at", NOW + timedelta(seconds=30)), cargo=cargo, **kw)


def reply(msg, sender=EXCHANGE, decide_fn=lambda c, t: (Quote(SAILING, 2, 520000), POLICY)):
    return build_reply(msg, sender=sender, carrier_id="atlas", carrier_address=CARRIER.address,
                       allowed_requesters={EXCHANGE}, decide_fn=decide_fn, now=NOW)


def test_offer_is_correlated_signed_and_verifiable():
    kind, fields = reply(request())
    assert kind == "offer"
    offer = ProposeOffer(**fields)
    offer.signature = CARRIER.sign_digest(payload_digest(offer))
    parsed = parse_carrier_reply(offer.json())
    assert parsed.in_reply_to == "m1" and parsed.request_id == "r1" and parsed.negotiation_id == "n1"
    assert parsed.expires_at == NOW + timedelta(seconds=600)
    assert verify_model(parsed, CARRIER.address)


def test_unauthorized_requester_gets_typed_error():
    kind, fields = reply(request(), sender=Identity.generate().address)
    assert kind == "error" and fields["error_code"] is ErrorCode.UNAUTHORIZED_REQUESTER


def test_expired_request_and_past_deadline_are_errors():
    assert reply(request(expires_at=NOW - timedelta(seconds=1)))[1]["error_code"] is ErrorCode.REQUEST_EXPIRED
    kind, fields = reply(request(latest_arrival=NOW - timedelta(hours=1)))
    assert fields["error_code"] is ErrorCode.INVALID_CARGO_REQUEST


def test_wrong_schema_version_is_error():
    msg = request()
    msg.schema_version = "9.9"
    assert reply(msg)[1]["error_code"] is ErrorCode.UNSUPPORTED_SCHEMA_VERSION


def test_decline_becomes_reject():
    from freight_contracts import RejectReason
    kind, fields = reply(request(), decide_fn=lambda c, t: Decline(RejectReason.INSUFFICIENT_CAPACITY, "full"))
    assert kind == "reject" and fields["reason_code"] is RejectReason.INSUFFICIENT_CAPACITY

"""AT-02-03 at unit level: typed errors for every failure path (real ACP run is in integration)."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from freight_contracts import (
    AgentError, CargoSpec, ErrorCode, ProposeOffer, RejectOffer, RejectReason, payload_digest,
)
from uagents_core.identity import Identity

from freight_exchange.carrier_client import CarrierClient, CarrierRef, TransportError, build_request

NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)
CARRIER_ID = Identity.generate()
CARRIER = CarrierRef("atlas", "Atlas", CARRIER_ID.address, "http://127.0.0.1:1/submit")
CARGO = CargoSpec(origin_port="AEJEA", destination_port="INBOM", container_type="20GP", quantity=2,
                  latest_arrival=NOW + timedelta(days=5), max_total_price_minor=560000)


def signed(model, identity=CARRIER_ID):
    model.signature = identity.sign_digest(payload_digest(model))
    return model.json()


def offer_for(req, identity=CARRIER_ID, **overrides):
    fields = dict(message_id="m2", correlation_id=req.correlation_id, request_id=req.request_id,
                  negotiation_id=req.negotiation_id, sent_at=NOW, carrier_id="atlas",
                  carrier_address=identity.address, in_reply_to=req.message_id, offer_id="o1",
                  sailing_id="ATL-101", vessel_name="Atlas Meridian", origin_port="AEJEA",
                  destination_port="INBOM", container_type="20GP", quantity=2, total_price_minor=520000,
                  currency="USD", departure_at=NOW + timedelta(days=1), arrival_at=NOW + timedelta(days=3),
                  expires_at=NOW + timedelta(minutes=10))
    fields.update(overrides)
    return ProposeOffer(**fields)


class FakeTransport:
    def __init__(self, respond):
        self.respond = respond
        self.seen = None

    async def request(self, carrier, message, sender, timeout_s):
        self.seen = message
        return await self.respond(message)


def run(respond, timeout_s=8.0):
    transport = FakeTransport(respond)
    client = CarrierClient(Identity.generate(), transport, timeout_s=timeout_s, clock=lambda: NOW)
    req = build_request(CARGO, request_id="req_1", correlation_id="flow_1", now=NOW, timeout_s=timeout_s)
    return asyncio.run(client.request_one(CARRIER, req)), req


def reply_with(build):
    async def respond(req):
        return build(req), "almanac"
    return respond


def test_valid_signed_offer_is_accepted():
    out, _ = run(reply_with(lambda req: signed(offer_for(req))))
    assert out.kind == "offer" and out.signature_verified and out.resolution == "almanac"


def test_reject_and_carrier_error_are_typed():
    def reject(req):
        return signed(RejectOffer(message_id="m2", correlation_id=req.correlation_id, request_id=req.request_id,
                                  negotiation_id=req.negotiation_id, sent_at=NOW, carrier_id="atlas",
                                  carrier_address=CARRIER_ID.address, in_reply_to=req.message_id,
                                  reason_code=RejectReason.INSUFFICIENT_CAPACITY, reason="full"))
    out, _ = run(reply_with(reject))
    assert out.kind == "reject" and out.reply.reason_code is RejectReason.INSUFFICIENT_CAPACITY

    def error(req):
        return signed(AgentError(message_id="m2", correlation_id=req.correlation_id, request_id=req.request_id,
                                 negotiation_id=req.negotiation_id, sent_at=NOW, carrier_id="atlas",
                                 carrier_address=CARRIER_ID.address, in_reply_to=req.message_id,
                                 error_code=ErrorCode.INVALID_CARGO_REQUEST, detail="bad"))
    out, _ = run(reply_with(error))
    assert out.kind == "carrier_error" and out.error_code is ErrorCode.INVALID_CARGO_REQUEST


@pytest.mark.parametrize("build, code", [
    (lambda req: "not json", ErrorCode.MALFORMED_REPLY),
    (lambda req: '{"message_type": "PROPOSE_OFFER"}', ErrorCode.MALFORMED_REPLY),
    (lambda req: signed(offer_for(req, carrier_id="beacon")), ErrorCode.UNKNOWN_CARRIER),
    (lambda req: signed(offer_for(req, identity=Identity.generate())), ErrorCode.UNKNOWN_CARRIER),
    (lambda req: signed(offer_for(req), identity=Identity.generate()), ErrorCode.INVALID_SIGNATURE),
    (lambda req: offer_for(req).json(), ErrorCode.INVALID_SIGNATURE),
    (lambda req: signed(offer_for(req, in_reply_to="other")), ErrorCode.CORRELATION_MISMATCH),
    (lambda req: signed(offer_for(req, expires_at=NOW - timedelta(seconds=1))), ErrorCode.STALE_OFFER),
    (lambda req: signed(offer_for(req, total_price_minor=600000)), ErrorCode.OFFER_CONSTRAINT_VIOLATION),
    (lambda req: signed(offer_for(req, arrival_at=NOW + timedelta(days=9))), ErrorCode.OFFER_CONSTRAINT_VIOLATION),
    (lambda req: signed(offer_for(req, quantity=1)), ErrorCode.OFFER_CONSTRAINT_VIOLATION),
    (lambda req: signed(offer_for(req, destination_port="USLAX")), ErrorCode.OFFER_CONSTRAINT_VIOLATION),
])
def test_invalid_replies_become_typed_exchange_errors(build, code):
    out, _ = run(reply_with(build))
    assert out.kind == "exchange_error" and out.error_code is code


def test_tampered_signed_offer_fails_signature():
    def tampered(req):
        offer = offer_for(req)
        offer.signature = CARRIER_ID.sign_digest(payload_digest(offer))
        offer.total_price_minor = 100  # changed after signing
        return offer.json()
    out, _ = run(reply_with(tampered))
    assert out.error_code is ErrorCode.INVALID_SIGNATURE


def test_transport_errors_and_timeouts():
    async def unreachable(req):
        raise TransportError(ErrorCode.CARRIER_UNREACHABLE, "ConnectError")
    out, _ = run(unreachable)
    assert out.kind == "exchange_error" and out.error_code is ErrorCode.CARRIER_UNREACHABLE

    async def hang(req):
        await asyncio.sleep(60)
    out, _ = run(hang, timeout_s=0.05)
    assert out.error_code is ErrorCode.CARRIER_TIMEOUT


def test_parallel_fan_out_gives_each_carrier_its_own_negotiation():
    seen = []

    class Recording(FakeTransport):
        async def request(self, carrier, message, sender, timeout_s):
            seen.append(message.negotiation_id)
            return signed(offer_for(message)), "registry_fallback"

    client = CarrierClient(Identity.generate(), Recording(None), clock=lambda: NOW)
    outs = asyncio.run(client.request_offers([CARRIER, CARRIER], CARGO, request_id="req_1", correlation_id="f"))
    assert len(set(seen)) == 2 and all(o.kind == "offer" for o in outs)



# ---- COUNTER_OFFER replies (AT-03-03 exchange side) ---------------------------------------
from freight_contracts import AcceptCounter, RejectCounter, RevisedOffer  # noqa: E402
from freight_contracts.messages import RejectReason as RR  # noqa: E402


def counter_round(build, proposed=494000):
    req = build_request(CARGO, request_id="req_1", correlation_id="flow_1", now=NOW, timeout_s=8)
    opening = offer_for(req)
    opening.signature = CARRIER_ID.sign_digest(payload_digest(opening))

    async def respond(counter):
        return build(counter, opening), "almanac"

    client = CarrierClient(Identity.generate(), FakeTransport(respond), clock=lambda: NOW)
    return asyncio.run(client.counter_one(CARRIER, opening, req, proposed))


def new_offer(cls, counter, opening, **overrides):
    fields = opening.dict(exclude={"message_type", "signature"})
    fields.update(message_id="m4", in_reply_to=counter.message_id, offer_id="o2", round=2,
                  supersedes_offer_id=opening.offer_id, total_price_minor=counter.proposed_total_price_minor,
                  expires_at=NOW + timedelta(minutes=10))
    fields.update(overrides)
    return signed(cls(**fields))


def test_accepted_counter_is_a_new_signed_offer_at_the_counter_price():
    out = counter_round(lambda c, o: new_offer(AcceptCounter, c, o))
    assert out.kind == "accept" and out.new_offer.total_price_minor == 494000 and out.signature_verified


def test_revised_offer_between_counter_and_opening():
    out = counter_round(lambda c, o: new_offer(RevisedOffer, c, o, total_price_minor=505000))
    assert out.kind == "revise" and out.new_offer.total_price_minor == 505000


def test_rejected_counter_keeps_opening():
    def reject(c, o):
        return signed(RejectCounter(message_id="m4", correlation_id=c.correlation_id, request_id=c.request_id,
                                    negotiation_id=c.negotiation_id, sent_at=NOW, carrier_id="atlas",
                                    carrier_address=CARRIER_ID.address, in_reply_to=c.message_id,
                                    offer_id=o.offer_id, reason_code=RR.BELOW_POLICY, reason="too low"))
    out = counter_round(reject)
    assert out.kind == "reject" and out.new_offer is None


@pytest.mark.parametrize("build, detail", [
    (lambda c, o: new_offer(AcceptCounter, c, o, total_price_minor=500000), "accepted price differs"),
    (lambda c, o: new_offer(RevisedOffer, c, o, total_price_minor=530000), "above the opening offer"),
    (lambda c, o: new_offer(RevisedOffer, c, o, total_price_minor=480000), "below counter"),
    (lambda c, o: new_offer(AcceptCounter, c, o, sailing_id="ATL-102"), "terms changed: sailing_id"),
    (lambda c, o: new_offer(AcceptCounter, c, o, quantity=1), "terms changed: quantity"),
    (lambda c, o: new_offer(AcceptCounter, c, o, supersedes_offer_id="other"), "supersedes a different offer"),
    (lambda c, o: new_offer(AcceptCounter, c, o, expires_at=NOW - timedelta(seconds=1)), "already expired"),
    (lambda c, o: new_offer(AcceptCounter, c, o, in_reply_to="x"), "does not answer this counter"),
    (lambda c, o: signed(o), "unexpected PROPOSE_OFFER"),
])
def test_invalid_counter_replies_are_typed_errors(build, detail):
    out = counter_round(build)
    assert out.kind == "exchange_error" and detail in out.detail and out.new_offer is None

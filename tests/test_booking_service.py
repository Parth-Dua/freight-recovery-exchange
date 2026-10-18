"""BookingService unit tests with fakes (NOT Stripe/ACP/Tiger evidence; live runs are scripted).

Covers spec 04 failure paths: forged payment claims, hold rejection, paid-but-expired hold,
failed checkout, idempotent re-checks, and that the receipt only follows verified payment +
carrier confirmation.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from freight_exchange.booking import BookingService, ConfirmResult, HoldResult, claim_code
from freight_exchange.payments import CheckoutSession, Verification

NOW = datetime.now(timezone.utc)
OFFER = {"offer_id": "off_beacon_1", "request_id": "req_1", "carrier_id": "beacon", "display_name": "Beacon",
         "agent_address": "agent1qbeacon", "endpoint_url": None, "sailing_id": "BEA-201", "quantity": 2,
         "container_type": "20GP", "total_price_minor": 528000, "currency": "USD",
         "departure_at": NOW + timedelta(days=1), "arrival_at": NOW + timedelta(days=3),
         "expires_at": NOW + timedelta(minutes=8), "origin_port": "AEJEA", "destination_port": "INBOM",
         "negotiation_id": "neg_1", "correlation_id": "flow_1"}


class FakeRepo:
    def __init__(self, offer=OFFER):
        self.offer = dict(offer) if offer else None
        self.intents = {}
        self.payment_checks = []

    async def latest_request(self, session_id):
        return {"request_id": "req_1"}

    async def selected_offer(self, request_id):
        return self.offer

    def _view(self, intent_id):
        i = self.intents[intent_id]
        return {**OFFER, **i, "payment_reference": i.get("session"), "checkout_url": i.get("url"),
                "payment_terms": "prepaid", "max_additional_cost_minor": i.get("allowance", 0)}

    async def live_intent(self, session_id):
        live = [k for k, v in self.intents.items() if v["status"] not in ("HOLD_REJECTED", "PAYMENT_FAILED",
                                                                          "RELEASED", "EXPIRED")]
        return self._view(live[-1]) if live else None

    async def create_intent(self, *, intent_id, max_additional_cost_minor=0, **kw):
        self.intents[intent_id] = {"intent_id": intent_id, "status": "HOLD_REQUESTED", "hold_id": None,
                                   "payment_id": None, "booking_id": None, "allowance": max_additional_cost_minor,
                                   **{k: kw[k] for k in ("freight_minor", "fee_minor", "currency", "request_id",
                                                          "carrier_id")}}
        return self._view(intent_id)

    async def update_intent(self, intent_id, status, *, detail=None):
        self.intents[intent_id].update(status=status, detail=detail)

    async def record_checkout(self, intent_id, *, hold_id, hold_expires_at, session, freight_minor, fee_minor):
        self.intents[intent_id].update(status="PAYMENT_PENDING", hold_id=hold_id, hold_expires_at=hold_expires_at,
                                       payment_id="pay_1", session=session.session_id, url=session.url)
        return self._view(intent_id)

    async def record_payment_check(self, payment_id, check):
        self.payment_checks.append(check.status)

    async def complete_booking(self, intent_id, booking_id):
        self.intents[intent_id].update(status="BOOKING_CONFIRMED", booking_id=booking_id)
        return self._view(intent_id)


class FakeHolds:
    def __init__(self, hold_ok=True, confirm=None):
        self.hold_ok, self.confirm = hold_ok, confirm
        self.calls = []

    async def request_hold(self, carrier, **kw):
        self.calls.append(("hold", kw))
        if not self.hold_ok:
            return HoldResult(False, "INSUFFICIENT_CAPACITY", "only 1 unit(s) free right now")
        return HoldResult(True, "HOLD_CONFIRMED", hold_id="hold_1", expires_at=NOW + timedelta(minutes=32))

    async def release_hold(self, carrier, **kw):
        self.calls.append(("release", kw))
        return HoldResult(True, "HOLD_RELEASED")

    async def confirm_booking(self, carrier, **kw):
        self.calls.append(("confirm", kw))
        return self.confirm or ConfirmResult(True, "BOOKING_CONFIRMED", booking_id="BK-TEST01", confirmed_at=NOW)


class FakePayments:
    def __init__(self, status="PAYMENT_PENDING"):
        self.status = status
        self.created, self.expired = [], []

    async def create_checkout(self, **kw):
        self.created.append(kw)
        return CheckoutSession("cs_test_1", "https://checkout.stripe.com/c/pay/cs_test_1", kw["expires_at"],
                               kw["freight_minor"] + kw["fee_minor"])

    async def verify(self, session_id, expected):
        return Verification(self.status, "fake", "fake")

    async def expire(self, session_id):
        self.expired.append(session_id)


def service(repo=None, holds=None, payments=None):
    return BookingService(repo or FakeRepo(), holds or FakeHolds(), payments or FakePayments(), b"secret")


def run(coro):
    return asyncio.run(coro)


def test_book_places_hold_then_two_line_test_checkout():
    pay, holds = FakePayments(), FakeHolds()
    step = run(service(holds=holds, payments=pay).start(session_id="s", buyer_id="b", max_additional_cost_minor=30000))
    assert step.kind == "checkout" and step.checkout_url.startswith("https://checkout.stripe.com/")
    assert holds.calls[0][1]["idempotency_key"].startswith("hold-intent_") and holds.calls[0][1]["quantity"] == 2
    created = pay.created[0]
    assert (created["freight_minor"], created["fee_minor"]) == (528000, 2500)
    assert created["expires_at"] >= NOW + timedelta(minutes=30)  # Stripe minimum
    assert step.intent["max_additional_cost_minor"] == 30000


def test_no_selection_and_expired_offer_never_hold():
    holds = FakeHolds()
    assert run(service(repo=FakeRepo(offer=None), holds=holds).start(session_id="s", buyer_id="b")).kind == "no_selection"
    expired = {**OFFER, "expires_at": NOW - timedelta(seconds=1)}
    assert run(service(repo=FakeRepo(offer=expired), holds=holds).start(session_id="s", buyer_id="b")).kind == "offer_expired"
    assert holds.calls == []


def test_hold_rejected_creates_no_checkout():
    pay = FakePayments()
    step = run(service(holds=FakeHolds(hold_ok=False), payments=pay).start(session_id="s", buyer_id="b"))
    assert step.kind == "hold_rejected" and "INSUFFICIENT_CAPACITY" in step.detail and pay.created == []


def test_repeated_book_reuses_the_same_checkout():
    repo, pay = FakeRepo(), FakePayments()
    svc = service(repo=repo, payments=pay)
    first = run(svc.start(session_id="s", buyer_id="b"))
    again = run(svc.start(session_id="s", buyer_id="b"))
    assert again.kind == "checkout" and again.checkout_url == first.checkout_url and len(pay.created) == 1


def test_at_04_03_forged_paid_claim_does_not_book():
    repo, holds = FakeRepo(), FakeHolds()
    svc = service(repo=repo, holds=holds, payments=FakePayments("PAYMENT_PENDING"))
    run(svc.start(session_id="s", buyer_id="b"))
    step = run(svc.check_payment(session_id="s"))  # buyer says "paid" but Stripe says unpaid
    assert step.kind == "pending" and not any(c[0] == "confirm" for c in holds.calls)
    assert all(i["status"] != "BOOKING_CONFIRMED" for i in repo.intents.values())


def test_verified_payment_then_carrier_confirmation_gives_receipt_once():
    repo, holds = FakeRepo(), FakeHolds()
    svc = service(repo=repo, holds=holds, payments=FakePayments("PAYMENT_VERIFIED_TEST"))
    run(svc.start(session_id="s", buyer_id="b"))
    step = run(svc.check_payment(session_id="s"))
    assert step.kind == "confirmed" and step.booking_id == "BK-TEST01"
    intent_id = next(iter(repo.intents))
    assert step.claim_code == claim_code(b"secret", intent_id)
    confirm = [c for c in holds.calls if c[0] == "confirm"][0][1]
    assert confirm["idempotency_key"] == f"confirm-{intent_id}" and confirm["payment_reference"] == "cs_test_1"
    again = run(svc.check_payment(session_id="s"))  # duplicate "paid": same receipt, no second confirm
    assert again.kind == "confirmed" and again.claim_code == step.claim_code
    assert sum(1 for c in holds.calls if c[0] == "confirm") == 1


def test_at_04_04_paid_but_hold_expired_is_truthful_booking_pending():
    repo = FakeRepo()
    holds = FakeHolds(confirm=ConfirmResult(False, "HOLD_EXPIRED", "hold is no longer active"))
    svc = service(repo=repo, holds=holds, payments=FakePayments("PAYMENT_VERIFIED_TEST"))
    run(svc.start(session_id="s", buyer_id="b"))
    step = run(svc.check_payment(session_id="s"))
    assert step.kind == "booking_pending" and "No second charge" in step.detail
    assert next(iter(repo.intents.values()))["status"] == "BOOKING_PENDING"
    assert run(svc.check_payment(session_id="s")).kind == "booking_pending"  # stays put, no retry charge


def test_failed_checkout_releases_hold():
    holds = FakeHolds()
    svc = service(holds=holds, payments=FakePayments("PAYMENT_FAILED"))
    run(svc.start(session_id="s", buyer_id="b"))
    assert run(svc.check_payment(session_id="s")).kind == "failed"
    assert any(c[0] == "release" for c in holds.calls)


def test_reconciliation_when_stripe_session_does_not_match():
    svc = service(payments=FakePayments("PAYMENT_RECONCILIATION_REQUIRED"))
    run(svc.start(session_id="s", buyer_id="b"))
    assert run(svc.check_payment(session_id="s")).kind == "reconciliation"


def test_cancel_releases_and_expires_but_never_cancels_a_paid_checkout():
    holds, pay = FakeHolds(), FakePayments("PAYMENT_PENDING")
    svc = service(holds=holds, payments=pay)
    run(svc.start(session_id="s", buyer_id="b"))
    assert run(svc.cancel(session_id="s")).kind == "cancelled"
    assert pay.expired == ["cs_test_1"] and any(c[0] == "release" for c in holds.calls)

    holds2, pay2 = FakeHolds(), FakePayments("PAYMENT_VERIFIED_TEST")
    svc2 = service(holds=holds2, payments=pay2)
    run(svc2.start(session_id="s", buyer_id="b"))
    assert run(svc2.cancel(session_id="s")).kind == "confirmed" and pay2.expired == []

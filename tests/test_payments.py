"""Stripe adapter unit tests with a fake client (NOT Stripe evidence; live run is scripted)."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from freight_exchange.payments import (
    PAYMENT_FAILED, PAYMENT_PENDING, PAYMENT_RECONCILIATION_REQUIRED, PAYMENT_VERIFIED_TEST, Expected,
    PaymentConfigError, StripeTestPayments, classify_session,
)

EXP = Expected("intent_1", 530500, "USD")


def session(**kw):
    base = {"id": "cs_test_1", "status": "complete", "payment_status": "paid", "livemode": False,
            "metadata": {"intent_id": "intent_1"}, "client_reference_id": "intent_1", "amount_total": 530500,
            "currency": "usd", "payment_intent": "pi_test_1"}
    return {**base, **kw}


@pytest.mark.parametrize("overrides, status", [
    ({}, PAYMENT_VERIFIED_TEST),
    ({"status": "open", "payment_status": "unpaid"}, PAYMENT_PENDING),
    ({"status": "complete", "payment_status": "unpaid"}, PAYMENT_PENDING),
    ({"status": "expired", "payment_status": "unpaid"}, PAYMENT_FAILED),
    ({"livemode": True}, PAYMENT_RECONCILIATION_REQUIRED),
    ({"amount_total": 1}, PAYMENT_RECONCILIATION_REQUIRED),
    ({"currency": "eur"}, PAYMENT_RECONCILIATION_REQUIRED),
    ({"metadata": {"intent_id": "other"}}, PAYMENT_RECONCILIATION_REQUIRED),
    ({"client_reference_id": "other"}, PAYMENT_RECONCILIATION_REQUIRED),
])
def test_classify_session(overrides, status):
    assert classify_session(session(**overrides), EXP).status == status


@pytest.mark.parametrize("key", ["", "sk_live_abc", "rk_live_abc", "pk_test_abc"])
def test_only_test_secret_keys_are_accepted(key):
    with pytest.raises(PaymentConfigError):
        StripeTestPayments(secret_key=key)


class FakeSessions:
    def __init__(self):
        self.created = []

    async def create_async(self, params, options):
        self.created.append((params, options))
        return {"id": "cs_test_9", "url": "https://checkout.stripe.com/c/pay/cs_test_9", "livemode": False,
                "expires_at": params["expires_at"], "amount_total": sum(i["price_data"]["unit_amount"]
                                                                         for i in params["line_items"])}

    async def retrieve_async(self, session_id):
        return session(id=session_id)


class FakeClient:
    def __init__(self):
        self.v1 = type("V1", (), {})()
        self.v1.checkout = type("C", (), {})()
        self.v1.checkout.sessions = FakeSessions()


def test_checkout_has_freight_and_fee_lines_and_idempotency_key():
    client = FakeClient()
    pay = StripeTestPayments(client=client)
    expires = datetime.now(timezone.utc) + timedelta(minutes=31)
    cs = asyncio.run(pay.create_checkout(intent_id="intent_1", request_id="req_1", offer_id="off_1",
                                         freight_minor=528000, fee_minor=2500, currency="USD",
                                         freight_label="Beacon BEA-201, 2x20GP", expires_at=expires))
    params, options = client.v1.checkout.sessions.created[0]
    names = [i["price_data"]["product_data"]["name"] for i in params["line_items"]]
    assert names == ["Simulated ocean freight (TEST)", "Booking service fee (TEST)"]
    assert cs.amount_total == 530500 and params["submit_type"] == "book"
    assert params["metadata"]["intent_id"] == params["client_reference_id"] == "intent_1"
    assert options == {"idempotency_key": "checkout-intent_1"}
    assert params["line_items"][0]["price_data"]["currency"] == "usd"
    assert "say \"paid\"" in params["custom_text"]["after_submit"]["message"]

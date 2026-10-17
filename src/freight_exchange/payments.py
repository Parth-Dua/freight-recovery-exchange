"""Stripe TEST-mode checkout for bookings (decision 0005, spec 04 §5).

Two line items: simulated ocean freight + booking service fee. Only `sk_test_` keys are
accepted, and any session reporting livemode=true is rejected. Verification is always a
server-side retrieve; chat claims ("I paid") and redirects are never proof (AT-04-03).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

PAYMENT_PENDING = "PAYMENT_PENDING"
PAYMENT_VERIFIED_TEST = "PAYMENT_VERIFIED_TEST"
PAYMENT_FAILED = "PAYMENT_FAILED"
PAYMENT_RECONCILIATION_REQUIRED = "PAYMENT_RECONCILIATION_REQUIRED"


class PaymentConfigError(RuntimeError):
    pass


def booking_fee_minor() -> int:
    return int(os.getenv("BOOKING_FEE_MINOR", "2500"))


@dataclass(frozen=True)
class CheckoutSession:
    session_id: str
    url: str
    expires_at: datetime
    amount_total: int


@dataclass(frozen=True)
class Expected:
    intent_id: str
    amount_total: int
    currency: str


@dataclass(frozen=True)
class Verification:
    status: str  # one of the PAYMENT_* constants
    detail: str
    provider_status: str
    livemode: bool | None = None
    payment_intent: str | None = None


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    try:
        return obj[key]
    except (KeyError, TypeError):
        return getattr(obj, key, default)


def classify_session(session: Any, expected: Expected) -> Verification:
    """Pure check of a retrieved Checkout Session against what we asked for."""
    status, paid = _get(session, "status"), _get(session, "payment_status")
    livemode = bool(_get(session, "livemode"))
    provider = f"{status}/{paid}"
    metadata = _get(session, "metadata") or {}
    if livemode:
        return Verification(PAYMENT_RECONCILIATION_REQUIRED, "live-mode session rejected: test mode only", provider, True)
    if _get(metadata, "intent_id") != expected.intent_id or _get(session, "client_reference_id") != expected.intent_id:
        return Verification(PAYMENT_RECONCILIATION_REQUIRED, "session does not belong to this booking", provider, False)
    if _get(session, "amount_total") != expected.amount_total or (_get(session, "currency") or "").upper() != expected.currency:
        return Verification(PAYMENT_RECONCILIATION_REQUIRED, "amount or currency mismatch", provider, False)
    if status == "complete" and paid == "paid":
        return Verification(PAYMENT_VERIFIED_TEST, "Stripe test payment verified", provider, False,
                            _get(session, "payment_intent"))
    if status == "expired":
        return Verification(PAYMENT_FAILED, "checkout expired without payment", provider, False)
    return Verification(PAYMENT_PENDING, "payment not completed yet", provider, False)


class StripeTestPayments:
    def __init__(self, secret_key: str | None = None, client: Any = None):
        key = secret_key if secret_key is not None else os.getenv("STRIPE_SECRET_KEY", "")
        if client is None:
            if not key.startswith("sk_test_"):
                raise PaymentConfigError("STRIPE_SECRET_KEY must be a Stripe TEST key (sk_test_…); refusing to run")
            import stripe

            client = stripe.StripeClient(key, http_client=stripe.HTTPXClient())
        self._sessions = client.v1.checkout.sessions
        self.success_url = os.getenv("STRIPE_SUCCESS_URL", "https://asi1.ai/")
        self.cancel_url = os.getenv("STRIPE_CANCEL_URL", "https://asi1.ai/")

    async def create_checkout(
        self, *, intent_id: str, request_id: str, offer_id: str, freight_minor: int, fee_minor: int,
        currency: str, freight_label: str, expires_at: datetime,
    ) -> CheckoutSession:
        line = lambda name, desc, amount: {  # noqa: E731
            "price_data": {"currency": currency.lower(), "unit_amount": amount,
                           "product_data": {"name": name, "description": desc}},
            "quantity": 1,
        }
        items = [line("Simulated ocean freight (TEST)", freight_label, freight_minor)]
        if fee_minor:
            items.append(line("Booking service fee (TEST)", "Freight Recovery Exchange platform fee", fee_minor))
        metadata = {"intent_id": intent_id, "request_id": request_id, "offer_id": offer_id, "environment": "test"}
        session = await self._sessions.create_async(
            params={
                "mode": "payment",
                "submit_type": "book",
                "line_items": items,
                "client_reference_id": intent_id,
                "metadata": metadata,
                "payment_intent_data": {"metadata": metadata},
                "expires_at": int(expires_at.timestamp()),
                "success_url": self.success_url,
                "cancel_url": self.cancel_url,
            },
            options={"idempotency_key": f"checkout-{intent_id}"},
        )
        if _get(session, "livemode"):
            raise PaymentConfigError("Stripe returned a live-mode session; refusing")
        return CheckoutSession(_get(session, "id"), _get(session, "url"),
                               datetime.fromtimestamp(_get(session, "expires_at"), timezone.utc),
                               _get(session, "amount_total"))

    async def verify(self, session_id: str, expected: Expected) -> Verification:
        return classify_session(await self._sessions.retrieve_async(session_id), expected)

    async def expire(self, session_id: str) -> None:
        try:
            await self._sessions.expire_async(session_id)
        except Exception:  # already completed/expired: nothing to do
            pass

"""BookingService: selected offer → carrier hold → Stripe TEST checkout → verified payment →
carrier confirmation → receipt (spec 04, decision 0005).

Carrier inventory changes happen only through `HoldClient` (carrier-owned, over ACP).
Payment truth comes only from Stripe via `StripeTestPayments.verify` — never from chat.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Protocol
from uuid import uuid4

from freight_exchange.carrier_client import CarrierRef
from freight_exchange.payments import (
    PAYMENT_FAILED, PAYMENT_PENDING, PAYMENT_RECONCILIATION_REQUIRED, PAYMENT_VERIFIED_TEST, Expected,
    StripeTestPayments, booking_fee_minor,
)

STRIPE_MIN_EXPIRY = timedelta(minutes=30, seconds=45)  # Stripe: 30 min minimum, plus clock margin


def hold_ttl_seconds() -> int:
    # A little over Stripe's 30-minute minimum so the checkout never outlives the hold by much.
    return int(os.getenv("HOLD_TTL_SECONDS", "1920"))


@dataclass(frozen=True)
class HoldResult:
    ok: bool
    code: str
    detail: str = ""
    hold_id: str | None = None
    expires_at: datetime | None = None


@dataclass(frozen=True)
class ConfirmResult:
    ok: bool
    code: str
    detail: str = ""
    booking_id: str | None = None
    confirmed_at: datetime | None = None


class HoldClient(Protocol):
    """Carrier-owned inventory actions (REQUEST_HOLD / RELEASE_HOLD / CONFIRM_BOOKING over ACP)."""

    async def request_hold(self, carrier: CarrierRef, *, offer_id: str, request_id: str, buyer_id: str,
                           quantity: int, idempotency_key: str, ttl_seconds: int) -> HoldResult: ...

    async def release_hold(self, carrier: CarrierRef, *, hold_id: str) -> HoldResult: ...

    async def confirm_booking(self, carrier: CarrierRef, *, hold_id: str, payment_reference: str,
                              idempotency_key: str, intent_id: str, claim_code_hash: str) -> ConfirmResult: ...


@dataclass
class BookingStep:
    kind: str  # checkout | no_selection | offer_expired | hold_rejected | pending | confirmed | failed
               # | reconciliation | booking_pending | cancelled | nothing_to_cancel | no_intent
    intent: dict = field(default_factory=dict)
    offer: dict = field(default_factory=dict)
    checkout_url: str | None = None
    booking_id: str | None = None
    claim_code: str | None = None
    detail: str = ""


_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def claim_code(secret: bytes, intent_id: str) -> str:
    """Deterministic per intent (so duplicate deliveries show the same code); never stored in clear."""
    digest = hmac.new(secret, intent_id.encode(), hashlib.sha256).digest()
    return "".join(_ALPHABET[b % len(_ALPHABET)] for b in digest[:6])


def claim_hash(booking_or_intent: str, code: str) -> str:
    return hashlib.sha256(f"{booking_or_intent}:{code}".encode()).hexdigest()


class BookingService:
    def __init__(self, repo, holds: HoldClient, payments: StripeTestPayments, claim_secret: bytes):
        self.repo = repo
        self.holds = holds
        self.payments = payments
        self.claim_secret = claim_secret

    async def start(self, *, session_id: str, buyer_id: str) -> BookingStep:
        latest = await self.repo.latest_request(session_id)
        offer = await self.repo.selected_offer(latest["request_id"]) if latest else None
        if offer is None:
            return BookingStep("no_selection")
        live = await self.repo.live_intent(session_id)
        if live is not None and live["request_id"] == offer["request_id"]:
            return await self._resume(live)
        if offer["expires_at"] <= datetime.now(timezone.utc):
            return BookingStep("offer_expired", offer=offer)

        carrier = CarrierRef(offer["carrier_id"], offer["display_name"], offer["agent_address"], offer["endpoint_url"])
        fee = booking_fee_minor()
        intent_id = f"intent_{uuid4().hex[:16]}"
        intent = await self.repo.create_intent(
            intent_id=intent_id, request_id=offer["request_id"], offer_id=offer["offer_id"], buyer_id=buyer_id,
            session_id=session_id, carrier_id=offer["carrier_id"], freight_minor=offer["total_price_minor"],
            fee_minor=fee, currency=offer["currency"])
        if intent["intent_id"] != intent_id:  # a concurrent duplicate already created the live intent
            return await self._resume(intent)

        hold = await self.holds.request_hold(
            carrier, offer_id=offer["offer_id"], request_id=offer["request_id"], buyer_id=buyer_id,
            quantity=offer["quantity"], idempotency_key=f"hold-{intent_id}", ttl_seconds=hold_ttl_seconds())
        if not hold.ok:
            await self.repo.update_intent(intent_id, "HOLD_REJECTED", detail=f"{hold.code}: {hold.detail}")
            return BookingStep("hold_rejected", offer=offer, detail=f"{hold.code}: {hold.detail}")

        expires_at = max(hold.expires_at, datetime.now(timezone.utc) + STRIPE_MIN_EXPIRY)
        label = (f"{offer['display_name']} sailing {offer['sailing_id']}, {offer['quantity']}x{offer['container_type']} "
                 f"{offer['origin_port']}->{offer['destination_port']} (simulated freight)")
        session = await self.payments.create_checkout(
            intent_id=intent_id, request_id=offer["request_id"], offer_id=offer["offer_id"],
            freight_minor=offer["total_price_minor"], fee_minor=fee, currency=offer["currency"],
            freight_label=label, expires_at=expires_at)
        intent = await self.repo.record_checkout(intent_id, hold_id=hold.hold_id, hold_expires_at=hold.expires_at,
                                                 session=session, freight_minor=offer["total_price_minor"], fee_minor=fee)
        return BookingStep("checkout", intent=intent, offer=offer, checkout_url=session.url)

    async def _resume(self, intent: dict) -> BookingStep:
        if intent["status"] == "PAYMENT_PENDING":
            return BookingStep("checkout", intent=intent, checkout_url=intent.get("checkout_url"))
        if intent["status"] == "BOOKING_CONFIRMED":
            return BookingStep("confirmed", intent=intent, booking_id=intent["booking_id"],
                               claim_code=claim_code(self.claim_secret, intent["intent_id"]))
        return BookingStep("reconciliation" if intent["status"] == PAYMENT_RECONCILIATION_REQUIRED else "pending",
                           intent=intent, checkout_url=intent.get("checkout_url"))

    async def check_payment(self, *, session_id: str) -> BookingStep:
        intent = await self.repo.live_intent(session_id)
        if intent is None:
            return BookingStep("no_intent")
        if intent["status"] == "BOOKING_CONFIRMED":
            return await self._resume(intent)
        if intent["status"] in (PAYMENT_RECONCILIATION_REQUIRED, "BOOKING_PENDING"):
            return BookingStep("reconciliation" if intent["status"] == PAYMENT_RECONCILIATION_REQUIRED
                               else "booking_pending", intent=intent, detail=intent.get("detail") or "")

        expected = Expected(intent["intent_id"], intent["freight_minor"] + intent["fee_minor"], intent["currency"])
        check = await self.payments.verify(intent["payment_reference"], expected)
        await self.repo.record_payment_check(intent["payment_id"], check)
        if check.status == PAYMENT_PENDING:
            return BookingStep("pending", intent=intent, checkout_url=intent.get("checkout_url"), detail=check.detail)
        if check.status == PAYMENT_FAILED:
            await self._release(intent)
            await self.repo.update_intent(intent["intent_id"], PAYMENT_FAILED, detail=check.detail)
            return BookingStep("failed", intent=intent, detail=check.detail)
        if check.status == PAYMENT_RECONCILIATION_REQUIRED:
            await self.repo.update_intent(intent["intent_id"], PAYMENT_RECONCILIATION_REQUIRED, detail=check.detail)
            return BookingStep("reconciliation", intent=intent, detail=check.detail)

        # PAYMENT_VERIFIED_TEST → only now ask the carrier to commit capacity
        await self.repo.update_intent(intent["intent_id"], PAYMENT_VERIFIED_TEST, detail=check.detail)
        code = claim_code(self.claim_secret, intent["intent_id"])
        carrier = CarrierRef(intent["carrier_id"], intent["display_name"], intent["agent_address"],
                             intent["endpoint_url"])
        confirm = await self.holds.confirm_booking(
            carrier, hold_id=intent["hold_id"], payment_reference=intent["payment_reference"],
            idempotency_key=f"confirm-{intent['intent_id']}", intent_id=intent["intent_id"],
            claim_code_hash=claim_hash(intent["intent_id"], code))
        if not confirm.ok:
            detail = (f"Stripe test payment verified, but {intent['display_name']} could not confirm capacity "
                      f"({confirm.code}). No second charge will be made; this booking needs reconciliation.")
            await self.repo.update_intent(intent["intent_id"], "BOOKING_PENDING", detail=detail)
            return BookingStep("booking_pending", intent=intent, detail=detail)
        intent = await self.repo.complete_booking(intent["intent_id"], confirm.booking_id)
        return BookingStep("confirmed", intent=intent, booking_id=confirm.booking_id, claim_code=code)

    async def cancel(self, *, session_id: str) -> BookingStep:
        intent = await self.repo.live_intent(session_id)
        if intent is None or intent["status"] != "PAYMENT_PENDING":
            return BookingStep("nothing_to_cancel", intent=intent or {})
        check = await self.payments.verify(intent["payment_reference"], Expected(
            intent["intent_id"], intent["freight_minor"] + intent["fee_minor"], intent["currency"]))
        if check.status == PAYMENT_VERIFIED_TEST:  # paid already: never cancel silently
            return await self.check_payment(session_id=session_id)
        await self.payments.expire(intent["payment_reference"])
        await self._release(intent)
        await self.repo.update_intent(intent["intent_id"], "RELEASED", detail="cancelled by buyer")
        return BookingStep("cancelled", intent=intent)

    async def _release(self, intent: dict) -> None:
        if intent.get("hold_id"):
            carrier = CarrierRef(intent["carrier_id"], intent["display_name"], intent["agent_address"],
                                 intent["endpoint_url"])
            await self.holds.release_hold(carrier, hold_id=intent["hold_id"])


def claim_secret_from(agent_uri_key: str) -> bytes:
    return hashlib.sha256(b"claim-code:" + agent_uri_key.encode()).digest()

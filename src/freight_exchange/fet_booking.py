"""Booking paid over the Fetch Payment Protocol (gateway = seller), v2 phases 2-3.

`stripe_test` (default, user decision 2026-10-18): Stripe TEST card checkout carried in the protocol.
    selected offer -> carrier hold -> payment record (REQUESTED) + two Stripe TEST Checkout sessions
    (embedded, rendered from RequestPayment.metadata["stripe"]; hosted, a fallback link in the chat)
    -> paid: CommitPayment (transaction_id = session id) or the poller / "paid" in chat
    -> server-side Stripe retrieve (paid, test mode, intent + amount match) -> VERIFIED (session id unique)
    -> carrier CONFIRM_BOOKING -> CompletePayment + receipt card. The unpaid sibling session is expired.
`fet_testnet`: tiny testnet FET fee, CommitPayment = claim, on-chain proof via the gateway (cosmpy).
`dev`: confirms without any payment and says so; regression only.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from psycopg.errors import UniqueViolation

from freight_exchange import cards
from freight_exchange.booking import claim_code, claim_hash, hold_ttl_seconds
from freight_exchange.carrier_client import CarrierRef
from freight_exchange.gateway_service import fet_amount, text, to_atestfet
from freight_exchange.observability import log_event
from freight_exchange.payments import (
    PAYMENT_PENDING, PAYMENT_VERIFIED_TEST, Expected, StripeTestPayments, booking_fee_minor, classify_session)
from freight_exchange.repository import _event
from freight_exchange.responses import money

DENOM = "atestfet"
NETWORK = "stable-testnet"
PAYMENT_DEADLINE_MARGIN = timedelta(minutes=3)  # leave time to verify and confirm before the hold lapses
MAX_PAYMENT_WINDOW = timedelta(minutes=10)
STRIPE_PAYMENT_WINDOW = timedelta(minutes=25)  # card entry takes longer; still inside the carrier hold
TEST_CARD = "4242 4242 4242 4242"
MODES = ("stripe_test", "fet_testnet", "dev")


def payment_mode() -> str:
    mode = os.getenv("PAYMENT_MODE", "stripe_test")
    if mode not in MODES:
        raise ValueError(f"PAYMENT_MODE must be one of {', '.join(MODES)}")
    if mode == "fet_testnet" and os.getenv("FET_USE_TESTNET", "true").lower() != "true":
        raise RuntimeError("FET mainnet is not allowed in the demo (FET_USE_TESTNET must be true)")
    if os.getenv("LEGACY_STRIPE_CHECKOUT_ENABLED", "false").lower() == "true" and mode != "dev":
        raise RuntimeError("Legacy Stripe checkout and the payment-protocol rail are mutually exclusive")
    return mode


def _carrier(intent: dict) -> CarrierRef:
    return CarrierRef(intent["carrier_id"], intent["display_name"], intent["agent_address"], intent["endpoint_url"])


class FetBookingService:
    def __init__(self, repo, holds, claim_secret: bytes, stripe: StripeTestPayments | None = None):
        self.repo = repo
        self.holds = holds
        self.claim_secret = claim_secret
        self._stripe = stripe

    @property
    def stripe(self) -> StripeTestPayments:
        if self._stripe is None:
            self._stripe = StripeTestPayments()  # refuses anything but an sk_test_ key
        return self._stripe

    # ---- confirm on the review card ----------------------------------------------------
    async def start(self, *, buyer_id: str, session_id: str, request_id: str, offer_id: str,
                    gateway_wallet: str) -> dict:
        mode = payment_mode()
        offer = await self.repo.selected_offer(request_id)
        async with self.repo.pool.connection() as conn:
            owner = await (await conn.execute("SELECT buyer_id, recovery_allowance_minor FROM cargo_requests "
                                              "WHERE request_id = %s", (request_id,))).fetchone()
        if owner is None or owner[0] != buyer_id or offer is None or offer["offer_id"] != offer_id:
            return text("That offer isn't your current selection, so nothing was reserved. Choose an offer first.")
        async with self.repo.pool.connection() as conn:
            live = await self.repo._intent(conn, "WHERE i.request_id = %s AND i.status = ANY(%s) "
                                                 "ORDER BY i.created_at DESC LIMIT 1", (request_id, list(self.repo._LIVE)))
        if live is not None:
            return await self._resume(live, gateway_wallet)
        if offer["expires_at"] <= datetime.now(timezone.utc):
            return text("That offer has expired, so nothing was reserved. Say 'ship cargo' to get fresh offers.")

        intent_id = f"intent_{uuid4().hex[:16]}"
        intent = await self.repo.create_intent(
            intent_id=intent_id, request_id=request_id, offer_id=offer_id, buyer_id=buyer_id, session_id=session_id,
            carrier_id=offer["carrier_id"], freight_minor=offer["total_price_minor"], fee_minor=0,
            currency=offer["currency"], max_additional_cost_minor=owner[1])
        if intent["intent_id"] != intent_id:
            return await self._resume(intent, gateway_wallet)
        async with self.repo.pool.connection() as conn:
            await conn.execute("UPDATE booking_intents SET payment_mode = %s WHERE intent_id = %s", (mode, intent_id))
        carrier = CarrierRef(offer["carrier_id"], offer["display_name"], offer["agent_address"], offer["endpoint_url"])
        hold = await self.holds.request_hold(
            carrier, offer_id=offer_id, request_id=request_id, buyer_id=buyer_id, quantity=offer["quantity"],
            idempotency_key=f"hold-{intent_id}", ttl_seconds=hold_ttl_seconds(),
            negotiation_id=offer["negotiation_id"], correlation_id=offer["correlation_id"])
        if not hold.ok:
            await self.repo.update_intent(intent_id, "HOLD_REJECTED", detail=f"{hold.code}: {hold.detail}")
            return text(f"{offer['display_name']} could not hold that space ({hold.code}); nothing was charged. "
                        "Say 'ship cargo' for fresh offers.")
        if mode == "dev":
            return await self._confirm(intent_id, payment_reference=f"dev:{intent_id}", hold_id=hold.hold_id,
                                       hold_expires_at=hold.expires_at,
                                       payment_line="Development mode: no payment was requested or taken")
        if mode == "stripe_test":
            return await self._request_stripe(intent_id, hold_id=hold.hold_id, hold_expires_at=hold.expires_at)
        return await self._request_payment(intent_id, hold_id=hold.hold_id, hold_expires_at=hold.expires_at,
                                           gateway_wallet=gateway_wallet)

    async def _request_payment(self, intent_id: str, *, hold_id: str, hold_expires_at: datetime,
                               gateway_wallet: str) -> dict:
        if not gateway_wallet.startswith("fetch1"):
            raise RuntimeError("gateway wallet missing; cannot name a payment recipient")
        amount = fet_amount()
        deadline = min(hold_expires_at - PAYMENT_DEADLINE_MARGIN, datetime.now(timezone.utc) + MAX_PAYMENT_WINDOW)
        if deadline <= datetime.now(timezone.utc) + timedelta(seconds=60):
            return text("The carrier's hold is too short to pay safely, so I did not ask for payment. Please confirm again.")
        reference, payment_id = f"fetpay_{uuid4().hex[:16]}", f"pay_{uuid4().hex[:16]}"
        async with self.repo.pool.connection() as conn, conn.transaction():
            intent = await (await conn.execute(
                "SELECT i.request_id, i.carrier_id, i.buyer_id, i.session_id, r.correlation_id FROM booking_intents i "
                "JOIN cargo_requests r USING (request_id) WHERE i.intent_id = %s FOR UPDATE", (intent_id,))).fetchone()
            await conn.execute(
                """INSERT INTO payment_records (payment_id, provider, reference, purpose, amount_minor, currency, status,
                       intent_id, method, network, funds_amount, funds_currency, sender_agent_address, chat_session_id,
                       gateway_wallet, hold_id, deadline_at, livemode, fee_minor, freight_minor, updated_at)
                   VALUES (%s, 'fetch', %s, 'booking coordination service (testnet FET)', %s, 'FET', 'REQUESTED', %s,
                           'fet_direct', %s, %s, 'FET', %s, %s, %s, %s, %s, false, 0, 0, now())""",
                (payment_id, reference, to_atestfet(amount), intent_id, NETWORK, str(amount), intent[2], intent[3],
                 gateway_wallet, hold_id, deadline))
            await conn.execute(
                """UPDATE booking_intents SET status = 'PAYMENT_PENDING', hold_id = %s, hold_expires_at = %s,
                       payment_id = %s, updated_at = now() WHERE intent_id = %s""",
                (hold_id, hold_expires_at, payment_id, intent_id))
            await _event(conn, "PAYMENT_REQUESTED", "payment", payment_id, correlation_id=intent[4], carrier_id=intent[1],
                         payload={"provider": "fetch", "method": "fet_direct", "network": NETWORK,
                                  "amount": str(amount), "currency": "FET", "hold_id": hold_id, "reference": reference})
        return self._payment_request(reference, amount, gateway_wallet, deadline)

    @staticmethod
    def _payment_request(reference: str, amount: Decimal, gateway_wallet: str, deadline: datetime) -> dict:
        seconds = max(60, int((deadline - datetime.now(timezone.utc)).total_seconds()))
        return {"kind": "PAYMENT_REQUEST",
                "narration": (f"The carrier is holding your space. Please pay the {amount} FET "
                              "booking-coordination fee to confirm."),
                "payment": {"reference": reference, "amount": str(amount), "currency": "FET", "method": "fet_direct",
                            "recipient": gateway_wallet, "deadline_seconds": seconds,
                            "description": f"Hormuz Exchange booking-coordination service: {amount} FET.",
                            "metadata": {"fet_network": NETWORK, "provider_agent_wallet": gateway_wallet,
                                         "service": "booking_coordination"}}}

    async def _resume(self, intent: dict, gateway_wallet: str) -> dict:
        if intent["status"] == "BOOKING_CONFIRMED":
            return await self._receipt(intent)
        if intent["status"] == "PAYMENT_PENDING" and (intent.get("payment_reference") or "").startswith("stripepay_"):
            row = await self._payment(intent["payment_reference"])
            if row and row["status"] in ("REQUESTED", "COMMIT_RECEIVED") and row["deadline_at"] > datetime.now(timezone.utc):
                settled = await self.check_stripe(row)
                if settled is not None:
                    return settled
                return await self._stripe_request_again(row, intent)
        if intent["status"] == "PAYMENT_PENDING" and intent.get("payment_reference", "").startswith("fetpay_"):
            async with self.repo.pool.connection() as conn:
                row = await (await conn.execute(
                    "SELECT funds_amount, gateway_wallet, deadline_at, status FROM payment_records WHERE reference = %s",
                    (intent["payment_reference"],))).fetchone()
            if row and row[3] == "REQUESTED" and row[2] > datetime.now(timezone.utc):
                return self._payment_request(intent["payment_reference"], Decimal(row[0]), row[1], row[2])
        return text(f"This booking is {intent['status'].replace('_', ' ').lower()}; nothing new was charged.")

    # ---- Stripe TEST over the payment protocol -------------------------------------------
    async def _request_stripe(self, intent_id: str, *, hold_id: str, hold_expires_at: datetime) -> dict:
        now = datetime.now(timezone.utc)
        deadline = min(hold_expires_at - PAYMENT_DEADLINE_MARGIN, now + STRIPE_PAYMENT_WINDOW)
        if deadline <= now + timedelta(seconds=120):
            await self._release(intent_id, hold_id, "carrier hold too short to pay safely")
            return text("The carrier's hold is too short to pay safely, so I released it and did not ask for "
                        "payment. Please choose the offer again.")
        intent = await self._intent(intent_id)
        fee = booking_fee_minor()
        total = intent["freight_minor"] + fee
        reference, payment_id = f"stripepay_{uuid4().hex[:16]}", f"pay_{uuid4().hex[:16]}"
        label = (f"{intent['display_name']} {intent['sailing_id']}: {intent['origin_port']} -> "
                 f"{intent['destination_port']}, {intent['quantity']}x20GP")
        common = dict(intent_id=intent_id, request_id=intent["request_id"], offer_id=intent["offer_id"],
                      freight_minor=intent["freight_minor"], fee_minor=fee, currency=intent["currency"],
                      freight_label=label, expires_at=deadline, reference=reference)
        embedded = await self.stripe.create_checkout(**common, embedded=True)
        hosted = await self.stripe.create_checkout(**common, embedded=False)
        async with self.repo.pool.connection() as conn, conn.transaction():
            await conn.execute(
                """INSERT INTO payment_records (payment_id, provider, reference, purpose, amount_minor, currency, status,
                       intent_id, method, network, funds_amount, funds_currency, sender_agent_address, chat_session_id,
                       hold_id, deadline_at, livemode, fee_minor, freight_minor, checkout_url, stripe_session_ids,
                       updated_at)
                   VALUES (%s, 'stripe', %s, 'simulated freight + booking service fee (Stripe TEST)', %s, %s,
                           'REQUESTED', %s, 'stripe', 'stripe-test', %s, %s, %s, %s, %s, %s, false, %s, %s, %s, %s,
                           now())""",
                (payment_id, reference, total, intent["currency"], intent_id, f"{total / 100:.2f}", intent["currency"],
                 intent["buyer_id"], intent["session_id"], hold_id, deadline, fee, intent["freight_minor"], hosted.url,
                 [embedded.session_id, hosted.session_id]))
            await conn.execute(
                """UPDATE booking_intents SET status = 'PAYMENT_PENDING', hold_id = %s, hold_expires_at = %s,
                       payment_id = %s, fee_minor = %s, updated_at = now() WHERE intent_id = %s""",
                (hold_id, hold_expires_at, payment_id, fee, intent_id))
            await _event(conn, "PAYMENT_REQUESTED", "payment", payment_id, correlation_id=intent["correlation_id"],
                         carrier_id=intent["carrier_id"],
                         payload={"provider": "stripe", "method": "stripe", "mode": "test", "amount_minor": total,
                                  "currency": intent["currency"], "hold_id": hold_id, "reference": reference})
        return self._stripe_request(reference, total, intent["currency"], embedded.session_id, embedded.client_secret,
                                    hosted.url, deadline, intent["display_name"])

    @staticmethod
    def _stripe_request(reference: str, total: int, currency: str, session_id: str, client_secret: str | None,
                        hosted_url: str | None, deadline: datetime, carrier: str) -> dict:
        seconds = max(60, int((deadline - datetime.now(timezone.utc)).total_seconds()))
        narration = (f"{carrier} is holding your space until {deadline.strftime('%H:%M')} UTC. "
                     f"Pay {money(total, currency)} by card to confirm your booking.")
        if hosted_url:
            narration += f"\n\nIf the payment form doesn't appear, pay securely with Stripe here:\n{hosted_url}"
        stripe_meta = {"ui_mode": "embedded", "publishable_key": os.getenv("STRIPE_TEST_PUBLISHABLE", ""),
                       "checkout_session_id": session_id, "currency": currency.lower(), "amount_cents": str(total)}
        if client_secret:
            stripe_meta["client_secret"] = client_secret
        metadata = {"stripe": stripe_meta, "service": "booking"}
        if hosted_url:
            metadata["checkout_url"] = hosted_url
        return {"kind": "PAYMENT_REQUEST", "narration": narration,
                "payment": {"reference": reference, "amount": f"{total / 100:.2f}", "currency": currency,
                            "method": "stripe", "recipient": "", "deadline_seconds": seconds,
                            "description": (f"Hormuz Exchange booking: {money(total, currency)} "
                                            "(ocean freight + booking service fee)."),
                            "metadata": metadata}}

    async def _stripe_request_again(self, row: dict, intent: dict) -> dict:
        embedded_id = (row["stripe_session_ids"] or [None])[0]
        secret = None
        if embedded_id:
            try:
                secret = getattr(await self.stripe.retrieve(embedded_id), "client_secret", None)
            except Exception:  # the hosted link still works
                secret = None
        return self._stripe_request(row["reference"], int(row["amount_minor"]), row["currency"], embedded_id or "",
                                    secret, row["checkout_url"], row["deadline_at"], intent["display_name"])

    async def _stripe_row_for(self, env: dict) -> dict | None:
        """The card payment a commit refers to: by Stripe session id, else the open one in that chat."""
        tx = (env.get("transaction_id") or "").strip()
        async with self.repo.pool.connection() as conn:
            cur = await conn.execute(
                """SELECT * FROM payment_records WHERE provider = 'stripe'
                     AND ((%s <> '' AND %s = ANY(stripe_session_ids))
                          OR (chat_session_id = %s AND status IN ('REQUESTED', 'COMMIT_RECEIVED')))
                   ORDER BY (%s <> '' AND %s = ANY(stripe_session_ids)) DESC, created_at DESC LIMIT 1""",
                (tx, tx, env.get("chat_session_id") or "", tx, tx))
            row = await cur.fetchone()
            return dict(zip([c.name for c in cur.description], row)) if row else None

    async def _commit_stripe(self, row: dict, env: dict) -> dict:
        if row["status"] == "VERIFIED":
            return await self._completed(row)
        if row["status"] not in ("REQUESTED", "COMMIT_RECEIVED"):
            return {"action": "none", "followups": []}
        session_id = (env.get("transaction_id") or "").strip()
        funds = env.get("funds") or {}
        if funds.get("payment_method") not in (None, "", "stripe"):
            return {"action": "none", "followups": []}  # not our card rail; the open checkout stays valid
        if session_id in (row["stripe_session_ids"] or []):
            settled = await self._settle_stripe(row, session_id, attempts=4)
        else:
            settled = await self.check_stripe(row)  # the client may report another id; Stripe is the truth
        # Not cleared yet (a card payment can take ~a minute): stay quiet; the poller posts the confirmation.
        return settled if settled is not None else {"action": "none", "followups": []}

    async def check_stripe(self, row: dict) -> dict | None:
        """Settle from whichever session (embedded or hosted) Stripe reports as paid; None if neither is paid."""
        for session_id in row["stripe_session_ids"] or []:
            settled = await self._settle_stripe(row, session_id, attempts=1)
            if settled is not None:
                return settled
        return None

    async def _settle_stripe(self, row: dict, session_id: str, *, attempts: int) -> dict | None:
        import asyncio

        expected = Expected(row["intent_id"], int(row["amount_minor"]), row["currency"])
        for i in range(attempts):
            verdict = classify_session(await self.stripe.retrieve(session_id), expected)
            if verdict.status != PAYMENT_PENDING:
                break
            if i + 1 < attempts:
                await asyncio.sleep(1.5)
        if verdict.status == PAYMENT_PENDING:
            return None
        if verdict.status != PAYMENT_VERIFIED_TEST:
            # an expired sibling session is normal; only a mismatch or live-mode session is a real failure
            if verdict.detail == "checkout expired without payment":
                return None
            await self._record(row, "VERIFICATION_FAILED", tx=None, error=verdict.detail, event="PAYMENT_FAILED",
                               proof={"session": session_id[:24], "provider_status": verdict.provider_status})
            await self._abandon(row, reason=verdict.detail)
            return self._cancel(session_id, f"Payment not accepted: {verdict.detail}. Nothing was booked.")
        intent = await self._intent(row["intent_id"])
        hold_ok = intent["status"] == "PAYMENT_PENDING" and intent["hold_expires_at"] > datetime.now(timezone.utc)
        proof = {"stripe_session": session_id, "provider_status": verdict.provider_status, "livemode": False,
                 "amount_minor": int(row["amount_minor"]), "currency": row["currency"]}
        async with self.repo.pool.connection() as conn:  # single winner between commit, poller and "paid"
            won = await (await conn.execute(
                """UPDATE payment_records SET status = %s, provider_tx_id = %s, provider_status = %s,
                       verification = %s, provider_verified_at = now(), verified_at = now(), updated_at = now()
                   WHERE reference = %s AND status IN ('REQUESTED', 'COMMIT_RECEIVED') RETURNING payment_id""",
                ("VERIFIED" if hold_ok else "RECONCILIATION_REQUIRED", session_id, verdict.provider_status,
                 _jsonb(proof), row["reference"]))).fetchone()
        if won is None:  # another path settled it first: same answer, never a second booking
            latest = await self._payment(row["reference"])
            return await self._completed(latest) if latest["status"] == "VERIFIED" else {"action": "none", "followups": []}
        for other in row["stripe_session_ids"] or []:
            if other != session_id:
                await self.stripe.expire(other)
        if not hold_ok:
            async with self.repo.pool.connection() as conn:
                await _event(conn, "PAYMENT_RECONCILIATION_REQUIRED", "payment", row["payment_id"],
                             correlation_id=row["reference"], payload={"reference": row["reference"],
                                                                       "detail": "paid after the carrier hold ended"})
            await self.repo.update_intent(row["intent_id"], "PAYMENT_RECONCILIATION_REQUIRED",
                                          detail="Stripe TEST payment verified after the carrier hold ended")
            return self._cancel(session_id, "Your test payment arrived after the carrier's hold ended, so I could "
                                "not book that space. It is recorded for reconciliation; nothing was booked.")
        async with self.repo.pool.connection() as conn:
            await _event(conn, "PAYMENT_VERIFIED", "payment", row["payment_id"], correlation_id=row["reference"],
                         payload={"reference": row["reference"], "status": "VERIFIED", "provider": "stripe",
                                  "tx": session_id[:16], "intent_id": row["intent_id"]})
        await self.repo.update_intent(row["intent_id"], "PAYMENT_VERIFIED_TEST", detail="Stripe TEST payment verified")
        receipt = await self._confirm(row["intent_id"], payment_reference=row["reference"], hold_id=intent["hold_id"],
                                      hold_expires_at=intent["hold_expires_at"],
                                      payment_line=f"{money(int(row['amount_minor']), row['currency'])} paid by card "
                                                   "(verified with Stripe)",
                                      already_held=True)
        return {"action": "complete", "transaction_id": session_id, "chat_session_id": row["chat_session_id"],
                "followups": [receipt]}

    async def open_stripe_payments(self, sender: str | None = None) -> list[dict]:
        sql = "SELECT * FROM payment_records WHERE provider = 'stripe' AND status IN ('REQUESTED', 'COMMIT_RECEIVED')"
        args: tuple = ()
        if sender:
            sql += " AND sender_agent_address = %s"
            args = (sender,)
        async with self.repo.pool.connection() as conn:
            cur = await conn.execute(sql + " ORDER BY created_at DESC", args)
            names = [c.name for c in cur.description]
            return [dict(zip(names, r)) for r in await cur.fetchall()]

    async def poll_stripe(self) -> list[tuple[dict, dict]]:
        """No webhook in mailbox mode: settle paid sessions, close windows that ran out. Returns (row, result)."""
        out = []
        for row in await self.open_stripe_payments():
            settled = await self.check_stripe(row)
            if settled is not None:
                out.append((row, settled))
                continue
            if row["deadline_at"] <= datetime.now(timezone.utc):
                for session_id in row["stripe_session_ids"] or []:
                    await self.stripe.expire(session_id)
                settled = await self.check_stripe(row)  # paid in the last instant before expiry
                if settled is not None:
                    out.append((row, settled))
                    continue
                await self._record(row, "EXPIRED", tx=None, error="payment window closed", event="PAYMENT_EXPIRED")
                await self._abandon(row, reason="payment window closed")
                out.append((row, {"action": "cancel", "transaction_id": None, "reason": "payment window closed",
                                  "chat_session_id": row["chat_session_id"],
                                  "followups": [text("The payment window closed, so I released the carrier's hold. "
                                                     "Nothing was charged or booked; say 'ship cargo' to start again.")]}))
        return out

    async def _release(self, intent_id: str, hold_id: str, reason: str) -> None:
        intent = await self._intent(intent_id)
        if intent:
            await self.holds.release_hold(_carrier(intent), hold_id=hold_id, request_id=intent["request_id"],
                                          negotiation_id=intent["negotiation_id"], correlation_id=intent["correlation_id"])
        await self.repo.update_intent(intent_id, "RELEASED", detail=reason)

    # ---- payment protocol events from the gateway --------------------------------------
    async def on_commit(self, env: dict) -> dict:
        """CommitPayment is a claim. Check it against what we asked for, then ask the gateway for ledger proof."""
        row = await self._payment(env.get("reference"))
        tx = (env.get("transaction_id") or "").strip()
        meta = env.get("metadata") or {}
        wallet = (meta.get("buyer_fet_wallet") or meta.get("buyer_fet_address") or "").strip()
        log_event("payment_commit_received", reference=(env.get("reference") or "")[:40], tx=tx[:20],
                  sender=(env.get("sender_agent_address") or "")[:24], session=(env.get("chat_session_id") or "")[:12],
                  method=(env.get("funds") or {}).get("payment_method"), matched=row is not None,
                  sender_matches=bool(row) and row["sender_agent_address"] == env.get("sender_agent_address"))
        if row is None or row["sender_agent_address"] != env.get("sender_agent_address"):
            # The client may echo a different reference or relay from another address. For card payments
            # that is harmless: a commit only triggers a Stripe check, and results go to the payer's own chat.
            card = await self._stripe_row_for(env)
            if card is not None:
                return await self._commit_stripe(card, env)
            if payment_mode() == "stripe_test":
                return {"action": "none", "followups": []}  # unknown claim: ignore quietly, never "failed"
            return self._cancel(tx, "This payment isn't one I requested from you; nothing was booked.")
        if row["status"] == "VERIFIED":  # duplicate commit: same answer, no second booking
            return await self._completed(row)
        if row["provider"] == "stripe":
            return await self._commit_stripe(row, env)
        if row["status"] not in ("REQUESTED", "COMMIT_RECEIVED"):
            return self._cancel(tx, f"This payment is already {row['status'].lower()}; nothing new was booked.")
        problem = None
        funds = env.get("funds") or {}
        if row["deadline_at"] <= datetime.now(timezone.utc):
            problem = "the payment window has closed"
        elif not tx:
            problem = "no transaction ID was supplied"
        elif not wallet.startswith("fetch1"):
            problem = "the payment did not include the buyer's FET wallet address"
        elif (str(funds.get("currency", "")).upper(), funds.get("payment_method"), Decimal(str(funds.get("amount", "0")))) \
                != ("FET", "fet_direct", Decimal(row["funds_amount"])):
            problem = "the paid amount, currency or method does not match the request"
        elif env.get("recipient") and env["recipient"] != row["gateway_wallet"]:
            problem = "the payment names a different recipient wallet"
        elif await self._tx_used_elsewhere(tx, row["reference"]):
            problem = "that transaction was already used for another payment"
        if problem is None:
            try:  # binds the tx to this payment; the unique index stops one tx paying for two bookings
                await self._record(row, "COMMIT_RECEIVED", tx=tx, wallet=wallet, error=None,
                                   event="PAYMENT_COMMIT_RECEIVED")
            except UniqueViolation:
                problem = "that transaction was already used for another payment"
        if problem:  # a refused claim never owns the tx id; it is kept only as audit detail
            await self._record(row, "VERIFICATION_FAILED", tx=None, wallet=wallet, error=problem,
                               event="PAYMENT_FAILED", proof={"claimed_tx": tx})
            await self._abandon(row, reason=problem)
            return self._cancel(tx, f"Payment not accepted: {problem}. Nothing was booked.")
        return {"action": "verify", "transaction_id": tx, "chat_session_id": row["chat_session_id"],
                "expected": {"reference": row["reference"], "tx_hash": tx, "sender": wallet,
                             "recipient": row["gateway_wallet"], "amount_units": int(row["amount_minor"]),
                             "denom": DENOM, "network": NETWORK}}

    async def on_ledger_result(self, env: dict) -> dict:
        row = await self._payment(env.get("reference"))
        tx = env.get("transaction_id") or ""
        if row is None or row["sender_agent_address"] != env.get("sender_agent_address"):
            return self._cancel(tx, "Unknown payment; nothing was booked.")
        if row["status"] == "VERIFIED":
            return await self._completed(row)
        if row["status"] != "COMMIT_RECEIVED" or row["provider_tx_id"] != tx:
            return self._cancel(tx, "This payment is not awaiting verification; nothing was booked.")
        if not env.get("verified"):
            detail = env.get("detail") or "ledger verification failed"
            await self._record(row, "VERIFICATION_FAILED", tx=tx, error=detail, event="PAYMENT_FAILED",
                               proof=env.get("proof"))
            await self._abandon(row, reason=detail)
            return self._cancel(tx, f"I could not verify that payment on the Fetch testnet ({detail}). Nothing was booked.")
        intent = await self._intent(row["intent_id"])
        if intent["hold_expires_at"] <= datetime.now(timezone.utc) or intent["status"] != "PAYMENT_PENDING":
            await self._record(row, "RECONCILIATION_REQUIRED", tx=tx, error="verified after the hold ended",
                               event="PAYMENT_RECONCILIATION_REQUIRED", proof=env.get("proof"), verified=True)
            await self.repo.update_intent(row["intent_id"], "PAYMENT_RECONCILIATION_REQUIRED",
                                          detail="testnet fee verified after the carrier hold ended")
            return self._cancel(tx, "Your testnet payment arrived after the carrier's hold ended, so I could not "
                                "book that space. It is recorded for reconciliation; it was not booked, and testnet "
                                "funds are not automatically returned.")
        await self._record(row, "VERIFIED", tx=tx, error=None, event="PAYMENT_VERIFIED", proof=env.get("proof"),
                           verified=True)
        await self.repo.update_intent(row["intent_id"], "PAYMENT_VERIFIED_TEST", detail="testnet FET fee verified")
        receipt = await self._confirm(row["intent_id"], payment_reference=row["reference"], hold_id=intent["hold_id"],
                                      hold_expires_at=intent["hold_expires_at"],
                                      payment_line=f"{row['funds_amount']} testnet FET verified on-chain (tx {tx[:12]}…)",
                                      already_held=True)
        return {"action": "complete", "transaction_id": tx, "chat_session_id": row["chat_session_id"],
                "followups": [receipt]}

    async def on_reject(self, env: dict) -> dict:
        async with self.repo.pool.connection() as conn:
            ref = (await (await conn.execute(
                "SELECT reference FROM payment_records WHERE sender_agent_address = %s AND chat_session_id = %s "
                "AND provider IN ('fetch', 'stripe') AND status = 'REQUESTED' ORDER BY created_at DESC LIMIT 1",
                (env.get("sender_agent_address"), env.get("chat_session_id")))).fetchone())
        row = await self._payment(ref[0]) if ref else None
        if row is None:
            return {"action": "none", "followups": [text("No open payment to reject; nothing was charged.")]}
        await self._record(row, "REJECTED", tx=None, error=env.get("reason") or "rejected by buyer", event="PAYMENT_REJECTED")
        await self._abandon(row, reason="payment rejected by buyer")
        return {"action": "none", "followups": [text("Payment declined. I released the carrier's hold; nothing was "
                                                     "charged or booked.")]}

    # ---- internals -------------------------------------------------------------------
    async def _confirm(self, intent_id: str, *, payment_reference: str, hold_id: str, hold_expires_at: datetime,
                       payment_line: str, already_held: bool = False) -> dict:
        intent = await self._intent(intent_id)
        if not already_held:
            async with self.repo.pool.connection() as conn:
                await conn.execute("UPDATE booking_intents SET hold_id = %s, hold_expires_at = %s, updated_at = now() "
                                   "WHERE intent_id = %s", (hold_id, hold_expires_at, intent_id))
            intent = await self._intent(intent_id)
        code = claim_code(self.claim_secret, intent_id)
        confirm = await self.holds.confirm_booking(
            _carrier(intent), hold_id=hold_id, payment_reference=payment_reference,
            idempotency_key=f"confirm-{intent_id}", intent_id=intent_id, claim_code_hash=claim_hash(intent_id, code),
            request_id=intent["request_id"], negotiation_id=intent["negotiation_id"],
            correlation_id=intent["correlation_id"])
        if not confirm.ok:
            await self.repo.update_intent(intent_id, "BOOKING_PENDING",
                                          detail=f"carrier could not confirm ({confirm.code}); needs reconciliation")
            return text(f"Payment step done, but {intent['display_name']} could not confirm the space ({confirm.code}). "
                        "No second charge will be made; this booking needs reconciliation.")
        intent = await self.repo.complete_booking(intent_id, confirm.booking_id)
        async with self.repo.pool.connection() as conn:
            await conn.execute("UPDATE bookings SET must_deliver = r.must_deliver FROM cargo_requests r "
                               "WHERE bookings.booking_id = %s AND r.request_id = bookings.request_id",
                               (confirm.booking_id,))
        return cards.booking_receipt(booking_id=confirm.booking_id, claim_code=code, offer=self._offer_view(intent),
                                     payment_line=payment_line)

    async def _receipt(self, intent: dict) -> dict:
        return cards.booking_receipt(booking_id=intent["booking_id"], claim_code=claim_code(self.claim_secret, intent["intent_id"]),
                                     offer=self._offer_view(intent), payment_line="already confirmed")

    async def _completed(self, row: dict) -> dict:
        intent = await self._intent(row["intent_id"])
        follow = [await self._receipt(intent)] if intent["status"] == "BOOKING_CONFIRMED" else []
        return {"action": "complete", "transaction_id": row["provider_tx_id"], "chat_session_id": row["chat_session_id"],
                "followups": follow}

    @staticmethod
    def _offer_view(intent: dict) -> dict:
        return {"display_name": intent["display_name"], "sailing_id": intent["sailing_id"],
                "origin_port": intent["origin_port"], "destination_port": intent["destination_port"],
                "quantity": intent["quantity"], "arrival_at": intent["arrival_at"],
                "total_price_minor": intent["freight_minor"]}

    @staticmethod
    def _cancel(tx: str, message: str) -> dict:
        return {"action": "cancel", "transaction_id": tx or None, "reason": message[:200], "followups": [text(message)]}

    async def _payment(self, reference: str | None) -> dict | None:
        if not reference:
            return None
        async with self.repo.pool.connection() as conn:
            cur = await conn.execute("SELECT * FROM payment_records WHERE reference = %s "
                                     "AND provider IN ('fetch', 'stripe')", (reference,))
            row = await cur.fetchone()
            return dict(zip([c.name for c in cur.description], row)) if row else None

    async def _intent(self, intent_id: str) -> dict:
        async with self.repo.pool.connection() as conn:
            return await self.repo._intent(conn, "WHERE i.intent_id = %s", (intent_id,))

    async def _tx_used_elsewhere(self, tx: str, reference: str) -> bool:
        async with self.repo.pool.connection() as conn:
            return bool(await (await conn.execute(
                "SELECT 1 FROM payment_records WHERE provider_tx_id = %s AND reference <> %s", (tx, reference))).fetchone())

    async def _record(self, row: dict, status: str, *, tx: str | None, error: str | None, event: str,
                      wallet: str | None = None, proof: dict | None = None, verified: bool = False) -> None:
        from psycopg.types.json import Jsonb
        async with self.repo.pool.connection() as conn, conn.transaction():
            await conn.execute(
                """UPDATE payment_records SET status = %s, provider_tx_id = coalesce(%s, provider_tx_id),
                       buyer_wallet = coalesce(%s, buyer_wallet), last_error = %s,
                       verification = coalesce(%s, verification),
                       provider_verified_at = CASE WHEN %s THEN now() ELSE provider_verified_at END,
                       verified_at = CASE WHEN %s THEN now() ELSE verified_at END, updated_at = now()
                   WHERE reference = %s""",
                (status, tx or None, wallet or None, error, Jsonb(proof) if proof else None, verified, verified,
                 row["reference"]))
            await _event(conn, event, "payment", row["payment_id"], correlation_id=row["reference"],
                         payload={"reference": row["reference"], "status": status, "tx": (tx or "")[:16],
                                  "detail": error, "intent_id": row["intent_id"]})

    async def _abandon(self, row: dict, *, reason: str) -> None:
        intent = await self._intent(row["intent_id"])
        if intent and intent["status"] == "PAYMENT_PENDING" and intent.get("hold_id"):
            await self.holds.release_hold(_carrier(intent), hold_id=intent["hold_id"], request_id=intent["request_id"],
                                          negotiation_id=intent["negotiation_id"], correlation_id=intent["correlation_id"])
            await self.repo.update_intent(row["intent_id"], "RELEASED", detail=reason)


def _jsonb(value: dict):
    from psycopg.types.json import Jsonb
    return Jsonb(value)

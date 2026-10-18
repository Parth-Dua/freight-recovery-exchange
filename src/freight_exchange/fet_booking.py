"""Booking with the Fetch Payment Protocol fee (FET stable testnet), v2 phases 2-3.

    selected offer -> carrier hold -> payment record (REQUESTED) -> RequestPayment (gateway)
    -> CommitPayment = a claim -> business checks here -> on-chain proof (gateway, cosmpy)
    -> VERIFIED (tx id unique) -> carrier CONFIRM_BOOKING -> receipt card
The FET fee pays for the demo booking-coordination service only; freight stays a simulated USD figure.
`dev` mode (PAYMENT_MODE=dev) confirms without any payment and says so; it is for regression only.
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
from freight_exchange.repository import _event

DENOM = "atestfet"
NETWORK = "stable-testnet"
PAYMENT_DEADLINE_MARGIN = timedelta(minutes=3)  # leave time to verify and confirm before the hold lapses
MAX_PAYMENT_WINDOW = timedelta(minutes=10)


def payment_mode() -> str:
    mode = os.getenv("PAYMENT_MODE", "fet_testnet")
    if mode not in ("fet_testnet", "dev"):
        raise ValueError("PAYMENT_MODE must be fet_testnet or dev")
    if mode == "fet_testnet" and os.getenv("FET_USE_TESTNET", "true").lower() != "true":
        raise RuntimeError("FET mainnet is not allowed in the demo (FET_USE_TESTNET must be true)")
    if os.getenv("LEGACY_STRIPE_CHECKOUT_ENABLED", "false").lower() == "true" and mode == "fet_testnet":
        raise RuntimeError("Legacy Stripe checkout and the FET fee are mutually exclusive")
    return mode


def _carrier(intent: dict) -> CarrierRef:
    return CarrierRef(intent["carrier_id"], intent["display_name"], intent["agent_address"], intent["endpoint_url"])


class FetBookingService:
    def __init__(self, repo, holds, claim_secret: bytes):
        self.repo = repo
        self.holds = holds
        self.claim_secret = claim_secret

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
                "narration": (f"The carrier is holding your space. Please pay the {amount} testnet FET "
                              "booking-coordination fee to confirm (simulated freight is not charged)."),
                "payment": {"reference": reference, "amount": str(amount), "currency": "FET", "method": "fet_direct",
                            "recipient": gateway_wallet, "deadline_seconds": seconds,
                            "description": (f"Hormuz Exchange booking-coordination service: {amount} testnet FET. "
                                            "Demo payment; no real freight is bought."),
                            "metadata": {"fet_network": NETWORK, "service": "booking_coordination"}}}

    async def _resume(self, intent: dict, gateway_wallet: str) -> dict:
        if intent["status"] == "BOOKING_CONFIRMED":
            return await self._receipt(intent)
        if intent["status"] == "PAYMENT_PENDING" and intent.get("payment_reference", "").startswith("fetpay_"):
            async with self.repo.pool.connection() as conn:
                row = await (await conn.execute(
                    "SELECT funds_amount, gateway_wallet, deadline_at, status FROM payment_records WHERE reference = %s",
                    (intent["payment_reference"],))).fetchone()
            if row and row[3] == "REQUESTED" and row[2] > datetime.now(timezone.utc):
                return self._payment_request(intent["payment_reference"], Decimal(row[0]), row[1], row[2])
        return text(f"This booking is {intent['status'].replace('_', ' ').lower()}; nothing new was charged.")

    # ---- payment protocol events from the gateway --------------------------------------
    async def on_commit(self, env: dict) -> dict:
        """CommitPayment is a claim. Check it against what we asked for, then ask the gateway for ledger proof."""
        row = await self._payment(env.get("reference"))
        tx = (env.get("transaction_id") or "").strip()
        meta = env.get("metadata") or {}
        wallet = (meta.get("buyer_fet_wallet") or meta.get("buyer_fet_address") or "").strip()
        if row is None or row["sender_agent_address"] != env.get("sender_agent_address"):
            return self._cancel(tx, "This payment isn't one I requested from you; nothing was booked.")
        if row["status"] == "VERIFIED":  # duplicate commit: same answer, no second booking
            return await self._completed(row)
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
                "AND provider = 'fetch' AND status = 'REQUESTED' ORDER BY created_at DESC LIMIT 1",
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
            cur = await conn.execute("SELECT * FROM payment_records WHERE reference = %s AND provider = 'fetch'", (reference,))
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

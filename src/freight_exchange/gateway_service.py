"""Buyer intents arriving through the gateway uAgent: one place that turns chat/card input into business calls.

Returns protocol-neutral results for the gateway to serialize:
  {"kind": "CARD", "narration", "card_kind", "card_payload"} | {"kind": "TEXT", "text"}
  | {"kind": "PAYMENT_REQUEST", "payment": {...}} | {"kind": "PENDING", "text"}
Card actions come back as untrusted text (JSON on a direct @mention, prose via the planner) and are
validated here against server-side state; buyer identity only ever comes from the gateway transport.
"""

from __future__ import annotations

import json
import logging
import os
import re
from decimal import Decimal
from uuid import uuid4

from freight_exchange.gateway_store import ChatContext, GatewayStore
from freight_exchange.observability import log_event
from freight_exchange.repository import Repository, _event

FET_DECIMALS = 18  # 1 FET = 10**18 atestfet on the Fetch stable testnet
SHIP_INTENT = re.compile(
    r"^(start|new shipment|ship cargo|book cargo)$|\b(ship|send|move|book)\b.{0,40}\b(cargo|containers?|freight|shipment)\b"
    r"|\bi (have|need to ship) cargo\b", re.IGNORECASE)
MENTION = re.compile(r"@[\w.-]+")


def fet_amount() -> Decimal:
    amount = Decimal(os.getenv("BOOKING_SERVICE_FET_AMOUNT", "0.1"))
    if amount <= 0:
        raise ValueError("BOOKING_SERVICE_FET_AMOUNT must be positive")
    return amount


def to_atestfet(amount: Decimal) -> int:
    units = amount * (Decimal(10) ** FET_DECIMALS)
    if units != units.to_integral_value():
        raise ValueError("FET amount has more than 18 decimals")
    return int(units)


def parse_action(text: str) -> dict | None:
    """A card selection: {"action": ..., plus form values / IDs}. None for ordinary chat text."""
    cleaned = MENTION.sub("", text).strip()
    start = cleaned.find("{")
    if start < 0:
        return None
    try:
        data = json.loads(cleaned[start:cleaned.rfind("}") + 1])
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    flat: dict = {}
    for key in ("values", "fields", "form", "data"):
        if isinstance(data.get(key), dict):
            flat.update(data[key])
    selection = data.get("selection") if isinstance(data.get("selection"), dict) else {}
    flat.update({k: v for k, v in data.items() if not isinstance(v, dict)})
    flat.update(selection)
    return flat if flat.get("action") else None


def card(narration: str, kind: str, payload: dict, *, terminal: bool = False, fallback_text: str = "") -> dict:
    return {"kind": "CARD", "narration": narration, "card_kind": kind, "card_payload": payload,
            "terminal": terminal, "fallback_text": fallback_text or narration}


def text(message: str) -> dict:
    return {"kind": "TEXT", "text": message}


class GatewayService:
    def __init__(self, repo: Repository, store: GatewayStore | None = None, legacy=None):
        self.repo = repo
        self.store = store or GatewayStore(repo.pool)
        self._legacy = legacy  # callable(sender, session, text) -> str; the existing LangGraph booking graph

    async def handle(self, ctx: ChatContext) -> dict:
        await self.store.bind(ctx)
        prior = await self.store.claim_event(ctx)
        if prior is not None:
            return prior
        try:
            result = await self._route(ctx)
        except Exception as exc:
            log_event("gateway_intent_failed", logging.ERROR, error_type=type(exc).__name__, event_id=ctx.event_id)
            result = text("Something went wrong on our side; nothing was booked or charged. Please try again.")
        await self.store.finish_event(ctx.event_id, result)
        log_event("gateway_intent", event_id=ctx.event_id, kind=result.get("kind"),
                  card_kind=result.get("card_kind"), sender=ctx.sender, session=ctx.session)
        return result

    async def _route(self, ctx: ChatContext) -> dict:
        action = parse_action(ctx.text)
        if action is not None:
            return await self.on_action(ctx, action)
        plain = MENTION.sub("", ctx.text).strip()
        if SHIP_INTENT.search(plain) and not re.search(r"\d", plain):
            return await self.cargo_form(ctx)
        if plain.lower() in ("payment test", "test payment"):
            return await self.payment_smoke(ctx)
        if plain.lower() in ("notify test", "test notification"):
            return await self.notify_smoke(ctx)
        return text(await self.legacy(ctx, plain))

    # ---- cards -------------------------------------------------------------------
    async def cargo_form(self, ctx: ChatContext) -> dict:
        corridors = await self.repo.corridors()
        origins = sorted({(c.origin.port_code, c.origin.name) for c in corridors})
        destinations = sorted({(c.destination.port_code, c.destination.name) for c in corridors})
        payload = {
            "title": "Tell us about your cargo",
            "fields": [
                {"name": "origin_port", "kind": "select", "label": "Origin", "required": True,
                 "options": [{"label": f"{n} ({p})", "value": p} for p, n in origins]},
                {"name": "destination_port", "kind": "select", "label": "Destination", "required": True,
                 "options": [{"label": f"{n} ({p})", "value": p} for p, n in destinations]},
                {"name": "quantity", "kind": "number", "label": "20GP containers", "required": True},
            ],
            "submit_cta": {"label": "Find sailings",
                           "selection": {"action": "submit_cargo_request", "card_version": "1",
                                         "card_instance_id": f"form_{uuid4().hex[:10]}"}},
        }
        return card("Fill in your shipment and I'll ask the carriers.", "form", payload)

    async def on_action(self, ctx: ChatContext, action: dict) -> dict:
        name = action.get("action")
        log_event("gateway_card_action", action=name, sender=ctx.sender, session=ctx.session)
        if name == "submit_cargo_request":
            return text(f"Got it: {action.get('quantity', '?')}×20GP {action.get('origin_port', '?')} → "
                        f"{action.get('destination_port', '?')}. (Phase 1 transport check: action received.)")
        return text("That card has expired or isn't one I recognise; nothing was changed.")

    # ---- legacy booking graph (text fallback) ----------------------------------------
    async def legacy(self, ctx: ChatContext, message: str) -> str:
        if self._legacy is None:
            from freight_exchange.legacy_graph import run_legacy_graph
            self._legacy = run_legacy_graph
        return await self._legacy(ctx.sender, ctx.session, message)

    # ---- phase 1 protocol smoke tests -------------------------------------------------
    async def payment_smoke(self, ctx: ChatContext) -> dict:
        """A real RequestPayment for 0.1 testnet FET that is never completed in phase 1."""
        amount, reference = fet_amount(), f"pay_{uuid4().hex[:16]}"
        async with self.repo.pool.connection() as conn, conn.transaction():
            await conn.execute(
                """INSERT INTO payment_records (payment_id, provider, reference, purpose, amount_minor, currency, status,
                       method, network, funds_amount, funds_currency, sender_agent_address, chat_session_id,
                       gateway_wallet, deadline_at)
                   VALUES (%s, 'fetch', %s, 'gateway_smoke', %s, 'FET', 'REQUESTED', 'fet_direct', 'stable-testnet',
                           %s, 'FET', %s, %s, %s, now() + interval '5 minutes')""",
                (reference, reference, to_atestfet(amount), str(amount), ctx.sender, ctx.session, ctx.gateway_wallet))
            await _event(conn, "PAYMENT_REQUESTED", "payment", reference, correlation_id=reference,
                         payload={"amount": str(amount), "currency": "FET", "purpose": "gateway_smoke"})
        return {"kind": "PAYMENT_REQUEST", "narration": "Protocol smoke test: this payment request is never "
                "completed and books nothing. Please reject it.",
                "payment": {"reference": reference, "amount": str(amount), "currency": "FET", "method": "fet_direct",
                            "recipient": ctx.gateway_wallet, "deadline_seconds": 300,
                            "description": "Hormuz Exchange gateway smoke test (testnet FET); books nothing.",
                            "metadata": {"fet_network": "stable-testnet", "service": "gateway_smoke"}}}

    async def notify_smoke(self, ctx: ChatContext) -> dict:
        key = f"smoke:{ctx.event_id}"
        await self.store.enqueue(event_key=key, buyer_id=ctx.buyer_id, recipient=ctx.sender, session=ctx.session,
                                 gateway=ctx.gateway, result=text("Background notification test: this message was "
                                                                   "pushed from the outbox into this same chat."))
        return text("Queued a background notification; it should appear here within a few seconds.")

    async def payment_event(self, envelope: dict) -> dict:
        """Phase 1: record Commit/Reject truthfully; never complete. Phase 3 replaces this with verification."""
        kind, reference = envelope.get("type"), envelope.get("reference")
        async with self.repo.pool.connection() as conn, conn.transaction():
            row = await (await conn.execute(
                "SELECT reference, status, purpose FROM payment_records WHERE sender_agent_address = %s "
                "AND (reference = %s OR %s::text IS NULL) ORDER BY created_at DESC LIMIT 1",
                (envelope.get("sender_agent_address"), reference, reference))).fetchone()
            if row is None:
                return {"action": "cancel" if kind == "commit" else "none", "transaction_id": envelope.get("transaction_id"),
                        "reason": "unknown payment reference", "followups": [text("I don't have that payment on record; nothing was booked.")]}
            ref = row[0]
            wallet = (envelope.get("metadata") or {}).get("buyer_fet_wallet") or (envelope.get("metadata") or {}).get("buyer_fet_address")
            status = "REJECTED" if kind == "reject" else "COMMIT_RECEIVED"
            await conn.execute(
                "UPDATE payment_records SET status = %s, buyer_wallet = coalesce(%s, buyer_wallet), last_error = %s, "
                "updated_at = now() WHERE reference = %s",
                (status, wallet, None if kind == "reject" else "phase-1 smoke: verification disabled", ref))
            await _event(conn, "PAYMENT_REJECTED" if kind == "reject" else "PAYMENT_COMMIT_RECEIVED", "payment", ref,
                         correlation_id=ref, payload={"wallet_metadata_present": bool(wallet),
                                                      "metadata_keys": sorted((envelope.get("metadata") or {}).keys()),
                                                      "has_tx": bool(envelope.get("transaction_id"))})
        if kind == "reject":
            return {"action": "none", "followups": [text("Payment request rejected; nothing was charged or booked.")]}
        return {"action": "cancel", "transaction_id": envelope.get("transaction_id"),
                "reason": "Smoke test only: payment verification is not enabled, nothing was booked.",
                "followups": [text("Payment commit received for a smoke test. I did not verify or complete it, "
                                   "and nothing was booked.")]}

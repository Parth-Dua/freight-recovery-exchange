"""Hormuz Exchange buyer gateway: the one mailbox uAgent ASI:One buyers talk to.

A protocol facade, not a second orchestrator: it speaks ACP Chat (with Interactive Cards) and the
seller side of the Agent Payment Protocol, and forwards every buyer intent to the Exchange backend,
which makes all business decisions. Background results (negotiations, recovery) come back through the
backend outbox and are pushed into the buyer's existing chat session.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import aiohttp
from uagents import Agent, Context, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement, ChatMessage, EndSessionContent, StartSessionContent, TextContent, chat_protocol_spec,
)
from uagents_core.contrib.protocols.payment import (
    CancelPayment, CommitPayment, CompletePayment, Funds, RejectPayment, RequestPayment, payment_protocol_spec,
)

from buyer_gateway.backend_client import BackendClient, BackendUnavailable
from buyer_gateway.card_wire import CardWireError, message_for, text_message
from buyer_gateway.payment_verifier import verify_payment

log = logging.getLogger("buyer_gateway")
README = Path(__file__).resolve().parent / "README.md"
UNAVAILABLE = ("The exchange backend is busy or restarting, so nothing was booked or changed. "
               "Please send that again in a moment.")


def incoming_text(msg: ChatMessage) -> str:
    return "\n".join(c.text for c in msg.content if isinstance(c, TextContent)).strip()


def build_gateway(*, seed: str, port: int, backend: BackendClient, name: str = "hormuz-exchange") -> Agent:
    agent = Agent(name=name, seed=seed, port=port, mailbox=True, publish_agent_details=True,
                  description=("Hormuz Exchange (demo): ship cargo with independent carrier agents. Native "
                               "cards for quotes and booking, Fetch payment for the booking-coordination "
                               "service, and automatic rebooking when a carrier withdraws a sailing. "
                               "Fictional market; testnet payments only."),
                  readme_path=str(README) if README.exists() else None)
    chat = Protocol(spec=chat_protocol_spec)
    payment = Protocol(spec=payment_protocol_spec, role="seller")
    seen: deque[tuple[str, str]] = deque(maxlen=2000)

    async def send_in_session(ctx: Context, recipient: str, session: str | None, msg) -> None:
        """Send inside the buyer's existing chat session (ASI:One drops server-started sessions)."""
        if session:
            ctx._session = UUID(session)  # uagents keeps the outgoing session on the context
        await ctx.send(recipient, msg)

    async def deliver(ctx: Context, recipient: str, session: str | None, result: dict) -> None:
        if result.get("kind") == "PAYMENT_REQUEST":
            p = result["payment"]
            if result.get("narration"):
                await send_in_session(ctx, recipient, session, text_message(result["narration"]))
            await send_in_session(ctx, recipient, session, RequestPayment(
                accepted_funds=[Funds(currency=p["currency"], amount=p["amount"], payment_method=p["method"])],
                recipient=p["recipient"], deadline_seconds=int(p["deadline_seconds"]), reference=p["reference"],
                description=p["description"], metadata=p.get("metadata") or {}))
            return
        try:
            msg = message_for(result)
        except CardWireError as exc:  # never silently lose the answer: fall back to narration text
            log.warning("card rejected before send: %s", exc)
            msg = text_message(result.get("narration") or result.get("fallback_text") or UNAVAILABLE)
        if msg is not None:
            await send_in_session(ctx, recipient, session, msg)
        for follow in result.get("followups") or []:
            await deliver(ctx, recipient, session, follow)

    @chat.on_message(ChatMessage)
    async def on_chat(ctx: Context, sender: str, msg: ChatMessage) -> None:
        await ctx.send(sender, ChatAcknowledgement(timestamp=datetime.now(timezone.utc), acknowledged_msg_id=msg.msg_id))
        key = (sender, str(msg.msg_id))
        if key in seen:  # duplicate delivery: one logical command
            return
        seen.append(key)
        if any(isinstance(c, (StartSessionContent, EndSessionContent)) for c in msg.content) and not incoming_text(msg):
            return
        text = incoming_text(msg)
        if not text:
            return
        session = str(ctx.session)
        envelope = {"event_id": f"{sender}:{msg.msg_id}", "sender_agent_address": sender, "chat_session_id": session,
                    "gateway_agent_address": agent.address, "gateway_wallet": str(agent.wallet.address()),
                    "kind": "CHAT_TEXT_OR_CARD_ACTION", "text": text}
        try:
            result = await backend.intent(envelope)
        except BackendUnavailable as exc:
            log.warning("backend unavailable for %s: %s", msg.msg_id, exc)
            result = {"kind": "TEXT", "text": UNAVAILABLE}
        await deliver(ctx, sender, session, result)

    @chat.on_message(ChatAcknowledgement)
    async def on_ack(ctx: Context, sender: str, msg: ChatAcknowledgement) -> None:
        return  # never answer an acknowledgement (no reply loops)

    async def payment_event(ctx: Context, sender: str, kind: str, **fields) -> None:
        envelope = {"type": kind, "sender_agent_address": sender, "chat_session_id": str(ctx.session),
                    "gateway_agent_address": agent.address, "gateway_wallet": str(agent.wallet.address()), **fields}
        try:
            verdict = await backend.payment_event(envelope)
            if verdict.get("action") == "verify":  # the backend accepted the claim; now prove it on-chain
                proof = await verify_payment(verdict["expected"])
                log.info("ledger verification %s: %s", verdict["expected"]["reference"], proof.detail)
                verdict = await backend.payment_event({**envelope, "type": "ledger_result",
                                                       "reference": verdict["expected"]["reference"],
                                                       "transaction_id": verdict["transaction_id"],
                                                       "verified": proof.verified, "detail": proof.detail,
                                                       "proof": proof.proof})
        except BackendUnavailable:
            # Unknown outcome: never complete; the payment record stays for reconciliation on retry.
            verdict = {"action": "none", "followups": [{"kind": "TEXT", "text": UNAVAILABLE}]}
        session = verdict.get("chat_session_id") or str(ctx.session)
        if verdict.get("action") == "complete":
            await send_in_session(ctx, sender, session, CompletePayment(transaction_id=verdict["transaction_id"]))
        elif verdict.get("action") == "cancel":
            await send_in_session(ctx, sender, session, CancelPayment(transaction_id=verdict.get("transaction_id"),
                                                                     reason=verdict.get("reason", "")))
        for follow in verdict.get("followups") or []:
            await deliver(ctx, sender, session, follow)

    @payment.on_message(CommitPayment)
    async def on_commit(ctx: Context, sender: str, msg: CommitPayment) -> None:
        # A commit is a claim, not proof: the backend verifies the ledger before any CompletePayment.
        await payment_event(ctx, sender, "commit", reference=msg.reference, transaction_id=msg.transaction_id,
                            funds=json.loads(msg.funds.json()), recipient=msg.recipient,
                            metadata=dict(msg.metadata or {}))

    @payment.on_message(RejectPayment)
    async def on_reject(ctx: Context, sender: str, msg: RejectPayment) -> None:
        await payment_event(ctx, sender, "reject", reason=msg.reason or "")

    @agent.on_interval(period=float(os.getenv("GATEWAY_OUTBOX_INTERVAL_S", "3")))
    async def push_outbox(ctx: Context) -> None:
        try:
            items = await backend.outbox(agent.address)
        except BackendUnavailable:
            return
        for item in items:
            try:
                await deliver(ctx, item["recipient"], item["chat_session_id"], item["result"])
                await backend.ack(item["outbox_id"], "SENT")
            except Exception as exc:  # keep the item for retry; never drop a buyer notification silently
                log.warning("outbox %s delivery failed: %s", item.get("outbox_id"), type(exc).__name__)
                await backend.ack(item["outbox_id"], "RETRY", type(exc).__name__)

    @agent.on_event("startup")
    async def connect_mailbox(ctx: Context) -> None:
        ctx.logger.info(f"gateway address {agent.address}; FET wallet {agent.wallet.address()}")
        token = os.getenv("AGENTVERSE_API_KEY", "").strip()
        if not token:
            ctx.logger.info("No AGENTVERSE_API_KEY: connect the mailbox once via the inspector link above.")
            return

        async def register() -> None:
            await asyncio.sleep(3)  # let the local server start
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.post(f"http://127.0.0.1:{port}/connect",
                                      json={"user_token": token, "agent_type": "mailbox"}) as r:
                        ctx.logger.info(f"Agentverse mailbox registration: HTTP {r.status} {(await r.text())[:120]}")
            except Exception as exc:
                ctx.logger.warning(f"mailbox registration failed: {type(exc).__name__}")
        asyncio.create_task(register())

    agent.include(chat, publish_manifest=True)
    agent.include(payment, publish_manifest=True)
    return agent

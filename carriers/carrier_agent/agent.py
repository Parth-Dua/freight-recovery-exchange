"""Company Carrier Agent: answers REQUEST_OFFER with a signed PROPOSE/REJECT/AGENT_ERROR.

Replies are sent with ctx.send(sender, ...) so the Exchange's sync request
resolves with them. Only requests from the allowed Exchange address(es) are served.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Callable
from uuid import uuid4

from freight_contracts import (
    SCHEMA_VERSION,
    AgentError,
    ErrorCode,
    ProposeOffer,
    RejectOffer,
    RequestOffer,
    payload_digest,
)
from uagents import Agent, Context, Protocol

from carrier_agent.inventory import CarrierInventory
from carrier_agent.policy import Decline, decide

log = logging.getLogger("carrier_agent")

PROTOCOL_NAME = "freight-offer"
PROTOCOL_VERSION = "1.0.0"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def reply_base(msg: RequestOffer, *, carrier_id: str, carrier_address: str, now: datetime) -> dict:
    return dict(
        schema_version=SCHEMA_VERSION,
        message_id=f"msg_{uuid4().hex}",
        correlation_id=msg.correlation_id,
        request_id=msg.request_id,
        negotiation_id=msg.negotiation_id,
        sent_at=now,
        carrier_id=carrier_id,
        carrier_address=carrier_address,
        in_reply_to=msg.message_id,
    )


def build_reply(
    msg: RequestOffer,
    *,
    sender: str,
    carrier_id: str,
    carrier_address: str,
    allowed_requesters: set[str],
    decide_fn: Callable,
    now: datetime,
) -> tuple[str, dict]:
    """Return (kind, fields) for the reply; pure so it can be unit-tested."""
    base = reply_base(msg, carrier_id=carrier_id, carrier_address=carrier_address, now=now)
    if sender not in allowed_requesters:
        return "error", {**base, "error_code": ErrorCode.UNAUTHORIZED_REQUESTER, "detail": "requester not authorized"}
    if msg.schema_version != SCHEMA_VERSION:
        return "error", {**base, "error_code": ErrorCode.UNSUPPORTED_SCHEMA_VERSION,
                         "detail": f"expected schema {SCHEMA_VERSION}"}
    if msg.expires_at <= now:
        return "error", {**base, "error_code": ErrorCode.REQUEST_EXPIRED, "detail": "request already expired"}
    if msg.cargo.latest_arrival is not None and msg.cargo.latest_arrival <= now:
        return "error", {**base, "error_code": ErrorCode.INVALID_CARGO_REQUEST,
                         "detail": "latest_arrival is in the past"}

    outcome = decide_fn(msg.cargo, now)
    if isinstance(outcome, Decline):
        return "reject", {**base, "reason_code": outcome.reason_code, "reason": outcome.reason}

    quote, policy = outcome
    s = quote.sailing
    return "offer", {
        **base,
        "offer_id": f"off_{carrier_id}_{uuid4().hex[:12]}",
        "sailing_id": s.sailing_id,
        "vessel_name": s.vessel_name,
        "origin_port": s.origin_port,
        "destination_port": s.destination_port,
        "container_type": msg.cargo.container_type,
        "quantity": quote.quantity,
        "total_price_minor": quote.total_price_minor,
        "currency": policy.currency,
        "departure_at": s.departure_at,
        "arrival_at": s.arrival_at,
        "expires_at": now + timedelta(seconds=policy.offer_ttl_seconds),
        "source_kind": "simulated",
    }


def build_carrier_agent(
    *, carrier_id: str, seed: str, port: int, inventory: CarrierInventory, allowed_requesters: set[str]
) -> Agent:
    agent = Agent(
        name=f"{carrier_id}-carrier",
        seed=seed,
        port=port,
        endpoint=[f"http://127.0.0.1:{port}/submit"],
        publish_agent_details=False,
        enable_agent_inspector=False,
    )
    protocol = Protocol(name=PROTOCOL_NAME, version=PROTOCOL_VERSION)
    models = {"offer": ProposeOffer, "reject": RejectOffer, "error": AgentError}

    @protocol.on_message(model=RequestOffer, replies={ProposeOffer, RejectOffer, AgentError})
    async def on_request_offer(ctx: Context, sender: str, msg: RequestOffer) -> None:
        started = time.perf_counter()
        now = _now()
        try:
            policy = await inventory.policy()
            sailings = await inventory.sailings_for(msg.cargo)

            def decide_fn(cargo, at):
                result = decide(cargo, sailings, policy, at)
                return result if isinstance(result, Decline) else (result, policy)

            kind, fields = build_reply(
                msg, sender=sender, carrier_id=carrier_id, carrier_address=agent.address,
                allowed_requesters=allowed_requesters, decide_fn=decide_fn, now=now,
            )
        except Exception as exc:  # never leave the Exchange waiting on an internal failure
            log.exception("quote failed")
            kind = "error"
            fields = {
                **reply_base(msg, carrier_id=carrier_id, carrier_address=agent.address, now=now),
                "error_code": ErrorCode.INTERNAL_ERROR,
                "detail": type(exc).__name__,
            }

        reply = models[kind](**fields)
        reply.signature = agent.sign_digest(payload_digest(reply))
        await ctx.send(sender, reply)
        ctx.logger.info(json.dumps({
            "event": "request_offer_handled", "carrier_id": carrier_id, "request_id": msg.request_id,
            "negotiation_id": msg.negotiation_id, "reply": reply.message_type,
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        }))

    agent.include(protocol, publish_manifest=False)
    return agent

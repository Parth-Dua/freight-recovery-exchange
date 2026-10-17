"""Company Carrier Agent.

REQUEST_OFFER -> signed PROPOSE_OFFER | REJECT_OFFER | AGENT_ERROR
COUNTER_OFFER -> signed ACCEPT_COUNTER | REVISED_OFFER | REJECT_COUNTER | AGENT_ERROR

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
    AcceptCounter,
    AgentError,
    CounterOffer,
    ErrorCode,
    ProposeOffer,
    RejectCounter,
    RejectOffer,
    RequestOffer,
    RevisedOffer,
    payload_digest,
)
from freight_contracts.messages import RejectReason
from uagents import Agent, Context, Protocol

from carrier_agent.inventory import CarrierInventory, LoggedOffer
from carrier_agent.policy import Decline, Policy, SailingInventory, decide, decide_counter

log = logging.getLogger("carrier_agent")

PROTOCOL_NAME = "freight-offer"
PROTOCOL_VERSION = "1.0.0"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def reply_base(msg: RequestOffer | CounterOffer, *, carrier_id: str, carrier_address: str, now: datetime) -> dict:
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


def counter_precheck(
    msg: CounterOffer,
    *,
    sender: str,
    allowed_requesters: set[str],
    logged: LoggedOffer | None,
    now: datetime,
) -> tuple[str, dict] | None:
    """Validation before the counter is claimed. Returns (kind, extra_fields) to reply with, or None."""
    if sender not in allowed_requesters:
        return "error", {"error_code": ErrorCode.UNAUTHORIZED_REQUESTER, "detail": "requester not authorized"}
    if msg.schema_version != SCHEMA_VERSION:
        return "error", {"error_code": ErrorCode.UNSUPPORTED_SCHEMA_VERSION, "detail": f"expected schema {SCHEMA_VERSION}"}
    if msg.expires_at <= now:
        return "error", {"error_code": ErrorCode.REQUEST_EXPIRED, "detail": "counter already expired"}
    if logged is None or (logged.negotiation_id, logged.request_id) != (msg.negotiation_id, msg.request_id):
        return "reject", {"reason_code": RejectReason.OFFER_NOT_FOUND, "reason": "no such offer from us in this negotiation"}
    if logged.expires_at <= now:
        return "reject", {"reason_code": RejectReason.OFFER_EXPIRED, "reason": "that offer has expired; request a new quote"}
    if logged.round != 1 or logged.counters_used >= 1:
        return "reject", {"reason_code": RejectReason.COUNTER_LIMIT_REACHED, "reason": "only one counter is allowed per offer"}
    if msg.currency != logged.currency:
        return "error", {"error_code": ErrorCode.MALFORMED_REQUEST, "detail": f"counter must be in {logged.currency}"}
    return None


def counter_offer_fields(
    logged: LoggedOffer, sailing: SailingInventory, *, total: int, policy: Policy, carrier_id: str, now: datetime
) -> dict:
    return {
        "offer_id": f"off_{carrier_id}_{uuid4().hex[:12]}",
        "supersedes_offer_id": logged.offer_id,
        "sailing_id": sailing.sailing_id,
        "vessel_name": sailing.vessel_name,
        "origin_port": sailing.origin_port,
        "destination_port": sailing.destination_port,
        "container_type": logged.container_type,
        "quantity": logged.quantity,
        "total_price_minor": total,
        "currency": logged.currency,
        "departure_at": sailing.departure_at,
        "arrival_at": sailing.arrival_at,
        "expires_at": now + timedelta(seconds=policy.offer_ttl_seconds),
        "round": 2,
        "terms": "Negotiated indicative quote; not a capacity hold or booking.",
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
        if kind == "offer":
            try:
                await inventory.log_offer(reply, negotiation_id=msg.negotiation_id, request_id=msg.request_id)
            except Exception:
                log.exception("offer log failed")
                reply = AgentError(**reply_base(msg, carrier_id=carrier_id, carrier_address=agent.address, now=now),
                                   error_code=ErrorCode.INTERNAL_ERROR, detail="could not record offer")
        reply.signature = agent.sign_digest(payload_digest(reply))
        await ctx.send(sender, reply)
        ctx.logger.info(json.dumps({
            "event": "request_offer_handled", "carrier_id": carrier_id, "request_id": msg.request_id,
            "negotiation_id": msg.negotiation_id, "reply": reply.message_type,
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        }))

    counter_models = {"accept": AcceptCounter, "revise": RevisedOffer, "reject": RejectCounter, "error": AgentError}

    @protocol.on_message(model=CounterOffer, replies={AcceptCounter, RevisedOffer, RejectCounter, AgentError})
    async def on_counter_offer(ctx: Context, sender: str, msg: CounterOffer) -> None:
        started = time.perf_counter()
        now = _now()
        base = reply_base(msg, carrier_id=carrier_id, carrier_address=agent.address, now=now)
        try:
            logged = await inventory.logged_offer(msg.offer_id)
            pre = counter_precheck(msg, sender=sender, allowed_requesters=allowed_requesters, logged=logged, now=now)
            if pre is not None:
                kind, extra = pre
            elif not await inventory.claim_counter(logged.offer_id):  # atomic: one counter per offer
                kind, extra = "reject", {"reason_code": RejectReason.COUNTER_LIMIT_REACHED,
                                         "reason": "only one counter is allowed per offer"}
            else:
                policy = await inventory.policy()
                sailing = await inventory.sailing(logged.sailing_id, logged.container_type)
                if sailing is None or sailing.available_units < logged.quantity:
                    kind, extra = "reject", {"reason_code": RejectReason.INSUFFICIENT_CAPACITY,
                                             "reason": "capacity on that sailing is no longer available"}
                else:
                    decision = decide_counter(logged.total_price_minor, logged.quantity,
                                              msg.proposed_total_price_minor, policy)
                    if decision.kind == "reject":
                        kind, extra = "reject", {"reason_code": decision.reason_code, "reason": decision.reason}
                    else:
                        kind = decision.kind
                        extra = counter_offer_fields(logged, sailing, total=decision.total_price_minor,
                                                     policy=policy, carrier_id=carrier_id, now=now)
            if kind == "reject":
                extra = {"offer_id": msg.offer_id, **extra}
            reply = counter_models[kind](**base, **extra)
            if kind in ("accept", "revise"):
                await inventory.log_offer(reply, negotiation_id=msg.negotiation_id, request_id=msg.request_id)
        except Exception as exc:
            log.exception("counter failed")
            reply = AgentError(**base, error_code=ErrorCode.INTERNAL_ERROR, detail=type(exc).__name__)

        reply.signature = agent.sign_digest(payload_digest(reply))
        await ctx.send(sender, reply)
        ctx.logger.info(json.dumps({
            "event": "counter_offer_handled", "carrier_id": carrier_id, "request_id": msg.request_id,
            "negotiation_id": msg.negotiation_id, "offer_id": msg.offer_id, "reply": reply.message_type,
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        }))

    agent.include(protocol, publish_manifest=False)
    return agent

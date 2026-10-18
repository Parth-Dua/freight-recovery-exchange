"""Company Carrier Agent.

REQUEST_OFFER -> signed PROPOSE_OFFER | REJECT_OFFER | AGENT_ERROR
COUNTER_OFFER -> signed ACCEPT_COUNTER | REVISED_OFFER | REJECT_COUNTER | AGENT_ERROR
REQUEST_RECOVERY_CAPACITY -> signed RECOVERY_CAPACITY_OFFERS | REJECT_OFFER | AGENT_ERROR (one-shot)

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
    BookingConfirmed,
    BookingRejected,
    ConfirmBooking,
    CounterOffer,
    HoldConfirmed,
    HoldRejected,
    HoldReleased,
    ReleaseHold,
    RequestHold,
    ErrorCode,
    ProposeOffer,
    RecoveryCapacityOffers,
    RejectCounter,
    RejectOffer,
    RequestOffer,
    RequestRecoveryCapacity,
    RevisedOffer,
    payload_digest,
)
from freight_contracts.messages import RejectReason
from uagents import Agent, Context, Protocol

from carrier_agent import holds
from carrier_agent.inventory import CarrierInventory, LoggedOffer
from carrier_agent.policy import (
    Decline, Policy, RecoveryQuote, SailingInventory, decide, decide_counter, decide_recovery, utilization,
)

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


def build_recovery_reply(
    msg: RequestRecoveryCapacity,
    *,
    sender: str,
    carrier_id: str,
    carrier_address: str,
    allowed_requesters: set[str],
    decide_fn: Callable,
    now: datetime,
) -> tuple[str, dict]:
    """Return (kind, fields) for a REQUEST_RECOVERY_CAPACITY reply; pure so it can be unit-tested.
    `decide_fn(items, now)` returns (list[RecoveryQuote], Policy) or a Decline."""
    base = reply_base(msg, carrier_id=carrier_id, carrier_address=carrier_address, now=now)
    err = envelope_precheck(msg, sender=sender, allowed_requesters=allowed_requesters, now=now)
    if err:
        return "error", {**base, **err}
    if any(i.latest_arrival <= now for i in msg.request_items):
        return "error", {**base, "error_code": ErrorCode.INVALID_CARGO_REQUEST,
                         "detail": "a request item's latest_arrival is in the past"}

    outcome = decide_fn(msg.request_items, now)
    if isinstance(outcome, Decline):
        return "reject", {**base, "reason_code": outcome.reason_code, "reason": outcome.reason}
    quotes, policy = outcome
    expires_at = now + timedelta(seconds=policy.offer_ttl_seconds)
    return "offers", {**base, "recovery_run_id": msg.recovery_run_id, "offers": [
        recovery_terms(q, policy=policy, carrier_id=carrier_id, expires_at=expires_at) for q in quotes]}


def recovery_terms(q: RecoveryQuote, *, policy: Policy, carrier_id: str, expires_at: datetime) -> dict:
    s = q.sailing
    return {
        "offer_id": f"rof_{carrier_id}_{uuid4().hex[:12]}",
        "sailing_id": s.sailing_id,
        "vessel_name": s.vessel_name,
        "origin_port": s.origin_port,
        "destination_port": s.destination_port,
        "container_type": q.container_type,
        "capacity_units": q.capacity_units,
        "total_price_minor": q.total_price_minor,
        "currency": policy.currency,
        "departure_at": s.departure_at,
        "arrival_at": s.arrival_at,
        "expires_at": expires_at,
        "eligible_booking_ids": list(q.eligible_booking_ids),
    }


def envelope_precheck(msg, *, sender: str, allowed_requesters: set[str], now: datetime) -> dict | None:
    """Authorization / schema / expiry checks shared by hold, release and confirm. Returns AGENT_ERROR fields."""
    if sender not in allowed_requesters:
        return {"error_code": ErrorCode.UNAUTHORIZED_REQUESTER, "detail": "requester not authorized"}
    if msg.schema_version != SCHEMA_VERSION:
        return {"error_code": ErrorCode.UNSUPPORTED_SCHEMA_VERSION, "detail": f"expected schema {SCHEMA_VERSION}"}
    if msg.expires_at <= now:
        return {"error_code": ErrorCode.REQUEST_EXPIRED, "detail": "request already expired"}
    return None


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
    if logged.offer_kind == "recovery":
        return "reject", {"reason_code": RejectReason.COUNTER_LIMIT_REACHED,
                          "reason": "recovery offers are one-shot; they cannot be countered"}
    if logged.expires_at <= now:
        return "reject", {"reason_code": RejectReason.OFFER_EXPIRED, "reason": "that offer has expired; request a new quote"}
    if logged.round >= 3 or logged.counters_used >= 1:  # v2: two buyer counters per session at most
        return "reject", {"reason_code": RejectReason.COUNTER_LIMIT_REACHED,
                          "reason": "no further counters are allowed in this negotiation"}
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
        "round": logged.round + 1,
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
                result = decide(cargo, sailings, policy, at, recovery=msg.kind == "recovery")
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
                    opening = await inventory.opening_total(logged.negotiation_id) or logged.total_price_minor
                    decision = decide_counter(opening, logged.quantity, msg.proposed_total_price_minor, policy,
                                              current_total=logged.total_price_minor, util=utilization(sailing))
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

    async def _send(ctx: Context, sender: str, reply, msg, event: str, started: float) -> None:
        reply.signature = agent.sign_digest(payload_digest(reply))
        await ctx.send(sender, reply)
        ctx.logger.info(json.dumps({
            "event": event, "carrier_id": carrier_id, "request_id": msg.request_id, "reply": reply.message_type,
            "code": getattr(reply, "code", None), "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        }))

    recovery_models = {"offers": RecoveryCapacityOffers, "reject": RejectOffer, "error": AgentError}

    @protocol.on_message(model=RequestRecoveryCapacity, replies={RecoveryCapacityOffers, RejectOffer, AgentError})
    async def on_request_recovery(ctx: Context, sender: str, msg: RequestRecoveryCapacity) -> None:
        started, now = time.perf_counter(), _now()
        base = reply_base(msg, carrier_id=carrier_id, carrier_address=agent.address, now=now)
        try:
            policy = await inventory.policy()
            sailings_by_type: dict[str, list[SailingInventory]] = {}
            for origin, destination, ctype in sorted({(i.origin_port, i.destination_port, i.container_type)
                                                      for i in msg.request_items}):
                sailings_by_type.setdefault(ctype, []).extend(
                    await inventory.sailings_on_lane(origin, destination, ctype))

            def decide_fn(items, at):
                result = decide_recovery(items, sailings_by_type, policy, at,
                                         withdrawn_sailing_id=msg.withdrawn_sailing_id)
                return result if isinstance(result, Decline) else (result, policy)

            kind, fields = build_recovery_reply(
                msg, sender=sender, carrier_id=carrier_id, carrier_address=agent.address,
                allowed_requesters=allowed_requesters, decide_fn=decide_fn, now=now)
            reply = recovery_models[kind](**fields)
            if kind == "offers":
                await inventory.log_recovery_offers(reply)
        except Exception as exc:
            log.exception("recovery offer failed")
            reply = AgentError(**base, error_code=ErrorCode.INTERNAL_ERROR, detail=type(exc).__name__)
        reply.signature = agent.sign_digest(payload_digest(reply))
        await ctx.send(sender, reply)
        ctx.logger.info(json.dumps({
            "event": "request_recovery_handled", "carrier_id": carrier_id, "recovery_run_id": msg.recovery_run_id,
            "reply": reply.message_type, "offers": len(getattr(reply, "offers", []) or []),
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        }))

    @protocol.on_message(model=RequestHold, replies={HoldConfirmed, HoldRejected, AgentError})
    async def on_request_hold(ctx: Context, sender: str, msg: RequestHold) -> None:
        started, now = time.perf_counter(), _now()
        base = reply_base(msg, carrier_id=carrier_id, carrier_address=agent.address, now=now)
        try:
            err = envelope_precheck(msg, sender=sender, allowed_requesters=allowed_requesters, now=now)
            if err:
                reply = AgentError(**base, **err)
            else:
                policy = await inventory.policy()
                out = await holds.request_hold(
                    inventory.pool, carrier_id, offer_id=msg.offer_id, request_id=msg.request_id,
                    buyer_id=msg.buyer_id, quantity=msg.quantity, idempotency_key=msg.idempotency_key,
                    ttl_seconds=min(msg.requested_ttl_seconds, policy.max_hold_seconds),
                    recovery_offer_id=msg.recovery_offer_id, run_id=msg.negotiation_id)
                reply = (HoldConfirmed(**base, hold_id=out.hold_id, offer_id=msg.offer_id, sailing_id=out.sailing_id,
                                       quantity=out.quantity, expires_at=out.expires_at, replay=out.replay)
                         if out.ok else
                         HoldRejected(**base, offer_id=msg.offer_id, code=out.code, detail=out.detail))
        except Exception as exc:
            log.exception("hold failed")
            reply = AgentError(**base, error_code=ErrorCode.INTERNAL_ERROR, detail=type(exc).__name__)
        await _send(ctx, sender, reply, msg, "request_hold_handled", started)

    @protocol.on_message(model=ReleaseHold, replies={HoldReleased, AgentError})
    async def on_release_hold(ctx: Context, sender: str, msg: ReleaseHold) -> None:
        started, now = time.perf_counter(), _now()
        base = reply_base(msg, carrier_id=carrier_id, carrier_address=agent.address, now=now)
        try:
            err = envelope_precheck(msg, sender=sender, allowed_requesters=allowed_requesters, now=now)
            if err:
                reply = AgentError(**base, **err)
            else:
                out = await holds.release_hold(inventory.pool, carrier_id, hold_id=msg.hold_id)
                reply = (HoldReleased(**base, hold_id=msg.hold_id, final_status=out.code.removeprefix("HOLD_"))
                         if out.ok else AgentError(**base, error_code=ErrorCode.MALFORMED_REQUEST, detail=out.code))
        except Exception as exc:
            log.exception("release failed")
            reply = AgentError(**base, error_code=ErrorCode.INTERNAL_ERROR, detail=type(exc).__name__)
        await _send(ctx, sender, reply, msg, "release_hold_handled", started)

    @protocol.on_message(model=ConfirmBooking, replies={BookingConfirmed, BookingRejected, AgentError})
    async def on_confirm_booking(ctx: Context, sender: str, msg: ConfirmBooking) -> None:
        started, now = time.perf_counter(), _now()
        base = reply_base(msg, carrier_id=carrier_id, carrier_address=agent.address, now=now)
        try:
            err = envelope_precheck(msg, sender=sender, allowed_requesters=allowed_requesters, now=now)
            if err:
                reply = AgentError(**base, **err)
            else:
                policy = await inventory.policy()
                out = await holds.confirm_booking(
                    inventory.pool, carrier_id, hold_id=msg.hold_id, payment_reference=msg.payment_reference,
                    idempotency_key=msg.idempotency_key, intent_id=msg.intent_id,
                    claim_code_hash=msg.claim_code_hash, payment_terms=policy.payment_terms,
                    replaces_booking_id=msg.replaces_booking_id)
                reply = (BookingConfirmed(**base, booking_id=out.booking_id, hold_id=msg.hold_id,
                                          confirmed_at=out.confirmed_at, payment_terms=policy.payment_terms,
                                          replay=out.replay)
                         if out.ok else BookingRejected(**base, hold_id=msg.hold_id, code=out.code, detail=out.detail))
        except Exception as exc:
            log.exception("confirm failed")
            reply = AgentError(**base, error_code=ErrorCode.INTERNAL_ERROR, detail=type(exc).__name__)
        await _send(ctx, sender, reply, msg, "confirm_booking_handled", started)

    agent.include(protocol, publish_manifest=False)
    return agent

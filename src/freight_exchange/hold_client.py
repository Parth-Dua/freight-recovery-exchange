"""ACP implementation of `HoldClient`: REQUEST_HOLD / RELEASE_HOLD / CONFIRM_BOOKING to the
owning carrier over the same signed uAgents sync transport as quotes (decision 0002).

Every reply is checked: registered carrier identity, carrier signature, correlation to this
exact request, and (for holds) the same offer and quantity. Anything else is a typed failure.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable
from uuid import uuid4

from freight_contracts import (
    AgentError, BookingConfirmed, BookingRejected, ConfirmBooking, HoldConfirmed, HoldRejected, HoldReleased,
    MalformedReply, ReleaseHold, RequestHold, parse_carrier_reply, verify_model,
)
from uagents_core.identity import Identity

from freight_exchange.booking import ConfirmResult, HoldResult
from freight_exchange.carrier_client import ALMANAC_LOOKUP_TIMEOUT_S, CarrierRef, TransportError, UAgentsSyncTransport

Audit = Callable[..., Awaitable[None]]


class AcpHoldClient:
    def __init__(self, identity: Identity, transport=None, timeout_s: float = 10.0, audit: Audit | None = None):
        self.identity = identity
        self.transport = transport or UAgentsSyncTransport()
        self.timeout_s = timeout_s
        self.audit = audit

    def _envelope(self, *, request_id: str, negotiation_id: str, correlation_id: str) -> dict:
        now = datetime.now(timezone.utc)
        return dict(message_id=f"msg_{uuid4().hex}", correlation_id=correlation_id, request_id=request_id,
                    negotiation_id=negotiation_id, sent_at=now, expires_at=now + timedelta(seconds=self.timeout_s))

    async def _exchange(self, carrier: CarrierRef, message, expected: tuple[type, ...]):
        """Send and validate. Returns (reply | None, code, detail, latency_ms)."""
        started = time.perf_counter()
        if self.audit:
            await self.audit(message=message, carrier=carrier, direction="OUTBOUND", status="SENT")
        try:
            raw, _ = await asyncio.wait_for(
                self.transport.request(carrier, message, self.identity, self.timeout_s),
                self.timeout_s + ALMANAC_LOOKUP_TIMEOUT_S)
            reply = parse_carrier_reply(raw)
            if not isinstance(reply, expected + (AgentError,)):
                raise MalformedReply(f"unexpected {reply.message_type}")
        except asyncio.TimeoutError:
            return None, "CARRIER_TIMEOUT", f"no reply within {self.timeout_s:.0f}s", self._ms(started)
        except TransportError as exc:
            return None, exc.code.value, exc.detail, self._ms(started)
        except MalformedReply as exc:
            return None, "MALFORMED_REPLY", str(exc)[:200], self._ms(started)
        except Exception as exc:
            return None, "CARRIER_UNREACHABLE", type(exc).__name__, self._ms(started)

        latency = self._ms(started)
        code = None
        if reply.carrier_id != carrier.carrier_id or reply.carrier_address != carrier.agent_address:
            code = "UNKNOWN_CARRIER"
        elif not verify_model(reply, carrier.agent_address):
            code = "INVALID_SIGNATURE"
        elif (reply.in_reply_to, reply.request_id) != (message.message_id, message.request_id):
            code = "CORRELATION_MISMATCH"
        if self.audit:
            await self.audit(message=reply, carrier=carrier, direction="INBOUND", status=code or "VERIFIED",
                             in_reply_to=message.message_id, latency_ms=latency)
        if code:
            return None, code, "carrier reply failed verification", latency
        if isinstance(reply, AgentError):
            return None, reply.error_code.value, reply.detail, latency
        return reply, None, "", latency

    @staticmethod
    def _ms(started: float) -> int:
        return round((time.perf_counter() - started) * 1000)

    async def request_hold(self, carrier: CarrierRef, *, offer_id: str, request_id: str, buyer_id: str,
                           quantity: int, idempotency_key: str, ttl_seconds: int, negotiation_id: str = "",
                           correlation_id: str = "", recovery_offer_id: str | None = None) -> HoldResult:
        msg = RequestHold(**self._envelope(request_id=request_id, negotiation_id=negotiation_id,
                                           correlation_id=correlation_id),
                          offer_id=offer_id, quantity=quantity, buyer_id=buyer_id, idempotency_key=idempotency_key,
                          requested_ttl_seconds=ttl_seconds, recovery_offer_id=recovery_offer_id)
        reply, code, detail, _ = await self._exchange(carrier, msg, (HoldConfirmed, HoldRejected))
        if reply is None:
            return HoldResult(False, code, detail)
        if isinstance(reply, HoldRejected):
            return HoldResult(False, reply.code, reply.detail)
        if (reply.offer_id, reply.quantity) != (offer_id, quantity) or reply.expires_at <= datetime.now(timezone.utc):
            return HoldResult(False, "HOLD_TERMS_MISMATCH", "hold does not match the requested offer/quantity")
        return HoldResult(True, "HOLD_CONFIRMED", hold_id=reply.hold_id, expires_at=reply.expires_at)

    async def release_hold(self, carrier: CarrierRef, *, hold_id: str, request_id: str = "",
                           negotiation_id: str = "", correlation_id: str = "") -> HoldResult:
        msg = ReleaseHold(**self._envelope(request_id=request_id, negotiation_id=negotiation_id,
                                           correlation_id=correlation_id), hold_id=hold_id)
        reply, code, detail, _ = await self._exchange(carrier, msg, (HoldReleased,))
        if reply is None:
            return HoldResult(False, code, detail, hold_id=hold_id)
        return HoldResult(True, f"HOLD_{reply.final_status}", hold_id=hold_id)

    async def confirm_booking(self, carrier: CarrierRef, *, hold_id: str, payment_reference: str,
                              idempotency_key: str, intent_id: str, claim_code_hash: str, request_id: str = "",
                              negotiation_id: str = "", correlation_id: str = "",
                              replaces_booking_id: str | None = None) -> ConfirmResult:
        msg = ConfirmBooking(**self._envelope(request_id=request_id, negotiation_id=negotiation_id,
                                              correlation_id=correlation_id),
                             hold_id=hold_id, payment_reference=payment_reference, idempotency_key=idempotency_key,
                             intent_id=intent_id, claim_code_hash=claim_code_hash,
                             replaces_booking_id=replaces_booking_id)
        reply, code, detail, _ = await self._exchange(carrier, msg, (BookingConfirmed, BookingRejected))
        if reply is None:
            return ConfirmResult(False, code, detail)
        if isinstance(reply, BookingRejected):
            return ConfirmResult(False, reply.code, reply.detail)
        if reply.hold_id != hold_id:
            return ConfirmResult(False, "CORRELATION_MISMATCH", "confirmation names a different hold")
        return ConfirmResult(True, "BOOKING_CONFIRMED", booking_id=reply.booking_id, confirmed_at=reply.confirmed_at,
                             detail=reply.payment_terms)

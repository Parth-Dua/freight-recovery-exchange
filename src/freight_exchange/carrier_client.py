"""Exchange → Carrier Agent transport and reply validation (decision 0002).

Sends typed, signed REQUEST_OFFER envelopes to each carrier over uAgents sync
messaging (``x-uagents-connection: sync``) in parallel, each with a deadline.
Every reply is checked in a fixed order and turned into a typed outcome:
transport → parse → carrier identity → carrier signature → correlation →
freshness → offer constraints. Nothing here writes to Tiger; see quotes.py.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Protocol
from uuid import uuid4

import httpx
from freight_contracts import (
    AcceptCounter,
    AgentError,
    CargoSpec,
    CounterOffer,
    ErrorCode,
    MalformedReply,
    OfferTerms,
    ProposeOffer,
    RejectCounter,
    RejectOffer,
    RequestOffer,
    RevisedOffer,
    parse_carrier_reply,
    verify_model,
)
from uagents_core.envelope import Envelope
from uagents_core.identity import Identity
from uagents_core.models import ERROR_MESSAGE_DIGEST, Model
from uagents_core.utils.resolver import lookup_endpoint_for_agent_async

ALMANAC_LOOKUP_TIMEOUT_S = 3.0


@dataclass(frozen=True)
class CarrierRef:
    carrier_id: str
    display_name: str
    agent_address: str
    endpoint_url: str | None


@dataclass
class CounterOutcome:
    carrier: CarrierRef
    counter: CounterOffer
    opening: OfferTerms
    kind: str  # accept | revise | reject | carrier_error | exchange_error
    latency_ms: int
    reply: AcceptCounter | RevisedOffer | RejectCounter | AgentError | None = None
    error_code: ErrorCode | None = None
    detail: str = ""
    signature_verified: bool = False
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def new_offer(self) -> AcceptCounter | RevisedOffer | None:
        return self.reply if self.kind in ("accept", "revise") else None


@dataclass
class CarrierOutcome:
    carrier: CarrierRef
    request: RequestOffer
    kind: str  # offer | reject | carrier_error | exchange_error
    latency_ms: int
    resolution: str  # almanac | registry_fallback | none
    reply: ProposeOffer | RejectOffer | AgentError | None = None
    error_code: ErrorCode | None = None
    detail: str = ""
    signature_verified: bool = False
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def offer(self) -> ProposeOffer | None:
        return self.reply if self.kind == "offer" else None


class TransportError(Exception):
    def __init__(self, code: ErrorCode, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


class Transport(Protocol):
    async def request(
        self, carrier: CarrierRef, message: Model, sender: Identity, timeout_s: float
    ) -> tuple[str, str]:
        """Return (reply payload JSON, resolution label) or raise TransportError."""


class UAgentsSyncTransport:
    """uAgents sync request/reply using only uagents-core (no full uagents dependency)."""

    async def resolve(self, carrier: CarrierRef) -> tuple[str, str]:
        try:
            endpoints = await asyncio.wait_for(
                lookup_endpoint_for_agent_async(carrier.agent_address), ALMANAC_LOOKUP_TIMEOUT_S
            )
        except Exception:
            endpoints = []
        if endpoints:
            return endpoints[0], "almanac"
        if carrier.endpoint_url:
            return carrier.endpoint_url, "registry_fallback"
        raise TransportError(ErrorCode.CARRIER_UNREACHABLE, "no endpoint in Almanac or registry")

    async def request(
        self, carrier: CarrierRef, message: Model, sender: Identity, timeout_s: float
    ) -> tuple[str, str]:
        endpoint, resolution = await self.resolve(carrier)
        env = Envelope(
            version=1,
            sender=sender.address,
            target=carrier.agent_address,
            session=uuid4(),
            schema_digest=Model.build_schema_digest(message),
            expires=int(time.time() + timeout_s),
        )
        env.encode_payload(message.json())
        env.sign(sender)
        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                resp = await client.post(
                    endpoint,
                    content=env.model_dump_json(),
                    headers={"content-type": "application/json", "x-uagents-connection": "sync"},
                )
        except httpx.TimeoutException as exc:
            raise TransportError(ErrorCode.CARRIER_TIMEOUT, f"no reply within {timeout_s:.0f}s") from exc
        except httpx.HTTPError as exc:
            raise TransportError(ErrorCode.CARRIER_UNREACHABLE, type(exc).__name__) from exc
        if resp.status_code >= 400:
            raise TransportError(ErrorCode.CARRIER_UNREACHABLE, f"HTTP {resp.status_code}")
        try:
            reply_env = Envelope.model_validate(resp.json())
            payload = reply_env.decode_payload()
        except Exception as exc:
            raise TransportError(ErrorCode.MALFORMED_REPLY, "reply is not a valid envelope") from exc
        if reply_env.sender != carrier.agent_address:
            raise TransportError(ErrorCode.UNKNOWN_CARRIER, "reply envelope from unexpected address")
        if reply_env.schema_digest == ERROR_MESSAGE_DIGEST:
            code = ErrorCode.CARRIER_TIMEOUT if "expired" in payload.lower() else ErrorCode.MALFORMED_REPLY
            raise TransportError(code, f"carrier runtime error: {payload[:120]}")
        return payload, resolution


def build_request(
    cargo: CargoSpec, *, request_id: str, correlation_id: str, now: datetime, timeout_s: float
) -> RequestOffer:
    return RequestOffer(
        message_id=f"msg_{uuid4().hex}",
        correlation_id=correlation_id,
        request_id=request_id,
        negotiation_id=f"neg_{uuid4().hex[:16]}",
        sent_at=now,
        expires_at=now + timedelta(seconds=timeout_s),
        cargo=cargo,
    )


def validate_reply(
    raw: str, carrier: CarrierRef, request: RequestOffer, now: datetime
) -> tuple[str, ProposeOffer | RejectOffer | AgentError | None, ErrorCode | None, str, bool]:
    """Return (kind, reply, error_code, detail, signature_verified)."""
    try:
        reply = parse_carrier_reply(raw)
    except MalformedReply as exc:
        return "exchange_error", None, ErrorCode.MALFORMED_REPLY, str(exc)[:200], False

    if reply.carrier_id != carrier.carrier_id or reply.carrier_address != carrier.agent_address:
        return "exchange_error", reply, ErrorCode.UNKNOWN_CARRIER, "reply identity does not match registered carrier", False
    if not verify_model(reply, carrier.agent_address):
        return "exchange_error", reply, ErrorCode.INVALID_SIGNATURE, "carrier signature did not verify", False
    if (reply.in_reply_to, reply.request_id, reply.negotiation_id, reply.correlation_id) != (
        request.message_id, request.request_id, request.negotiation_id, request.correlation_id
    ):
        return "exchange_error", reply, ErrorCode.CORRELATION_MISMATCH, "reply does not answer this request", True

    if isinstance(reply, AgentError):
        return "carrier_error", reply, reply.error_code, reply.detail, True
    if isinstance(reply, RejectOffer):
        return "reject", reply, None, reply.reason, True

    if reply.expires_at <= now:
        return "exchange_error", reply, ErrorCode.STALE_OFFER, "offer already expired", True
    violation = offer_violation(reply, request.cargo)
    if violation:
        return "exchange_error", reply, ErrorCode.OFFER_CONSTRAINT_VIOLATION, violation, True
    return "offer", reply, None, "", True


def build_counter(opening: OfferTerms, request: RequestOffer, proposed: int, now: datetime, timeout_s: float) -> CounterOffer:
    return CounterOffer(
        message_id=f"msg_{uuid4().hex}",
        correlation_id=request.correlation_id,
        request_id=request.request_id,
        negotiation_id=request.negotiation_id,
        sent_at=now,
        expires_at=now + timedelta(seconds=timeout_s),
        offer_id=opening.offer_id,
        proposed_total_price_minor=proposed,
        currency=opening.currency,
    )


_SAME_TERMS = ("sailing_id", "origin_port", "destination_port", "container_type", "quantity", "currency",
               "departure_at", "arrival_at")


def validate_counter_reply(
    raw: str, carrier: CarrierRef, counter: CounterOffer, opening: OfferTerms, cargo: CargoSpec, now: datetime
) -> tuple[str, object, ErrorCode | None, str, bool]:
    """Return (kind, reply, error_code, detail, signature_verified) for a counter reply."""
    try:
        reply = parse_carrier_reply(raw)
    except MalformedReply as exc:
        return "exchange_error", None, ErrorCode.MALFORMED_REPLY, str(exc)[:200], False
    if not isinstance(reply, (AcceptCounter, RevisedOffer, RejectCounter, AgentError)):
        return "exchange_error", reply, ErrorCode.MALFORMED_REPLY, f"unexpected {reply.message_type} to a counter", False
    if reply.carrier_id != carrier.carrier_id or reply.carrier_address != carrier.agent_address:
        return "exchange_error", reply, ErrorCode.UNKNOWN_CARRIER, "reply identity does not match registered carrier", False
    if not verify_model(reply, carrier.agent_address):
        return "exchange_error", reply, ErrorCode.INVALID_SIGNATURE, "carrier signature did not verify", False
    if (reply.in_reply_to, reply.request_id, reply.negotiation_id, reply.correlation_id) != (
        counter.message_id, counter.request_id, counter.negotiation_id, counter.correlation_id
    ):
        return "exchange_error", reply, ErrorCode.CORRELATION_MISMATCH, "reply does not answer this counter", True
    if isinstance(reply, AgentError):
        return "carrier_error", reply, reply.error_code, reply.detail, True
    if isinstance(reply, RejectCounter):
        if reply.offer_id != opening.offer_id:
            return "exchange_error", reply, ErrorCode.CORRELATION_MISMATCH, "rejection names a different offer", True
        return "reject", reply, None, reply.reason, True

    # ACCEPT_COUNTER / REVISED_OFFER: a fresh offer on exactly the same terms, cheaper or equal
    if reply.supersedes_offer_id != opening.offer_id:
        return "exchange_error", reply, ErrorCode.CORRELATION_MISMATCH, "new offer supersedes a different offer", True
    changed = [f for f in _SAME_TERMS if getattr(reply, f) != getattr(opening, f)]
    if changed:
        return "exchange_error", reply, ErrorCode.OFFER_CONSTRAINT_VIOLATION, f"terms changed: {', '.join(changed)}", True
    if reply.total_price_minor > opening.total_price_minor:
        return "exchange_error", reply, ErrorCode.OFFER_CONSTRAINT_VIOLATION, "negotiated price is above the opening offer", True
    if isinstance(reply, AcceptCounter) and reply.total_price_minor != min(counter.proposed_total_price_minor,
                                                                         opening.total_price_minor):
        return "exchange_error", reply, ErrorCode.OFFER_CONSTRAINT_VIOLATION, "accepted price differs from counter", True
    if isinstance(reply, RevisedOffer) and reply.total_price_minor < counter.proposed_total_price_minor:
        return "exchange_error", reply, ErrorCode.OFFER_CONSTRAINT_VIOLATION, "revised price below counter (should accept)", True
    if reply.expires_at <= now:
        return "exchange_error", reply, ErrorCode.STALE_OFFER, "negotiated offer already expired", True
    violation = offer_violation(reply, cargo)
    if violation:
        return "exchange_error", reply, ErrorCode.OFFER_CONSTRAINT_VIOLATION, violation, True
    return ("accept" if isinstance(reply, AcceptCounter) else "revise"), reply, None, "", True


def offer_violation(offer: OfferTerms, cargo: CargoSpec) -> str | None:
    if (offer.origin_port, offer.destination_port) != (cargo.origin_port, cargo.destination_port):
        return "offer is for a different corridor"
    if offer.container_type != cargo.container_type:
        return "offer is for a different container type"
    if offer.currency != cargo.currency:
        return "offer currency differs from request"
    if offer.quantity > cargo.quantity or (offer.quantity < cargo.quantity and not cargo.allow_split):
        return "offer quantity does not match request"
    if cargo.latest_arrival and offer.arrival_at > cargo.latest_arrival:
        return "offer arrives after the requested deadline"
    if cargo.earliest_departure and offer.departure_at < cargo.earliest_departure:
        return "offer departs before the requested window"
    if cargo.max_total_price_minor and offer.total_price_minor > cargo.max_total_price_minor:
        return "offer exceeds budget"
    if offer.arrival_at <= offer.departure_at:
        return "offer arrival is not after departure"
    return None


class CarrierClient:
    def __init__(
        self,
        identity: Identity,
        transport: Transport | None = None,
        timeout_s: float = 8.0,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self.identity = identity
        self.transport = transport or UAgentsSyncTransport()
        self.timeout_s = timeout_s
        self.clock = clock

    async def request_one(
        self,
        carrier: CarrierRef,
        request: RequestOffer,
        on_sent: Callable[[CarrierRef, RequestOffer], Awaitable[None]] | None = None,
    ) -> CarrierOutcome:
        if on_sent:
            await on_sent(carrier, request)
        started = time.perf_counter()  # carrier round-trip only, excluding our own Tiger write

        def elapsed() -> int:
            return round((time.perf_counter() - started) * 1000)
        try:
            raw, resolution = await asyncio.wait_for(
                self.transport.request(carrier, request, self.identity, self.timeout_s),
                self.timeout_s + ALMANAC_LOOKUP_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            return CarrierOutcome(carrier, request, "exchange_error", elapsed(), "none",
                                  error_code=ErrorCode.CARRIER_TIMEOUT, detail=f"no reply within {self.timeout_s:.0f}s")
        except TransportError as exc:
            return CarrierOutcome(carrier, request, "exchange_error", elapsed(), "none",
                                  error_code=exc.code, detail=exc.detail)
        except Exception as exc:
            return CarrierOutcome(carrier, request, "exchange_error", elapsed(), "none",
                                  error_code=ErrorCode.CARRIER_UNREACHABLE, detail=type(exc).__name__)

        kind, reply, code, detail, verified = validate_reply(raw, carrier, request, self.clock())
        return CarrierOutcome(carrier, request, kind, elapsed(), resolution, reply=reply,
                              error_code=code, detail=detail, signature_verified=verified)

    async def counter_one(
        self,
        carrier: CarrierRef,
        opening: OfferTerms,
        request: RequestOffer,
        proposed: int,
        on_sent: Callable[[CarrierRef, CounterOffer], Awaitable[None]] | None = None,
    ) -> CounterOutcome:
        counter = build_counter(opening, request, proposed, self.clock(), self.timeout_s)
        if on_sent:
            await on_sent(carrier, counter)
        started = time.perf_counter()

        def outcome(kind, **kw) -> CounterOutcome:
            return CounterOutcome(carrier, counter, opening, kind, round((time.perf_counter() - started) * 1000), **kw)

        try:
            raw, _ = await asyncio.wait_for(
                self.transport.request(carrier, counter, self.identity, self.timeout_s),
                self.timeout_s + ALMANAC_LOOKUP_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            return outcome("exchange_error", error_code=ErrorCode.CARRIER_TIMEOUT,
                           detail=f"no reply within {self.timeout_s:.0f}s")
        except TransportError as exc:
            return outcome("exchange_error", error_code=exc.code, detail=exc.detail)
        except Exception as exc:
            return outcome("exchange_error", error_code=ErrorCode.CARRIER_UNREACHABLE, detail=type(exc).__name__)
        kind, reply, code, detail, verified = validate_counter_reply(
            raw, carrier, counter, opening, request.cargo, self.clock())
        return outcome(kind, reply=reply, error_code=code, detail=detail, signature_verified=verified)

    async def request_offers(
        self,
        carriers: list[CarrierRef],
        cargo: CargoSpec,
        *,
        request_id: str,
        correlation_id: str,
        on_sent: Callable[[CarrierRef, RequestOffer], Awaitable[None]] | None = None,
    ) -> list[CarrierOutcome]:
        now = self.clock()
        requests = [
            build_request(cargo, request_id=request_id, correlation_id=correlation_id, now=now,
                          timeout_s=self.timeout_s)
            for _ in carriers
        ]
        return list(await asyncio.gather(*(
            self.request_one(c, r, on_sent) for c, r in zip(carriers, requests)
        )))

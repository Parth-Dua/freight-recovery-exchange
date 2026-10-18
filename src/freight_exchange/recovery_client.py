"""Exchange -> Carrier one-shot recovery round (spec 06 WP1, recovery step 3 in OWNERSHIP.md).

Sends one signed REQUEST_RECOVERY_CAPACITY to every active carrier in parallel, with a
deadline, over the same uAgents sync transport as quotes (decision 0002). Recovery offers
are final: this module has no counter path, and the carriers refuse counters on them.

Checks here are about the *message* (Parth's side): transport, parse, carrier identity,
signature, correlation, freshness, and each offer's terms against the Tiger sailing record
and the request. Whether the carrier really has the capacity it claims is checked inside
AllocationSeam (Tisya's side). Nothing here writes to Tiger; see recovery.py.
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable
from uuid import uuid4

from freight_contracts import (
    AgentError, ErrorCode, MalformedReply, RecoveryCapacityOffers, RecoveryCapacityTerms, RecoveryItem,
    RejectOffer, RequestRecoveryCapacity, parse_carrier_reply, verify_model,
)
from freight_seams import DisruptedBooking, RecoveryOffer
from uagents_core.identity import Identity

from freight_exchange.carrier_client import (
    ALMANAC_LOOKUP_TIMEOUT_S, CarrierRef, Transport, TransportError, UAgentsSyncTransport,
)


@dataclass(frozen=True)
class SailingFacts:
    """The Tiger record an offered sailing is checked against."""

    sailing_id: str
    carrier_id: str
    origin_port: str
    destination_port: str
    departure_at: datetime
    arrival_at: datetime
    status: str


@dataclass(frozen=True)
class OfferCheck:
    terms: RecoveryCapacityTerms
    error_code: ErrorCode | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.error_code is None


@dataclass
class RecoveryReplyOutcome:
    carrier: CarrierRef
    request: RequestRecoveryCapacity
    kind: str  # offers | reject | carrier_error | exchange_error
    latency_ms: int
    reply: RecoveryCapacityOffers | RejectOffer | AgentError | None = None
    error_code: ErrorCode | None = None
    detail: str = ""
    signature_verified: bool = False
    checks: list[OfferCheck] = field(default_factory=list)
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def valid_offers(self) -> list[RecoveryOffer]:
        """Offers that passed every message-level check, in the AllocationSeam's input shape."""
        return [to_recovery_offer(c.terms, self.carrier.carrier_id) for c in self.checks if c.ok]


def to_recovery_offer(terms: RecoveryCapacityTerms, carrier_id: str) -> RecoveryOffer:
    return RecoveryOffer(
        offer_id=terms.offer_id,
        carrier_id=carrier_id,
        sailing_id=terms.sailing_id,
        capacity_units=terms.capacity_units,
        total_price_minor=terms.total_price_minor,
        currency=terms.currency,
        arrival_at=terms.arrival_at,
        expires_at=terms.expires_at,
        eligible_booking_ids=tuple(terms.eligible_booking_ids),
    )


def recovery_items(stranded: list[DisruptedBooking]) -> list[RecoveryItem]:
    """Hard requirements only. Price envelopes stay with the Exchange (decision 0007)."""
    return [
        RecoveryItem(booking_id=b.booking_id, container_type=b.container_type, quantity=b.quantity,
                     origin_port=b.origin_port, destination_port=b.destination_port,
                     latest_arrival=b.latest_arrival, allow_split=b.allow_split, currency=b.currency)
        for b in sorted(stranded, key=lambda b: b.booking_id)
    ]


def build_recovery_request(
    *, recovery_run_id: str, withdrawn_sailing_id: str, items: list[RecoveryItem], correlation_id: str,
    now: datetime, timeout_s: float,
) -> RequestRecoveryCapacity:
    return RequestRecoveryCapacity(
        message_id=f"msg_{uuid4().hex}",
        correlation_id=correlation_id,
        request_id="",  # a recovery has no cargo request; the run id travels as negotiation_id
        negotiation_id=recovery_run_id,
        sent_at=now,
        expires_at=now + timedelta(seconds=timeout_s),
        recovery_run_id=recovery_run_id,
        withdrawn_sailing_id=withdrawn_sailing_id,
        request_items=items,
    )


def validate_recovery_envelope(
    raw: str, carrier: CarrierRef, request: RequestRecoveryCapacity,
) -> tuple[str, object, ErrorCode | None, str, bool]:
    """Return (kind, reply, error_code, detail, signature_verified). Offer terms are checked separately."""
    try:
        reply = parse_carrier_reply(raw)
    except MalformedReply as exc:
        return "exchange_error", None, ErrorCode.MALFORMED_REPLY, str(exc)[:200], False
    if not isinstance(reply, (RecoveryCapacityOffers, RejectOffer, AgentError)):
        return "exchange_error", reply, ErrorCode.MALFORMED_REPLY, f"unexpected {reply.message_type} to a recovery request", False
    if reply.carrier_id != carrier.carrier_id or reply.carrier_address != carrier.agent_address:
        return "exchange_error", reply, ErrorCode.UNKNOWN_CARRIER, "reply identity does not match registered carrier", False
    if not verify_model(reply, carrier.agent_address):
        return "exchange_error", reply, ErrorCode.INVALID_SIGNATURE, "carrier signature did not verify", False
    if (reply.in_reply_to, reply.request_id, reply.negotiation_id, reply.correlation_id) != (
        request.message_id, request.request_id, request.negotiation_id, request.correlation_id
    ) or (isinstance(reply, RecoveryCapacityOffers) and reply.recovery_run_id != request.recovery_run_id):
        return "exchange_error", reply, ErrorCode.CORRELATION_MISMATCH, "reply does not answer this recovery request", True
    if isinstance(reply, AgentError):
        return "carrier_error", reply, reply.error_code, reply.detail, True
    if isinstance(reply, RejectOffer):
        return "reject", reply, None, reply.reason, True
    return "offers", reply, None, "", True


def offer_violation(
    terms: RecoveryCapacityTerms, *, carrier: CarrierRef, request: RequestRecoveryCapacity,
    facts: SailingFacts | None, now: datetime,
) -> tuple[ErrorCode, str] | None:
    """Message-level checks on one offer: the Tiger sailing record and the request it answers."""
    violation = ErrorCode.OFFER_CONSTRAINT_VIOLATION
    if terms.sailing_id == request.withdrawn_sailing_id:
        return violation, "offers the withdrawn sailing"
    if facts is None:
        return violation, f"unknown sailing {terms.sailing_id}"
    if facts.carrier_id != carrier.carrier_id:
        return violation, f"sailing {terms.sailing_id} is not owned by {carrier.carrier_id}"
    if facts.status != "ACTIVE":
        return violation, f"sailing {terms.sailing_id} is {facts.status}"
    record = (facts.origin_port, facts.destination_port, facts.departure_at, facts.arrival_at)
    if (terms.origin_port, terms.destination_port, terms.departure_at, terms.arrival_at) != record:
        return violation, "route or schedule differs from the sailing record"
    if terms.departure_at <= now:
        return violation, "sailing has already departed"
    if terms.expires_at <= now:
        return ErrorCode.STALE_OFFER, "offer already expired"

    items = {i.booking_id: i for i in request.request_items}
    eligible = terms.eligible_booking_ids
    if len(set(eligible)) != len(eligible) or any(b not in items for b in eligible):
        return violation, "eligible bookings are not a subset of the request"
    for booking_id in eligible:
        item = items[booking_id]
        if (item.origin_port, item.destination_port) != (terms.origin_port, terms.destination_port):
            return violation, f"{booking_id} is on a different corridor"
        if item.container_type != terms.container_type:
            return violation, f"{booking_id} needs {item.container_type}"
        if item.currency != terms.currency:
            return violation, f"{booking_id} is priced in {item.currency}"
        if terms.arrival_at > item.latest_arrival:
            return violation, f"arrives after {booking_id}'s deadline"
    return None


def check_offers(
    outcome: RecoveryReplyOutcome, facts: dict[str, SailingFacts], now: datetime,
) -> list[OfferCheck]:
    """Check every offer in a valid reply. Duplicate offer ids or sailings in one reply are
    all rejected: the same space must not be claimed twice."""
    reply = outcome.reply
    if outcome.kind != "offers" or not isinstance(reply, RecoveryCapacityOffers):
        return []
    ids = Counter(o.offer_id for o in reply.offers)
    sailings = Counter((o.sailing_id, o.container_type) for o in reply.offers)
    checks = []
    for terms in reply.offers:
        if ids[terms.offer_id] > 1 or sailings[(terms.sailing_id, terms.container_type)] > 1:
            checks.append(OfferCheck(terms, ErrorCode.OFFER_CONSTRAINT_VIOLATION, "duplicate offer or sailing in reply"))
            continue
        bad = offer_violation(terms, carrier=outcome.carrier, request=outcome.request,
                              facts=facts.get(terms.sailing_id), now=now)
        checks.append(OfferCheck(terms, *bad) if bad else OfferCheck(terms))
    return checks


class RecoveryClient:
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
        request: RequestRecoveryCapacity,
        on_sent: Callable[[CarrierRef, RequestRecoveryCapacity], Awaitable[None]] | None = None,
    ) -> RecoveryReplyOutcome:
        if on_sent:
            await on_sent(carrier, request)
        started = time.perf_counter()

        def outcome(kind, **kw) -> RecoveryReplyOutcome:
            return RecoveryReplyOutcome(carrier, request, kind, round((time.perf_counter() - started) * 1000), **kw)

        try:
            raw, _ = await asyncio.wait_for(
                self.transport.request(carrier, request, self.identity, self.timeout_s),
                self.timeout_s + ALMANAC_LOOKUP_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            return outcome("exchange_error", error_code=ErrorCode.CARRIER_TIMEOUT,
                           detail=f"no reply within {self.timeout_s:.0f}s")
        except TransportError as exc:
            return outcome("exchange_error", error_code=exc.code, detail=exc.detail)
        except Exception as exc:
            return outcome("exchange_error", error_code=ErrorCode.CARRIER_UNREACHABLE, detail=type(exc).__name__)
        kind, reply, code, detail, verified = validate_recovery_envelope(raw, carrier, request)
        return outcome(kind, reply=reply, error_code=code, detail=detail, signature_verified=verified)

    async def request_capacity(
        self,
        carriers: list[CarrierRef],
        *,
        recovery_run_id: str,
        withdrawn_sailing_id: str,
        items: list[RecoveryItem],
        correlation_id: str,
        on_sent: Callable[[CarrierRef, RequestRecoveryCapacity], Awaitable[None]] | None = None,
    ) -> list[RecoveryReplyOutcome]:
        """Exactly one request per carrier, in parallel. No retries, no counters: one shot."""
        now = self.clock()
        requests = [
            build_recovery_request(recovery_run_id=recovery_run_id, withdrawn_sailing_id=withdrawn_sailing_id,
                                   items=items, correlation_id=correlation_id, now=now, timeout_s=self.timeout_s)
            for _ in carriers
        ]
        return list(await asyncio.gather(*(self.request_one(c, r, on_sent) for c, r in zip(carriers, requests))))

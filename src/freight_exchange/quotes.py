"""Quote round: validate → persist request → parallel REQUEST_OFFER → one parallel counter per
carrier → rank final offers → persist. Indicative only: nothing is held, booked or charged.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlparse
from uuid import uuid4

from freight_contracts import CargoSpec, ErrorCode, OfferTerms
from pydantic.v1 import ValidationError
from uagents_core.identity import Identity

from freight_exchange.carrier_client import CarrierClient, CarrierOutcome, CarrierRef, CounterOutcome
from freight_exchange.negotiation import counter_target, negotiation_enabled
from freight_exchange.observability import log_event
from freight_exchange.repository import Repository
from freight_exchange.request_parser import ParsedRequest


class InvalidCargoRequest(ValueError):
    pass


@dataclass
class CarrierResult:
    """Everything that happened with one carrier in this round."""

    opening: CarrierOutcome
    counter: CounterOutcome | None = None

    @property
    def carrier(self) -> CarrierRef:
        return self.opening.carrier

    @property
    def final_offer(self) -> OfferTerms | None:
        if self.counter is not None and self.counter.new_offer is not None:
            return self.counter.new_offer
        return self.opening.offer  # counter declined/failed: the opening offer still stands


@dataclass
class QuoteRound:
    request_id: str
    correlation_id: str
    cargo: CargoSpec
    results: list[CarrierResult]
    ranked: list[CarrierResult] = field(default_factory=list)

    @property
    def outcomes(self) -> list[CarrierOutcome]:
        return [r.opening for r in self.results]


def rank_results(results: list[CarrierResult]) -> list[CarrierResult]:
    """Ranked comparison: total price, then earliest arrival, then offer id (stable)."""
    with_offers = [r for r in results if r.final_offer is not None]
    return sorted(with_offers, key=lambda r: (r.final_offer.total_price_minor, r.final_offer.arrival_at,
                                              r.final_offer.offer_id))


def to_cargo_spec(parsed: ParsedRequest, now: datetime) -> CargoSpec:
    if parsed.missing:
        raise InvalidCargoRequest(f"missing {', '.join(parsed.missing)}")
    if parsed.latest_arrival is not None and parsed.latest_arrival <= now:
        raise InvalidCargoRequest("the arrival deadline is already in the past")
    try:
        return CargoSpec(
            origin_port=parsed.origin.port_code,
            destination_port=parsed.destination.port_code,
            container_type=parsed.container_type,
            quantity=parsed.quantity,
            earliest_departure=parsed.earliest_departure,
            latest_arrival=parsed.latest_arrival,
            max_total_price_minor=parsed.max_total_price_minor,
            currency="USD",
            allow_split=parsed.allow_split,
        )
    except ValidationError as exc:
        first = exc.errors()[0]
        raise InvalidCargoRequest(f"{'.'.join(map(str, first['loc']))}: {first['msg']}") from exc


def cargo_from_requirements(requirements: dict) -> CargoSpec:
    return CargoSpec.parse_obj(requirements["cargo"])


class QuoteService:
    def __init__(self, repo: Repository, client: CarrierClient):
        self.repo = repo
        self.client = client

    async def run(
        self,
        cargo: CargoSpec,
        *,
        buyer_id: str,
        request_id: str | None = None,
        correlation_id: str | None = None,
        session_id: str | None = None,
    ) -> QuoteRound:
        request_id = request_id or f"req_{uuid4().hex[:12]}"
        correlation_id = correlation_id or f"flow_{uuid4().hex[:12]}"
        exchange = self.client.identity.address

        await self.repo.create_request(
            cargo, request_id=request_id, correlation_id=correlation_id, buyer_id=buyer_id,
            requirements={"cargo": json.loads(cargo.json()), "channel": "agentverse-chat"},
            session_id=session_id,
        )
        carriers = await self.repo.active_carriers()

        async def on_sent(carrier: CarrierRef, message) -> None:
            await self.repo.record_sent(carrier, message, exchange)

        openings = await self.client.request_offers(
            carriers, cargo, request_id=request_id, correlation_id=correlation_id, on_sent=on_sent)
        for outcome in openings:
            offer = outcome.offer
            if offer is not None and await self.repo.sailing_owner(offer.sailing_id) != outcome.carrier.carrier_id:
                outcome.kind = "exchange_error"
                outcome.error_code = ErrorCode.OFFER_CONSTRAINT_VIOLATION
                outcome.detail = f"sailing {offer.sailing_id} is not owned by {outcome.carrier.carrier_id}"
        await asyncio.gather(*(self.repo.record_outcome(o, exchange) for o in openings))
        for o in openings:
            self._log(request_id, o.carrier.carrier_id, "opening", o.kind, o.error_code, o.latency_ms)

        results = [CarrierResult(o) for o in openings]
        if negotiation_enabled():
            await self._negotiate(results, cargo, exchange)

        ranked = rank_results(results)
        await self.repo.set_display_ranks(request_id, [r.final_offer.offer_id for r in ranked])
        await self.repo.finish_request(request_id, correlation_id, len(ranked))
        return QuoteRound(request_id, correlation_id, cargo, results, ranked)

    async def _negotiate(self, results: list[CarrierResult], cargo: CargoSpec, exchange: str) -> None:
        offers = [r for r in results if r.opening.offer is not None]
        if not offers:
            return
        best = min(r.opening.offer.total_price_minor for r in offers)

        async def on_sent(carrier: CarrierRef, counter) -> None:
            await self.repo.record_counter_sent(carrier, counter, exchange)

        async def negotiate(result: CarrierResult) -> None:
            opening = result.opening.offer
            target = counter_target(opening.total_price_minor, best, cargo.max_total_price_minor)
            if target is None:
                return
            result.counter = await self.client.counter_one(
                result.carrier, opening, result.opening.request, target, on_sent=on_sent)
            await self.repo.record_counter_outcome(result.counter, exchange)
            c = result.counter
            self._log(c.counter.request_id, result.carrier.carrier_id, "counter", c.kind, c.error_code, c.latency_ms)

        await asyncio.gather(*(negotiate(r) for r in offers))

    @staticmethod
    def _log(request_id, carrier_id, stage, kind, error_code, latency_ms) -> None:
        log_event(
            "carrier_outcome", request_id=request_id, carrier_id=carrier_id, stage=stage, kind=kind,
            error_code=error_code.value if error_code else None, latency_ms=latency_ms,
            level=logging.WARNING if kind == "exchange_error" else logging.INFO,
        )


def exchange_identity(agent_uri: str) -> Identity:
    """The Exchange signs carrier requests with its own Agentverse identity (key inside AGENT_URI)."""
    key = urlparse(agent_uri).password
    if not key:
        raise ValueError("AGENT_URI has no agent key")
    return Identity.from_seed(key, 0)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)

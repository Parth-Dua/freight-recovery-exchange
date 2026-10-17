"""Quote round: validate → persist request → parallel carrier REQUEST_OFFER → persist outcomes.

Indicative quotes only: nothing here holds capacity, books, or charges.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse
from uuid import uuid4

from freight_contracts import CargoSpec, ErrorCode
from pydantic.v1 import ValidationError
from uagents_core.identity import Identity

from freight_exchange.carrier_client import CarrierClient, CarrierOutcome, CarrierRef
from freight_exchange.observability import log_event
from freight_exchange.repository import Repository
from freight_exchange.request_parser import ParsedRequest


class InvalidCargoRequest(ValueError):
    pass


@dataclass
class QuoteRound:
    request_id: str
    correlation_id: str
    cargo: CargoSpec
    outcomes: list[CarrierOutcome]

    @property
    def offers(self) -> list[CarrierOutcome]:
        return sorted((o for o in self.outcomes if o.kind == "offer"),
                      key=lambda o: (o.offer.total_price_minor, o.offer.arrival_at))


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


class QuoteService:
    def __init__(self, repo: Repository, client: CarrierClient):
        self.repo = repo
        self.client = client

    async def run(self, cargo: CargoSpec, *, buyer_id: str, request_id: str | None = None,
                  correlation_id: str | None = None) -> QuoteRound:
        request_id = request_id or f"req_{uuid4().hex[:12]}"
        correlation_id = correlation_id or f"flow_{uuid4().hex[:12]}"
        exchange = self.client.identity.address

        await self.repo.create_request(
            cargo, request_id=request_id, correlation_id=correlation_id, buyer_id=buyer_id,
            requirements={"cargo": json.loads(cargo.json()), "channel": "agentverse-chat"},
        )
        carriers = await self.repo.active_carriers()

        async def on_sent(carrier: CarrierRef, request):
            await self.repo.record_sent(carrier, request, exchange)

        outcomes = await self.client.request_offers(
            carriers, cargo, request_id=request_id, correlation_id=correlation_id, on_sent=on_sent)

        for outcome in outcomes:
            offer = outcome.offer
            if offer is not None and await self.repo.sailing_owner(offer.sailing_id) != outcome.carrier.carrier_id:
                outcome.kind = "exchange_error"
                outcome.error_code = ErrorCode.OFFER_CONSTRAINT_VIOLATION
                outcome.detail = f"sailing {offer.sailing_id} is not owned by {outcome.carrier.carrier_id}"
            await self.repo.record_outcome(outcome, exchange)
            log_event(
                "carrier_outcome", request_id=request_id, carrier_id=outcome.carrier.carrier_id,
                kind=outcome.kind, error_code=outcome.error_code.value if outcome.error_code else None,
                latency_ms=outcome.latency_ms, resolution=outcome.resolution,
                level=logging.WARNING if outcome.kind == "exchange_error" else logging.INFO,
            )

        round_ = QuoteRound(request_id, correlation_id, cargo, outcomes)
        await self.repo.finish_request(request_id, correlation_id, len(round_.offers))
        return round_


def exchange_identity(agent_uri: str) -> Identity:
    """The Exchange signs carrier requests with its own Agentverse identity (key inside AGENT_URI)."""
    key = urlparse(agent_uri).password
    if not key:
        raise ValueError("AGENT_URI has no agent key")
    return Identity.from_seed(key, 0)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)

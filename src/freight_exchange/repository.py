"""Exchange-side Tiger access (async). Business truth lives here, not in graph state.

Every state change writes its market_events row in the same transaction.
The Exchange never reads carrier_policies (private to each carrier).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from freight_contracts import CargoSpec, RequestOffer
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from freight_exchange.carrier_client import CarrierOutcome, CarrierRef
from freight_exchange.config import database_url

SOURCE = "simulated"  # all Part 1 executions run against the simulated fixture market


@dataclass(frozen=True)
class Port:
    port_code: str
    name: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class Corridor:
    origin: Port
    destination: Port
    container_types: tuple[str, ...]
    carriers: int
    sailings: int
    next_departure: datetime | None


async def _event(
    conn: AsyncConnection,
    event_type: str,
    entity_type: str,
    entity_id: str,
    *,
    correlation_id: str | None = None,
    carrier_id: str | None = None,
    sailing_id: str | None = None,
    corridor_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    await conn.execute(
        """INSERT INTO market_events (ts, event_id, event_type, entity_type, entity_id, correlation_id,
                                      carrier_id, sailing_id, corridor_id, source_kind, payload)
           VALUES (now(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (uuid4(), event_type, entity_type, entity_id, correlation_id, carrier_id, sailing_id,
         corridor_id, SOURCE, Jsonb(payload or {})),
    )


class Repository:
    def __init__(self, pool: AsyncConnectionPool):
        self.pool = pool

    # ---- reference data -------------------------------------------------
    async def ports(self) -> list[Port]:
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(
                "SELECT port_code, name, aliases FROM ports ORDER BY port_code")).fetchall()
        return [Port(r[0], r[1], tuple(r[2])) for r in rows]

    async def corridors(self) -> list[Corridor]:
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(
                """SELECT s.origin_port, o.name, o.aliases, s.destination_port, d.name, d.aliases,
                          array_agg(DISTINCT c.container_type), count(DISTINCT s.carrier_id),
                          count(DISTINCT s.sailing_id), min(s.departure_at) FILTER (WHERE s.departure_at > now())
                   FROM sailings s
                   JOIN ports o ON o.port_code = s.origin_port
                   JOIN ports d ON d.port_code = s.destination_port
                   JOIN carriers k ON k.carrier_id = s.carrier_id AND k.active
                   JOIN capacity_slots c ON c.sailing_id = s.sailing_id
                   WHERE s.status = 'ACTIVE'
                   GROUP BY s.origin_port, o.name, o.aliases, s.destination_port, d.name, d.aliases
                   ORDER BY 1, 4""")).fetchall()
        return [
            Corridor(Port(r[0], r[1], tuple(r[2])), Port(r[3], r[4], tuple(r[5])),
                     tuple(sorted(r[6])), r[7], r[8], r[9])
            for r in rows
        ]

    async def active_carriers(self) -> list[CarrierRef]:
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(
                """SELECT carrier_id, display_name, agent_address, endpoint_url
                   FROM carriers WHERE active ORDER BY carrier_id""")).fetchall()
        return [CarrierRef(*r) for r in rows]

    # ---- quote round ----------------------------------------------------
    async def create_request(
        self, cargo: CargoSpec, *, request_id: str, correlation_id: str, buyer_id: str, requirements: dict
    ) -> None:
        async with self.pool.connection() as conn, conn.transaction():
            await conn.execute(
                """INSERT INTO cargo_requests (request_id, buyer_id, correlation_id, origin_port, destination_port,
                       container_type, quantity, earliest_departure, latest_arrival, max_total_price_minor,
                       currency, allow_split, requirements, status, source_kind)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'OPEN', %s)""",
                (request_id, buyer_id, correlation_id, cargo.origin_port, cargo.destination_port,
                 cargo.container_type, cargo.quantity, cargo.earliest_departure, cargo.latest_arrival,
                 cargo.max_total_price_minor, cargo.currency, cargo.allow_split, Jsonb(requirements), SOURCE),
            )
            await _event(conn, "CARGO_REQUEST_CREATED", "cargo_request", request_id,
                         correlation_id=correlation_id, corridor_id=f"{cargo.origin_port}-{cargo.destination_port}",
                         payload={"quantity": cargo.quantity, "container_type": cargo.container_type})

    async def record_sent(self, carrier: CarrierRef, request: RequestOffer, sender_address: str) -> None:
        async with self.pool.connection() as conn, conn.transaction():
            await conn.execute(
                """INSERT INTO agent_messages (message_id, correlation_id, request_id, negotiation_id, direction,
                       message_type, carrier_id, sender_address, recipient_address, status, payload, ts)
                   VALUES (%s, %s, %s, %s, 'OUTBOUND', 'REQUEST_OFFER', %s, %s, %s, 'SENT', %s, %s)""",
                (request.message_id, request.correlation_id, request.request_id, request.negotiation_id,
                 carrier.carrier_id, sender_address, carrier.agent_address,
                 Jsonb(json.loads(request.json())), request.sent_at),
            )
            await _event(conn, "OFFER_REQUESTED", "negotiation", request.negotiation_id,
                         correlation_id=request.correlation_id, carrier_id=carrier.carrier_id,
                         payload={"request_id": request.request_id, "message_id": request.message_id})

    async def record_outcome(self, outcome: CarrierOutcome, exchange_address: str) -> None:
        req, carrier, reply = outcome.request, outcome.carrier, outcome.reply
        message_id = reply.message_id if reply is not None else f"local_{uuid4().hex}"
        direction = "INBOUND" if reply is not None else "LOCAL"
        message_type = reply.message_type if reply is not None else "EXCHANGE_ERROR"
        status = {"offer": "ACCEPTED_FOR_REVIEW", "reject": "DECLINED",
                  "carrier_error": "CARRIER_ERROR", "exchange_error": "REJECTED_BY_EXCHANGE"}[outcome.kind]
        payload = json.loads(reply.json()) if reply is not None else {}
        payload["_exchange"] = {"error_code": outcome.error_code.value if outcome.error_code else None,
                                "detail": outcome.detail, "resolution": outcome.resolution,
                                "signature_verified": outcome.signature_verified}

        async with self.pool.connection() as conn, conn.transaction():
            await conn.execute(
                """INSERT INTO agent_messages (message_id, correlation_id, request_id, negotiation_id, in_reply_to,
                       direction, message_type, carrier_id, sender_address, recipient_address, status,
                       latency_ms, payload, ts)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (message_id) DO NOTHING""",
                (message_id, req.correlation_id, req.request_id, req.negotiation_id, req.message_id,
                 direction, message_type, carrier.carrier_id,
                 carrier.agent_address if reply is not None else exchange_address,
                 exchange_address, status, outcome.latency_ms, Jsonb(payload), outcome.received_at),
            )
            offer = outcome.offer
            if offer is not None:
                await conn.execute(
                    """INSERT INTO offers (offer_id, request_id, negotiation_id, correlation_id, message_id,
                           carrier_id, sailing_id, container_type, quantity, total_price_minor, currency,
                           departure_at, arrival_at, expires_at, round, status, carrier_signature, terms,
                           source_kind, created_at, received_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'PROPOSED', %s, %s, %s, %s, %s)
                       ON CONFLICT (offer_id) DO NOTHING""",
                    (offer.offer_id, req.request_id, req.negotiation_id, req.correlation_id, offer.message_id,
                     carrier.carrier_id, offer.sailing_id, offer.container_type, offer.quantity,
                     offer.total_price_minor, offer.currency, offer.departure_at, offer.arrival_at,
                     offer.expires_at, offer.round, offer.signature, offer.terms, offer.source_kind,
                     offer.sent_at, outcome.received_at),
                )
                await _event(conn, "OFFER_SUBMITTED", "offer", offer.offer_id,
                             correlation_id=req.correlation_id, carrier_id=carrier.carrier_id,
                             sailing_id=offer.sailing_id,
                             corridor_id=f"{offer.origin_port}-{offer.destination_port}",
                             payload={"total_price_minor": offer.total_price_minor, "currency": offer.currency,
                                      "quantity": offer.quantity, "unit_price_minor":
                                      offer.total_price_minor // offer.quantity,
                                      "latency_ms": outcome.latency_ms})
            else:
                event_type = "OFFER_DECLINED" if outcome.kind == "reject" else "CARRIER_REPLY_ERROR"
                await _event(conn, event_type, "negotiation", req.negotiation_id,
                             correlation_id=req.correlation_id, carrier_id=carrier.carrier_id,
                             payload={"reason": getattr(reply, "reason_code", None) and reply.reason_code.value,
                                      "error_code": outcome.error_code.value if outcome.error_code else None,
                                      "latency_ms": outcome.latency_ms})

    async def finish_request(self, request_id: str, correlation_id: str, offers: int) -> None:
        status = "QUOTED" if offers else "NO_OFFERS"
        async with self.pool.connection() as conn, conn.transaction():
            await conn.execute(
                "UPDATE cargo_requests SET status = %s, updated_at = now() WHERE request_id = %s",
                (status, request_id))
            await _event(conn, "QUOTE_ROUND_COMPLETED", "cargo_request", request_id,
                         correlation_id=correlation_id, payload={"offers": offers, "status": status})

    async def sailing_owner(self, sailing_id: str) -> str | None:
        async with self.pool.connection() as conn:
            row = await (await conn.execute(
                "SELECT carrier_id FROM sailings WHERE sailing_id = %s", (sailing_id,))).fetchone()
        return row[0] if row else None


_pools: dict[int, AsyncConnectionPool] = {}


async def get_repository() -> Repository:
    """One pool per event loop (LangGraph dev runs graphs on its own loop)."""
    loop_id = id(asyncio.get_running_loop())
    pool = _pools.get(loop_id)
    if pool is None:
        pool = AsyncConnectionPool(
            database_url(), min_size=1, max_size=5, open=False,
            kwargs={"connect_timeout": 15}, check=AsyncConnectionPool.check_connection,
        )
        await pool.open(wait=True, timeout=20)
        _pools[loop_id] = pool
    return Repository(pool)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)

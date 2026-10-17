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

from freight_contracts import CargoSpec, CounterOffer, RequestOffer
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from freight_exchange.carrier_client import CarrierOutcome, CarrierRef, CounterOutcome
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
        self, cargo: CargoSpec, *, request_id: str, correlation_id: str, buyer_id: str, requirements: dict,
        session_id: str | None = None,
    ) -> None:
        async with self.pool.connection() as conn, conn.transaction():
            await conn.execute(
                """INSERT INTO cargo_requests (request_id, buyer_id, correlation_id, origin_port, destination_port,
                       container_type, quantity, earliest_departure, latest_arrival, max_total_price_minor,
                       currency, allow_split, requirements, status, source_kind, session_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'OPEN', %s, %s)""",
                (request_id, buyer_id, correlation_id, cargo.origin_port, cargo.destination_port,
                 cargo.container_type, cargo.quantity, cargo.earliest_departure, cargo.latest_arrival,
                 cargo.max_total_price_minor, cargo.currency, cargo.allow_split, Jsonb(requirements), SOURCE,
                 session_id),
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

    # ---- negotiation (Phase 3) --------------------------------------------
    async def record_counter_sent(self, carrier: CarrierRef, counter: CounterOffer, sender_address: str) -> None:
        async with self.pool.connection() as conn, conn.transaction():
            await conn.execute(
                """INSERT INTO agent_messages (message_id, correlation_id, request_id, negotiation_id, direction,
                       message_type, carrier_id, sender_address, recipient_address, status, payload, ts)
                   VALUES (%s, %s, %s, %s, 'OUTBOUND', 'COUNTER_OFFER', %s, %s, %s, 'SENT', %s, %s)""",
                (counter.message_id, counter.correlation_id, counter.request_id, counter.negotiation_id,
                 carrier.carrier_id, sender_address, carrier.agent_address,
                 Jsonb(json.loads(counter.json())), counter.sent_at),
            )
            await _event(conn, "COUNTER_OFFER_SENT", "offer", counter.offer_id,
                         correlation_id=counter.correlation_id, carrier_id=carrier.carrier_id,
                         payload={"proposed_total_price_minor": counter.proposed_total_price_minor,
                                  "round": counter.round, "negotiation_id": counter.negotiation_id})

    async def record_counter_outcome(self, outcome: CounterOutcome, exchange_address: str) -> None:
        counter, carrier, reply, opening = outcome.counter, outcome.carrier, outcome.reply, outcome.opening
        message_id = reply.message_id if reply is not None else f"local_{uuid4().hex}"
        payload = json.loads(reply.json()) if reply is not None else {}
        payload["_exchange"] = {"error_code": outcome.error_code.value if outcome.error_code else None,
                                "detail": outcome.detail, "signature_verified": outcome.signature_verified}
        status = {"accept": "COUNTER_ACCEPTED", "revise": "OFFER_REVISED", "reject": "COUNTER_REJECTED",
                  "carrier_error": "CARRIER_ERROR", "exchange_error": "REJECTED_BY_EXCHANGE"}[outcome.kind]
        async with self.pool.connection() as conn, conn.transaction():
            await conn.execute(
                """INSERT INTO agent_messages (message_id, correlation_id, request_id, negotiation_id, in_reply_to,
                       direction, message_type, carrier_id, sender_address, recipient_address, status,
                       latency_ms, payload, ts)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (message_id) DO NOTHING""",
                (message_id, counter.correlation_id, counter.request_id, counter.negotiation_id, counter.message_id,
                 "INBOUND" if reply is not None else "LOCAL",
                 reply.message_type if reply is not None else "EXCHANGE_ERROR", carrier.carrier_id,
                 carrier.agent_address if reply is not None else exchange_address, exchange_address,
                 status, outcome.latency_ms, Jsonb(payload), outcome.received_at),
            )
            new = outcome.new_offer
            if new is not None:
                await conn.execute(
                    """INSERT INTO offers (offer_id, request_id, negotiation_id, correlation_id, message_id,
                           carrier_id, sailing_id, container_type, quantity, total_price_minor, currency,
                           departure_at, arrival_at, expires_at, round, status, carrier_signature, terms,
                           source_kind, created_at, received_at, supersedes_offer_id)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'PROPOSED', %s, %s, %s,
                               %s, %s, %s)
                       ON CONFLICT (offer_id) DO NOTHING""",
                    (new.offer_id, counter.request_id, counter.negotiation_id, counter.correlation_id, new.message_id,
                     carrier.carrier_id, new.sailing_id, new.container_type, new.quantity, new.total_price_minor,
                     new.currency, new.departure_at, new.arrival_at, new.expires_at, new.round, new.signature,
                     new.terms, new.source_kind, new.sent_at, outcome.received_at, opening.offer_id),
                )
                await conn.execute("UPDATE offers SET status = 'SUPERSEDED' WHERE offer_id = %s AND status = 'PROPOSED'",
                                   (opening.offer_id,))
            event = {"accept": "COUNTER_ACCEPTED", "revise": "OFFER_REVISED", "reject": "COUNTER_REJECTED"}.get(
                outcome.kind, "COUNTER_REPLY_ERROR")
            await _event(conn, event, "offer", new.offer_id if new is not None else opening.offer_id,
                         correlation_id=counter.correlation_id, carrier_id=carrier.carrier_id,
                         sailing_id=opening.sailing_id,
                         payload={"opening_total_minor": opening.total_price_minor,
                                  "proposed_total_minor": counter.proposed_total_price_minor,
                                  "final_total_minor": new.total_price_minor if new is not None else None,
                                  "error_code": outcome.error_code.value if outcome.error_code else None,
                                  "latency_ms": outcome.latency_ms})

    async def set_display_ranks(self, request_id: str, offer_ids: list[str]) -> None:
        async with self.pool.connection() as conn, conn.transaction():
            for rank, offer_id in enumerate(offer_ids, 1):
                await conn.execute(
                    "UPDATE offers SET display_rank = %s WHERE offer_id = %s AND request_id = %s",
                    (rank, offer_id, request_id))

    # ---- selection --------------------------------------------------------
    async def latest_request(self, session_id: str) -> dict | None:
        async with self.pool.connection() as conn:
            row = await (await conn.execute(
                """SELECT request_id, status, correlation_id, requirements FROM cargo_requests
                   WHERE session_id = %s ORDER BY created_at DESC LIMIT 1""", (session_id,))).fetchone()
        if row is None:
            return None
        return {"request_id": row[0], "status": row[1], "correlation_id": row[2], "requirements": row[3]}

    async def presented_offers(self, request_id: str) -> list[dict]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """SELECT o.offer_id, o.display_rank, o.carrier_id, k.display_name, o.sailing_id, o.quantity,
                          o.container_type, o.total_price_minor, o.currency, o.departure_at, o.arrival_at,
                          o.expires_at, o.status, o.round
                   FROM offers o JOIN carriers k ON k.carrier_id = o.carrier_id
                   WHERE o.request_id = %s AND o.display_rank IS NOT NULL ORDER BY o.display_rank""",
                (request_id,))
            cols = [c.name for c in cur.description]
            return [dict(zip(cols, r)) for r in await cur.fetchall()]

    async def select_offer(self, request_id: str, offer_id: str, *, session_id: str | None, source: str) -> dict:
        """Persist the buyer's choice. Returns {'status': SELECTED|ALREADY_SELECTED|OFFER_EXPIRED|NOT_SELECTABLE}.
        Selection is not a hold and not a booking."""
        async with self.pool.connection() as conn, conn.transaction():
            req = await (await conn.execute(
                "SELECT status, correlation_id FROM cargo_requests WHERE request_id = %s FOR UPDATE",
                (request_id,))).fetchone()
            offer = await (await conn.execute(
                """SELECT status, expires_at, display_rank, carrier_id, sailing_id FROM offers
                   WHERE offer_id = %s AND request_id = %s FOR UPDATE""", (offer_id, request_id))).fetchone()
            if req is None or offer is None or offer[2] is None or offer[0] not in ("PROPOSED", "SELECTED"):
                return {"status": "NOT_SELECTABLE"}
            if offer[1] <= utcnow():
                await conn.execute("UPDATE offers SET status = 'EXPIRED' WHERE offer_id = %s AND status = 'PROPOSED'",
                                   (offer_id,))
                await _event(conn, "OFFER_EXPIRED", "offer", offer_id, correlation_id=req[1], carrier_id=offer[3])
                return {"status": "OFFER_EXPIRED"}
            existing = await (await conn.execute(
                "SELECT offer_id FROM offer_selections WHERE request_id = %s", (request_id,))).fetchone()
            if existing and existing[0] == offer_id:
                return {"status": "ALREADY_SELECTED"}
            if existing:
                await conn.execute("UPDATE offers SET status = 'PROPOSED' WHERE offer_id = %s AND status = 'SELECTED'",
                                   (existing[0],))
                await conn.execute(
                    "UPDATE offer_selections SET offer_id = %s, source = %s, selected_at = now() WHERE request_id = %s",
                    (offer_id, source, request_id))
            else:
                await conn.execute(
                    "INSERT INTO offer_selections (request_id, offer_id, session_id, source) VALUES (%s, %s, %s, %s)",
                    (request_id, offer_id, session_id, source))
            await conn.execute("UPDATE offers SET status = 'SELECTED' WHERE offer_id = %s", (offer_id,))
            await conn.execute(
                "UPDATE cargo_requests SET status = 'OFFER_SELECTED', updated_at = now() WHERE request_id = %s",
                (request_id,))
            await _event(conn, "OFFER_SELECTED", "offer", offer_id, correlation_id=req[1], carrier_id=offer[3],
                         sailing_id=offer[4], payload={"source": source, "replaced": existing[0] if existing else None})
        return {"status": "SELECTED"}

    # ---- chat-turn idempotency ----------------------------------------------
    async def claim_turn(self, turn_key: str, session_id: str, request_ref: str, window_s: int) -> dict | None:
        """Claim this turn. Returns None if we own it, else the existing turn row (duplicate delivery)."""
        async with self.pool.connection() as conn, conn.transaction():
            row = await (await conn.execute(
                """INSERT INTO chat_turns (turn_key, session_id, request_ref, status) VALUES (%s, %s, %s, 'PENDING')
                   ON CONFLICT (turn_key) DO UPDATE
                   SET session_id = EXCLUDED.session_id, request_ref = EXCLUDED.request_ref, status = 'PENDING',
                       reply = NULL, route = NULL, created_at = now(), completed_at = NULL
                   WHERE chat_turns.created_at < now() - make_interval(secs => %s)
                   RETURNING turn_key""",
                (turn_key, session_id, request_ref, window_s))).fetchone()
            if row is not None:
                return None
            existing = await (await conn.execute(
                "SELECT request_ref, status, route, reply FROM chat_turns WHERE turn_key = %s", (turn_key,))).fetchone()
        return {"request_ref": existing[0], "status": existing[1], "route": existing[2], "reply": existing[3]}

    async def get_turn(self, turn_key: str) -> dict | None:
        async with self.pool.connection() as conn:
            row = await (await conn.execute(
                "SELECT request_ref, status, route, reply FROM chat_turns WHERE turn_key = %s", (turn_key,))).fetchone()
        return None if row is None else {"request_ref": row[0], "status": row[1], "route": row[2], "reply": row[3]}

    async def complete_turn(self, turn_key: str, route: str, reply: str) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "UPDATE chat_turns SET status = 'DONE', route = %s, reply = %s, completed_at = now() WHERE turn_key = %s",
                (route, reply, turn_key))

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

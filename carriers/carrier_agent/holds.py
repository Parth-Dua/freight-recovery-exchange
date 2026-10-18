"""Carrier-owned inventory transactions: hold, release, confirm (spec 04 §5, PRD §10.3).

Every function is one PostgreSQL transaction against this carrier's own rows, and
appends its market_events row in the same transaction. Invariants:

  INV-1  a slot has at most one ACTIVE hold / COMMITTED booking
         (FOR UPDATE SKIP LOCKED + conditional UPDATE + partial unique indexes)
  INV-2  WITHDRAWN sailings are never held or confirmed
  INV-3  EXPIRED/RELEASED/CANCELLED holds never become COMMITTED
  INV-4  confirmation needs a currently valid hold owned by this carrier
  Idempotency: holds by holds.idempotency_key, bookings by bookings.confirmation_key.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import uuid4

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

SOURCE = "simulated"


@dataclass(frozen=True)
class HoldOutcome:
    ok: bool
    code: str  # HOLD_CONFIRMED | <rejection code>
    detail: str = ""
    hold_id: str | None = None
    expires_at: datetime | None = None
    sailing_id: str | None = None
    quantity: int | None = None
    replay: bool = False


@dataclass(frozen=True)
class ConfirmOutcome:
    ok: bool
    code: str  # BOOKING_CONFIRMED | <rejection code>
    detail: str = ""
    booking_id: str | None = None
    confirmed_at: datetime | None = None
    replay: bool = False


async def _event(conn: AsyncConnection, event_type: str, entity_type: str, entity_id: str, *,
                 carrier_id: str, sailing_id: str | None, correlation_id: str | None,
                 payload: dict[str, Any]) -> None:
    await conn.execute(
        """INSERT INTO market_events (ts, event_id, event_type, entity_type, entity_id, correlation_id,
                                      carrier_id, sailing_id, source_kind, payload)
           VALUES (now(), %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (uuid4(), event_type, entity_type, entity_id, correlation_id, carrier_id, sailing_id, SOURCE, Jsonb(payload)),
    )


async def _correlation(conn: AsyncConnection, request_id: str) -> str | None:
    row = await (await conn.execute(
        "SELECT correlation_id FROM cargo_requests WHERE request_id = %s", (request_id,))).fetchone()
    return row[0] if row else None


async def expire_stale_holds(conn: AsyncConnection, carrier_id: str) -> list[str]:
    """Expire this carrier's ACTIVE holds past expires_at and put their slots back on sale."""
    expired = await (await conn.execute(
        """UPDATE holds SET status = 'EXPIRED', released_at = now()
           WHERE carrier_id = %s AND status = 'ACTIVE' AND expires_at <= now()
           RETURNING hold_id, sailing_id, request_id""", (carrier_id,))).fetchall()
    for hold_id, sailing_id, request_id in expired:
        slots = [r[0] for r in await (await conn.execute(
            "UPDATE hold_slots SET status = 'RELEASED' WHERE hold_id = %s AND status = 'ACTIVE' RETURNING slot_id",
            (hold_id,))).fetchall()]
        if slots:  # a withdrawn sailing's slots are already WITHDRAWN and stay that way
            await conn.execute(
                "UPDATE capacity_slots SET status = 'AVAILABLE', version = version + 1 "
                "WHERE slot_id = ANY(%s) AND status = 'HELD'", (slots,))
        await _event(conn, "HOLD_EXPIRED", "hold", hold_id, carrier_id=carrier_id, sailing_id=sailing_id,
                     correlation_id=await _correlation(conn, request_id) if request_id else None,
                     payload={"released_slots": len(slots)})
    return [r[0] for r in expired]


async def request_hold(pool: AsyncConnectionPool, carrier_id: str, *, offer_id: str, request_id: str,
                       buyer_id: str, quantity: int, idempotency_key: str, ttl_seconds: int) -> HoldOutcome:
    async with pool.connection() as conn, conn.transaction():
        prior = await (await conn.execute(
            """SELECT hold_id, status, expires_at, sailing_id, quantity, carrier_id FROM holds
               WHERE idempotency_key = %s""", (idempotency_key,))).fetchone()
        if prior is not None:
            if prior[5] != carrier_id:
                return HoldOutcome(False, "IDEMPOTENCY_CONFLICT", "key already used by another carrier")
            if prior[1] == "ACTIVE":
                return HoldOutcome(True, "HOLD_CONFIRMED", hold_id=prior[0], expires_at=prior[2],
                                   sailing_id=prior[3], quantity=prior[4], replay=True)
            return HoldOutcome(False, f"HOLD_{prior[1]}", "this hold request was already used", hold_id=prior[0],
                               replay=True)

        await expire_stale_holds(conn, carrier_id)

        offer = await (await conn.execute(
            """SELECT sailing_id, container_type, quantity, expires_at, round, negotiation_id, request_id
               FROM carrier_offer_log WHERE offer_id = %s AND carrier_id = %s""",
            (offer_id, carrier_id))).fetchone()
        if offer is None or offer[6] != request_id:
            return HoldOutcome(False, "OFFER_NOT_FOUND", "no such offer from this carrier for this request")
        sailing_id, container_type, offer_qty, offer_expires, rnd, negotiation_id, _ = offer
        superseded = await (await conn.execute(
            """SELECT 1 FROM carrier_offer_log WHERE carrier_id = %s AND negotiation_id = %s AND round > %s""",
            (carrier_id, negotiation_id, rnd))).fetchone()
        if superseded:
            return HoldOutcome(False, "OFFER_SUPERSEDED", "a newer negotiated offer replaced this one")
        if offer_expires <= datetime.now(offer_expires.tzinfo):
            return HoldOutcome(False, "OFFER_EXPIRED", "offer expired; request a new quote")
        if quantity != offer_qty:
            return HoldOutcome(False, "QUANTITY_MISMATCH", f"offer is for {offer_qty} units")

        sailing = await (await conn.execute(
            "SELECT status FROM sailings WHERE sailing_id = %s AND carrier_id = %s FOR SHARE",
            (sailing_id, carrier_id))).fetchone()
        if sailing is None or sailing[0] != "ACTIVE":
            return HoldOutcome(False, "SAILING_NOT_ACTIVE", "sailing is not open for holds")

        slots = [r[0] for r in await (await conn.execute(
            """SELECT slot_id FROM capacity_slots
               WHERE sailing_id = %s AND container_type = %s AND status = 'AVAILABLE'
               ORDER BY slot_no FOR UPDATE SKIP LOCKED LIMIT %s""",
            (sailing_id, container_type, quantity))).fetchall()]
        if len(slots) < quantity:
            return HoldOutcome(False, "INSUFFICIENT_CAPACITY", f"only {len(slots)} unit(s) free right now")
        updated = await conn.execute(
            "UPDATE capacity_slots SET status = 'HELD', version = version + 1 "
            "WHERE slot_id = ANY(%s) AND status = 'AVAILABLE'", (slots,))
        if updated.rowcount != quantity:  # defensive: never partially hold
            raise RuntimeError("slot state changed under lock")

        hold_id = f"hold_{carrier_id}_{uuid4().hex[:12]}"
        row = await (await conn.execute(
            """INSERT INTO holds (hold_id, buyer_id, carrier_id, offer_id, status, idempotency_key, expires_at,
                                  request_id, sailing_id, container_type, quantity)
               VALUES (%s, %s, %s, %s, 'ACTIVE', %s, now() + make_interval(secs => %s), %s, %s, %s, %s)
               RETURNING expires_at""",
            (hold_id, buyer_id, carrier_id, offer_id, idempotency_key, ttl_seconds, request_id, sailing_id,
             container_type, quantity))).fetchone()
        for slot_id in slots:
            await conn.execute("INSERT INTO hold_slots (hold_id, slot_id, status) VALUES (%s, %s, 'ACTIVE')",
                               (hold_id, slot_id))
        await _event(conn, "HOLD_CREATED", "hold", hold_id, carrier_id=carrier_id, sailing_id=sailing_id,
                     correlation_id=await _correlation(conn, request_id),
                     payload={"offer_id": offer_id, "quantity": quantity, "ttl_seconds": ttl_seconds})
        return HoldOutcome(True, "HOLD_CONFIRMED", hold_id=hold_id, expires_at=row[0], sailing_id=sailing_id,
                           quantity=quantity)


async def release_hold(pool: AsyncConnectionPool, carrier_id: str, *, hold_id: str) -> HoldOutcome:
    async with pool.connection() as conn, conn.transaction():
        row = await (await conn.execute(
            "SELECT status, sailing_id, request_id FROM holds WHERE hold_id = %s AND carrier_id = %s FOR UPDATE",
            (hold_id, carrier_id))).fetchone()
        if row is None:
            return HoldOutcome(False, "HOLD_NOT_FOUND")
        if row[0] != "ACTIVE":  # idempotent: already released/expired/committed
            return HoldOutcome(True, f"HOLD_{row[0]}", hold_id=hold_id, replay=True)
        await conn.execute("UPDATE holds SET status = 'RELEASED', released_at = now() WHERE hold_id = %s", (hold_id,))
        slots = [r[0] for r in await (await conn.execute(
            "UPDATE hold_slots SET status = 'RELEASED' WHERE hold_id = %s AND status = 'ACTIVE' RETURNING slot_id",
            (hold_id,))).fetchall()]
        await conn.execute("UPDATE capacity_slots SET status = 'AVAILABLE', version = version + 1 "
                           "WHERE slot_id = ANY(%s) AND status = 'HELD'", (slots,))
        await _event(conn, "HOLD_RELEASED", "hold", hold_id, carrier_id=carrier_id, sailing_id=row[1],
                     correlation_id=await _correlation(conn, row[2]), payload={"released_slots": len(slots)})
        return HoldOutcome(True, "HOLD_RELEASED", hold_id=hold_id)


def new_claim_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I
    return "".join(secrets.choice(alphabet) for _ in range(6))


async def confirm_booking(pool: AsyncConnectionPool, carrier_id: str, *, hold_id: str, payment_reference: str,
                          idempotency_key: str, intent_id: str, claim_code_hash: str,
                          payment_terms: str) -> ConfirmOutcome:
    async with pool.connection() as conn, conn.transaction():
        prior = await (await conn.execute(
            "SELECT booking_id, confirmed_at, carrier_id FROM bookings WHERE confirmation_key = %s",
            (idempotency_key,))).fetchone()
        if prior is not None:
            if prior[2] != carrier_id:
                return ConfirmOutcome(False, "IDEMPOTENCY_CONFLICT", "key already used by another carrier")
            return ConfirmOutcome(True, "BOOKING_CONFIRMED", booking_id=prior[0], confirmed_at=prior[1], replay=True)

        hold = await (await conn.execute(
            """SELECT h.status, h.expires_at <= now(), h.sailing_id, h.quantity, h.buyer_id, h.request_id,
                      h.offer_id, o.total_price_minor, o.currency
               FROM holds h JOIN offers o ON o.offer_id = h.offer_id
               WHERE h.hold_id = %s AND h.carrier_id = %s FOR UPDATE OF h""",
            (hold_id, carrier_id))).fetchone()
        if hold is None:
            return ConfirmOutcome(False, "HOLD_NOT_FOUND", "no such hold for this carrier")
        status, expired, sailing_id, quantity, buyer_id, request_id, offer_id, total, currency = hold
        if status == "ACTIVE" and expired:
            await expire_stale_holds(conn, carrier_id)
            status = "EXPIRED"
        if status != "ACTIVE":
            return ConfirmOutcome(False, f"HOLD_{status}", "hold is no longer active; capacity was not committed")
        sailing = await (await conn.execute(
            "SELECT status FROM sailings WHERE sailing_id = %s FOR SHARE", (sailing_id,))).fetchone()
        if sailing is None or sailing[0] != "ACTIVE":
            return ConfirmOutcome(False, "SAILING_NOT_ACTIVE", "sailing is no longer active")

        slots = [r[0] for r in await (await conn.execute(
            "SELECT slot_id FROM hold_slots WHERE hold_id = %s AND status = 'ACTIVE' FOR UPDATE", (hold_id,))).fetchall()]
        if len(slots) != quantity:
            raise RuntimeError("hold slot count mismatch")
        booking_id = f"BK-{uuid4().hex[:8].upper()}"
        confirmed = (await (await conn.execute(
            """INSERT INTO bookings (booking_id, buyer_id, request_id, offer_id, carrier_id, sailing_id, status,
                                     total_price_minor, currency, confirmation_key, source_kind, hold_id, intent_id,
                                     payment_reference, claim_code_hash, payment_terms, confirmed_at)
               VALUES (%s, %s, %s, %s, %s, %s, 'COMMITTED', %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
               RETURNING confirmed_at""",
            (booking_id, buyer_id, request_id, offer_id, carrier_id, sailing_id, total, currency, idempotency_key,
             SOURCE, hold_id, intent_id, payment_reference, claim_code_hash, payment_terms))).fetchone())[0]
        for slot_id in slots:
            await conn.execute("INSERT INTO booking_slots (booking_id, slot_id, status) VALUES (%s, %s, 'COMMITTED')",
                               (booking_id, slot_id))
        updated = await conn.execute(
            "UPDATE capacity_slots SET status = 'COMMITTED', version = version + 1 "
            "WHERE slot_id = ANY(%s) AND status = 'HELD'", (slots,))
        if updated.rowcount != quantity:
            raise RuntimeError("held slots changed state before commit")
        await conn.execute("UPDATE hold_slots SET status = 'COMMITTED' WHERE hold_id = %s", (hold_id,))
        await conn.execute("UPDATE holds SET status = 'COMMITTED' WHERE hold_id = %s", (hold_id,))
        await _event(conn, "BOOKING_COMMITTED", "booking", booking_id, carrier_id=carrier_id, sailing_id=sailing_id,
                     correlation_id=await _correlation(conn, request_id),
                     payload={"hold_id": hold_id, "quantity": quantity, "total_price_minor": total,
                              "currency": currency, "payment_reference": payment_reference,
                              "intent_id": intent_id})
        return ConfirmOutcome(True, "BOOKING_CONFIRMED", booking_id=booking_id, confirmed_at=confirmed)

"""Create COMMITTED bookings so a withdrawal has something to disrupt.

This is docs/specs/05-disruption-detection.md work package 5: "Preseed Buyer B and
unrelated Buyer C via same domain booking service/commitment invariants". It exists
because the console cannot be built or demoed until committed bookings exist, and
phase 4 is several gates away.

What it preserves (the invariants that matter):
  - capacity is claimed atomically under FOR UPDATE; it refuses to oversell
  - every FK is real: cargo_requests -> offers -> bookings -> booking_slots
  - slots move AVAILABLE -> COMMITTED, so sellable counts stay truthful
  - each write emits its market_events row in the same transaction

What it does NOT do, and must never be described as doing:
  - no ACP negotiation, so the offer row carries PRESEED_SIGNATURE, not a verified
    carrier signature. Nothing here is phase 2 or phase 3 evidence.
  - no payment. Nothing here is phase 4 evidence.
  - Buyer A is deliberately refused. Per PRD 16.1 Buyer A must be booked live through
    ASI:One so the judge watches that exact booking recover.

NEEDS-FROM-PARTH: when phase 4 lands a real commit path, this should call it instead
of writing rows itself. The seam is BookingSeam.commit_booking in packages/freight_seams.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from carrier_console.db import SOURCE, emit_event

PRESEED_SIGNATURE = "PRESEED_NOT_A_VERIFIED_CARRIER_SIGNATURE"

# How late a preseeded buyer will accept a replacement, measured from their original
# arrival. Without slack, every later sailing would be ineligible and no preseeded
# booking could ever be recovered.
ARRIVAL_SLACK = timedelta(days=2)
LIVE_ONLY_BUYERS = {"buyer_a"}


class PreseedRefused(Exception):
    pass


@dataclass(frozen=True)
class PreseedResult:
    booking_id: str
    buyer_id: str
    sailing_id: str
    carrier_id: str
    slot_ids: tuple[str, ...]
    total_price_minor: int


async def _claim_slots(
    conn: AsyncConnection, sailing_id: str, container_type: str, quantity: int
) -> list[str]:
    """Atomically take `quantity` AVAILABLE slots, or refuse. Never oversells."""
    rows = await (
        await conn.execute(
            """SELECT slot_id
               FROM capacity_slots
               WHERE sailing_id = %s AND container_type = %s AND status = 'AVAILABLE'
               ORDER BY slot_no
               LIMIT %s
               FOR UPDATE""",
            (sailing_id, container_type, quantity),
        )
    ).fetchall()
    if len(rows) < quantity:
        raise PreseedRefused(
            f"{sailing_id} has {len(rows)} available {container_type} slot(s), need {quantity}"
        )
    slot_ids = [row[0] for row in rows]
    await conn.execute(
        """UPDATE capacity_slots
           SET status = 'COMMITTED', version = version + 1
           WHERE slot_id = ANY(%s)""",
        (slot_ids,),
    )
    return slot_ids


async def preseed_booking(
    pool: AsyncConnectionPool,
    *,
    buyer_id: str,
    sailing_id: str,
    quantity: int,
    container_type: str = "20GP",
    unit_price_minor: int = 262_500,
    currency: str = "USD",
    allow_split: bool = True,
) -> PreseedResult:
    if buyer_id in LIVE_ONLY_BUYERS:
        raise PreseedRefused(
            f"{buyer_id} must be booked live through ASI:One (PRD 16.1), not preseeded"
        )

    suffix = uuid4().hex[:12]
    request_id = f"req_{suffix}"
    offer_id = f"OFR-{suffix}"
    booking_id = f"BK-{suffix}"
    total_price_minor = unit_price_minor * quantity

    async with pool.connection() as conn, conn.transaction():
        sailing = await (
            await conn.execute(
                """SELECT carrier_id, origin_port, destination_port, corridor_id,
                          departure_at, arrival_at, status
                   FROM sailings WHERE sailing_id = %s FOR UPDATE""",
                (sailing_id,),
            )
        ).fetchone()
        if sailing is None:
            raise PreseedRefused(f"no sailing {sailing_id}")

        carrier_id, origin, destination, corridor_id, departure_at, arrival_at, status = sailing
        if status != "ACTIVE":
            raise PreseedRefused(f"{sailing_id} is {status}, cannot book onto it")

        slot_ids = await _claim_slots(conn, sailing_id, container_type, quantity)

        await conn.execute(
            """INSERT INTO cargo_requests (request_id, buyer_id, correlation_id, origin_port,
                   destination_port, container_type, quantity, latest_arrival,
                   max_total_price_minor, currency, allow_split, status, source_kind)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'BOOKED', %s)""",
            (request_id, buyer_id, f"flow_{suffix}", origin, destination, container_type,
             quantity, arrival_at + ARRIVAL_SLACK, total_price_minor, currency, allow_split, SOURCE),
        )
        await conn.execute(
            """INSERT INTO offers (offer_id, request_id, negotiation_id, correlation_id, message_id,
                   carrier_id, sailing_id, container_type, quantity, total_price_minor, currency,
                   departure_at, arrival_at, expires_at, status, carrier_signature, terms,
                   source_kind, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                       'ACCEPTED', %s, %s, %s, now())""",
            (offer_id, request_id, f"neg_{suffix}", f"flow_{suffix}", f"msg_{suffix}",
             carrier_id, sailing_id, container_type, quantity, total_price_minor, currency,
             departure_at, arrival_at, arrival_at + timedelta(days=1),
             PRESEED_SIGNATURE, "preseeded demo commitment", SOURCE),
        )
        await conn.execute(
            """INSERT INTO bookings (booking_id, buyer_id, request_id, offer_id, carrier_id,
                   sailing_id, status, total_price_minor, currency, confirmation_key, source_kind)
               VALUES (%s, %s, %s, %s, %s, %s, 'COMMITTED', %s, %s, %s, %s)""",
            (booking_id, buyer_id, request_id, offer_id, carrier_id, sailing_id,
             total_price_minor, currency, f"conf_{suffix}", SOURCE),
        )
        for slot_id in slot_ids:
            await conn.execute(
                "INSERT INTO booking_slots (booking_id, slot_id, status) VALUES (%s, %s, 'COMMITTED')",
                (booking_id, slot_id),
            )

        await emit_event(
            conn, "BOOKING_COMMITTED", "booking", booking_id,
            correlation_id=f"flow_{suffix}", carrier_id=carrier_id, sailing_id=sailing_id,
            corridor_id=corridor_id,
            payload={"buyer_id": buyer_id, "quantity": quantity,
                     "total_price_minor": total_price_minor, "currency": currency,
                     "preseeded": True, "evidence": "none; preseed is not phase 3 or 4 evidence"},
        )

    return PreseedResult(
        booking_id=booking_id,
        buyer_id=buyer_id,
        sailing_id=sailing_id,
        carrier_id=carrier_id,
        slot_ids=tuple(slot_ids),
        total_price_minor=total_price_minor,
    )

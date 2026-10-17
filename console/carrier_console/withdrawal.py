"""Carrier-authorized sailing withdrawal.

Implements the WITHDRAW_SAILING command from docs/specs/05-disruption-detection.md
section 5, against the Tiger schema already migrated in 001_core.sql.

Invariants this module is responsible for:

  AT-05-01  A risk event never withdraws anything by itself. This function only runs
            when a carrier operator explicitly authorizes it; risk.py performs no writes.
  AT-05-02  Only bookings on the withdrawn sailing become DISRUPTED. Every other
            booking is untouched, because the UPDATE is scoped by sailing_id.
  AT-05-03  Withdrawing twice produces one disruption event and re-disrupts nothing.
            Enforced by UNIQUE (carrier_id, sailing_id, event_type) plus an early return.
  AT-05-04  A carrier cannot withdraw another carrier's sailing.
  AT-05-06  Current timestamps, immutable ids, source_kind = 'simulated'.

NEEDS-FROM-PARTH (does not block this module, see console/README.md):
  1. disruption_events has no column for the risk event id, the actor's authorization
     reference, or the idempotency key. They are recorded in the market_events payload
     and the reason text for now. Phase 5 may want real columns.
  2. COMMITTED capacity_slots are intentionally left alone, see _withdraw_slots below.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from uuid import uuid4

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from carrier_console.db import SOURCE, emit_event


class RejectionCode(str, Enum):
    UNKNOWN_SAILING = "UNKNOWN_SAILING"
    CROSS_CARRIER_WITHDRAWAL = "CROSS_CARRIER_WITHDRAWAL"
    SAILING_NOT_WITHDRAWABLE = "SAILING_NOT_WITHDRAWABLE"


class WithdrawalRejected(Exception):
    def __init__(self, code: RejectionCode, detail: str):
        super().__init__(f"{code.value}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class WithdrawSailing:
    """The WITHDRAW_SAILING command (spec 05 section 5)."""

    sailing_id: str
    carrier_id: str
    reason_risk_event_id: str
    actor_id: str
    authorization_ref: str
    idempotency_key: str
    reason_text: str


@dataclass(frozen=True)
class WithdrawalResult:
    event_id: str
    sailing_id: str
    carrier_id: str
    already_withdrawn: bool
    disrupted_booking_ids: tuple[str, ...] = field(default=())
    withdrawn_slot_count: int = 0
    untouched_booking_count: int = 0


def new_event_id() -> str:
    """Matches the exchange's id style, for example req_c0e72a57f2a2."""
    return f"evt_{uuid4().hex[:12]}"


async def _locked_sailing(conn: AsyncConnection, sailing_id: str) -> tuple[str, str, str] | None:
    row = await (
        await conn.execute(
            """SELECT carrier_id, status, corridor_id
               FROM sailings
               WHERE sailing_id = %s
               FOR UPDATE""",
            (sailing_id,),
        )
    ).fetchone()
    return (row[0], row[1], row[2]) if row else None


async def _existing_event_id(conn: AsyncConnection, carrier_id: str, sailing_id: str) -> str:
    row = await (
        await conn.execute(
            """SELECT event_id
               FROM disruption_events
               WHERE carrier_id = %s AND sailing_id = %s AND event_type = 'SAILING_WITHDRAWN'""",
            (carrier_id, sailing_id),
        )
    ).fetchone()
    return row[0]


async def _withdraw_slots(conn: AsyncConnection, sailing_id: str) -> int:
    """Withdraw sellable capacity only.

    AVAILABLE and HELD slots stop being sellable the moment the sailing is withdrawn.
    COMMITTED slots are deliberately left as they are: they are the physical record of
    a booking that phase 7 will supersede via replaces_booking_id, and clearing them
    here would erase the lineage before the replacement exists.
    """
    result = await conn.execute(
        """UPDATE capacity_slots
           SET status = 'WITHDRAWN', version = version + 1
           WHERE sailing_id = %s AND status IN ('AVAILABLE', 'HELD')""",
        (sailing_id,),
    )
    return result.rowcount


async def _disrupt_bookings(conn: AsyncConnection, sailing_id: str) -> tuple[str, ...]:
    rows = await (
        await conn.execute(
            """UPDATE bookings
               SET status = 'DISRUPTED'
               WHERE sailing_id = %s AND status = 'COMMITTED'
               RETURNING booking_id""",
            (sailing_id,),
        )
    ).fetchall()
    return tuple(row[0] for row in rows)


async def _untouched_booking_count(conn: AsyncConnection, sailing_id: str) -> int:
    """Committed bookings elsewhere in the market. Proof for AT-05-02 and the demo."""
    row = await (
        await conn.execute(
            """SELECT count(*)
               FROM bookings
               WHERE sailing_id <> %s AND status = 'COMMITTED'""",
            (sailing_id,),
        )
    ).fetchone()
    return int(row[0])


async def withdraw_sailing(pool: AsyncConnectionPool, cmd: WithdrawSailing) -> WithdrawalResult:
    """Withdraw one sailing and disrupt only its committed bookings, in one transaction."""
    async with pool.connection() as conn, conn.transaction():
        sailing = await _locked_sailing(conn, cmd.sailing_id)
        if sailing is None:
            raise WithdrawalRejected(
                RejectionCode.UNKNOWN_SAILING,
                f"no sailing {cmd.sailing_id}",
            )

        owner_id, status, corridor_id = sailing
        if owner_id != cmd.carrier_id:
            raise WithdrawalRejected(
                RejectionCode.CROSS_CARRIER_WITHDRAWAL,
                f"{cmd.carrier_id} does not own {cmd.sailing_id}",
            )
        if status not in ("ACTIVE", "WITHDRAWN"):
            raise WithdrawalRejected(
                RejectionCode.SAILING_NOT_WITHDRAWABLE,
                f"{cmd.sailing_id} is {status}",
            )

        event_id = new_event_id()
        inserted = await (
            await conn.execute(
                """INSERT INTO disruption_events (event_id, carrier_id, sailing_id, event_type,
                                                  authorized_by, reason, source_kind, occurred_at)
                   VALUES (%s, %s, %s, 'SAILING_WITHDRAWN', %s, %s, %s, now())
                   ON CONFLICT (carrier_id, sailing_id, event_type) DO NOTHING
                   RETURNING event_id""",
                (
                    event_id,
                    cmd.carrier_id,
                    cmd.sailing_id,
                    cmd.actor_id,
                    f"{cmd.reason_risk_event_id}: {cmd.reason_text}",
                    SOURCE,
                ),
            )
        ).fetchone()

        if inserted is None:
            # AT-05-03: already withdrawn. Change nothing, report the original event.
            return WithdrawalResult(
                event_id=await _existing_event_id(conn, cmd.carrier_id, cmd.sailing_id),
                sailing_id=cmd.sailing_id,
                carrier_id=cmd.carrier_id,
                already_withdrawn=True,
                untouched_booking_count=await _untouched_booking_count(conn, cmd.sailing_id),
            )

        await conn.execute(
            """UPDATE sailings
               SET status = 'WITHDRAWN', updated_at = now()
               WHERE sailing_id = %s AND status = 'ACTIVE'""",
            (cmd.sailing_id,),
        )
        slot_count = await _withdraw_slots(conn, cmd.sailing_id)
        disrupted = await _disrupt_bookings(conn, cmd.sailing_id)
        untouched = await _untouched_booking_count(conn, cmd.sailing_id)

        await emit_event(
            conn,
            "SAILING_WITHDRAWN",
            "sailing",
            cmd.sailing_id,
            correlation_id=event_id,
            carrier_id=cmd.carrier_id,
            sailing_id=cmd.sailing_id,
            corridor_id=corridor_id,
            payload={
                "risk_event_id": cmd.reason_risk_event_id,
                "authorized_by": cmd.actor_id,
                "authorization_ref": cmd.authorization_ref,
                "idempotency_key": cmd.idempotency_key,
                "reason": cmd.reason_text,
                "withdrawn_slots": slot_count,
                "disrupted_bookings": len(disrupted),
            },
        )
        for booking_id in disrupted:
            await emit_event(
                conn,
                "BOOKING_DISRUPTED",
                "booking",
                booking_id,
                correlation_id=event_id,
                carrier_id=cmd.carrier_id,
                sailing_id=cmd.sailing_id,
                corridor_id=corridor_id,
                payload={"risk_event_id": cmd.reason_risk_event_id, "withdrawal_event_id": event_id},
            )

        return WithdrawalResult(
            event_id=event_id,
            sailing_id=cmd.sailing_id,
            carrier_id=cmd.carrier_id,
            already_withdrawn=False,
            disrupted_booking_ids=disrupted,
            withdrawn_slot_count=slot_count,
            untouched_booking_count=untouched,
        )

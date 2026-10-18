"""Buyer approval for recovery that costs more than the buyer agreed to (spec 07 WP3).

The exchange asks with a NEEDS_APPROVAL message (messages.py); the buyer replies in the same
ASI:One conversation with "approve BK-..." or "decline BK-...". This module records the
request, reads the reply, and answers.

State lives in Tiger's market_events, not a new table, so nothing touches the migrations:
  APPROVAL_REQUESTED  correlation_id = approval_id, payload = who, what, until when
  APPROVAL_DECIDED    correlation_id = approval_id, payload = APPROVED / DECLINED, by whom
Status is derived: decided -> that decision; past expires_at -> EXPIRED; else PENDING.

Rules:
  - Only the buyer the request was made for can decide it. Anyone else is told nothing exists.
  - The first decision wins; later replies are answered, never applied.
  - Expired requests cannot be approved.
  - Requests are idempotent per (recovery run, booking).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Protocol
from uuid import uuid4

from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from freight_exchange.asi import messages as m
from freight_seams import RecoveryProposal

APPROVED = "APPROVED"
DECLINED = "DECLINED"
PENDING = "PENDING"
EXPIRED = "EXPIRED"

APPROVAL_TTL = timedelta(minutes=30)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def approval_id_for(recovery_run_id: str, booking_id: str) -> str:
    return f"apr_{recovery_run_id}_{booking_id}"


# ---- reading the reply -----------------------------------------------------------------

@dataclass(frozen=True)
class ApprovalReply:
    decision: str | None          # APPROVED, DECLINED, or None when the reply says both
    booking_id: str | None = None


_MENTION = re.compile(r"(?<![\w.])@[\w.-]+")
_BOOKING = re.compile(r"\bBK-[A-Za-z0-9]+\b", re.IGNORECASE)
_NEGATED_APPROVE = re.compile(r"\b(?:don'?t|do not|not|never)\s+(?:approve|accept)\b")
_APPROVE = re.compile(r"\b(?:approve|approved|accept|accepted)\b")
_DECLINE = re.compile(r"\b(?:decline|declined|reject|rejected)\b")


def parse_approval_reply(text: str) -> ApprovalReply | None:
    """None if the text is not an approval reply at all. Deliberately narrow: only the words
    the NEEDS_APPROVAL message asks for, so ordinary chat never triggers a decision."""
    if not text or not text.strip():
        return None
    try:
        card = json.loads(text)
    except ValueError:
        card = None
    if isinstance(card, dict) and str(card.get("action", "")).lower() in ("approve", "decline"):
        decision = APPROVED if card["action"].lower() == "approve" else DECLINED
        return ApprovalReply(decision, card.get("booking_id"))

    cleaned = _MENTION.sub(" ", text)
    lowered = cleaned.lower()
    match = _BOOKING.search(cleaned)
    booking_id = match.group(0) if match else None
    if _NEGATED_APPROVE.search(lowered):
        return ApprovalReply(DECLINED, booking_id)
    approve, decline = bool(_APPROVE.search(lowered)), bool(_DECLINE.search(lowered))
    if not approve and not decline:
        return None
    if approve and decline:
        return ApprovalReply(None, booking_id)
    return ApprovalReply(APPROVED if approve else DECLINED, booking_id)


def is_approval_reply(text: str) -> bool:
    """For the graph's router: check this BEFORE the booking and recovery routes."""
    return parse_approval_reply(text) is not None


# ---- records and storage -------------------------------------------------------------

@dataclass(frozen=True)
class ApprovalRecord:
    approval_id: str
    recovery_run_id: str
    booking_id: str
    buyer_id: str
    expires_at: datetime
    plan: tuple[dict, ...] = ()
    total_minor: int = 0
    currency: str = "USD"
    decision: str | None = None

    def status(self, now: datetime) -> str:
        if self.decision:
            return self.decision
        return EXPIRED if now >= self.expires_at else PENDING


class ApprovalStore(Protocol):
    async def record_request(self, record: ApprovalRecord) -> bool: ...
    async def record_decision(self, approval_id: str, decision: str, decided_by: str) -> str: ...
    async def find(self, approval_id: str) -> ApprovalRecord | None: ...
    async def latest_for_booking(self, booking_id: str) -> ApprovalRecord | None: ...
    async def open_for_buyer(self, buyer_id: str, now: datetime) -> list[ApprovalRecord]: ...


class MemoryApprovalStore:
    """Same rules as the Tiger store, in memory. For tests."""

    def __init__(self) -> None:
        self.requests: dict[str, ApprovalRecord] = {}
        self.decisions: dict[str, str] = {}

    def _with_decision(self, record: ApprovalRecord) -> ApprovalRecord:
        decision = self.decisions.get(record.approval_id)
        return record if not decision else ApprovalRecord(**{**record.__dict__, "decision": decision})

    async def record_request(self, record: ApprovalRecord) -> bool:
        if record.approval_id in self.requests:
            return False
        self.requests[record.approval_id] = record
        return True

    async def record_decision(self, approval_id: str, decision: str, decided_by: str) -> str:
        return self.decisions.setdefault(approval_id, decision)

    async def find(self, approval_id: str) -> ApprovalRecord | None:
        record = self.requests.get(approval_id)
        return self._with_decision(record) if record else None

    async def latest_for_booking(self, booking_id: str) -> ApprovalRecord | None:
        matches = [r for r in self.requests.values() if r.booking_id.lower() == booking_id.lower()]
        return self._with_decision(matches[-1]) if matches else None

    async def open_for_buyer(self, buyer_id: str, now: datetime) -> list[ApprovalRecord]:
        records = [self._with_decision(r) for r in self.requests.values() if r.buyer_id == buyer_id]
        return [r for r in records if r.status(now) == PENDING]


def _record_from(payload: dict, decision: str | None) -> ApprovalRecord:
    return ApprovalRecord(
        approval_id=payload["approval_id"],
        recovery_run_id=payload["recovery_run_id"],
        booking_id=payload["booking_id"],
        buyer_id=payload["buyer_id"],
        expires_at=datetime.fromisoformat(payload["expires_at"]),
        plan=tuple(payload.get("plan") or ()),
        total_minor=int(payload.get("total_minor") or 0),
        currency=payload.get("currency") or "USD",
        decision=decision,
    )


_EVENT_SQL = """
INSERT INTO market_events (ts, event_id, event_type, entity_type, entity_id, correlation_id,
                           source_kind, payload)
VALUES (now(), %s, %s, 'booking', %s, %s, 'simulated', %s)
"""

_REQUEST_SQL = """
SELECT payload FROM market_events
WHERE event_type = 'APPROVAL_REQUESTED' AND correlation_id = %s
ORDER BY ts LIMIT 1
"""

_DECISION_SQL = """
SELECT payload->>'decision' FROM market_events
WHERE event_type = 'APPROVAL_DECIDED' AND correlation_id = %s
ORDER BY ts LIMIT 1
"""


class TigerApprovalStore:
    """Approvals as market_events. An advisory lock per approval makes check-then-write atomic,
    so two concurrent replies cannot both be applied."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def record_request(self, record: ApprovalRecord) -> bool:
        payload = {
            "approval_id": record.approval_id, "recovery_run_id": record.recovery_run_id,
            "booking_id": record.booking_id, "buyer_id": record.buyer_id,
            "expires_at": record.expires_at.isoformat(), "plan": list(record.plan),
            "total_minor": record.total_minor, "currency": record.currency,
        }
        async with self._pool.connection() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (record.approval_id,))
            if await (await conn.execute(_REQUEST_SQL, (record.approval_id,))).fetchone():
                return False
            await conn.execute(_EVENT_SQL, (uuid4(), "APPROVAL_REQUESTED", record.booking_id,
                                            record.approval_id, Jsonb(payload)))
            return True

    async def record_decision(self, approval_id: str, decision: str, decided_by: str) -> str:
        async with self._pool.connection() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (approval_id,))
            existing = await (await conn.execute(_DECISION_SQL, (approval_id,))).fetchone()
            if existing:
                return existing[0]
            request = await (await conn.execute(_REQUEST_SQL, (approval_id,))).fetchone()
            booking_id = request[0]["booking_id"] if request else approval_id
            await conn.execute(_EVENT_SQL, (uuid4(), "APPROVAL_DECIDED", booking_id, approval_id,
                                            Jsonb({"approval_id": approval_id, "decision": decision,
                                                   "decided_by": decided_by})))
            return decision

    async def find(self, approval_id: str) -> ApprovalRecord | None:
        async with self._pool.connection() as conn:
            request = await (await conn.execute(_REQUEST_SQL, (approval_id,))).fetchone()
            if not request:
                return None
            decision = await (await conn.execute(_DECISION_SQL, (approval_id,))).fetchone()
        return _record_from(request[0], decision[0] if decision else None)

    async def latest_for_booking(self, booking_id: str) -> ApprovalRecord | None:
        async with self._pool.connection() as conn:
            row = await (await conn.execute(
                """SELECT correlation_id FROM market_events
                   WHERE event_type = 'APPROVAL_REQUESTED' AND lower(entity_id) = lower(%s)
                   ORDER BY ts DESC LIMIT 1""", (booking_id,))).fetchone()
        return await self.find(row[0]) if row else None

    async def open_for_buyer(self, buyer_id: str, now: datetime) -> list[ApprovalRecord]:
        async with self._pool.connection() as conn:
            rows = await (await conn.execute(
                """SELECT r.correlation_id FROM market_events r
                   WHERE r.event_type = 'APPROVAL_REQUESTED' AND r.payload->>'buyer_id' = %s
                     AND NOT EXISTS (SELECT 1 FROM market_events d
                                     WHERE d.event_type = 'APPROVAL_DECIDED'
                                       AND d.correlation_id = r.correlation_id)
                   ORDER BY r.ts""", (buyer_id,))).fetchall()
        records = [await self.find(row[0]) for row in rows]
        return [r for r in records if r and r.status(now) == PENDING]


# ---- the seam and the reply handler ----------------------------------------------------

class TigerApprovalSeam:
    """Implements freight_seams.ApprovalSeam. Owner: Tisya."""

    def __init__(self, store: ApprovalStore, ttl: timedelta = APPROVAL_TTL) -> None:
        self._store = store
        self._ttl = ttl

    async def request_approval(self, *, proposal: RecoveryProposal, booking_id: str, buyer_id: str) -> str:
        assignments = [a for a in proposal.assignments if a.booking_id == booking_id]
        approval_id = approval_id_for(proposal.recovery_run_id, booking_id)
        await self._store.record_request(ApprovalRecord(
            approval_id=approval_id,
            recovery_run_id=proposal.recovery_run_id,
            booking_id=booking_id,
            buyer_id=buyer_id,
            expires_at=utcnow() + self._ttl,
            plan=tuple({"sailing_id": a.sailing_id, "carrier_id": a.carrier_id, "quantity": a.quantity,
                        "quoted_price_minor": a.quoted_price_minor, "arrival_at": a.arrival_at.isoformat()}
                       for a in assignments),
            total_minor=sum(a.quoted_price_minor for a in assignments),
        ))
        return approval_id

    async def approval_status(self, approval_id: str) -> str:
        record = await self._store.find(approval_id)
        return record.status(utcnow()) if record else EXPIRED


@dataclass(frozen=True)
class ApprovalReplyResult:
    text: str
    approval_id: str | None = None
    status: str | None = None
    booking_ids_waiting: tuple[str, ...] = field(default_factory=tuple)


async def handle_approval_reply(
    store: ApprovalStore, buyer_id: str, text: str, now: datetime | None = None
) -> ApprovalReplyResult | None:
    """The graph's approval node. None means 'not an approval reply': route it elsewhere."""
    reply = parse_approval_reply(text)
    if reply is None:
        return None
    now = now or utcnow()

    if reply.booking_id:
        record = await store.latest_for_booking(reply.booking_id)
        if record is None or record.buyer_id != buyer_id:
            return ApprovalReplyResult(m.approval_not_found(reply.booking_id))
    else:
        waiting = await store.open_for_buyer(buyer_id, now)
        if not waiting:
            return ApprovalReplyResult(m.approval_nothing_waiting())
        if len(waiting) > 1:
            ids = tuple(r.booking_id for r in waiting)
            return ApprovalReplyResult(m.approval_which_one(ids), booking_ids_waiting=ids)
        record = waiting[0]

    if reply.decision is None:
        return ApprovalReplyResult(m.approval_ambiguous(record.booking_id), record.approval_id, PENDING)

    current = record.status(now)
    if current in (APPROVED, DECLINED):
        return ApprovalReplyResult(m.approval_already(record.booking_id, current), record.approval_id, current)
    if current == EXPIRED:
        return ApprovalReplyResult(m.approval_expired(record.booking_id), record.approval_id, EXPIRED)

    final = await store.record_decision(record.approval_id, reply.decision, buyer_id)
    if final != reply.decision:  # someone else's decision landed first
        return ApprovalReplyResult(m.approval_already(record.booking_id, final), record.approval_id, final)
    text_out = (m.approval_approved(record.booking_id, record.plan) if final == APPROVED
                else m.approval_declined(record.booking_id))
    return ApprovalReplyResult(text_out, record.approval_id, final)

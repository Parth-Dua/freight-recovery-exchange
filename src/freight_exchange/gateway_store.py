"""Tiger state behind the buyer gateway: chat bindings, idempotent events, outbox."""

from __future__ import annotations

from dataclasses import dataclass

from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from freight_exchange.repository import _event


@dataclass(frozen=True)
class ChatContext:
    """Verified by the gateway transport; the only source of buyer identity."""

    event_id: str
    sender: str
    session: str
    gateway: str
    gateway_wallet: str
    text: str

    @property
    def buyer_id(self) -> str:
        return self.sender  # ASI:One user agent address: one per account, stable across chats


class GatewayStore:
    def __init__(self, pool: AsyncConnectionPool):
        self.pool = pool

    async def bind(self, ctx: ChatContext) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                """INSERT INTO chat_bindings (sender_agent_address, chat_session_id, gateway_agent_address, buyer_id)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (sender_agent_address, chat_session_id) DO UPDATE SET last_seen = now()""",
                (ctx.sender, ctx.session, ctx.gateway, ctx.buyer_id))

    async def claim_event(self, ctx: ChatContext) -> dict | None:
        """None if this call owns the event; else the stored (or pending) answer for a duplicate delivery."""
        async with self.pool.connection() as conn:
            row = await (await conn.execute(
                """INSERT INTO gateway_events (event_id, sender_agent_address, chat_session_id, status)
                   VALUES (%s, %s, %s, 'PENDING') ON CONFLICT (event_id) DO NOTHING RETURNING event_id""",
                (ctx.event_id, ctx.sender, ctx.session))).fetchone()
            if row is not None:
                return None
            prior = await (await conn.execute(
                "SELECT status, response FROM gateway_events WHERE event_id = %s", (ctx.event_id,))).fetchone()
        if prior[0] == "DONE" and prior[1]:
            return prior[1]
        return {"kind": "TEXT", "text": "Still working on your previous message; I'll reply here."}

    async def finish_event(self, event_id: str, response: dict) -> None:
        async with self.pool.connection() as conn:
            await conn.execute("UPDATE gateway_events SET status = 'DONE', response = %s, completed_at = now() "
                               "WHERE event_id = %s", (Jsonb(response), event_id))

    async def latest_binding(self, buyer_id: str) -> tuple[str, str, str] | None:
        """(recipient, chat_session_id, gateway) of the buyer's most recent gateway conversation."""
        async with self.pool.connection() as conn:
            row = await (await conn.execute(
                """SELECT sender_agent_address, chat_session_id, gateway_agent_address FROM chat_bindings
                   WHERE buyer_id = %s ORDER BY last_seen DESC LIMIT 1""", (buyer_id,))).fetchone()
        return tuple(row) if row else None

    async def enqueue(self, *, event_key: str, buyer_id: str, recipient: str, session: str, gateway: str,
                      result: dict) -> bool:
        """Durable push into an existing conversation. False if this event was already queued."""
        async with self.pool.connection() as conn, conn.transaction():
            row = await (await conn.execute(
                """INSERT INTO chat_outbox (event_key, buyer_id, recipient, chat_session_id, gateway_agent_address, payload)
                   VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (event_key) DO NOTHING RETURNING outbox_id""",
                (event_key, buyer_id, recipient, session, gateway, Jsonb(result)))).fetchone()
            if row is not None:
                await _event(conn, "CHAT_NOTIFICATION_QUEUED", "chat_outbox", str(row[0]),
                             payload={"event_key": event_key, "kind": result.get("card_kind") or result.get("kind")})
        return row is not None

    async def pending_outbox(self, gateway: str, limit: int = 20) -> list[dict]:
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(
                """SELECT outbox_id, recipient, chat_session_id, payload FROM chat_outbox
                   WHERE gateway_agent_address = %s AND status = 'PENDING' ORDER BY outbox_id LIMIT %s""",
                (gateway, limit))).fetchall()
        return [{"outbox_id": r[0], "recipient": r[1], "chat_session_id": r[2], "result": r[3]} for r in rows]

    async def ack_outbox(self, outbox_id: int, status: str, detail: str = "") -> None:
        async with self.pool.connection() as conn:
            if status == "SENT":
                await conn.execute("UPDATE chat_outbox SET status = 'SENT', sent_at = now() WHERE outbox_id = %s",
                                   (outbox_id,))
            else:
                await conn.execute(
                    """UPDATE chat_outbox SET attempts = attempts + 1, last_error = %s,
                              status = CASE WHEN attempts + 1 >= 10 THEN 'FAILED' ELSE 'PENDING' END
                       WHERE outbox_id = %s""", (detail[:200], outbox_id))

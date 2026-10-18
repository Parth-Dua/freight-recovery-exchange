"""Message a buyer in ASI:One without them writing first.

A normal reply is nothing special inside agentverse-sdk: it is a ChatMessage, signed with
the agent's identity, sent to the buyer's agent address and tagged with the conversation's
session id (agentverse_sdk.langgraph._app._send_reply). This sends the same thing later,
from outside a run, so a buyer whose ship was cancelled hears about it in the conversation
where they booked.

Where to send: the buyer is identified by their ASI:One agent address (agentverse_sender,
one per ASI:One account, stable across chats; Parth's Phase 4 finding), and the conversation
by the session id the exchange stores on cargo_requests.session_id.

Uses one private SDK function, agentverse_sdk._common.av.send_message_to_agent. The root
project pins agentverse-sdk==0.2.1, and tests/asi/test_notify.py fails loudly if that pin
or the function's signature changes.

Command line (the notification spike):

    uv run python src/freight_exchange/asi/notify.py --to agent1q... --session <uuid> "text"
    uv run python src/freight_exchange/asi/notify.py --to agent1q... --session new  "text"
    uv run python src/freight_exchange/asi/notify.py --to agent1q... --session none "text"

The key comes from NOTIFY_AGENT_URI in the repo-root .env, falling back to AGENT_URI.
Keep the exchange's URI under NOTIFY_AGENT_URI on any machine that is not the one running
the exchange agent, so that machine can send as the agent but never starts a second copy.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from agentverse_sdk._common.av import send_message_to_agent
from agentverse_sdk._common.types import AgentUri
from uagents_core.contrib.protocols.chat import ChatMessage, TextContent

REPO_ROOT = Path(__file__).resolve().parents[3]
AGENT_ADDRESS_PREFIX = "agent1"


@dataclass(frozen=True)
class BuyerAddress:
    """Where to reach one buyer: their ASI:One agent address and, ideally, the conversation."""

    agent_address: str
    session_id: UUID | None = None


@dataclass(frozen=True)
class Notified:
    msg_id: UUID
    agent_address: str
    session_id: UUID | None
    sent_at: datetime


class NotNotifiable(ValueError):
    """The buyer id is not an ASI:One agent address (for example a preseeded demo buyer)."""


def is_notifiable(buyer_id: str | None) -> bool:
    return bool(buyer_id) and buyer_id.startswith(AGENT_ADDRESS_PREFIX)


def parse_session(value: str | None) -> UUID | None:
    """A stored session id, 'new' for a fresh conversation id, or 'none' for no session."""
    if value is None or value.strip().lower() in ("", "none"):
        return None
    if value.strip().lower() == "new":
        return uuid4()
    return UUID(value.strip())


def build_message(text: str, now: datetime) -> ChatMessage:
    if not text.strip():
        raise ValueError("refusing to send an empty notification")
    return ChatMessage(timestamp=now, msg_id=uuid4(), content=[TextContent(type="text", text=text)])


def agent_uri_from_env() -> str:
    uri = (os.getenv("NOTIFY_AGENT_URI") or os.getenv("AGENT_URI") or "").strip()
    if not uri:
        raise RuntimeError("Set NOTIFY_AGENT_URI (or AGENT_URI) in the repo-root .env. Never paste it in chat.")
    return uri


async def notify_buyer(agent_uri: str, buyer: BuyerAddress, text: str) -> Notified:
    """Send `text` to the buyer, signed as the agent in `agent_uri`."""
    if not is_notifiable(buyer.agent_address):
        raise NotNotifiable(f"{buyer.agent_address!r} is not an ASI:One agent address")
    uri = AgentUri.from_str(agent_uri)
    now = datetime.now(timezone.utc)
    message = build_message(text, now)
    await send_message_to_agent(
        buyer.agent_address,
        message,
        uri.identity,
        session_id=buyer.session_id,
        agentverse_config=uri.agentverse,
    )
    return Notified(
        msg_id=message.msg_id,
        agent_address=buyer.agent_address,
        session_id=buyer.session_id,
        sent_at=now,
    )


BUYER_FOR_BOOKING_SQL = """
SELECT b.buyer_id, COALESCE(bi.session_id, r.session_id)
FROM bookings b
JOIN cargo_requests r ON r.request_id = b.request_id
LEFT JOIN LATERAL (
    SELECT session_id FROM booking_intents
    WHERE booking_id = b.booking_id AND session_id IS NOT NULL
    ORDER BY created_at DESC LIMIT 1
) bi ON TRUE
WHERE b.booking_id = %s
"""

BUYER_FOR_BOOKING_SQL_PRE_PHASE_4 = """
SELECT b.buyer_id, r.session_id
FROM bookings b
JOIN cargo_requests r ON r.request_id = b.request_id
WHERE b.booking_id = %s
"""


async def buyer_address_for_booking(conn, booking_id: str) -> BuyerAddress | None:
    """Where to notify the buyer of a booking, or None if they can't be notified.

    Prefers the session where they booked (booking_intents, Phase 4), then the session
    where they asked for quotes. Without a stored session the buyer is still reachable
    by address, but the message may not land in the conversation they expect.
    """
    try:
        row = await (await conn.execute(BUYER_FOR_BOOKING_SQL, (booking_id,))).fetchone()
    except Exception as error:  # booking_intents only exists after migration 004
        if "booking_intents" not in str(error):
            raise
        await conn.rollback()
        row = await (await conn.execute(BUYER_FOR_BOOKING_SQL_PRE_PHASE_4, (booking_id,))).fetchone()
    if row is None or not is_notifiable(row[0]):
        return None
    return BuyerAddress(agent_address=row[0], session_id=parse_session(row[1]))


def _main(argv: list[str]) -> int:
    from dotenv import load_dotenv

    parser = argparse.ArgumentParser(description="Message a buyer in ASI:One, unprompted.")
    parser.add_argument("--to", required=True, help="buyer's ASI:One agent address (agent1q...)")
    parser.add_argument("--session", default="none", help="stored session id, 'new', or 'none'")
    parser.add_argument("text", help="what to say")
    args = parser.parse_args(argv)

    load_dotenv(REPO_ROOT / ".env")
    buyer = BuyerAddress(agent_address=args.to, session_id=parse_session(args.session))
    result = asyncio.run(notify_buyer(agent_uri_from_env(), buyer, args.text))
    session = str(result.session_id) if result.session_id else "none"
    print(f"sent msg {result.msg_id} to {result.agent_address[:16]}... session={session} at {result.sent_at:%H:%M:%S} UTC")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))

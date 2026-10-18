"""Unit tests for buyer notifications. Nothing is sent: the SDK call is replaced.

These prove the message is built and addressed correctly. Whether ASI:One shows it
(and whether the phone buzzes) is only provable by the live spike in the PR description.
"""

from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timezone
from importlib.metadata import version
from uuid import UUID

import pytest

from freight_exchange.asi import notify
from freight_exchange.asi.notify import (
    BuyerAddress,
    NotNotifiable,
    build_message,
    buyer_address_for_booking,
    is_notifiable,
    notify_buyer,
    parse_session,
)

FAKE_URI = "https://spike-handle:" + "ab" * 32 + "@agentverse.ai/test-agent"
BUYER = "agent1q0zqghjhffthx27hku0uvr6q7wxapkvpcc0ghkm8t36dx0g5p6j6v2x8cc7"
SESSION = "769e19b5-1f2a-4c3d-9e8f-0a1b2c3d4e5f"


def test_sdk_pin_and_private_function_still_match() -> None:
    """notify.py uses agentverse_sdk._common.av.send_message_to_agent, a private function.
    If this fails, the SDK changed: re-read _app._send_reply before trusting notify again."""
    assert version("agentverse-sdk") == "0.2.1"
    params = inspect.signature(notify.send_message_to_agent).parameters
    for name in ("destination", "msg", "sender", "session_id", "agentverse_config"):
        assert name in params


def test_only_asi_one_addresses_are_notifiable() -> None:
    assert is_notifiable(BUYER)
    assert not is_notifiable("buyer_b")
    assert not is_notifiable("")
    assert not is_notifiable(None)


def test_session_parsing() -> None:
    assert parse_session(SESSION) == UUID(SESSION)
    assert parse_session("none") is None
    assert parse_session(None) is None
    assert isinstance(parse_session("new"), UUID)
    with pytest.raises(ValueError):
        parse_session("not-a-uuid")


def test_message_is_plain_text_and_never_empty() -> None:
    now = datetime(2026, 10, 18, 2, 0, tzinfo=timezone.utc)
    message = build_message("Your ship was cancelled.", now)
    assert message.content[0].text == "Your ship was cancelled."
    assert build_message("x", now).msg_id != build_message("x", now).msg_id
    with pytest.raises(ValueError):
        build_message("   ", now)


def test_notify_sends_to_the_buyer_in_their_conversation(monkeypatch) -> None:
    calls = []

    async def fake_send(destination, msg, sender, *, session_id=None, agentverse_config=None, **_):
        calls.append({"to": destination, "session": session_id, "text": msg.content[0].text,
                      "sender": sender.address, "host": agentverse_config.base_url})

    monkeypatch.setattr(notify, "send_message_to_agent", fake_send)
    result = asyncio.run(notify_buyer(FAKE_URI, BuyerAddress(BUYER, UUID(SESSION)), "Rebooked you."))

    assert len(calls) == 1
    assert calls[0]["to"] == BUYER
    assert calls[0]["session"] == UUID(SESSION)
    assert calls[0]["text"] == "Rebooked you."
    assert calls[0]["sender"].startswith("agent1")
    assert calls[0]["host"] == "agentverse.ai"
    assert result.session_id == UUID(SESSION)


def test_demo_buyers_are_refused_before_anything_is_sent(monkeypatch) -> None:
    async def must_not_send(*args, **kwargs):
        raise AssertionError("should not send")

    monkeypatch.setattr(notify, "send_message_to_agent", must_not_send)
    with pytest.raises(NotNotifiable):
        asyncio.run(notify_buyer(FAKE_URI, BuyerAddress("buyer_b"), "hi"))


class _FakeConn:
    def __init__(self, row):
        self._row = row

    async def execute(self, sql, params):
        row = self._row

        class _Cursor:
            async def fetchone(self):
                return row

        return _Cursor()

    async def rollback(self):
        pass


def test_booking_lookup_returns_where_to_reach_the_buyer() -> None:
    found = asyncio.run(buyer_address_for_booking(_FakeConn((BUYER, SESSION)), "BK-1"))
    assert found == BuyerAddress(BUYER, UUID(SESSION))


def test_booking_lookup_skips_demo_buyers_and_missing_bookings() -> None:
    assert asyncio.run(buyer_address_for_booking(_FakeConn(("buyer_b", None)), "BK-1")) is None
    assert asyncio.run(buyer_address_for_booking(_FakeConn(None), "BK-404")) is None

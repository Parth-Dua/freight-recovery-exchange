"""ASI:One Interactive Card wire format (official: TextContent + MetadataContent with string values).

https://innovationlab.fetch.ai/resources/docs/interactive-cards/asi-interactive-cards
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from uagents_core.contrib.protocols.chat import ChatMessage, MetadataContent, TextContent

CARD_KINDS = {"form", "carousel", "review", "detail"}
MAX_PAYLOAD_BYTES = 64 * 1024


class CardWireError(ValueError):
    """The card would not render in ASI:One; send the narration as text instead."""


def validate_card(kind: str, payload: dict) -> str:
    if kind not in CARD_KINDS:
        raise CardWireError(f"unsupported card kind {kind!r}")
    if not isinstance(payload, dict) or "title" not in payload:
        raise CardWireError("card payload must be an object with a title")
    if kind == "form" and not payload.get("fields"):
        raise CardWireError("form needs fields")
    if kind == "carousel" and not payload.get("items"):
        raise CardWireError("carousel needs items")
    encoded = json.dumps(payload, separators=(",", ":"))
    if len(encoded.encode()) > MAX_PAYLOAD_BYTES:
        raise CardWireError("card payload exceeds 64KB")
    return encoded


def text_message(text: str) -> ChatMessage:
    return ChatMessage(timestamp=datetime.now(timezone.utc), msg_id=uuid4(),
                       content=[TextContent(type="text", text=text)])


def card_message(narration: str, kind: str, payload: dict, *, terminal: bool = False) -> ChatMessage:
    """Metadata values are strings and `card_payload` is the JSON payload itself (no extra envelope).
    ASI:One only renders a block as a card when requires_card_interaction == "true" (a "false" card shows as
    plain text); read-only cards say so with is_terminal instead. Forms and approvals never set is_terminal."""
    metadata = {
        "card_protocol_version": "1",
        "requires_card_interaction": "true",
        "card_kind": kind,
        "card_payload": validate_card(kind, payload),
    }
    if terminal:
        metadata["is_terminal"] = "true"
    return ChatMessage(timestamp=datetime.now(timezone.utc), msg_id=uuid4(), content=[
        TextContent(type="text", text=narration),
        MetadataContent(type="metadata", metadata=metadata),
    ])


def message_for(result: dict) -> ChatMessage | None:
    """Backend result -> ChatMessage. Invalid cards degrade to their narration (logged by caller)."""
    kind = result.get("kind")
    if kind == "CARD":
        return card_message(result.get("narration") or "", result["card_kind"], result["card_payload"],
                            terminal=bool(result.get("terminal")))
    if kind in ("TEXT", "PENDING"):
        return text_message(result.get("text") or "Working on it — I'll post the result here.")
    return None

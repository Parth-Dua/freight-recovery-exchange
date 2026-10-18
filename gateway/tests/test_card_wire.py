"""Gateway transport (unit): official card wire format, fallbacks. Not ASI:One evidence."""

import json

import pytest
from uagents_core.contrib.protocols.chat import MetadataContent, TextContent

from buyer_gateway.card_wire import CardWireError, card_message, message_for

FORM = {"title": "Tell us about your cargo", "fields": [{"name": "quantity", "kind": "number", "label": "Qty"}],
        "submit_cta": {"label": "Go", "selection": {"action": "submit_cargo_request"}}}


def test_card_is_text_plus_string_metadata_with_unwrapped_payload():
    msg = card_message("Fill this in", "form", FORM)
    text, meta = msg.content
    assert isinstance(text, TextContent) and isinstance(meta, MetadataContent)
    assert all(isinstance(v, str) for v in meta.metadata.values())
    assert meta.metadata["card_kind"] == "form" and meta.metadata["requires_card_interaction"] == "true"
    assert "is_terminal" not in meta.metadata                       # never on forms/approvals
    assert json.loads(meta.metadata["card_payload"]) == FORM          # no extra {"data": ...} envelope


def test_terminal_detail_card_and_invalid_cards_refused():
    meta = card_message("Receipt", "detail", {"title": "Booked"}, terminal=True).content[1].metadata
    assert meta["is_terminal"] == "true"
    with pytest.raises(CardWireError):
        card_message("x", "spreadsheet", {"title": "t"})
    with pytest.raises(CardWireError):
        card_message("x", "form", {"title": "no fields"})
    with pytest.raises(CardWireError):
        card_message("x", "detail", {"title": "big", "blob": "x" * 70000})


def test_backend_results_map_to_messages():
    assert message_for({"kind": "TEXT", "text": "hi"}).content[0].text == "hi"
    assert len(message_for({"kind": "CARD", "narration": "n", "card_kind": "form", "card_payload": FORM}).content) == 2
    assert message_for({"kind": "UNKNOWN"}) is None

"""Card seam types.

Kept dependency-free on purpose: this package must install into the Exchange
runtime, the carrier runtime and the console without pulling uagents-core along.
So a card crosses the seam as a neutral CardSpec, and the thin adapter on the
ASI:One side turns it into a real `MetadataContent` via
`uagents_core.contrib.protocols.chat.cards.create_card_content`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

CARD_KINDS = ("carousel", "detail", "form", "review", "custom")


@dataclass(frozen=True)
class CardSpec:
    """One card, ready for the ASI:One adapter to wrap and send.

    `narration` is the TextContent that accompanies the card. ASI:One shows it when
    the card cannot be rendered, so it must stand alone as a complete answer.
    """

    card_kind: str
    payload: dict
    narration: str
    is_terminal: bool = False

    def __post_init__(self) -> None:
        if self.card_kind not in CARD_KINDS:
            raise ValueError(f"unknown card_kind {self.card_kind!r}; expected one of {CARD_KINDS}")
        if not self.narration.strip():
            raise ValueError("narration is required: it is the fallback when a card cannot render")


@dataclass(frozen=True)
class CardAction:
    """A card submission coming back from ASI:One.

    ASI:One returns a submission as text: JSON on a direct @mention, prose through
    the planner. Both shapes must resolve to the same action, which is why parsing
    belongs on the ASI:One side of the seam rather than in the graph.
    """

    action: str
    values: dict[str, str] = field(default_factory=dict)
    raw_text: str = ""

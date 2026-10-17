"""Resolve a buyer's choice ("select 2", "go with Atlas", an offer id, card JSON) to a real offer id.

Works on the offers actually presented for the session's latest request (display ranks
stored in Tiger), so a selection can only ever point at an offer the buyer has seen.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

OFFER_ID = re.compile(r"\boff_[a-z0-9]+_[0-9a-f]{12}\b")
_VERB = r"(select|choose|pick|take|go with|go for|book|reserve|want|prefer|accept|i'?ll have|option|offer|number|#)"
_RANK = re.compile(_VERB + r"\D{0,15}?#?\s*(\d{1,2})\b", re.IGNORECASE)
_BARE_RANK = re.compile(r"^\s*(?:option|offer|number|#)?\s*#?\s*(\d{1,2})\s*[.!]?\s*$", re.IGNORECASE)
_ORDINALS = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4}
_ORDINAL = re.compile(r"\b(" + "|".join(_ORDINALS) + r")\b(?:\s+(?:one|option|offer))?", re.IGNORECASE)
_CHEAPEST = re.compile(r"\b(cheapest|lowest|least expensive|best price)\b", re.IGNORECASE)
_FASTEST = re.compile(r"\b(fastest|earliest|quickest|soonest)\b", re.IGNORECASE)
SELECT_INTENT = re.compile(
    r"\b(select|choose|pick|go with|go for|i'?ll take|take (?:the|option|offer|#)|cheapest one|fastest one|"
    r"option\s*#?\d|offer\s*#?\d)\b|\boff_[a-z0-9]+_[0-9a-f]{12}\b|^\s*#?\d{1,2}\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Choice:
    offer_id: str | None
    source: str
    problem: str = ""  # set when the text names something that is not on offer


def _from_json(text: str) -> dict | None:
    stripped = text.strip()
    if not stripped.startswith("{"):
        return None
    try:
        data = json.loads(stripped)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def resolve_choice(text: str, offers: list[dict]) -> Choice | None:
    """offers: rows from Repository.presented_offers (display_rank order). None = not a selection."""
    by_rank = {o["display_rank"]: o for o in offers}

    data = _from_json(text)  # direct @mention card selection arrives as JSON
    if data and data.get("offer_id"):
        oid = str(data["offer_id"])
        return Choice(oid if any(o["offer_id"] == oid for o in offers) else None, "card",
                      "" if any(o["offer_id"] == oid for o in offers) else f"offer {oid} is not in your latest quotes")

    m = OFFER_ID.search(text)
    if m:
        oid = m.group(0)
        known = any(o["offer_id"] == oid for o in offers)
        return Choice(oid if known else None, "chat:offer_id", "" if known else f"offer {oid} is not in your latest quotes")

    lowered = text.lower()
    named = [o for o in offers if o["carrier_id"] in lowered or o["display_name"].split(" (")[0].lower() in lowered]
    if len(named) == 1:
        return Choice(named[0]["offer_id"], "chat:carrier")

    rank = None
    for pattern in (_BARE_RANK, _RANK):
        m = pattern.search(text)
        if m:
            rank = int(m.group(m.lastindex))
            break
    if rank is None:
        m = _ORDINAL.search(text)
        if m:
            rank = _ORDINALS[m.group(1).lower()]
    if rank is not None:
        if rank in by_rank:
            return Choice(by_rank[rank]["offer_id"], "chat:rank")
        return Choice(None, "chat:rank", f"there is no option {rank}; choose 1–{len(offers)}")

    if _CHEAPEST.search(text) and offers:
        best = min(offers, key=lambda o: (o["total_price_minor"], o["display_rank"]))
        return Choice(best["offer_id"], "chat:cheapest")
    if _FASTEST.search(text) and offers:
        best = min(offers, key=lambda o: (o["arrival_at"], o["display_rank"]))
        return Choice(best["offer_id"], "chat:fastest")
    if len(named) > 1:
        return Choice(None, "chat:carrier", "that matches more than one carrier; say the option number")
    return None

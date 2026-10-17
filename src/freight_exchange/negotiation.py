"""Buyer-side counter strategy (deterministic, explainable).

One counter per carrier: aim NEGOTIATION_DISCOUNT_PCT below the best opening offer,
never above the buyer's budget, never above this carrier's own offer, rounded down
to whole $10. The buyer's budget is never sent to carriers.
"""

from __future__ import annotations

import os

PRICE_STEP_MINOR = 1000


def negotiation_enabled() -> bool:
    return os.getenv("NEGOTIATION_MODE", "auto").strip().lower() != "off"


def discount_pct() -> float:
    return float(os.getenv("NEGOTIATION_DISCOUNT_PCT", "5"))


def counter_target(own_total: int, best_total: int, budget: int | None, pct: float | None = None) -> int | None:
    """Counter price for one carrier, or None when a counter makes no sense."""
    pct = discount_pct() if pct is None else pct
    anchor = min(own_total, best_total, budget or own_total)
    target = int(anchor * (100 - pct) / 100) // PRICE_STEP_MINOR * PRICE_STEP_MINOR
    if target <= 0 or target >= own_total:
        return None
    return target

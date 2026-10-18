"""Private, deterministic quoting policy (pure functions; no I/O).

A carrier independently picks a currently eligible sailing it owns and prices
it from its own private policy and its own utilization. The policy object is
never sent to the Exchange — only the resulting offer terms are.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from freight_contracts import CargoSpec, RejectReason


@dataclass(frozen=True)
class SailingInventory:
    sailing_id: str
    vessel_name: str
    origin_port: str
    destination_port: str
    departure_at: datetime
    arrival_at: datetime
    capacity_units: int
    available_units: int


@dataclass(frozen=True)
class Policy:
    currency: str
    min_price_per_unit_minor: int
    target_price_per_unit_minor: int
    utilization_premium_pct: int
    offer_ttl_seconds: int
    selection: str  # earliest_arrival | earliest_departure
    concession_pct: int = 0  # max discount off our own opening price on a counter (private)
    payment_terms: str = "Freight prepaid to the carrier before departure"  # shown on receipts
    max_hold_seconds: int = 3600

    @classmethod
    def from_json(cls, data: dict) -> "Policy":
        return cls(**{k: data[k] for k in cls.__dataclass_fields__ if k in data})


@dataclass(frozen=True)
class Quote:
    sailing: SailingInventory
    quantity: int
    total_price_minor: int


@dataclass(frozen=True)
class Decline:
    reason_code: RejectReason
    reason: str


SUPPORTED_CONTAINER_TYPES = {"20GP"}


def unit_price_minor(policy: Policy, sailing: SailingInventory) -> int:
    utilization = 1 - sailing.available_units / sailing.capacity_units
    premium = policy.target_price_per_unit_minor * policy.utilization_premium_pct * utilization / 100
    return max(policy.min_price_per_unit_minor, round(policy.target_price_per_unit_minor + premium))


def decide(cargo: CargoSpec, sailings: list[SailingInventory], policy: Policy, now: datetime) -> Quote | Decline:
    if cargo.container_type not in SUPPORTED_CONTAINER_TYPES:
        return Decline(RejectReason.UNSUPPORTED_CONTAINER_TYPE, f"{cargo.container_type} not offered")
    if cargo.currency != policy.currency:
        return Decline(RejectReason.UNSUPPORTED_CORRIDOR, f"quotes only in {policy.currency}")

    lane = [s for s in sailings if (s.origin_port, s.destination_port) == (cargo.origin_port, cargo.destination_port)]
    if not lane:
        return Decline(RejectReason.UNSUPPORTED_CORRIDOR, "no sailings on this corridor")

    timely = [
        s for s in lane
        if s.departure_at > now
        and (cargo.earliest_departure is None or s.departure_at >= cargo.earliest_departure)
        and (cargo.latest_arrival is None or s.arrival_at <= cargo.latest_arrival)
    ]
    if not timely:
        return Decline(RejectReason.NO_ELIGIBLE_SAILING, "no sailing fits the requested dates")

    full = [s for s in timely if s.available_units >= cargo.quantity]
    if full:
        candidates, quantity_for = full, (lambda s: cargo.quantity)
    elif cargo.allow_split and any(s.available_units > 0 for s in timely):
        candidates, quantity_for = [s for s in timely if s.available_units > 0], (lambda s: s.available_units)
    else:
        return Decline(RejectReason.INSUFFICIENT_CAPACITY, "not enough capacity on eligible sailings")

    key = (lambda s: (s.departure_at, s.sailing_id)) if policy.selection == "earliest_departure" \
        else (lambda s: (s.arrival_at, s.sailing_id))
    sailing = min(candidates, key=key)
    quantity = quantity_for(sailing)
    total = unit_price_minor(policy, sailing) * quantity

    if cargo.max_total_price_minor is not None and total > cargo.max_total_price_minor:
        floor_total = policy.min_price_per_unit_minor * quantity
        if cargo.max_total_price_minor < floor_total:
            # Do not reveal the private floor; just decline.
            return Decline(RejectReason.BUDGET_BELOW_POLICY, "budget is below what we can offer")
        total = cargo.max_total_price_minor
    enforce_floor(total, policy, quantity)
    return Quote(sailing=sailing, quantity=quantity, total_price_minor=total)


# ---- recovery capacity (Phase 6, one-shot) ------------------------------------

MAX_RECOVERY_OFFERS = 5


@dataclass(frozen=True)
class RecoveryQuote:
    sailing: SailingInventory
    container_type: str
    capacity_units: int
    total_price_minor: int
    eligible_booking_ids: tuple[str, ...]


def recovery_eligible(item, sailing: SailingInventory) -> bool:
    """Could this displaced booking ride on this sailing? Same lane, on time, and it fits
    (whole, or partly when the buyer allows a split)."""
    return ((item.origin_port, item.destination_port) == (sailing.origin_port, sailing.destination_port)
            and sailing.arrival_at <= item.latest_arrival
            and (item.quantity <= sailing.available_units or item.allow_split))


def decide_recovery(items: list, sailings_by_type: dict[str, list[SailingInventory]], policy: Policy,
                    now: datetime, *, withdrawn_sailing_id: str) -> list[RecoveryQuote] | Decline:
    """One-shot replacement offers: every sailing of ours that can take at least one displaced
    booking, priced by the same private policy as a quote. There is no bargaining after this."""
    supported = [i for i in items if i.container_type in SUPPORTED_CONTAINER_TYPES and i.currency == policy.currency]
    if not supported:
        return Decline(RejectReason.UNSUPPORTED_CONTAINER_TYPE, "no displaced booking we can carry")

    quotes, on_lane = [], False
    for container_type in sorted({i.container_type for i in supported}):
        typed = [i for i in supported if i.container_type == container_type]
        for s in sorted(sailings_by_type.get(container_type, []), key=lambda s: (s.arrival_at, s.sailing_id)):
            if s.sailing_id == withdrawn_sailing_id or s.departure_at <= now:
                continue
            on_lane = on_lane or any((i.origin_port, i.destination_port) == (s.origin_port, s.destination_port)
                                     for i in typed)
            if s.available_units < 1:
                continue
            eligible = [i for i in typed if recovery_eligible(i, s)]
            if not eligible:
                continue
            units = min(s.available_units, sum(i.quantity for i in eligible))
            total = unit_price_minor(policy, s) * units
            enforce_floor(total, policy, units)
            quotes.append(RecoveryQuote(s, container_type, units, total, tuple(sorted(i.booking_id for i in eligible))))

    if quotes:
        return quotes[:MAX_RECOVERY_OFFERS]
    if not on_lane:
        return Decline(RejectReason.UNSUPPORTED_CORRIDOR, "no upcoming sailings on this corridor")
    return Decline(RejectReason.NO_ELIGIBLE_SAILING, "no sailing with space arrives in time")


# ---- counter-offers ---------------------------------------------------------

PRICE_STEP_MINOR = 1000  # quote in whole $10 steps


class PolicyViolation(AssertionError):
    """Raised if any code path tries to issue a price below the private floor."""


@dataclass(frozen=True)
class CounterDecision:
    kind: str  # accept | revise | reject
    total_price_minor: int | None = None
    reason_code: RejectReason | None = None
    reason: str = ""


def floor_total(policy: Policy, quantity: int) -> int:
    return policy.min_price_per_unit_minor * quantity


def decide_counter(original_total: int, quantity: int, proposed: int, policy: Policy) -> CounterDecision:
    """One counter: accept, meet part-way, or reject. Never below the private floor, never above
    our own opening price, and the floor itself is never revealed in the reason text."""
    floor = floor_total(policy, quantity)
    concession_limit = math.ceil(original_total * (100 - policy.concession_pct) / 100 / PRICE_STEP_MINOR) * PRICE_STEP_MINOR
    min_acceptable = min(original_total, max(floor, concession_limit))

    if proposed >= min_acceptable:
        decision = CounterDecision("accept", min(proposed, original_total))
    elif proposed >= floor:
        decision = CounterDecision("revise", min_acceptable)
    else:
        return CounterDecision("reject", reason_code=RejectReason.BELOW_POLICY,
                               reason="that price is below what we can offer on this sailing")
    enforce_floor(decision.total_price_minor, policy, quantity)
    return decision


def enforce_floor(total_price_minor: int, policy: Policy, quantity: int) -> None:
    if total_price_minor < floor_total(policy, quantity):
        raise PolicyViolation("refusing to issue a price below the carrier floor")

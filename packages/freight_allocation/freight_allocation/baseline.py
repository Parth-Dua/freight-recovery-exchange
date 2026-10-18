"""First-come-first-served comparison (v2 BASE-1): a pure simulation, never executed.

Same final offers and the same claim-checked capacity snapshot as the solver. Bookings take turns in
posting order (then booking id); each grabs its own best feasible offer by its buyer priority (price plus
an arrival penalty) from a COPY of the capacity, so the comparison shows what greedy booking would have
achieved. Tiger slots, holds and approvals are never touched.
"""

from __future__ import annotations

from datetime import datetime

from freight_seams import DisruptedBooking, RecoveryOffer
from freight_allocation.model import ineligibility
from freight_allocation.verify import ClaimCheck

BASELINE_KIND = "FIRST_COME_FIRST_SERVED_SIMULATED"
VALUE_PER_DAY_MINOR = {"cheapest": 0, "balanced": 5000, "fastest": 20000}  # same constants as the buyer agent


def first_come_first_served(stranded: list[DisruptedBooking], offers: list[RecoveryOffer],
                            checks: dict[str, ClaimCheck], as_of: datetime,
                            posting_order: dict[str, int] | None = None) -> dict:
    order = sorted(stranded, key=lambda b: ((posting_order or {}).get(b.booking_id, 0), b.booking_id))
    offer_left = {o.offer_id: checks[o.offer_id].trusted for o in offers}
    sailing_left: dict[str, int] = {}
    for o in offers:
        sailing_left.setdefault(o.sailing_id, checks[o.offer_id].sellable_on_sailing)
    fastest = min((o.arrival_at for o in offers), default=as_of)
    mapping, recovered, must, extra = {}, [], 0, 0
    for b in order:
        per_day = VALUE_PER_DAY_MINOR.get(b.priority, 5000) * b.quantity

        def rank(o: RecoveryOffer):
            days = max(0.0, (o.arrival_at - fastest).total_seconds()) / 86400
            return (o.unit_price_minor * b.quantity + per_day * days, o.arrival_at, o.offer_id)

        usable = sorted((o for o in offers if ineligibility(b, o, as_of) is None), key=rank)
        need, plan = b.quantity, []
        for o in usable:
            room = min(offer_left[o.offer_id], sailing_left[o.sailing_id])
            take = min(room, need) if b.allow_split else (need if room >= need else 0)
            if take:
                plan.append((o, take))
                need -= take
            if need == 0:
                break
        if need:
            continue  # unrecovered: nothing reserved in the copy
        for o, take in plan:
            offer_left[o.offer_id] -= take
            sailing_left[o.sailing_id] -= take
        mapping[b.booking_id] = [{"offer_id": o.offer_id, "sailing_id": o.sailing_id, "quantity": t} for o, t in plan]
        recovered.append(b.booking_id)
        must += b.must_deliver
        extra += sum(o.unit_price_minor * t for o, t in plan) - b.original_price_minor
    return {"baseline_kind": BASELINE_KIND, "order": [b.booking_id for b in order], "recovered": len(recovered),
            "must_deliver_recovered": must, "incremental_cost_minor": extra, "assignments": mapping,
            "unrecovered": [b.booking_id for b in order if b.booking_id not in recovered]}

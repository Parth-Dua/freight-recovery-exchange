"""Step 5 of recovery: decide who goes where. Pure; no network, no database.

Variables
    y[b, o]  units of booking b placed on offer o. For a booking that may not be split,
             y[b, o] = quantity * x[b, o] with x a yes/no choice, so the whole booking
             lands on one sailing or nowhere.
    r[b]     booking b is fully recovered.
    a[b]     booking b needs the buyer's approval (costs more than they agreed to).

Rules (docs/specs/06-joint-recovery-allocation.md)
    - all or nothing:     sum over o of y[b, o] = quantity(b) * r[b]
    - offer capacity:     sum over b of y[b, o] <= units trusted for o  (claim checked)
    - sailing capacity:   offers on the same sailing share its sellable slots
    - never late, never an expired or foreign-currency offer, only offers made for b
    - price over the approved extra cost is allowed but marks a[b] for approval

Objective (v2, SOLVER-1), five strict CP-SAT solves, each fixing the previous optimum as an equality
    1. most must-deliver bookings fully recovered (buyer-declared priority, never an authority override)
    2. most bookings fully recovered
    3. fewest bookings needing approval
    4. lowest total incremental simulated freight cost: sum over recovered b of (new cost - original
       price); signed, so savings offset increases (approval still depends on increases only)
    5. earliest arrival: units-weighted arrival rank of the chosen offers (rank ties broken by offer id),
       then a fixed single-worker seed for anything left

The model is built from inputs sorted by id, so the result never depends on the order
the inputs arrive in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from ortools.sat.python import cp_model

from freight_seams import DisruptedBooking, RecoveryAssignment, RecoveryOffer
from freight_allocation.verify import ClaimCheck

SECONDS_PER_STAGE = 5.0


class AllocationInputError(ValueError):
    pass


@dataclass(frozen=True)
class Solution:
    assignments: tuple[RecoveryAssignment, ...]
    recovered_booking_ids: tuple[str, ...]
    unrecovered_booking_ids: tuple[str, ...]
    needs_approval_booking_ids: tuple[str, ...]
    cost_by_booking: dict[str, int]
    objective: dict[str, int] = field(default_factory=dict)
    proven_optimal: bool = True


def ineligibility(booking: DisruptedBooking, offer: RecoveryOffer, as_of: datetime) -> str | None:
    """Why `offer` can never serve `booking`, or None if it can (capacity aside)."""
    if offer.expires_at <= as_of:
        return "expired"
    if offer.currency != booking.currency:
        return "currency"
    if offer.eligible_booking_ids and booking.booking_id not in offer.eligible_booking_ids:
        return "not_offered"
    if offer.arrival_at > booking.latest_arrival:
        return "too_late"
    return None


def validate_inputs(stranded: list[DisruptedBooking], offers: list[RecoveryOffer]) -> None:
    booking_ids = [b.booking_id for b in stranded]
    if len(booking_ids) != len(set(booking_ids)):
        raise AllocationInputError("duplicate booking_id in stranded bookings")
    offer_ids = [o.offer_id for o in offers]
    if len(offer_ids) != len(set(offer_ids)):
        raise AllocationInputError("duplicate offer_id in recovery offers")
    container_types = {b.container_type for b in stranded}
    if len(container_types) > 1:
        # RecoveryOffer carries no container type (spec 06 section 5), so one run must
        # cover one container type. Part 1 is 20GP only (PRD decision D3).
        raise AllocationInputError(f"one container type per recovery run, got {sorted(container_types)}")


def _new_solver() -> cp_model.CpSolver:
    solver = cp_model.CpSolver()
    solver.parameters.num_workers = 1
    solver.parameters.random_seed = 0
    solver.parameters.max_time_in_seconds = SECONDS_PER_STAGE
    return solver


def solve(
    stranded: list[DisruptedBooking],
    offers: list[RecoveryOffer],
    checks: dict[str, ClaimCheck],
    as_of: datetime,
) -> Solution:
    validate_inputs(stranded, offers)
    bookings = sorted(stranded, key=lambda b: b.booking_id)
    offer_list = sorted(offers, key=lambda o: o.offer_id)
    if not bookings:
        return Solution((), (), (), (), {}, {"must_deliver": 0, "recovered": 0, "approvals": 0,
                                             "incremental_cost_minor": 0, "arrival_rank": 0})

    model = cp_model.CpModel()
    placed: dict[tuple[str, str], cp_model.LinearExprT] = {}
    recovered: dict[str, cp_model.IntVar] = {}
    approval: dict[str, cp_model.IntVar] = {}
    cost: dict[str, cp_model.LinearExprT] = {}

    for booking in bookings:
        for offer in offer_list:
            trusted = checks[offer.offer_id].trusted
            if trusted <= 0 or ineligibility(booking, offer, as_of) is not None:
                continue
            key = (booking.booking_id, offer.offer_id)
            if booking.allow_split:
                placed[key] = model.new_int_var(0, min(booking.quantity, trusted), f"y_{key[0]}_{key[1]}")
            elif trusted >= booking.quantity:
                choose = model.new_bool_var(f"x_{key[0]}_{key[1]}")
                placed[key] = booking.quantity * choose

    for booking in bookings:
        bid = booking.booking_id
        units = [expr for (b, _), expr in placed.items() if b == bid]
        recovered[bid] = model.new_bool_var(f"r_{bid}")
        model.add(sum(units, 0) == booking.quantity * recovered[bid])

        cost[bid] = sum(
            (offer.unit_price_minor * placed[(bid, offer.offer_id)]
             for offer in offer_list if (bid, offer.offer_id) in placed),
            0,
        )
        approval[bid] = model.new_bool_var(f"a_{bid}")
        model.add(approval[bid] <= recovered[bid])
        if not isinstance(cost[bid], int):
            limit = booking.original_price_minor + booking.max_additional_cost_minor
            model.add(cost[bid] <= limit).only_enforce_if(~approval[bid])

    for offer in offer_list:
        on_offer = [expr for (_, o), expr in placed.items() if o == offer.offer_id]
        if on_offer:
            model.add(sum(on_offer, 0) <= checks[offer.offer_id].trusted)

    for sailing_id in sorted({o.sailing_id for o in offer_list}):
        offer_ids = {o.offer_id for o in offer_list if o.sailing_id == sailing_id}
        on_sailing = [expr for (_, o), expr in placed.items() if o in offer_ids]
        if on_sailing:
            sellable = checks[next(iter(sorted(offer_ids)))].sellable_on_sailing
            model.add(sum(on_sailing, 0) <= sellable)

    arrival_rank = {
        offer.offer_id: rank
        for rank, offer in enumerate(sorted(offer_list, key=lambda o: (o.arrival_at, o.offer_id)))
    }
    by_id = {b.booking_id: b for b in bookings}
    stages = [
        ("must_deliver", sum((recovered[b] for b in recovered if by_id[b].must_deliver), 0), True),
        ("recovered", sum(recovered.values(), 0), True),
        ("approvals", sum(approval.values(), 0), False),
        ("incremental_cost_minor",
         sum((cost[b] - by_id[b].original_price_minor * recovered[b] for b in recovered), 0), False),
        ("arrival_rank", sum((arrival_rank[o] * e for (_, o), e in placed.items()), 0), False),
    ]

    objective: dict[str, int] = {}
    solver: cp_model.CpSolver | None = None
    proven_optimal = True
    for name, expr, maximize in stages:
        if isinstance(expr, int):
            objective[name] = expr
            continue
        model.clear_objective()
        if maximize:
            model.maximize(expr)
        else:
            model.minimize(expr)
        solver = _new_solver()
        status = solver.solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            raise RuntimeError(f"allocation stage {name!r} failed: {solver.status_name(status)}")
        proven_optimal = proven_optimal and status == cp_model.OPTIMAL
        value = int(round(solver.objective_value))
        objective[name] = value
        model.add(expr == value)

    assert solver is not None, "every run has at least the recovered stage"
    offers_by_id = {o.offer_id: o for o in offer_list}
    assignments: list[RecoveryAssignment] = []
    for (bid, oid), expr in sorted(placed.items()):
        units = solver.value(expr)
        if units <= 0:
            continue
        offer = offers_by_id[oid]
        assignments.append(
            RecoveryAssignment(
                booking_id=bid,
                offer_id=oid,
                sailing_id=offer.sailing_id,
                carrier_id=offer.carrier_id,
                quantity=units,
                quoted_price_minor=offer.unit_price_minor * units,
                arrival_at=offer.arrival_at,
            )
        )

    recovered_ids = tuple(bid for bid in sorted(recovered) if solver.boolean_value(recovered[bid]))
    return Solution(
        assignments=tuple(assignments),
        recovered_booking_ids=recovered_ids,
        unrecovered_booking_ids=tuple(bid for bid in sorted(recovered) if bid not in recovered_ids),
        needs_approval_booking_ids=tuple(bid for bid in sorted(approval) if solver.boolean_value(approval[bid])),
        cost_by_booking={bid: sum(a.quoted_price_minor for a in assignments if a.booking_id == bid)
                         for bid in recovered_ids},
        objective=objective,
        proven_optimal=proven_optimal,
    )

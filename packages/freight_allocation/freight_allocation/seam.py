"""The real AllocationSeam: check claims, solve, explain. Returns a proposal, never a booking."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Callable

from freight_seams import DisruptedBooking, RecoveryOffer, RecoveryProposal
from freight_allocation.baseline import first_come_first_served
from freight_allocation.capacity import CapacityReader
from freight_allocation.explain import explain
from freight_allocation.model import solve, validate_inputs
from freight_allocation.verify import check_claims

DEFAULT_CONTAINER = "20GP"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def input_version(
    stranded: list[DisruptedBooking], offers: list[RecoveryOffer], sellable: dict[str, int]
) -> str:
    """Stable fingerprint of what the decision was based on, for persistence and audit.
    Independent of input order. Excludes the clock on purpose."""
    snapshot = {
        "stranded": sorted((asdict(b) for b in stranded), key=lambda d: d["booking_id"]),
        "offers": sorted((asdict(o) for o in offers), key=lambda d: d["offer_id"]),
        "sellable": dict(sorted(sellable.items())),
    }
    encoded = json.dumps(snapshot, sort_keys=True, default=lambda v: v.isoformat())
    return "alloc_" + hashlib.sha256(encoded.encode()).hexdigest()[:16]


class SolverAllocationSeam:
    """Implements freight_seams.AllocationSeam.

    `capacity` supplies the physical truth for claim checks: TigerCapacityReader in
    production, StaticCapacity in tests. `clock` decides which offers have expired.
    """

    def __init__(self, capacity: CapacityReader, clock: Callable[[], datetime] = utcnow,
                 posting_order: dict[str, int] | None = None) -> None:
        self._capacity = capacity
        self._clock = clock
        self._posting_order = posting_order  # disrupted-booking posting sequence for the FCFS baseline

    async def allocate(
        self,
        *,
        recovery_run_id: str,
        stranded: list[DisruptedBooking],
        offers: list[RecoveryOffer],
    ) -> RecoveryProposal:
        validate_inputs(stranded, offers)
        container_type = stranded[0].container_type if stranded else DEFAULT_CONTAINER
        sellable = await self._capacity.sellable_slots(sorted({o.sailing_id for o in offers}), container_type)
        checks = check_claims(offers, sellable)
        as_of = self._clock()

        solution = solve(stranded, offers, checks, as_of)
        baseline = first_come_first_served(stranded, offers, checks, as_of, self._posting_order)
        return RecoveryProposal(
            recovery_run_id=recovery_run_id,
            assignments=solution.assignments,
            unrecovered_booking_ids=solution.unrecovered_booking_ids,
            needs_approval_booking_ids=solution.needs_approval_booking_ids,
            explanations=explain(stranded, offers, checks, solution, as_of),
            verified_capacity={offer_id: check.trusted for offer_id, check in checks.items()},
            input_version=input_version(stranded, offers, sellable),
            objective=dict(solution.objective),
            baseline=baseline,
        )

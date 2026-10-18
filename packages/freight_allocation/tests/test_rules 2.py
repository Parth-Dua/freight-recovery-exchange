"""Claim checks, approvals, deadlines, explanations, validation and determinism."""

from __future__ import annotations

import pytest

from builders import AS_OF, booking, offer, placed, run
from freight_allocation import AllocationInputError, check_claims, explain, ineligibility

A = booking("BK-1001", 2, split=False, price=525_000, extra=35_000)
B = booking("BK-1002", 1, split=True, latest="2026-10-24T08:00")


def test_overstated_claim_is_trusted_only_for_what_tiger_confirms() -> None:
    """Beacon claims 2 slots, but only 1 is sellable: A (2, no split) cannot be placed."""
    beacon = offer("ROF-BEA-201", "BEA-201", "beacon", 2, 540_000)
    cove = offer("ROF-COV-301", "COV-301", "cove", 1, 270_000, arrival="2026-10-23T18:00")
    offers = [beacon, cove]
    checks = check_claims(offers, {"BEA-201": 1, "COV-301": 1})
    assert checks["ROF-BEA-201"].overstated and checks["ROF-BEA-201"].trusted == 1

    solution = run([A, B], offers, sellable={"BEA-201": 1, "COV-301": 1})
    assert solution.unrecovered_booking_ids == ("BK-1001",)
    reason = explain([A, B], offers, checks, solution, AS_OF)["BK-1001"]
    assert "can't be split" in reason
    assert "beacon offered 2 slot(s) on BEA-201, but only 1 are actually sellable" in reason


def test_a_sailing_tiger_does_not_know_is_not_trusted() -> None:
    solution = run([B], [offer("ROF-GHOST", "GHOST-1", "cove", 5, 1_000_000)], sellable={})
    assert solution.unrecovered_booking_ids == ("BK-1002",)


def test_over_limit_is_proposed_but_flagged_for_approval() -> None:
    pricey = offer("ROF-PRICEY", "PRICEY-1", "cove", 1, 400_000)
    solution = run([B], [pricey])
    assert placed(solution) == {"BK-1002": [("PRICEY-1", 1)]}
    assert solution.needs_approval_booking_ids == ("BK-1002",)
    reason = explain([B], [pricey], check_claims([pricey], {"PRICEY-1": 1}), solution, AS_OF)["BK-1002"]
    assert "nothing is booked until you approve it" in reason


def test_fewer_approvals_beat_lower_cost() -> None:
    """Cheapest plan: B takes P and A falls back to Q, which is over A's limit.
    The solver prefers the plan nobody has to approve, even though it costs more."""
    a = booking("A", 1, split=True, price=100_000, extra=20_000)
    b = booking("B", 1, split=True, price=280_000, extra=100_000)
    offers = [
        offer("P", "P-1", "beacon", 1, 110_000, eligible=("A", "B")),
        offer("Q", "Q-1", "beacon", 1, 130_000, eligible=("A",)),
        offer("S", "S-1", "cove", 1, 300_000, eligible=("B",)),
    ]
    solution = run([a, b], offers)
    assert placed(solution) == {"A": [("P-1", 1)], "B": [("S-1", 1)]}
    assert solution.needs_approval_booking_ids == ()


def test_late_offers_are_never_used() -> None:
    late = offer("ROF-LATE", "LATE-1", "cove", 1, 270_000, arrival="2026-10-25T00:00")
    solution = run([B], [late])
    assert solution.unrecovered_booking_ids == ("BK-1002",)
    reason = explain([B], [late], check_claims([late], {"LATE-1": 1}), solution, AS_OF)["BK-1002"]
    assert "arrives by your deadline" in reason


def test_expired_and_foreign_currency_offers_are_ineligible() -> None:
    expired = offer("ROF-OLD", "OLD-1", "cove", 1, 270_000, expires="2026-10-17T12:00")
    euros = offer("ROF-EUR", "EUR-1", "cove", 1, 270_000, currency="EUR")
    assert ineligibility(B, expired, AS_OF) == "expired"
    assert ineligibility(B, euros, AS_OF) == "currency"
    assert run([B], [expired, euros]).unrecovered_booking_ids == ("BK-1002",)


def test_every_booking_gets_an_explanation() -> None:
    offers = [offer("ROF-BEA-201", "BEA-201", "beacon", 2, 540_000)]
    solution = run([A, B], offers)
    reasons = explain([A, B], offers, check_claims(offers, {"BEA-201": 2}), solution, AS_OF)
    assert set(reasons) == {"BK-1001", "BK-1002"}
    assert all(text.strip() for text in reasons.values())


def test_no_offers_at_all() -> None:
    solution = run([A], [])
    assert solution.unrecovered_booking_ids == ("BK-1001",)
    assert explain([A], [], {}, solution, AS_OF)["BK-1001"] == "No carrier offered replacement space."


def test_no_stranded_bookings_is_an_empty_answer() -> None:
    solution = run([], [offer("ROF-1", "S-1", "cove", 1, 270_000)])
    assert solution.assignments == () and solution.unrecovered_booking_ids == ()


def test_inputs_are_validated() -> None:
    with pytest.raises(AllocationInputError, match="duplicate booking_id"):
        run([B, B], [])
    with pytest.raises(AllocationInputError, match="one container type"):
        run([B, booking("BK-40", 1, container="40GP")], [])


def test_same_inputs_same_answer_in_any_order() -> None:
    offers = [
        offer("ROF-BEA-201", "BEA-201", "beacon", 2, 540_000),
        offer("ROF-COV-301", "COV-301", "cove", 1, 270_000, arrival="2026-10-23T18:00"),
        offer("ROF-COV-302", "COV-302", "cove", 1, 270_000, arrival="2026-10-23T18:00"),
    ]
    first = run([A, B], offers)
    for _ in range(3):
        assert run([B, A], list(reversed(offers))).assignments == first.assignments

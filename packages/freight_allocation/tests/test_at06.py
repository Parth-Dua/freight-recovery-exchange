"""Spec 06 acceptance cases, AT-06-01 to AT-06-05, as unit tests (no ACP, no Tiger).

AT-06-06 (recovery requests are one-shot, no counteroffers) is about the ACP round on
Parth's side, not the allocation, so it is not tested here.
"""

from __future__ import annotations

from builders import booking, offer, placed, run
from freight_seams import demo_displaced_bookings, demo_recovery_offers

BEACON = offer("ROF-BEA-201", "BEA-201", "beacon", 2, 540_000)
COVE = offer("ROF-COV-301", "COV-301", "cove", 1, 270_000, arrival="2026-10-23T18:00")
A = booking("BK-1001", 2, split=False, latest="2026-10-23T08:00", price=525_000, extra=35_000)
B = booking("BK-1002", 1, split=True, latest="2026-10-24T08:00", price=262_500, extra=20_000)


def test_at_06_01_baseline_matches_spec() -> None:
    """Beacon 2 to A, Cove 1 to B."""
    solution = run(demo_displaced_bookings(), demo_recovery_offers())
    assert placed(solution) == {"BK-1001": [("BEA-201", 2)], "BK-1002": [("COV-301", 1)]}
    assert solution.unrecovered_booking_ids == ()
    assert solution.objective["recovered"] == 2
    assert solution.proven_optimal


def test_at_06_01_answer_comes_from_constraints_not_ids() -> None:
    """Rename everything and reverse the input order: the shape of the answer holds."""
    big = booking("ZZ-9", 2, split=False, price=525_000, extra=35_000)
    small = booking("AA-1", 1, split=True, latest="2026-10-24T08:00")
    offers = [
        offer("o-small", "S-SMALL", "zeta", 1, 270_000, arrival="2026-10-23T18:00"),
        offer("o-big", "S-BIG", "alpha", 2, 540_000),
    ]
    solution = run([small, big], offers)
    assert placed(solution) == {"ZZ-9": [("S-BIG", 2)], "AA-1": [("S-SMALL", 1)]}


def test_joint_beats_cheapest_first() -> None:
    """If B grabbed the cheaper Beacon slot first, A (2, no split) would be stranded.
    The joint model recovers both."""
    cheap_beacon = offer("ROF-BEA-201", "BEA-201", "beacon", 2, 500_000)
    solution = run([A, B], [cheap_beacon, COVE])
    assert placed(solution) == {"BK-1001": [("BEA-201", 2)], "BK-1002": [("COV-301", 1)]}


def test_at_06_02_without_cove_the_result_is_an_honest_partial() -> None:
    """Beacon alone (2 slots) can save A (2, no split) or B (1), not both. Either way one
    booking is recovered; spec 06's tie-break is lower cost, so B is chosen. Which one is
    chosen is PRD decision D4, still open; what AT-06-02 requires is the honesty."""
    solution = run([A, B], [BEACON])
    assert solution.objective["recovered"] == 1
    assert len(solution.unrecovered_booking_ids) == 1
    assert sum(a.quantity for a in solution.assignments) <= 2
    assert set(placed(solution)) | set(solution.unrecovered_booking_ids) == {"BK-1001", "BK-1002"}
    assert placed(solution) == {"BK-1002": [("BEA-201", 1)]}


def test_at_06_02_without_beacon_a_cannot_squeeze_onto_cove() -> None:
    solution = run([A, B], [COVE])
    assert solution.unrecovered_booking_ids == ("BK-1001",)
    assert placed(solution) == {"BK-1002": [("COV-301", 1)]}


def test_at_06_03_a_cheaper_second_option_changes_the_answer() -> None:
    cheaper_cove = offer("ROF-COV-302", "COV-302", "cove", 1, 255_000, arrival="2026-10-23T18:00")
    solution = run([A, B], [BEACON, COVE, cheaper_cove])
    assert placed(solution)["BK-1002"] == [("COV-302", 1)]


def test_at_06_03_equal_price_goes_to_the_earlier_arrival() -> None:
    late = offer("ROF-LATE", "LATE-1", "cove", 1, 270_000, arrival="2026-10-24T06:00")
    early = offer("ROF-EARLY", "EARLY-1", "cove", 1, 270_000, arrival="2026-10-22T06:00")
    solution = run([B], [late, early])
    assert placed(solution) == {"BK-1002": [("EARLY-1", 1)]}


def test_at_06_04_a_no_split_booking_is_never_split() -> None:
    one_each = [
        offer("ROF-BEA-1", "BEA-201", "beacon", 1, 270_000),
        offer("ROF-COV-1", "COV-301", "cove", 1, 270_000),
    ]
    solution = run([A], one_each)
    assert solution.unrecovered_booking_ids == ("BK-1001",)
    assert solution.assignments == ()


def test_at_06_04_a_splittable_booking_may_span_sailings() -> None:
    splittable = booking("BK-2001", 2, split=True, price=525_000, extra=35_000)
    one_each = [
        offer("ROF-BEA-1", "BEA-201", "beacon", 1, 270_000),
        offer("ROF-COV-1", "COV-301", "cove", 1, 270_000),
    ]
    solution = run([splittable], one_each)
    assert sorted(placed(solution)["BK-2001"]) == [("BEA-201", 1), ("COV-301", 1)]


def test_at_06_05_one_slot_is_never_used_twice() -> None:
    first = booking("BK-1", 1, split=True)
    second = booking("BK-2", 1, split=True)
    solution = run([first, second], [offer("ROF-ONE", "ONE-1", "cove", 1, 270_000)])
    assert sum(a.quantity for a in solution.assignments) == 1
    assert len(solution.unrecovered_booking_ids) == 1


def test_at_06_05_offers_on_one_sailing_share_its_slots() -> None:
    """Two offers both claim 2 slots on the same ship, which only has 2 to sell."""
    offers = [
        offer("ROF-X", "SHARED-1", "beacon", 2, 540_000),
        offer("ROF-Y", "SHARED-1", "beacon", 2, 540_000),
    ]
    stranded = [booking(f"BK-{i}", 1, split=True) for i in range(3)]
    solution = run(stranded, offers, sellable={"SHARED-1": 2})
    on_ship = sum(a.quantity for a in solution.assignments if a.sailing_id == "SHARED-1")
    assert on_ship == 2
    assert solution.objective["recovered"] == 2

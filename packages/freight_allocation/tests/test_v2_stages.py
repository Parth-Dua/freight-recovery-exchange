"""v2 five-stage lexicographic clearing + first-come-first-served baseline (unit, real OR-Tools)."""

from dataclasses import replace

from freight_allocation import check_claims
from freight_allocation.baseline import BASELINE_KIND, first_come_first_served
from tests.builders import AS_OF, booking, offer, placed, run


def must(b):
    return replace(b, must_deliver=True)


def fastest(b):
    return replace(b, priority="fastest")


def test_prd_fixture_joint_clearing_beats_first_come_3_vs_1():
    # PRD 9 / spec 05 6: A 2 units by Oct 25 (fastest), B 1 by Oct 22 must-deliver, C 1 by Oct 22.
    a = fastest(booking("A", 2, latest="2026-10-25T23:59", extra=200_000))
    b = must(booking("B", 1, latest="2026-10-22T23:59", extra=50_000))
    c = booking("C", 1, latest="2026-10-22T23:59", extra=50_000)
    offers = [  # per-booking negotiated final offers on two ships
        offer("bea-A", "BEA-201", "beacon", 2, 580_000, arrival="2026-10-22T04:00", eligible=("A",)),
        offer("bea-B", "BEA-201", "beacon", 1, 290_000, arrival="2026-10-22T04:00", eligible=("B",)),
        offer("bea-C", "BEA-201", "beacon", 1, 290_000, arrival="2026-10-22T04:00", eligible=("C",)),
        offer("cov-A", "COV-302", "cove", 2, 520_000, arrival="2026-10-24T18:00", eligible=("A",)),
    ]
    sellable = {"BEA-201": 2, "COV-302": 2}
    solution = run([a, b, c], offers, sellable)
    assert placed(solution) == {"A": [("COV-302", 2)], "B": [("BEA-201", 1)], "C": [("BEA-201", 1)]}
    assert solution.objective["must_deliver"] == 1 and solution.objective["recovered"] == 3
    base = first_come_first_served([a, b, c], offers, check_claims(offers, sellable), AS_OF,
                                   {"A": 1, "B": 2, "C": 3})
    assert base["baseline_kind"] == BASELINE_KIND and base["recovered"] == 1
    assert base["assignments"]["A"][0]["sailing_id"] == "BEA-201" and base["unrecovered"] == ["B", "C"]


def test_stage1_must_deliver_beats_a_larger_total():
    m = must(booking("M", 2, extra=100_000))
    x, y = booking("X", 1, extra=100_000), booking("Y", 1, extra=100_000)
    offers = [offer("o-M", "S1", "beacon", 2, 540_000, eligible=("M",)),
              offer("o-X", "S1", "beacon", 1, 270_000, eligible=("X",)),
              offer("o-Y", "S1", "beacon", 1, 270_000, eligible=("Y",))]
    solution = run([m, x, y], offers, {"S1": 2})
    assert placed(solution) == {"M": [("S1", 2)]} and solution.objective["recovered"] == 1


def test_stage2_same_must_count_prefers_larger_total():
    m = must(booking("M", 1, extra=100_000))
    x, y = booking("X", 1, extra=100_000), booking("Y", 2, extra=100_000)
    offers = [offer("o-M", "S1", "beacon", 1, 270_000, eligible=("M",)),
              offer("o-X", "S1", "beacon", 1, 270_000, eligible=("X",)),
              offer("o-Y", "S1", "beacon", 2, 540_000, eligible=("Y",))]
    solution = run([m, x, y], offers, {"S1": 2})
    assert set(placed(solution)) == {"M", "X"} and solution.objective["recovered"] == 2


def test_stage3_zero_approvals_preferred_even_if_costlier():
    b = booking("B", 1, extra=10_000)                     # limit 262500 + 10000
    offers = [offer("cheap-over", "S1", "beacon", 1, 250_000, eligible=("B",)),   # cheaper than original: no approval
              offer("pricey-over", "S2", "cove", 1, 300_000, eligible=("B",))]
    assert placed(run([b], offers, {"S1": 1, "S2": 1})) == {"B": [("S1", 1)]}
    within = [offer("within", "S1", "beacon", 1, 270_000, eligible=("B",)),
              offer("approval", "S2", "cove", 1, 265_000, eligible=("B",), arrival="2026-10-21T00:00")]
    b0 = booking("B", 1, extra=7_000)                      # 270000 > 269500 -> needs approval; 265000 does not
    assert placed(run([b0], within, {"S1": 1, "S2": 1})) == {"B": [("S2", 1)]}


def test_stage4_lower_incremental_cost_then_stage5_earlier_arrival():
    b = booking("B", 1, extra=100_000)
    offers = [offer("dear", "S1", "beacon", 1, 300_000, eligible=("B",)),
              offer("cheap", "S2", "cove", 1, 280_000, eligible=("B",))]
    assert placed(run([b], offers, {"S1": 1, "S2": 1})) == {"B": [("S2", 1)]}
    tie = [offer("late", "S1", "beacon", 1, 280_000, eligible=("B",), arrival="2026-10-22T20:00"),
           offer("early", "S2", "cove", 1, 280_000, eligible=("B",), arrival="2026-10-21T20:00")]
    assert placed(run([b], tie, {"S1": 1, "S2": 1})) == {"B": [("S2", 1)]}


def test_indivisible_booking_never_split_across_single_slots():
    b = booking("B", 2, extra=100_000)
    offers = [offer("s1", "S1", "beacon", 1, 270_000, eligible=("B",)), offer("s2", "S2", "cove", 1, 270_000, eligible=("B",))]
    solution = run([b], offers, {"S1": 1, "S2": 1})
    assert placed(solution) == {} and solution.unrecovered_booking_ids == ("B",)
    base = first_come_first_served([b], offers, check_claims(offers, {"S1": 1, "S2": 1}), AS_OF)
    assert base["recovered"] == 0

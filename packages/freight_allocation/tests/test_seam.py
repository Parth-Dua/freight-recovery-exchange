"""The real AllocationSeam, wired to the fakes Parth's side uses."""

from __future__ import annotations

from builders import AS_OF
from freight_allocation import SolverAllocationSeam, StaticCapacity, input_version
from freight_seams import AllocationSeam, FakeDisruptionSeam, demo_displaced_bookings, demo_recovery_offers


def _seam(counts: dict[str, int]) -> SolverAllocationSeam:
    return SolverAllocationSeam(StaticCapacity(counts), clock=lambda: AS_OF)


def test_it_implements_the_seam() -> None:
    assert isinstance(_seam({}), AllocationSeam)


async def test_end_to_end_from_a_withdrawal() -> None:
    disruption = FakeDisruptionSeam()
    await disruption.withdraw(sailing_id="ATL-101", carrier_id="atlas", risk_event_id="risk_sim_0001", actor_id="op")
    proposal = await _seam({"BEA-201": 2, "COV-301": 1}).allocate(
        recovery_run_id="rec_1",
        stranded=await disruption.disrupted_bookings("ATL-101"),
        offers=demo_recovery_offers(),
    )
    assert {a.booking_id: a.sailing_id for a in proposal.assignments} == {"BK-1001": "BEA-201", "BK-1002": "COV-301"}
    assert proposal.recovered_count == 2
    assert proposal.verified_capacity == {"ROF-BEA-201": 2, "ROF-COV-301": 1}
    assert set(proposal.explanations) == {"BK-1001", "BK-1002"}
    assert proposal.input_version.startswith("alloc_")


async def test_claim_checks_flow_through_the_seam() -> None:
    proposal = await _seam({"BEA-201": 1, "COV-301": 1}).allocate(
        recovery_run_id="rec_2", stranded=demo_displaced_bookings(), offers=demo_recovery_offers()
    )
    assert proposal.verified_capacity["ROF-BEA-201"] == 1
    assert "BK-1001" in proposal.unrecovered_booking_ids
    assert "only 1 are actually sellable" in proposal.explanations["BK-1001"]


def test_input_version_ignores_order_but_not_content() -> None:
    stranded, offers = demo_displaced_bookings(), demo_recovery_offers()
    sellable = {"BEA-201": 2, "COV-301": 1}
    v1 = input_version(stranded, offers, sellable)
    assert v1 == input_version(list(reversed(stranded)), list(reversed(offers)), sellable)
    assert v1 != input_version(stranded, offers, {"BEA-201": 1, "COV-301": 1})

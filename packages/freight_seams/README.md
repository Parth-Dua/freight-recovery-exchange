# freight-seams

The promises between the two lanes, so both of us can build at once instead of queueing behind phase gates.

The problem this solves: phases 3 to 7 are a straight line. Phase 5 needs committed
bookings from phase 4, phase 6 needs displaced bookings from phase 5, phase 7 needs
phase 6. Splitting folders does not help, because the second person is still waiting.

So each side declares a function signature now and ships a fake behind it. The other
side codes against the signature today and swaps in the real thing when it lands.

## Who owes what

| Seam | Owner | Real implementation | What the other side may assume today |
|---|---|---|---|
| `BookingSeam` | **Parth** | phase 3 + 4 | `FakeBookingSeam`, plus real `COMMITTED` rows from `console/carrier_console/preseed.py` |
| `DisruptionSeam` | **Tisya** | phase 5 (`console/`) | `FakeDisruptionSeam`, returning the spec 06 WP5 fixture |
| `AllocationSeam` | **Tisya** | phase 6 (allocation) | `FakeAllocationSeam`, a greedy stand-in that is **not** the solver |
| `ApprovalSeam` | **Tisya** | phase 7 WP3 | `FakeApprovalSeam(decision=...)` |
| `RecoverySeam` | **Parth** | phase 6 (offers) + 7 (commitments) | `FakeRecoverySeam`, which returns a PARTIAL outcome on purpose |
| `CardSeam` | **Tisya** | phase 3 WP5 + phase 7 WP5 | `FakeCardSeam`, text-only, which is also the agreed fallback |

Read `seams.py` for the signatures. They are short on purpose: two methods each, nothing
speculative. A seam that needs a third method gets a new Protocol, never a new method on
an existing one, because adding one breaks every implementer at once.

## What this unblocks right now

Recovery in one line: **Parth talks to the carriers and makes bookings real; Tisya decides
who goes where, checks the carriers aren't overpromising, and explains it.** The 9-step
table with an owner per step is in `OWNERSHIP.md`.

**Parth can build phase 6 offers and phase 7 commitments before the allocation exists.**

```python
from freight_seams import FakeAllocationSeam, FakeDisruptionSeam, demo_recovery_offers

disruption = FakeDisruptionSeam()
await disruption.withdraw(sailing_id="ATL-101", carrier_id="atlas",
                          risk_event_id="risk_sim_0001", actor_id="op")
stranded = await disruption.disrupted_bookings("ATL-101")
proposal = await FakeAllocationSeam().allocate(
    recovery_run_id="rec_1", stranded=stranded, offers=demo_recovery_offers())
# -> A to BEA-201, B to COV-301: the spec 06 WP5 fixture and its AT-06-01 answer.
```

**Tisya can build the OR-Tools model** against the same fixture, with no ACP and no holds.

**Tisya can build the console and the ASI:One disruption reply before phase 4 exists**,
against `FakeBookingSeam` and `FakeRecoverySeam`.

The allocation has one input nobody stores yet: each booking's approved extra cost
(`max_additional_cost_minor`, PRD decision D5). Until phase 4 records it at booking time it
defaults to 0, so any pricier replacement goes to the buyer for approval.

## The one contingency

`progress.md` records that card rendering from an external LangGraph agent is **not yet
verified**, and that agentverse-sdk 0.2.1's `extract_ai_content` drops `MetadataContent`.

So `CardSeam` is agreed with its fallback already decided: if the live spike fails, every
method keeps its name and signature and returns a text-only `CardSpec` with the full
answer in `narration`. Call sites in the graph do not change either way. `FakeCardSeam`
is exactly that fallback, so it is already tested.

`CardSpec` crosses the seam instead of a `MetadataContent` so this package stays
dependency-free; the adapter on the ASI:One side wraps it with
`uagents_core...chat.cards.create_card_content`.

## Rules

- **Additive only.** Changing an existing signature needs the other owner's yes in chat.
- **Fakes are never evidence.** Per `AGENTS.md`, a run against a fake is not Agentverse,
  ASI:One, Stripe, ACP or Tiger evidence. Label those tests as unit tests.
- **No dependencies.** Plain frozen dataclasses, so this installs into the Exchange
  runtime, the carrier runtime and the console without disturbing any resolver.
- Money is integer minor units. Datetimes are timezone-aware UTC. Both are validated.

## Install

```
# Parth's branch parth/seams-and-disruption-status adds it to the root project (not on main yet).
# console/ does not yet; it will once TigerDisruptionSeam lands. To add it anywhere:
#   dependencies += "freight-seams"
#   [tool.uv.sources] freight-seams = { path = "packages/freight_seams", editable = false }
```

## Test

```
cd packages/freight_seams && uv run pytest -q
```

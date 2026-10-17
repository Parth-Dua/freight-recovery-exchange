# freight-seams

Three promises, so both of us can build at once instead of queueing behind phase gates.

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
| `RecoverySeam` | **Parth** | phase 6 + 7 | `FakeRecoverySeam`, which returns a PARTIAL outcome on purpose |
| `CardSeam` | **Tisya** | phase 3 WP5 + phase 7 WP5 | `FakeCardSeam`, text-only, which is also the agreed fallback |

Read `seams.py` for the signatures. They are short on purpose: two methods each, nothing
speculative. A seam that needs a third method gets a new Protocol, never a new method on
an existing one, because adding one breaks every implementer at once.

## What this unblocks right now

**Parth can write the phase 6 solver before phase 5 exists.**

```python
from freight_seams import FakeDisruptionSeam

seam = FakeDisruptionSeam()
await seam.withdraw(sailing_id="ATL-101", carrier_id="atlas",
                    risk_event_id="risk_sim_0001", actor_id="op")
displaced = await seam.disrupted_bookings("ATL-101")
# -> Buyer A: 2 unsplittable, Buyer B: 1, exactly the spec 06 WP5 fixture.
# Field names already match request_items in spec 06 section 5.
```

**Tisya can build the console and the ASI:One disruption reply before phase 4 exists**,
against `FakeBookingSeam` and `FakeRecoverySeam`.

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
# console/pyproject.toml already wires this up.
# For the root project, add two lines (NEEDS-FROM-PARTH):
#   dependencies += "freight-seams"
#   [tool.uv.sources] freight-seams = { path = "packages/freight_seams", editable = false }
```

## Test

```
cd packages/freight_seams && uv run pytest -q
```

# freight-allocation

The recovery decision: when a sailing closes, who goes on which replacement sailing.
Owner: Tisya (see `OWNERSHIP.md`, recovery steps 4, 5 and 8).

`SolverAllocationSeam` is the real implementation of `freight_seams.AllocationSeam`. It takes
the stranded bookings and the carrier offers that passed Parth's message checks, and returns a
`RecoveryProposal`. A proposal is never a booking; Parth's phase 7 code reserves and confirms it.

## What it does

1. **Checks each carrier's claim** (`verify.py`). An offer is trusted for at most the number of
   sellable slots Tiger records on that sailing (AVAILABLE slots on an ACTIVE sailing, so held
   and committed slots never count). Two offers on the same ship share its slots.
2. **Solves the joint allocation** (`model.py`), with OR-Tools CP-SAT. Rules from spec 06:
   never exceed trusted capacity, never split a booking that can't be split, never arrive after
   the buyer's deadline, never use an expired or foreign-currency offer or one not made for that
   booking, never use a slot twice, and every booking is recovered in full or not at all.
3. **Explains every booking** (`explain.py`) in the words the recovery card will show: where it
   went and at what price, why it couldn't be recovered, and when a carrier's claim didn't hold up.

## How it decides

Four stages, each locking in the result of the one before:

1. Recover the **most bookings in full**.
2. Among those plans, need the **fewest buyer approvals** (price over the approved extra cost).
3. Then the **lowest total cost**.
4. Then the **earliest arrival**. Any remaining tie is settled by a single worker and a fixed seed,
   and the model is built from inputs sorted by id, so the same inputs always give the same answer.

Stage 2 is an addition to spec 06's default ("most recovered, then lowest cost"). Over-limit
prices are allowed but flagged in `needs_approval_booking_ids`, which feeds `ApprovalSeam`.

## Decisions still open

- **PRD D4, recovery priority.** Stage 1 counts *bookings*, not containers. With only Beacon's
  2 slots left, saving Buyer B (1 container) and saving Buyer A (2 containers, no split) both
  count as one booking, and the cost tie-break picks B, leaving a slot empty. That matches the
  spec's default; if containers recovered should matter more, it is one extra stage.
- **PRD D5, approved extra cost.** Read from `DisruptedBooking.max_additional_cost_minor`.
  Nothing stores it yet, so it is 0 until phase 4 records it, and every pricier replacement
  goes to the buyer for approval.
- **One container type per run.** `RecoveryOffer` has no container type (spec 06 section 5),
  so mixed types are rejected. Part 1 is 20GP only (PRD D3).

## Not covered here

AT-06-06 (recovery requests are one-shot, no counteroffers) is about the ACP round on Parth's
side, not the allocation.

## Run and test

```
cd packages/freight_allocation
uv sync
uv run pytest -q          # unit tests; the live Tiger read skips without TIGER_DATABASE_URL
```

```python
from freight_allocation import SolverAllocationSeam, TigerCapacityReader

seam = SolverAllocationSeam(TigerCapacityReader(pool))
proposal = await seam.allocate(recovery_run_id=run_id, stranded=stranded, offers=offers)
```

OR-Tools is a large wheel (tens of MB). The root project only needs it once phase 7 calls the
real seam; until then Parth's code can keep using `FakeAllocationSeam`.

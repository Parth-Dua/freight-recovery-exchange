# freight-disruption

The real `DisruptionSeam` (Tisya's lane): withdraw a sailing, and read who it stranded.

- **`withdraw_sailing`**: the carrier-authorized withdrawal transaction (spec 05 §5). The
  carrier console runs it; it lives here so the exchange can use the same code. Fills the
  migration 003 columns (`reason_risk_event_id`, `authorization_ref`, `idempotency_key`).
- **`TigerDisruptionSeam(pool)`** implements `freight_seams.DisruptionSeam`:
  - `withdraw(...)`: the same transaction, for callers that only have a risk event id.
  - `disrupted_bookings(sailing_id)`: still-`DISRUPTED` bookings on that sailing, in the
    solver's input shape (`DisruptedBooking`). Bookings leave this list when phase 7
    supersedes them, so after a partial recovery it returns only who is still waiting.
  - `pending_withdrawals()`: withdrawals with no `recovery_runs` row yet. This is the work
    queue for the recovery trigger.

## For the recovery trigger (Parth)

`recovery_runs.withdrawal_event_id` is `UNIQUE`. A poller that inserts the run row *before*
working on a withdrawal from `pending_withdrawals()` can never recover the same withdrawal
twice, even if two loops race.

```python
seam = TigerDisruptionSeam(pool)
for withdrawal in await seam.pending_withdrawals():
    # INSERT INTO recovery_runs (..., withdrawal_event_id, ...)  -- claim it first
    stranded = await seam.disrupted_bookings(withdrawal.sailing_id)
    # REQUEST_RECOVERY_CAPACITY over ACP -> offers
    # proposal = await SolverAllocationSeam(TigerCapacityReader(pool)).allocate(...)
```

Root project needs, when you wire it (non-editable, like `freight-seams`):

```toml
dependencies = [..., "freight-disruption", "freight-allocation"]

[tool.uv.sources]
freight-disruption = { path = "packages/freight_disruption", editable = false }
freight-allocation = { path = "packages/freight_allocation", editable = false }
```

## Assumptions worth knowing

- **No stated deadline** (`cargo_requests.latest_arrival` is null) means any arrival is
  acceptable: it maps to `NO_DEADLINE` (2100-01-01).
- **PRD D5 is not stored yet,** so `max_additional_cost_minor` is 0 and every pricier
  replacement goes to the buyer for approval.
- **Quantity** is the booking's committed slots, falling back to the accepted offer's
  quantity when a booking has no `booking_slots` rows.

## Test

```
cd packages/freight_disruption
uv run pytest -q    # 6 unit; 2 live tests run when TIGER_DATABASE_URL is set
```

The live tests withdraw a real sailing: run them only against your own dev database
(`cd console && uv run python scripts/dev_db.py reset` first).

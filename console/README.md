# Carrier Console

Operator-facing web page for the demo's disruption moment. A carrier withdraws one of
its own sailings, naming the simulated advisory that prompted it. The withdrawal is a
single Tiger transaction that marks the sailing `WITHDRAWN`, withdraws its sellable
capacity, flips that sailing's `COMMITTED` bookings to `DISRUPTED`, and appends
`SAILING_WITHDRAWN` plus one `BOOKING_DISRUPTED` per booking to `market_events`.

Implements the `WITHDRAW_SAILING` command in
`docs/specs/05-disruption-detection.md` section 5, against the schema already in
`src/freight_exchange/db/migrations/001_core.sql`.

Owner: Tisya (see `OWNERSHIP.md`). Separate `uv` project, like `carriers/`, so it shares
no dependency resolution with the exchange.

## Run

Needs `TIGER_DATABASE_URL` and `PGPASSWORD` in the repo-root `.env`, and a seeded market
(`uv run python scripts/db.py migrate && uv run python scripts/db.py seed` from the root).

```
cd console
uv sync
uv run uvicorn carrier_console.web:app --host 0.0.0.0 --port 8800
```

Then open http://localhost:8800. For judging, serve it on a public HTTPS URL rather than
venue Wi-Fi.

## Test

```
cd console
uv run pytest -q                      # unit tests only, no database
TIGER_DATABASE_URL=... uv run pytest -q   # adds the live Tiger suite
```

`test_withdrawal_logic.py` is unit-only and proves nothing about Tiger.
`test_withdrawal_live.py` is the phase 5 evidence and skips without a database URL.

## Design decisions worth a second opinion

**Committed capacity slots are left alone.** Withdrawing a sailing sets `AVAILABLE` and
`HELD` slots to `WITHDRAWN`, but leaves `COMMITTED` ones as they are. They are the
physical record behind a booking that phase 7 supersedes via `replaces_booking_id`, and
clearing them before a replacement exists would erase the lineage. Easy to change if
phase 6's capacity accounting wants them gone.

**Idempotency rides on the table's unique key.** `UNIQUE (carrier_id, sailing_id,
event_type)` already makes `SAILING_WITHDRAWN` once-only, so a second withdrawal returns
the original event and writes nothing.

## NEEDS-FROM-PARTH

Neither item blocks this console. Both are in folders you own.

1. **`disruption_events` has no column** for `reason_risk_event_id`, `authorization_ref`
   or `idempotency_key`, which spec 05 section 5 names in the command. They currently go
   into the `market_events` payload, and the risk event id is prefixed onto `reason`.
   If phase 5 wants them as real columns, that is a migration in your folder.

2. **`ROUTE_RECOVERY` in `src/freight_exchange/responses.py:37`** matches
   "withdraw/disrupt/recover" and returns the canned "Coming next" line. Pointing it at
   real booking status is the one change that makes a withdrawal visible inside ASI:One,
   which is what turns this into a demo rather than an internal tool. One function, your
   file, your call.

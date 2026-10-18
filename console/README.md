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

## Local dev database

A TimescaleDB container on your own machine (the same software as Tiger Cloud), so tests
that withdraw sailings never touch the shared database. Needs Docker running.

```
cd console
uv run python scripts/dev_db.py up       # first time: creates it; later: starts it
uv run python scripts/dev_db.py reset    # wipe, migrate, seed, preseed Buyers B and C
```

`up` fills the repo-root `.env` the first time (local URL, generated password, carrier seeds;
nothing printed). `reset` is the clean slate after live tests cancel sailings. Both refuse to
run unless `TIGER_DATABASE_URL` points at 127.0.0.1 or localhost, so they can never wipe the
shared Tiger Cloud database used for the demo.

## Seeding something to disrupt

A withdrawal is only interesting if the sailing carries commitments. Phase 4 is several
gates away, so `preseed.py` writes Buyer B and Buyer C as `COMMITTED` bookings through
the real commitment invariants (atomic slot claim under `FOR UPDATE`, no overselling,
real foreign keys, same-transaction events). This is spec 05 work package 5.

```
cd console && uv run python -m carrier_console.preseed_cli
```

Buyer A is deliberately refused: PRD 16.1 requires Buyer A to be booked live through
ASI:One so the judge watches that exact booking get disrupted and recovered.

Preseeded offers carry `PRESEED_NOT_A_VERIFIED_CARRIER_SIGNATURE`. Nothing produced by
the preseed is phase 2, 3 or 4 evidence, and it should never be described as such.

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

2. **`ROUTE_RECOVERY` in `src/freight_exchange/responses.py:47`** (handled at line 100)
   matches "withdraw/disrupt/recover" and still returns the canned "Coming next" line. Pointing it at
   real booking status is the one change that makes a withdrawal visible inside ASI:One,
   which is what turns this into a demo rather than an internal tool. One function, your
   file, your call.

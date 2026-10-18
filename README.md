# Freight Recovery Exchange (LA Hacks AI 2026)

An agent-native ocean-freight exchange on Fetch.ai. A buyer books container space by chatting with
**`@freight-recovery-exc`** in ASI:One; independent **carrier agents** quote, take one counteroffer, hold
and confirm space over ACP; payment is a **Stripe TEST** checkout. When a carrier **withdraws a sailing**,
the exchange asks every other carrier once for replacement space, decides **all** displaced bookings
together with **OR-Tools**, holds and confirms the replacements, and tells each buyer what happened.
Every message, hold, booking and event is recorded in **Tiger (TimescaleDB)**.

> **Demo market.** Carriers (Atlas, Beacon, Cove, Meridian, Dhow), vessels, sailings, prices and sailing
> withdrawals are **fictional simulations**, labelled as such. Payments are Stripe **test mode** only:
> no real money moves, and recovery never charges or refunds anything.

## How it fits together

```
ASI:One buyer ──chat──▶ Exchange agent (LangGraph, Agentverse mailbox)  src/agent.py
                            │  signed uAgents messages (ACP)               packages/freight_contracts
                            ▼
              Carrier agents ×5 (uAgents, own private policy)            carriers/
                            │
                            ▼
                 Tiger: bookings, holds, offers, market_events hypertable  src/freight_exchange/db
Carrier console (withdraw a sailing) ─▶ disruption ─▶ recovery worker ─▶ offers ─▶ OR-Tools ─▶ holds ─▶ confirm
console/                    packages/freight_disruption  src/freight_exchange/recovery*.py  packages/freight_allocation
```

Owners and folders: `OWNERSHIP.md`. Decisions: `docs/decisions/`. Phase specs: `docs/specs/`. Progress and
evidence: `progress.md`.

## Setup (once)

Needs [uv](https://docs.astral.sh/uv/), Python 3.12, a Tiger/TimescaleDB database, an Agentverse
external-LangGraph agent and a Stripe **test** secret key.

```bash
cp .env.example .env     # fill AGENT_URI, TIGER_DATABASE_URL, PGPASSWORD, EXCHANGE_AGENT_ADDRESS, STRIPE_TEST_SECRET
uv sync && (cd carriers && uv sync) && (cd console && uv sync)
uv run python scripts/init_carrier_identities.py   # fills the five CARRIER_*_SEED values, prints addresses
uv run python scripts/db.py migrate                # schema + hypertables (idempotent)
uv run python scripts/demo.py reset                # clean, freshly anchored demo market
```

## Demo runbook (about 5 minutes)

1. **Reset and seed:** `uv run python scripts/demo.py reset && uv run python scripts/demo.py seed`.
   Seed books Buyer B (1×20GP) on ATL-101 and an unrelated Buyer C elsewhere, and prints the exact
   prompt for step 3 (its arrival date follows the market's anchor).
2. **Start everything:** `./scripts/run_demo.sh` (carriers, Exchange, recovery worker, console). Wait for
   `Registered with agentverse` in `logs/exchange.log`.
3. **Quote (ASI:One):** paste the printed prompt, e.g.
   `@freight-recovery-exc Quote 2x20GP from Jebel Ali to Mumbai, arrive by Oct 23, budget $5,600, don't split`.
   You see signed offers from the carriers, one counter each, and the declines.
4. **Book:** `select atlas`, then `book it, auto-rebook up to $600 extra`. Open the Stripe TEST link, pay
   with card `4242 4242 4242 4242` (any future expiry, any CVC), return and send `paid`. You get a booking
   ID and claim code.
5. **Disrupt:** open http://localhost:8800 and withdraw **ATL-101** (simulated advisory), confirm.
6. **Recover:** within about a minute ASI:One shows your sailing was cancelled, then that you were rebooked
   (Beacon BEA-201) once the carrier confirmed. `@freight-recovery-exc my bookings` shows "replaced by BK-…".
   Without an allowance (`book it`), you are asked instead: reply `approve BK-…` or `decline BK-…`.
7. **Prove it:** `uv run python scripts/demo.py audit` (invariants) and `uv run python scripts/demo.py evidence`
   (ACP traffic, Stripe test verification, bookings and lineage, solver fingerprint, holds, hypertable).

Unattended rehearsal of steps 1, 5, 6 and 7 with a preseeded stand-in for Buyer A:
`uv run python scripts/demo.py rehearse --runs 3`.

## Verify

```bash
uv run pytest -q && (cd carriers && uv run pytest -q)          # carrier live tests need TIGER_DATABASE_URL
for p in packages/freight_seams packages/freight_disruption packages/freight_allocation console; do (cd $p && uv run pytest -q); done
uv run python scripts/quote_smoke.py          # live quote round + Tiger rows
uv run python scripts/acp_failure_smoke.py    # typed errors: unauthorized, expired, malformed, unreachable
uv run python scripts/negotiation_smoke.py    # one counter per offer, private floor, idempotent selection
uv run python scripts/recovery_smoke.py ATL-101 [--withdraw] [--propose-only]   # one recovery run + evidence
uv run python scripts/demo.py audit           # transaction invariants on the live database
```

The console and disruption live tests withdraw sailings, so they run only against a local database
(`cd console && uv run python scripts/dev_db.py up`).

## Status against the hackathon requirements

| Item | Status | Evidence |
|---|---|---|
| Agent registered on Agentverse, reachable from ASI:One (`@freight-recovery-exc`, mailbox) | Verified | `progress.md` Phase 1–4 checkpoints (ASI:One request IDs) |
| Multi-agent interaction over ACP: 5 carrier uAgents, signed and verified replies | Verified | `quote_smoke`, recovery runs (`agent_messages`) |
| End-to-end ASI:One booking with Stripe **test** checkout | Verified | BK-28C01D37 (Phase 4); decline-card case needs a human run |
| Disruption → joint OR-Tools recovery → confirmed replacement bookings | Verified (preseeded buyers) | `rec_649c34bc7ace`, 3/3 rehearsals |
| Buyer approval for pricier replacements | Verified (handler level) | `rec_4b2d9d5fb9a6`; the ASI:One message itself needs a human run |
| Recovery messages appearing in the buyer's ASI:One chat | Not yet verified | needs a live ASI:One Buyer A (runbook step 6) |
| ASI:One discovery without the @handle | Not verified | to try: ask ASI:One for an ocean-freight booking agent |
| Tiger time-series (hypertable `market_events`) as runtime proof | Verified | `demo.py evidence` |
| Fetch Agent Payment Protocol (bonus) | **Not claimed** | Stripe test checkout only; see `docs/decisions/0006` |
| ASI:One Interactive Cards (bonus) | Not implemented | text replies (agreed fallback in `CardSeam`) |

## Limitations

- Simulated market and simulated withdrawals only; no real vessel schedules, advisories or shipments.
- Recovery confirms freight in the simulated ledger; no payment difference is charged or refunded.
- Multi-carrier recovery is not atomic: each hold and confirmation is idempotent and recorded per leg;
  an unanswered confirmation leaves the run `RECONCILIATION_REQUIRED` until the worker retries it.
- The recovery worker signs as the Exchange but never reads its mailbox; run it on the same machine.

## Gotchas

- `AGENT_URI` contains the agent's private key. Treat it, `CARRIER_*_SEED`, `PGPASSWORD` and Stripe keys as
  secrets; never commit `.env`. Check a secret is set by its length, not its value.
- Only one machine may run the Exchange (or `run_demo.sh`) with a given `AGENT_URI`: mailbox mode delivers
  each message once.
- Carriers run on the same machine as the Exchange (localhost endpoints, resolved via the Almanac).
- `freight-contracts` is installed non-editable. After editing it: `uv sync --reinstall-package freight-contracts`
  (also in `carriers/`), then restart the carriers **and** the Exchange: message schema digests must match.
- Sailing dates are relative to the fixture anchor; `demo.py reset` re-anchors to tomorrow 00:00 UTC.
- Add `?log=INFO` to `AGENT_URI` to see Agentverse SDK logs.

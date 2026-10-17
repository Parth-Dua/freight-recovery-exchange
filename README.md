# Part 1 Spec-Driven Development Pack

Place `AGENTS.md` and `progress.md` in repository root. Place the full PRD in `docs/PRD.md` and phase specs in `docs/specs/`.

Follow phases 01→08, gated by observable acceptance evidence. Phase 01 was preserved from the earlier Phase 1 specification. Docs describe requirements, **not completed implementation**.

**Latest payment choice:** Stripe test-only API and official test card; no actual-money transfers. Stripe test Checkout is not automatically equivalent to Fetch's Agent Payment Protocol bonus; verify separately. Any conflicting payment recommendation in the historical PRD is superseded.

**Part 2 deferred:** real risk data, Sentry and the Market Command Center; schemas in Part 1 remain future-data-compatible.

---

## Runbook (Phase 1 + Phase 2)

**Layout**
- `src/agent.py` — Exchange Agent graph (`langgraph.json` → `agent:agent`), registered on Agentverse as `@freight-recovery-exc`, mailbox mode.
- `src/freight_exchange/` — routing/replies, request parser, `carrier_client` (uAgents sync transport + reply validation), `quotes` (quote round), `repository` (Tiger), `db/` (migrations + seed).
- `packages/freight_contracts/` — shared versioned messages (`REQUEST_OFFER`, `PROPOSE_OFFER`, `REJECT_OFFER`, `AGENT_ERROR`) + carrier signing. Used by both runtimes.
- `carriers/` — company Carrier Agents (uAgents 0.26.1), **separate uv project** (uagents needs protobuf 5; langgraph-api needs ≥6). See `docs/decisions/0002`.
- `fixtures/demo_carriers.json` — fictional Atlas/Beacon/Cove, AEJEA→INBOM 20GP sailings.

**Setup (once)**
```bash
cp .env.example .env            # fill AGENT_URI, TIGER_DATABASE_URL, PGPASSWORD (never commit)
uv sync && (cd carriers && uv sync)
uv run python scripts/init_carrier_identities.py   # creates carrier seeds in .env, prints addresses
uv run python scripts/db.py migrate                # Tiger schema + hypertables (idempotent)
uv run python scripts/db.py seed                   # fictional carriers/sailings (idempotent)
```

**Run (two terminals)**
```bash
./scripts/run_carriers.sh                          # Atlas :8101, Beacon :8102, Cove :8103
uv run langgraph-av dev --no-browser               # Exchange Agent (Agentverse mailbox)
```

**Verify**
```bash
uv run pytest -q && (cd carriers && uv run pytest -q)
uv run python scripts/quote_smoke.py               # real quote round + Tiger SELECT evidence
uv run python scripts/acp_failure_smoke.py         # typed errors from live carriers
```
In ASI:One: `@freight-recovery-exc Quote 2x20GP from Jebel Ali to Mumbai, arrive by Oct 23, budget $5,600, don't split`. Every reply ends with `Ref: req_…`, which is also the Tiger `cargo_requests.request_id`.

**Gotchas**
- `AGENT_URI` contains the agent's private key. Treat it, `CARRIER_*_SEED` and `PGPASSWORD` as secrets.
- Only one machine should run the Exchange with a given `AGENT_URI` (they would share one mailbox).
- Carriers must run on the same machine as the Exchange (localhost endpoints, resolved via Almanac).
- Set `?log=INFO` at the end of `AGENT_URI` to see Agentverse SDK logs (silent by default).
- This repo lives in an iCloud-synced folder on one machine; macOS re-hides `.pth` files there and Python skips hidden `.pth` files, so `freight-contracts` is installed **non-editable**. After editing contracts: `uv sync --reinstall-package freight-contracts` (and in `carriers/`).
- Sailing dates are relative to `anchor_date` in the fixture; re-anchor with `uv run python scripts/db.py seed --anchor 2026-10-25T00:00:00Z`.
- Tunnel fallback: `AGENTVERSE_MAILBOX=false TUNNEL_TRANSPORT_PROTOCOL=http2 uv run langgraph-av dev --tunnel --no-browser`.

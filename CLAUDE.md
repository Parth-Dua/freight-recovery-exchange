# Freight Recovery Exchange (LA Hacks AI Hackathon 2026)

Agent-native ocean-freight exchange on Fetch.ai: buyers book through ASI:One, independent
carrier agents quote and negotiate over ACP, and when a sailing is withdrawn the exchange
recovers the displaced bookings.

Before doing anything, read in this order:
1. `AGENTS.md`: project rules, including the mandatory `progress.md` updates.
2. `OWNERSHIP.md`: who owns which folders, and the git workflow.
3. `progress.md` and the active `docs/specs/NN-*.md`.

## Hard rules

- **Process since 2026-10-18 (user decision):** Parth owns the whole build. Work directly on `main`
  in `~/Downloads/LAHacksAI2026`, commit and push to `main`, no PRs or approvals needed. Work the
  phases in `docs/specs_2/` in order (PRD: `docs/PRD.md`; old PRD: `docs/oldPRD.md`).
- Anything needing the user goes in `docs/critical_alerts.md`, with current testing steps kept up
  to date there.
- Before each push: run every affected test suite.
- A test against a fake is a unit test. Never present it as Agentverse, ASI:One, ACP,
  Stripe or Tiger evidence.
- Never commit `.env`, and never print a key, seed or `AGENT_URI`. Check that a secret is
  set by its length, not its value.

## Shared resources (parallel work)

- **Tiger:** each person uses their own database. The console withdraws sailings, and
  `scripts/db.py seed` does not un-withdraw them. Local dev database:
  `cd console && uv run python scripts/dev_db.py up` (first time) or `reset` (clean slate).
- **Agent identity:** only one process may run with a given `AGENT_URI` (mailbox mode).
  Use your own throwaway Agentverse agent for experiments.

## Commands

- Root: `uv run pytest -q`
- Console: `cd console && uv run pytest -q` (live Tiger tests skip without `TIGER_DATABASE_URL`)
- Seams: `cd packages/freight_seams && uv run pytest -q`
- If root tests fail at import after `freight_contracts` changes, the install is stale
  (it is non-editable): `uv sync --reinstall-package freight-contracts`

# Freight Recovery Exchange (LA Hacks AI Hackathon 2026)

Agent-native ocean-freight exchange on Fetch.ai: buyers book through ASI:One, independent
carrier agents quote and negotiate over ACP, and when a sailing is withdrawn the exchange
recovers the displaced bookings.

Before doing anything, read in this order:
1. `AGENTS.md`: project rules, including the mandatory `progress.md` updates.
2. `OWNERSHIP.md`: who owns which folders, and the git workflow.
3. `progress.md` and the active `docs/specs/NN-*.md`.

## Hard rules

- Only edit folders owned by the person you are working with. If a change is needed in
  someone else's folder, write the exact change under `NEEDS-FROM-<owner>` in the PR and
  tell the user to ask the owner.
- Never commit or push to `main`. Branch from fresh `main` as `<name>/<feature>`, open a PR
  with `gh pr create --base main`, and never merge unless the user explicitly says to.
- Before opening a PR: merge `origin/main`, then run every affected test suite.
- Seams in `packages/freight_seams/` and contracts in `packages/freight_contracts/` are
  additive only, and need the other owner's yes before any signature changes.
- A test against a fake is a unit test. Never present it as Agentverse, ASI:One, ACP,
  Stripe or Tiger evidence.
- Never commit `.env`, and never print a key, seed or `AGENT_URI`. Check that a secret is
  set by its length, not its value.

## Shared resources (parallel work)

- **Tiger:** each person uses their own database. The console withdraws sailings, and
  `scripts/db.py seed` does not un-withdraw them.
- **Agent identity:** only one process may run with a given `AGENT_URI` (mailbox mode).
  Use your own throwaway Agentverse agent for experiments.

## Commands

- Root: `uv run pytest -q`
- Console: `cd console && uv run pytest -q` (live Tiger tests skip without `TIGER_DATABASE_URL`)
- Seams: `cd packages/freight_seams && uv run pytest -q`
- If root tests fail at import after `freight_contracts` changes, the install is stale
  (it is non-editable): `uv sync --reinstall-package freight-contracts`

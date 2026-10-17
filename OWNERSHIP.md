# Folder ownership and two-person workflow

Two builders on this repo: **Parth** (lead, exchange + agents + solver) and **Tisya**
(carrier console, demo surface, verification). `docs/specs/` remains the contract.
This file only covers *who edits what* and *how changes land*.

## Ownership

The cut is **two agent boundaries**. Parth owns agent-to-carrier: ACP out to the
carrier uAgents, Almanac, policy, Tiger, the solver, payments. Tisya owns
agent-to-human: the ASI:One boundary, discovery, cards, session state, approvals,
plus the carrier console and the demo.

| Owner | Folders |
|---|---|
| Parth | `src/` except `src/freight_exchange/asi/`, `carriers/`, `scripts/`, `docs/`, `tests/`, root `pyproject.toml` |
| Tisya | `console/`, `src/freight_exchange/asi/`, `.github/`, `OWNERSHIP.md`, demo assets |
| Shared, additive only | `packages/freight_contracts/`, `packages/freight_seams/` |

`src/freight_exchange/asi/` does not exist yet. It is proposed as the home for the
ASI:One-facing adapter (card build and parse, discovery profile, approval state) so
that surface has one owner without splitting the graph itself, which stays Parth's.

One rule: **never edit a folder you do not own.** If you need a change in the other
person's folder, write down the exact change (PR description, or a `NEEDS-FROM-<owner>`
note in your PR) and let the owner apply it.

## Git workflow

- Never commit or push to `main`.
- One branch per feature, cut from fresh `main`, named `<name>/<feature>`.
- One PR per feature: `gh pr create --base main`.
- Before opening: `git merge origin/main`, then build and run tests.
- Nobody merges another person's PR without being asked. Squash merge, delete branch.
- If `main` breaks, a tiny hotfix PR goes first and nothing else merges until green.

## Changing the shared contract

`docs/specs/` and `packages/freight_contracts/` are shared truth. Changes are **additive
only** during the event, and need the other person's explicit OK in chat first.

## Open questions for the owner

Tracked in PR descriptions under `NEEDS-FROM-<owner>`, so neither of us silently
decides something that belongs to the other.

# Folder ownership and two-person workflow

Two builders on this repo: **Parth** (lead, exchange + agents + solver) and **Tisya**
(carrier console, demo surface, verification). `docs/specs/` remains the contract.
This file only covers *who edits what* and *how changes land*.

## Ownership

| Owner | Folders |
|---|---|
| Parth | `src/`, `carriers/`, `packages/`, `scripts/`, `docs/`, `tests/`, root `pyproject.toml` |
| Tisya | `console/`, `.github/`, `OWNERSHIP.md`, demo assets |

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

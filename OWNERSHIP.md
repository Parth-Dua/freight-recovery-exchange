# Folder ownership and two-person workflow

Two builders on this repo: **Parth** (lead; the idea, the agent network, booking, and making
recovery real) and **Tisya** (the ASI:One surface and the recovery decision).
`docs/specs/` remains the contract. This file only covers *who owns what* and *how changes land*.

## The split

**Parth talks to the carriers and makes bookings real. Tisya decides who goes where, checks
the carriers aren't overpromising, and explains it to the buyer.**

- **Parth:** agent-to-carrier and booking. ACP to the carrier uAgents, Almanac, carrier policy,
  quoting and negotiation (phase 3), holds and payment (phase 4), recovery offers over ACP
  (phase 6, the `REQUEST_RECOVERY_CAPACITY` round), reserving and confirming replacements
  (phase 7), Tiger schema and writes, integration and reliability (phase 8).
- **Tisya:** agent-to-human and the recovery decision. The ASI:One boundary (discovery, buyer
  identity, cards, approvals), sailing withdrawal and the carrier console (phase 5), and the
  recovery decision (phase 6 allocation): verify carrier claims, solve, explain.

### Recovery, step by step

| Step | What happens | Owner | Seam |
|---|---|---|---|
| 1 | A carrier closes a sailing | Tisya | `DisruptionSeam.withdraw` |
| 2 | Work out who is stranded | Tisya | `DisruptionSeam.disrupted_bookings` |
| 3 | Ask the other carriers for replacement space over ACP; check each message is valid (signature, schema, freshness) | Parth | |
| 4 | Check each carrier's *claim* against Tiger: does that ship really have the capacity it offers | Tisya | inside `AllocationSeam.allocate` |
| 5 | Decide who goes where (OR-Tools) | Tisya | `AllocationSeam.allocate` |
| 6 | If it exceeds what the buyer approved, ask them in ASI:One and persist the answer | Tisya | `ApprovalSeam` |
| 7 | Reserve and confirm replacements with each carrier; supersede old bookings | Parth | `RecoverySeam` |
| 8 | If a reservation fails, release the rest and re-decide once | Parth triggers, Tisya decides | `AllocationSeam.allocate` again |
| 9 | Tell the buyer what happened and why | Tisya | `CardSeam.build_recovery_summary` |

Step 3 versus step 4: Parth checks the *message*; Tisya checks the *claim*.

## Folders

| Owner | Folders |
|---|---|
| Parth | `src/` except `src/freight_exchange/asi/`; `carriers/`; `scripts/`; `docs/`; `tests/` except `tests/asi/`; `packages/freight_contracts/`; root `pyproject.toml` |
| Tisya | `console/`; `src/freight_exchange/asi/`; `tests/asi/`; `packages/freight_allocation/`; `.github/`; `OWNERSHIP.md`; demo assets |
| Per seam | `packages/freight_seams/`: each Protocol belongs to its owner (see its README). Signatures change only with both people's yes. |

`src/freight_exchange/asi/` and `packages/freight_allocation/` do not exist yet. `asi/` is the
home for the ASI:One-facing adapter (cards, approval state, buyer identity) so that surface has
one owner without splitting the graph, which stays Parth's. `freight_allocation/` is the home
for the claim checks and the OR-Tools model behind `AllocationSeam`.

Not yet settled, to agree separately: buyer-facing reply text in `responses.py`, the Agentverse
profile in `profile.py`, and Fetch's Agent Payment Protocol bonus.

One rule: **never edit a folder you do not own.** If you need a change in the other person's
folder, write down the exact change (PR description, or a `NEEDS-FROM-<owner>` note in your PR)
and let the owner apply it.

## Git workflow

- Never commit or push to `main`.
- One branch per feature, cut from fresh `main`, named `<name>/<feature>`.
- One PR per feature: `gh pr create --base main`.
- Before opening: `git merge origin/main`, then build and run tests.
- Nobody merges another person's PR without being asked. Squash merge, delete branch.
- If `main` breaks, a tiny hotfix PR goes first and nothing else merges until green.

## Changing the shared contract

`docs/specs/`, `packages/freight_contracts/` and `packages/freight_seams/` are shared truth.
Changes are **additive only** during the event, and need the other person's explicit OK in
chat first.

## Open questions for the owner

Tracked in PR descriptions under `NEEDS-FROM-<owner>`, so neither of us silently
decides something that belongs to the other.

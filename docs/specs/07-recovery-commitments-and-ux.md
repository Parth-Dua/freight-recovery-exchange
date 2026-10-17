# Phase 7 Specification — Recovery Holds, Commitments & ASI:One Experience

**Project:** Autonomous Freight Recovery Exchange · LA Hacks AI 2026  
**Status:** NOT STARTED (unless `progress.md` has verified evidence)  
**Active only after:** Phase 6 returns feasible proposals and Phase 4 provides atomic holds/commits.  
**Context:** `docs/PRD.md` (reference; later user decisions supersede historical proposals)  
**Scope:** Part 1 P0 only. No Part 2 work.

## 1. Objective

Convert joint recovery proposals into actual replacement commitments, truthfully notify buyers, and preserve unrelated bookings.

## 2. Inputs / prerequisites

Phase 6 returns feasible proposals and Phase 4 provides atomic holds/commits. Do not substitute mocks for prerequisite integration without labeling the test as a unit test; the gate requires the actual external boundary.

## 3. In scope

- LangGraph RecoveryGraph lifecycle: trigger, detect, collect, solve, acquire holds, approval policy, confirm, supersede old, persist, notify.
- Acquire all selected replacement holds with deterministic ordering; release partial holds and attempt at most one fresh re-solve if a hold fails.
- Honor buyer-approved recovery envelopes; outside limits require explicit ASI:One approval before final commitment.
- Support persisted asynchronous buyer approval/pending status; no invented consent or refund claims.
- ASI:One Interactive Cards (or safe fallback if documented capabilities differ) presenting actual recovery outcomes.
- Preserve Buyer C and any other unrelated committed bookings; maintain link from original to replacement booking.

## 4. Explicitly out of scope

- Recovery negotiation/counteroffers, automatic refund, freight payment differences actually charged/refunded, escrow, final dashboard and real risk feeds.

## 5. Technical architecture and contracts

`recovery_run`: `CREATED|OFFERS_REQUESTED|PROPOSED|HOLDING|AWAITING_APPROVAL|CONFIRMING|COMPLETED|PARTIAL|FAILED|RECONCILIATION_REQUIRED`; each assignment `PROPOSED|HELD|CONFIRMED|RELEASED|FAILED`. `replaces_booking_id` creates auditable lineage; original booking may be `DISRUPTED` then `SUPERSEDED`, never deleted.

Buyer authority: `max_additional_simulated_cost_minor`, `latest_acceptable_arrival_at`, `allow_split`, `auto_approve_within_limits` captured during booking if explicitly approved; absence means require approval before recovery, not silent consent. No additional Stripe charge for simulated recovery price difference in Part 1.

Booking order: reserve candidate holds → verify buyer authority or wait for explicit approval → confirm all possible replacements → update Tiger commitments and emit per-booking events. Multi-carrier network transactions are not globally ACID: use idempotent saga/reconciliation states and don't claim all-or-nothing atomicity across agents.

## 6. Ordered work packages

1. [ ] Implement persisted RecoveryGraph state and keyed idempotent withdrawal triggers.
2. [ ] Acquire distributed replacement holds, retry boundedly, and release failures safely.
3. [ ] Add buyer authorization state, ASI:One approval/decline or `AWAITING_APPROVAL` flow.
4. [ ] Confirm replacement bookings and supersede old booking lineage with reconciliation safeguards.
5. [ ] Render Interactive Card or supported response showing recovered, pending, unrecovered by real booking ID.
6. [ ] Prove same actual Buyer A booking made in ASI:One is recovered after withdrawal.


## 7. Acceptance tests (must actually execute)

- [ ] **AT-07-01:** Recovered bookings have confirmed holds and new booking IDs, not just solver assignments.
- [ ] **AT-07-02:** Buyer C remains committed unchanged.
- [ ] **AT-07-03:** Retries and duplicate withdrawal triggers do not create duplicate replacement bookings.
- [ ] **AT-07-04:** One carrier hold fails: release safe holds/re-solve once and return truthful partial outcome.
- [ ] **AT-07-05:** No authorization provided: user approval is required and persists across resumed session.
- [ ] **AT-07-06:** ASI:One never displays recovered until carrier confirmation is validated.


## 8. Definition of Done

**Gate:** From withdrawal to ASI:One recovery summary, ≥2 bookings get actual replacement commitments within authorized constraints and unrelated booking remains unchanged.

Mark this phase passed only after its test results and actual integration evidence are recorded in `progress.md`; unmet conditions remain blocked, not assumed. Never count a mock unit test as an Agentverse, ASI:One, Stripe, or ACP end-to-end test.

## Engineering rules shared by every phase

- **Checkpoint discipline:** read `AGENTS.md`, `progress.md`, this phase spec, and relevant `docs/PRD.md` sections before changing code. After each completed work package, actual test run, blocker, or architectural decision, update `progress.md` concisely. Update again before stopping, switching models, or handing off; record the **exact next action**.
- Preserve backward compatibility with passed phases. Test real service behavior, not hardcoded UI narratives. Avoid fabricated confirmations.
- Use shared Python types / versioned payloads with UUID identifiers, ISO-8601 UTC timestamps, decimal money as integer minor units + currency, typed statuses, and canonical domain event fields. Do not silently conflate a quote, a hold, and a commitment.
- Never store API keys, seed phrases, database credentials or card details in source, fixtures, logs, `progress.md`, or version control.
- **Source hierarchy:** current explicit user decisions > active phase spec > historical PRD; external Fetch/Stripe API syntax must be verified against installed SDK and current official docs before coding. Record deviations in `docs/decisions/` and `progress.md`.
- Do not work on Part 2 (live advisories, Sentry, Market Command Center, rich aggregates) before Phase 8 passes.
- Every integration boundary needs timeouts, safe errors, idempotency where state can mutate, correlation IDs, and tests including failure paths.

## Gate / evidence requirements

To pass this phase, attach to `progress.md` a short summary of tests actually run (commands and pass/fail counts), working end-to-end evidence for the phase's critical path, unresolved risks, affected files, and exactly what Phase N+1 may assume. Mark user-dependent UI tests `NEEDS HUMAN VERIFICATION` rather than guessed success.

## 9. Reference sections in full PRD

See `docs/PRD.md`: §4 (scope), §5–6 (architecture), §7 (booking), §8 (recovery), §9 (contracts), §10–12 (Tiger/state), §13–14 (LangGraph/errors), §15 (historical payment proposal; superseded by Stripe **test-only** choice), §16 (demo) and §18 (acceptance). Phase-specific choices and explicit latest user decisions override earlier unapproved PRD defaults.

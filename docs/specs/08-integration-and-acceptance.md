# Phase 8 Specification — Full Vertical Slice, Reliability & Submission

**Project:** Autonomous Freight Recovery Exchange · LA Hacks AI 2026  
**Status:** NOT STARTED (unless `progress.md` has verified evidence)  
**Active only after:** Phases 1–7 passed their gate or explicitly documented blocking constraints.  
**Context:** `docs/PRD.md` (reference; later user decisions supersede historical proposals)  
**Scope:** Part 1 P0 only. No Part 2 work.

## 1. Objective

Prove Part 1 works reliably from ASI:One through payment, disruption and joint recovery; prepare reproducible hackathon submission.

## 2. Inputs / prerequisites

Phases 1–7 passed their gate or explicitly documented blocking constraints. Do not substitute mocks for prerequisite integration without labeling the test as a unit test; the gate requires the actual external boundary.

## 3. In scope

- End-to-end demo test with one buyer live booking, preseeded second displaced booking, and unaffected third booking.
- Direct verification of Agentverse discoverability, ACP across company-agent identities, ASI:One usability and card behavior.
- Stripe official test card checkout verification; no real-money claims.
- Tiger PostgreSQL business-event hypertable query as runtime proof, transaction invariant audits.
- Reproducible reset/seed script, controlled demos, deployment documentation, public repository content, README, short recorded video and backup trace.
- Reconcile hackpack mandatory requirements and bonus statuses; do not claim Fetch Payment Protocol if only Stripe checkout is tested.

## 4. Explicitly out of scope

- Part 2 ingestion, Sentry, full Market Command Center, escrow/refunds, sophisticated financial reconciliation, real shipment fulfillment.

## 5. Technical architecture and contracts

Reproducible `demo:reset` (or equivalent safe script) must reset **only designated simulated demo schema/data**, never a production/non-demo database. `demo:seed` creates Atlas, Beacon, Cove, Buyer B and unaffected Buyer C through business services; live Buyer A goes through ASI:One. `demo:withdraw` enforces carrier authorization and consumes a simulated risk event.

The demo output must prove provenance: ACP traces/correlation IDs, Stripe `test` payment session verification, Tiger booking and event query, OR-Tools solution ID and confirmed replacement holds. Final status table should distinguish mandatory Fetch requirements, attempted bonuses, verified bonuses and not-yet-supported items.

## 6. Ordered work packages

1. [ ] Write replayable end-to-end smoke runner and exact human ASI:One steps.
2. [ ] Run concurrency, idempotency, expiry, ACP loss, Stripe failure and recovery partial-capacity tests.
3. [ ] Verify clean start with only sanitized env examples and seed scripts.
4. [ ] Confirm hackpack agent registration, ACP, ASI:One discovery, end-to-end interaction and repository eligibility.
5. [ ] Capture recorded demo including distinct carrier agents, live booking, test checkout, disruption, recovery, Tiger events.
6. [ ] Freeze code, document open limitations and hand off to Part 2 only if gate green.


## 7. Acceptance tests (must actually execute)

- [ ] **AT-08-01:** At least three consecutive clean full demos with no manual DB patching.
- [ ] **AT-08-02:** Buyer A created live is one of the exact bookings later recovered; Buyer B seeded correctly; Buyer C unchanged.
- [ ] **AT-08-03:** No duplicate booking or overselling after concurrent attempts/retry.
- [ ] **AT-08-04:** Test-only Stripe payment verified; no secret or test card details committed.
- [ ] **AT-08-05:** A simulated disruption is prominently labeled simulated; no unsupported claims about real vessel schedules.
- [ ] **AT-08-06:** ASI:One discovery and actual backend ACP invocation evidenced, not inferred.
- [ ] **AT-08-07:** README/setup and video can be followed by a fresh teammate.


## 8. Definition of Done

**Gate:** All Part 1 critical gates pass and documentary evidence exists. Only then authorize Part 2.

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

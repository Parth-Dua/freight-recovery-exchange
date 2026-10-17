# Phase 6 Specification — One-Shot Recovery Offers & Joint OR-Tools Allocation

**Project:** Autonomous Freight Recovery Exchange · LA Hacks AI 2026  
**Status:** NOT STARTED (unless `progress.md` has verified evidence)  
**Active only after:** Phase 5 creates multiple disrupted bookings and Phase 2 can request real carrier offers.  
**Context:** `docs/PRD.md` (reference; later user decisions supersede historical proposals)  
**Scope:** Part 1 P0 only. No Part 2 work.

## 1. Objective

Recover multiple displaced bookings as one constrained allocation using fresh independent carrier offers and OR-Tools, with no recovery bargaining rounds.

## 2. Inputs / prerequisites

Phase 5 creates multiple disrupted bookings and Phase 2 can request real carrier offers. Do not substitute mocks for prerequisite integration without labeling the test as a unit test; the gate requires the actual external boundary.

## 3. In scope

- Enumerate all impacted commitments for a withdrawal and derive existing buyer constraints.
- Ask independent carrier agents for fresh replacement availability/price via ACP once, with typed declines, timeouts, TTLs.
- Implement joint integer optimization model supporting quantity, slot capacity, latest arrival, valid price, indivisibility/no-split, policy-approved extra cost and eligibility.
- Define explicit objective: maximize count of fully recovered bookings; then minimize total authorized simulated price; deterministic tie-breaking. This is a proposed default requiring confirmation if business priority is disputed.
- Return structured feasible, partially feasible or infeasible proposal with explanations and never claim it is booked.

## 4. Explicitly out of scope

- Interactive carrier counteroffers during recovery, routing optimization, reassigning unrelated intact bookings, magical capacity creation, actual booking confirmation (Phase 7).

## 5. Technical architecture and contracts

Carrier `REQUEST_RECOVERY_CAPACITY {recovery_run_id,withdrawn_sailing_id,request_items:[{booking_id,container_type,quantity,origin_port,destination_port,latest_arrival,allow_split,authorization_envelope}],response_deadline}` → `RECOVERY_OFFER {offer_id,carrier_id,sailing_id,capacity_units,total_price_minor,currency,arrival_at,expires_at,eligible_booking_ids}`. The agent may respond with multiple sailing alternatives, but no counteroffers.

Solver input = snapshot of persisted disrupted bookings and validated offer capacities; output `RecoveryProposal {recovery_run_id,assignments:[{booking_id,offer_id,sailing_id,quantity,quoted_price_minor}],unrecovered_booking_ids,objective_values,diagnostics,input_version}`. For each booking, either allocate its entire required quantity (or split only when explicitly allowed) or mark unrecovered. For each sailing, total allocated quantity ≤ offer inventory, after accounting for existing active holds/commitments. No assignment outside buyer approved arrival/price envelope. Use integer minor money units. Do not choose based on hardcoded demo buyer IDs.

## 6. Ordered work packages

1. [ ] Implement fresh one-shot `REQUEST_RECOVERY_CAPACITY` message and strict validation against Tiger.
2. [ ] Build pure allocation model with binary or integer assignment variables and booking-complete indicator.
3. [ ] Encode seller capacity constraints, indivisibility, deadlines and budget/authorization envelopes.
4. [ ] Set deterministic solution ordering and solve-time limit; persist input snapshot/version and proposal status.
5. [ ] Create fixture where Atlas displaced A:2 unsplittable and B:1, Beacon:2 and Cove:1.
6. [ ] Add constrained shortage fixtures and a test that Buyer C remains untouched.


## 7. Acceptance tests (must actually execute)

- [ ] **AT-06-01:** Baseline solver returns 2 slots Beacon to A and 1 Cove to B, based on constraints not special IDs.
- [ ] **AT-06-02:** Remove Cove capacity: solution is partial/infeasible and never pretends all buyers recovered.
- [ ] **AT-06-03:** Adding a second compatible option changes output when objective prefers it.
- [ ] **AT-06-04:** Insufficient single sailing capacity cannot split Buyer A against policy.
- [ ] **AT-06-05:** More than one booking cannot consume the same slot twice.
- [ ] **AT-06-06:** All carrier requests are one-shot; no recovery counteroffer messages emitted.


## 8. Definition of Done

**Gate:** The RecoveryGraph can produce and persist a validated OR-Tools multi-buyer proposal from actual ACP offers, with correct partial-recovery handling.

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

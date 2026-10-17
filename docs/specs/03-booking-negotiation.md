# Phase 3 Specification — Immediate Booking & Bounded Negotiation

**Project:** Autonomous Freight Recovery Exchange · LA Hacks AI 2026  
**Status:** NOT STARTED (unless `progress.md` has verified evidence)  
**Active only after:** Phase 2 ACP and Tiger services verified.  
**Context:** `docs/PRD.md` (reference; later user decisions supersede historical proposals)  
**Scope:** Part 1 P0 only. No Part 2 work.

## 1. Objective

Complete single-buyer discovery, commercial quoting, optional one counteroffer round and offer selection without batching or holds.

## 2. Inputs / prerequisites

Phase 2 ACP and Tiger services verified. Do not substitute mocks for prerequisite integration without labeling the test as a unit test; the gate requires the actual external boundary.

## 3. In scope

- BookingGraph handles buyer request, clarification, hard-constraint validation, request persistence, concurrent carrier quotation, comparison and offer selection.
- One buyer to many company agents; initial offer plus at most one bounded counter per carrier.
- Private carrier floor/target pricing and deterministic validation; an LLM may propose prices only when available, and must never bypass policy.
- ASI:One capability/offer card or documented fallbacks preserving the conversational workflow; cards must map to real offer IDs.
- Reject expired, over-budget, capacity-infeasible, wrong-corridor or late-arrival quotes; no wait for market rounds.

## 4. Explicitly out of scope

- OR-Tools recovery, booking commitment, test checkout, iterative recovery negotiation, batching coordinator.

## 5. Technical architecture and contracts

Extend request: `recovery_policy:{max_extra_cost_minor,max_arrival_delay_hours,auto_approve_within_limits}` is optional until Phase 7; never default to blanket auto-authority. Offer metadata: `valid_until`, `carrier_agent_address`, `sailing_id`, `quantity`, `total_price_minor`, `currency`, `departure_at`, `arrival_at`, `allow_split_supported`, `source_kind`. Negotiation messages: `COUNTER_OFFER {negotiation_id,offer_id,round:1,proposed_total_price_minor}` and `ACCEPT_COUNTER|REJECT_COUNTER|REVISED_OFFER`. If the carrier counter is under its internal floor, reject deterministically.

Buyer choice emits `OFFER_SELECTED` without implying a hold; remember the selection using a durable `request_id/offer_id`. Provide error `NO_FEASIBLE_OFFERS` and `OFFER_EXPIRED` with a re-quote path.

## 6. Ordered work packages

1. [ ] Build request validation and explicit clarification workflow for missing dimensions.
2. [ ] Implement BookingGraph nodes through offer selection; persist state/checkpoints if compatible, otherwise durable state keyed by request ID.
3. [ ] Implement private carrier price-policy enforcement and one-counter exchange.
4. [ ] Provide ranked comparison (total simulated price, valid arrival, stable tie-breaker) without overriding buyer choice.
5. [ ] Add Interactive Card experiment plus compatible text fallback, tested in ASI:One.
6. [ ] Persist offer histories and tests for no more than one counter per carrier.


## 7. Acceptance tests (must actually execute)

- [ ] **AT-03-01:** Single request returns immediately when a feasible offer exists; there is no timer/batch wait.
- [ ] **AT-03-02:** User can select one actual, unexpired offer ID visible in ASI:One.
- [ ] **AT-03-03:** Private floor cannot be breached even if LLM asks; counters cannot be repeated.
- [ ] **AT-03-04:** Unfeasible and expired offers never appear as bookable.
- [ ] **AT-03-05:** Wrong/unparseable user input results in clarification, not invented requirements.
- [ ] **AT-03-06:** Carrier disconnect still produces partial offers or clear unavailable response.


## 8. Definition of Done

**Gate:** One buyer can request freight and select one policy-valid, negotiated, expiring carrier offer from ASI:One; selection is persisted but not called booked.

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

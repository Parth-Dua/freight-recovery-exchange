# Phase 4 Specification — Atomic Holds, Stripe Test Checkout & Booking Commit

**Project:** Autonomous Freight Recovery Exchange · LA Hacks AI 2026  
**Status:** NOT STARTED (unless `progress.md` has verified evidence)  
**Active only after:** Phase 3 selection and Tiger transaction services verified.  
**Context:** `docs/PRD.md` (reference; later user decisions supersede historical proposals)  
**Scope:** Part 1 P0 only. No Part 2 work.

## 1. Objective

Turn selected simulated freight offers into authoritative, non-double-booked commitments after Stripe test payment verification.

## 2. Inputs / prerequisites

Phase 3 selection and Tiger transaction services verified. Do not substitute mocks for prerequisite integration without labeling the test as a unit test; the gate requires the actual external boundary.

## 3. In scope

- Carrier-owned, short-lived `REQUEST_HOLD`, `RELEASE_HOLD`, `CONFIRM_BOOKING` actions with authoritative transactional inventory.
- Stripe **test mode only** via `sk_test_...` Checkout Session and official test card; test transactions move no actual money.
- Persist booking/payment lifecycle and verify Checkout Session status server-side/webhook; never trust browser redirect or buyer chat claim alone.
- Record fulfillment only after verified test payment and carrier confirmation, with typed reconciliation/pending states for partial failure.
- Test payment rail / product decision: demo fee or simulated fee represented clearly; freight price remains a simulated quote, not settled ocean freight.
- Investigate Fetch Agent Payment Protocol bonus separately and record VERIFIED/NOT VERIFIED—not assumed achieved by Stripe checkout.

## 4. Explicitly out of scope

- Live Stripe keys or real charges, refunds, escrow, card storage, full accounting, freight settlement, real carrier contracts.

## 5. Technical architecture and contracts

Hold `REQUEST_HOLD {offer_id,request_id,quantity,idempotency_key,requested_ttl_seconds}` → `HOLD_CONFIRMED {hold_id,expires_at,sailing_id,quantity}` or `HOLD_REJECTED {code}`. `CONFIRM_BOOKING {hold_id,payment_reference,idempotency_key}` → `BOOKING_CONFIRMED {booking_id,confirmed_at}`. Carrier enforces `free = capacity - committed - active_holds` under locks or conditional atomic SQL; idempotency unique constraints protect retries.

Stripe adapter `create_test_checkout(booking_intent_id,amount_minor,currency)` returns hosted checkout URL and Stripe session ID; `verify_checkout(session_id)` must match exact amount, currency, intent, status and environment. Checkout redirect is not proof. Distinguish `PAYMENT_PENDING|PAYMENT_VERIFIED_TEST|PAYMENT_FAILED|PAYMENT_RECONCILIATION_REQUIRED`. Allow one buyer-visible `BOOKING_PENDING` state when payment succeeds but inventory confirmation fails; never silently issue another charge.

**Known spec conflict:** original PRD §15 proposed actual Fetch Payment Protocol payment; user's later decision is Stripe test API, so use test-only Stripe in Phase 4. Fetch bonus fulfillment needs separately verified protocol messages; do not claim Stripe checkout constitutes that protocol.

## 6. Ordered work packages

1. [ ] Implement optimistic/row-locked capacity hold transaction, TTL expiry and unique idempotency keys.
2. [ ] Implement carrier confirmation and booking state transitions in Tiger.
3. [ ] Create Stripe test Checkout session from backend; return its URL in ASI:One and validate webhook signature or retrieve verified session status.
4. [ ] Connect BookingGraph pause/resume/redirect behavior; expire/release holds safely.
5. [ ] Persist real test payment metadata and separate simulated freight prices from checkout price.
6. [ ] Document whether official Fetch payment protocol can wrap/test Stripe; if unsupported, mark bonus unresolved and preserve working checkout.


## 7. Acceptance tests (must actually execute)

- [ ] **AT-04-01:** Two concurrent holds cannot oversell one slot.
- [ ] **AT-04-02:** Repeated hold/confirm/Stripe webhook events are idempotent.
- [ ] **AT-04-03:** Forged payment success message or success redirect does not commit booking.
- [ ] **AT-04-04:** Expired hold cannot confirm; test payment succeeds but expired hold produces truthful reconciliation state.
- [ ] **AT-04-05:** Stripe official test success card works; simulated failure card does not book.
- [ ] **AT-04-06:** ASI:One shows committed receipt only after Tiger has a confirmed booking and verified test payment.


## 8. Definition of Done

**Gate:** Repeatable ASI:One offer selection → hold → official Stripe test checkout → server verification → carrier confirmation → durable booking; zero overselling.

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

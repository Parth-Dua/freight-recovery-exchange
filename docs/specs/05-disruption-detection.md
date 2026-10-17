# Phase 5 Specification — Simulated Maritime Disruption & Affected Bookings

**Project:** Autonomous Freight Recovery Exchange · LA Hacks AI 2026  
**Status:** NOT STARTED (unless `progress.md` has verified evidence)  
**Active only after:** Phase 4 committed bookings exist in Tiger.  
**Context:** `docs/PRD.md` (reference; later user decisions supersede historical proposals)  
**Scope:** Part 1 P0 only. No Part 2 work.

## 1. Objective

Withdraw a specific carrier sailing based on an explicitly simulated, real-data-compatible risk event, and mark only its committed bookings disrupted.

## 2. Inputs / prerequisites

Phase 4 committed bookings exist in Tiger. Do not substitute mocks for prerequisite integration without labeling the test as a unit test; the gate requires the actual external boundary.

## 3. In scope

- Create canonical maritime risk schema compatible with later UKMTO/MARAD adapters but use fixture input only.
- Associate risk geography with route corridor using transparent heuristics; advisory alone does NOT automatically revoke sailing capacity.
- Carrier-authorized `WITHDRAW_SAILING` operation (CLI/admin control is acceptable minimum; tiny carrier console if already feasible).
- Transactional sailing withdrawal + affected booking identification + durable event emission with deduplication.
- Preseed Buyer B and unrelated Buyer C via same domain booking service/commitment invariants; Buyer A must be demonstrably booked live in Phase 4.

## 4. Explicitly out of scope

- Live external advisories, AIS, map UI, Sentry, automatic withdrawal from a risk headline, refund processing.

## 5. Technical architecture and contracts

Canonical `risk_event {risk_event_id, source_kind:"simulated",source_provider:"fixture",source_reference,source_url:null,published_at,ingested_at,category,severity,description,region_code,incident_point:null,geometry:null,affected_corridors:[...],raw_payload_ref:null}`. In Part 2 the same fields accept real source URL and coordinates when supplied; do not reinterpret incident point as a legally restricted polygon.

`WITHDRAW_SAILING {sailing_id,carrier_id,reason_risk_event_id,actor_id,authorization_ref,idempotency_key}`. Only sailing owner or authorized operator may invoke; reject cross-carrier withdrawal. Record `SAILING_WITHDRAWN` once, transition each current committed booking on that sailing to `DISRUPTED`, and leave unrelated commitments intact. Do not cancel a settled Stripe test payment or claim a refund.

## 6. Ordered work packages

1. [ ] Define risk event and corridor-match schema with test fixture and `source_kind`.
2. [ ] Create backend risk simulation ingestion route/CLI and list impacted sailings.
3. [ ] Implement carrier-owned explicit withdrawal authorization and idempotent transaction.
4. [ ] Produce durable `SAILING_WITHDRAWN` and `BOOKING_DISRUPTED` business events.
5. [ ] Seed independently committed Buyer B and unaffected Buyer C via legitimate service pathways.
6. [ ] Expose ASI:One disruption status for Buyer A and provide inspection command for affected booking IDs.


## 7. Acceptance tests (must actually execute)

- [ ] **AT-05-01:** Risk event alone never automatically withdraws a sailing.
- [ ] **AT-05-02:** Withdrawing Atlas sailing affects Buyers A and B, not Buyer C.
- [ ] **AT-05-03:** Duplicate withdrawal does not duplicate events or re-disrupt replacement bookings.
- [ ] **AT-05-04:** Untrusted actor cannot withdraw another carrier sailing.
- [ ] **AT-05-05:** Matching logic supports region-only data and optional point geometry without assuming a regulatory closure zone.
- [ ] **AT-05-06:** Events use current timestamps, immutable IDs, and `simulated` provenance.


## 8. Definition of Done

**Gate:** Single authorized withdrawal produces ≥2 truly disrupted committed bookings and preserves unrelated Buyer C; queryable in Tiger.

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

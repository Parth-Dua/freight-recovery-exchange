# Phase 2 Specification — Carrier Agents, ACP Contracts & Tiger Foundations

**Project:** Autonomous Freight Recovery Exchange · LA Hacks AI 2026  
**Status:** NOT STARTED (unless `progress.md` has verified evidence)  
**Active only after:** Phase 1 verified from ASI:One, with a stable Exchange Agent entry point.  
**Context:** `docs/PRD.md` (reference; later user decisions supersede historical proposals)  
**Scope:** Part 1 P0 only. No Part 2 work.

## 1. Objective

Exchange gateway calls at least two independently identified company Carrier Agents through ACP and stores accurate quotes/inventory in Tiger PostgreSQL.

## 2. Inputs / prerequisites

Phase 1 verified from ASI:One, with a stable Exchange Agent entry point. Do not substitute mocks for prerequisite integration without labeling the test as a unit test; the gate requires the actual external boundary.

## 3. In scope

- Use one reusable company-agent implementation with distinct seed/identity, configuration, sailing inventory and commercial policy per company; a third instance is desirable for the eventual recovery demo.
- Create Tiger schema/migrations for carriers, sailings, slots, cargo requests, offers, negotiations, holds, bookings, payment records, disruptions, recovery runs and append-only market_events; only populate fields required now.
- Version canonical request/offer/error envelope and persist request/response correlation IDs.
- Carrier independently selects a currently eligible sailing and policy-valid indicative quote.
- Seed fictional Atlas/Beacon/Cove carrier and 20GP container inventory; distinguish seeded simulation from real data.

## 4. Explicitly out of scope

- Real shipping datasets, actual carrier integrations, optimization, booking settlement, Stripe, complex negotiation, React analytics.

## 5. Technical architecture and contracts

Minimum ACP application payloads (not assumed built-in ACP verbs): `REQUEST_OFFER {schema_version, request_id, negotiation_id, cargo_request:{origin_port,destination_port,container_type,quantity,earliest_departure,latest_arrival,max_total_price_minor,currency,allow_split}, timestamp}` and `PROPOSE_OFFER {offer_id, carrier_id, sailing_id,quantity,total_price_minor,currency,expires_at,terms,request_id}`. Define typed `REJECT_OFFER` and `AGENT_ERROR` with machine-readable reason. Ports use stable UN/LOCODE-style keys where feasible; fixture values may be fictional/simulated and must be labeled.

Tiger: add stable PK/FK constraints, unique agent identity and sailing inventory keys, `TIMESTAMPTZ`, and `market_events(ts,event_id,event_type,entity_type,entity_id,correlation_id,source_kind,payload JSONB)`. Make `market_events` a Tiger hypertable only after checking the installed extension/API and partitioning requirements. Model `source_kind` as `simulated|external` so Part 2 can ingest live inputs without changing contracts.

## 6. Ordered work packages

1. [ ] Define domain models and migration scripts; run migration from empty Tiger DB.
2. [ ] Build configurable company-agent factory; deploy/register Atlas and Beacon, optionally Cove.
3. [ ] Implement ACP REQUEST_OFFER/PROPOSE_OFFER with validation and independent policies.
4. [ ] Create deterministic seeding script (idempotent, no fake live feeds).
5. [ ] Wire Exchange adapter to parallel carrier invocations with timeouts and persist offers/events.
6. [ ] Write smoke tests for Agentverse addresses and test real ACP calls from the Exchange Agent.


## 7. Acceptance tests (must actually execute)

- [ ] **AT-02-01:** Cold DB migrate + seed twice: no duplicate records.
- [ ] **AT-02-02:** Agent A and B return offers with distinct verified identities and sailing ownership.
- [ ] **AT-02-03:** Invalid cargo constraint, unknown carrier, timeouts, stale offers and malformed ACP payloads return typed errors.
- [ ] **AT-02-04:** SELECT in Tiger proves request and offers are persisted with correlation IDs.
- [ ] **AT-02-05:** The Exchange Agent invocation is visible from ASI:One and returns actual carrier results, not preset text.


## 8. Definition of Done

**Gate:** ASI:One → Exchange Agent → ≥2 separately addressable Carrier Agents over ACP → persisted distinct offers in Tiger is observed.

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

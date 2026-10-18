# Phase 04 — Two-sided buyer/carrier negotiation for normal bookings

**Priority:** after native Cards + verified Payment Protocol  
**Owner:** Parth (all work in this phase)  
**Depends on:** Phases 1–3 green and existing signed carrier request/reply channel  
**Gate:** one buyer creates parallel sessions with at least 2 eligible independently addressed carrier companies, receives private-policy initial offers, makes ≤2 counters per company, displays final offers, selects, and gets a confirmed booking. All negotiation stages are traceable and all price floors/private limits stay private.

## 1. Source-of-truth design

Implement the two-sided-market document **as written** for normal times: pairing is deterministic; sessions are first-class durable business records; carrier pricing depends on vessel utilization and private policy; buyer countermoves use competitive rival evidence and own hidden priorities/limits; allow one optional second counter; no carrier-to-carrier auctions, no batching, no speculative buyer LLM prices. Each carrier has one uAgent identity for its entire company and may offer any appropriate sailing it owns.

**Identity precision:** v1 is **one LangGraph codebase with a separate isolated buyer state per buyer**, not newly deployed uAgents for each individual buyer. Buyer-side counter decisions have durable buyer-scoped provenance; messages sent over the existing transport use the already proven platform/agent signing mechanism. Do not claim independent cryptographic buyer-agent identities unless they are actually instantiated. Existing carrier replies are signed by their real carrier uAgent addresses.

## 2. Changes to contracts (additive)

### Buyer requirements

```json
{
  "request_id":"req_x001",
  "buyer_id":"server-owned-buyer-id",
  "origin_port":"AEJEA",
  "destination_port":"INBOM",
  "quantity":2,
  "container_type":"20GP",
  "earliest_departure":"2026-10-20T00:00:00Z",
  "latest_arrival":"2026-10-23T23:59:00Z",
  "allow_split":false,
  "cargo_class":"general",
  "priority":"balanced",
  "must_deliver":false,
  "max_total_price_minor":560000,
  "recovery_allowance_minor":25000
}
```

**Note:** this is a **private full state** inside the buyer service, not the carrier message. `max_total_price_minor`, `must_deliver`, `recovery_allowance_minor` and private urgency must be dropped from every carrier-facing envelope. Buyer/Exchange checks use server-side data. Current schema/field names may differ; provide versioned serializers rather than destructive renames.

### Carrier private policy JSON (illustrative seeded values)

```json
{
  "business_goal":"fill_ship",
  "target_price_per_unit":"2600.00",
  "min_price_per_unit":"2300.00",
  "utilization_premium_pct":"10",
  "concession_empty_pct":"12",
  "concession_full_pct":"2",
  "disruption_surge_pct":"15",
  "cargo_surcharge_pct":{"general":"0","high_value":"8","hazardous":"reject"},
  "bulk_discount_pct":"5",
  "bulk_discount_min_units":3,
  "selection":"earliest_arrival",
  "offer_ttl_seconds":600,
  "max_hold_seconds":3600
}
```

**Migration:** keep existing `carrier_policies.policy` JSON storage; loader fills defaults for missing keys, aliases old `concession_pct` to an explicitly defined compatibility rule (e.g., both empty/full), handles existing `minimum_price_per_unit`/`target_price_per_unit` keys, verifies `floor ≤ target` and bounded percentages. Never silently ignore a newly edited policy field and still pretend it applies. Carrier uAgent **reloads own policy on every quote** so later UI edits apply without restart.

### Negotiation wire envelope

```json
{
  "schema_version":"1.1",
  "message_type":"COUNTER_OFFER",
  "message_id":"uuid",
  "correlation_id":"session-id",
  "session_id":"session-id",
  "request_id":"req_x001",
  "buyer_agent_key":"internal-isolated-buyer-key",
  "carrier_id":"carrier_b",
  "carrier_agent_address":"agent1q...",
  "offer_id":"offer_b104_01",
  "round":1,
  "created_at":"2026-10-17T19:00:00Z",
  "expires_at":"2026-10-17T19:02:00Z",
  "payload":{"proposed_total_price_minor":510000,"public_rationale":"Comparable verified rival offer"}
}
```

Use current signed typed message transport; add session, round, correlation and schema version without replacing old ACP channel. Never accept `carrier_agent_address` solely because it is in the payload—validate transport sender against registered carrier record.

## 3. Session persistence

**New table:** `negotiation_sessions(session_id PRIMARY KEY, buyer_agent_key, carrier_id, carrier_agent_address, request_id NULLABLE, booking_id NULLABLE, recovery_id NULLABLE, kind ENUM(booking,recovery), round, rounds_max DEFAULT 2, status ENUM(OPEN,ACCEPTED,REJECTED,EXHAUSTED,EXPIRED), opened_at, expires_at, closed_at, final_offer_id, reason)`.

**New/updated messages:** `negotiation_messages(message_id, session_id, sender_role, sender_address, round, message_type, public_terms_json, ts)` and/or existing `agent_messages` enrichment. Unique `(sender_address,message_id)` for dedup; index `(session_id, round)`, `(recovery_id,status)`, `(request_id,carrier_id)`.

Session can only be advanced transactionally from current round/status. Store final offers separately in existing `offers`; expiration checked on counter, display, selection, hold. Persist market events with session correlation: `NEGOTIATION_OPENED`, `OFFER_SENT`, `COUNTER_SENT`, `OFFER_ACCEPTED`, `OFFER_REJECTED`, `NEGOTIATION_CLOSED`; don't log buyer private state or carrier policy.

## 4. Pairing policy

Implement pure `pair_candidates(request, current_inventory) -> list[Pair]` with deterministic sorted output and a reason. Preconditions: same corridor or permitted route mapping, carrier active, sailing ACTIVE, correct 20GP type, cargo class accepted, departure after buyer cargo-ready, arrival by latest deadline, sufficient **real** available slots for booking (when indivisible), compatible split policy. No hidden budget to carrier, no guessing route availability by language model. Prefer all eligible carrier companies, at most five. Use Tiger read to filter candidates and recheck inventory at quote/hold time.

At kickoff create session and send to carrier only **public cargo requirements + session context**. Buyer agent side is an internal callable actor; its private data stays in its state namespace. Carrier receives buyer counter *price* and a limited public competitive rationale, never raw willingness-to-pay or must-deliver. Multiple carrier sessions run concurrently (async tasks/worker jobs); one slow carrier does not block forever.

### Pure API shapes

```python
pair_candidates(request_public, inventory) -> list[Pair]
open_negotiation(buyer_agent_key, carrier_agent_address, request_id, *, rounds_max, expires_at) -> Session
close_negotiation(session_id, outcome, final_offer_id=None) -> Session
buyer_counter(own_offer, verified_rival_offers, private_buyer_state, round_number) -> Counter | NoCounter
carrier_quote(carrier_policy, sailing_inventory, public_request, kind) -> Offer | Reject
carrier_reply(carrier_policy, previous_offer, buyer_counter, round_number) -> Offer | Accept | Reject
```

The `open_*`/`close_*` methods persist durable session state and emit events; `buyer_counter`/pricing are deterministic pure functions. No LLM computes unguarded commercial prices.

## 5. Carrier opening price and concession rules

For each viable sailing, compute **utilization from Tiger slots** (available includes neither held nor committed nor withdrawn units):

```text
utilization = clamp(1 - sellable_available_slots / total_capacity_slots, 0, 1)
opening_unit = target_unit
             * (1 + premium_fraction * utilization)
             * (1 + surge_fraction if recovery else 1)
             * (1 + surcharge_fraction)
             * (1 - bulk_discount_fraction if q >= bulk_threshold else 1)
concession_fraction = empty_fraction + (full_fraction - empty_fraction) * utilization
minimum_counter_unit = max(floor_unit, opening_unit * (1 - concession_fraction))
opening_unit = max(opening_unit, floor_unit)  # never violate floor even after discount
```

- Values are money decimals or integer cents; percentages converted from percentage points to fractions, rounded at a documented currency step.
- For quantity `q`, `total_price = unit_price * q` using current terms; do not compare a 1-container quote with a 3-container price without normalization.
- `cargo_surcharge_pct[class] == reject` means **no offer** for that class.
- At each valid buyer counter, accept if at/above own least-permitted current price; otherwise counter **toward** it within allowed concession, or reject; never publish less than `min_price_per_unit` or below `minimum_counter_unit` policy.
- Business goal presets are known deterministic parameter bundles, not model suggestions. Preserve carrier ability to protect margins at high occupancy.
- Include only carrier-sanctioned rationales, e.g. “Space is limited on this departure” rather than leaking its exact floor.

## 6. Buyer strategy + rounds

Buyer private policy computes an adjusted offer cost based on price and **arrival penalty**: `adjusted_cost = price + value_of_a_day(priority)*days_later_than_fastest_comparable_valid_offer`. Prioritize hard eligibility before comparing. `value_of_a_day(cheapest)=0`, `balanced` intermediate, `fastest` higher (seed deterministic actual constants). `urgency` from time left and buyer must-deliver; as urgency increases, counter aggression **shrinks**.

Suggested codable rule preserving §9 source intent:

```text
comparable_rivals = current, in-lane, compatible, unexpired other-carrier offers
leverage = min(adjusted_cost(rival) for rival in comparable_rivals)
           if rivals exist else adjusted_cost(own_offer)
step = clamp(base_step * (1 - normalized_urgency), minimum_step, maximum_step)
proposed = min(own_offer.price, leverage) - step
proposed = min(proposed, buyer.private_max_total_price) if cap exists
if invalid/unhelpful or below policy-valid positive minimum: return NO_COUNTER
else return COUNTER(proposed, public_rival_evidence_without_budget)
```

This is a **concrete implementation refinement**; exact `base_step`/priority penalty constants should live in seeded config and tests. A counter is a request, not an assumption that carrier agrees. Carrier can respond with a valid counter higher than buyer's proposal but ≤ its current valid price, or reject. Buyer must never accept final offer above private hard max.

**Round lifecycle:**
1. Parallel `REQUEST_OFFER` to all paired companies.
2. Validate/snapshot opening offers; calculate rival leverage only from already-known valid offers.
3. Buyer counter #1 → carrier accepts/meets partway/rejects.
4. Optional counter #2 only if prior counter produced an intermediate offer and further bargaining could change buyer rank/feasibility. Carrier accepts/meets partway/rejects; close session.
5. Persist finalized offers, select/rank for ASI:One carousel. Ranking respects buyer priority and hard requirements; buyer authorizes selection. Old BookingGraph hold→payment→confirm resumes normally.

**Do not create a second open negotiation for every textual follow-up.** Session state/IDs survive asynchronous messages and process restarts. Offers with expired TTL cannot be selected. Consistent message deadlines required.

## 7. Test-driven cases

| Input | Expected |
|---|---|
| Two eligible carriers same route | Two separate sessions, both real signed replies |
| Wrong lane, late arrival, unsupported class | Pair excluded before session created |
| 0%, 50%, 95% utilized sailings | Opening prices/concession adjust in intended direction |
| Ship half empty vs nearly full | Larger concession for empty ship, smaller when full |
| Target below floor | Config rejected or normalized safely, not underfloor quote |
| Bulk 3 containers, high-value cargo | Discounts/surcharge applied once in documented order |
| No rival | Buyer produces deterministic bounded counter or NO_COUNTER |
| Rival offer expired / incompatible quantity | Rival excluded as leverage |
| Priority cheapest/balanced/fastest | Arrival penalty affects ranking as documented |
| Buyer must-deliver true | Alters private urgency, not public carrier message |
| Carrier two-round negotiation | Never a third counter, all rounds associated with same session |
| Reply from wrong signed agent ID or session | Rejected; no final offer pollution |
| Timeout / late / duplicate carrier packet | Session expiry enforced; replay cannot change closed session |
| Payment and booking after selected negotiated offer | Hold remains carrier-owned; actual selected final price persisted |
| Modify carrier policy in Tiger between quotes | Next quote reflects current policy without restart |
| Inspect `market_events` and external logs | No raw buyer max/floor, only public final offer terms |

## 8. Commit slices, release strategy, gate

1. Add migrations, validators and private buyer-state partition; create stable regression fixtures.
2. Implement private carrier-policy loader + price/concession pure functions; test in isolated carrier environment.
3. Implement private buyer strategy pure function; document exact seeded numeric knobs.
4. Implement session `pair/open/guard/close`, durable DB state, agent messages and event logging.
5. Integrate sessions with current BookingGraph (initial offers → rounds → final-offer card) behind feature flag `TWO_SIDED_NEGOTIATION_ENABLED`.
6. Demonstrate live two-carrier chat-driven negotiation followed by actual carrier hold, verified Phase-3 Fetch fee, and committed booking; keep old path only as rollback during verification.

**Exit:** signed carrier responses in real concurrent sessions, max-two-round logs, policy-differentiated prices, final offers in native ASI carousel, verified paid & held booking, unchanged unaffected slot semantics. Confirm buyer hidden limits and carrier floors are absent from public trace.

**Not in Phase 4:** timed recovery sessions, must-deliver solver stage, FCFS baseline, full carrier dashboard or risk-source ingestion (Phase 5 or later).

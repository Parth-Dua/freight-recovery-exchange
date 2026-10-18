# Phase 02 — Native Interactive Cards for the buyer experience

**Priority:** immediately after Phase 1 · **Owner:** Parth (all work in this phase)  
**Dependencies:** proven native form delivery and action return in Phase 1  
**Gate:** the buyer completes the complete *interactive* booking-selection journey and sees disruption/approval/result cards in the same established ASI:One session. Actual Fetch payment verification is the next phase; do not represent legacy checkout as native payment success.

## 1. Objective and unchangeable requirements

A buyer says “I have cargo to ship” → **setup form** → system validates fields and negotiates → **offer carousel** → **booking review** → capacity hold/payment handoff → **booking detail/receipt**. When a sailing is withdrawn, the buyer gets an **affected-shipment notice**, **replacement approval card** when required, and **recovery outcome card**. The gateway serializes ASI:One wire messages; backend makes all state decisions. Card actions come back as `TextContent` and are untrusted until validated.

Native schemas: `form`, `carousel`, `review`, `detail`. Implement custom cards only if a critical field cannot fit. Use official Card Playground to validate payloads: https://innovationlab.fetch.ai/resources/docs/interactive-cards/asi-interactive-cards .

## 2. Buyer input contract (from two-sided design)

| Field | Type for ASI card | Visibility | Validation |
|---|---|---|---|
| `origin_port` | required `select` | public | Known active lane source |
| `destination_port` | required `select` | public | Known active lane destination; not equal origin |
| `quantity` | required `number` | public | Integer ≥1 and ≤ seeded demo maximum |
| `container_type` | fixed `20GP` (not a field) | public | Server sets fixed demo value |
| `latest_arrival` | required `select` of eligible scheduled arrivals, or validated ISO text if needed | public | Date after cargo available/departure; deadline hard |
| `earliest_departure` | optional `select` | public | ≤ latest arrival and coherent with sailing |
| `allow_split` | checkbox | public | Boolean, defaults to false |
| `cargo_class` | `select` | public | `general`, `high_value`, `hazardous` only when accepted by policies; default `general` |
| `max_total_price` | optional `number` | buyer agent private | USD ≥0; never forwarded to carrier |
| `priority` | `select` | buyer agent private | `cheapest`, `balanced`, `fastest`, default `balanced` |
| `must_deliver` | checkbox | Exchange only | Buyer-declared priority; never sent to carrier |
| `recovery_allowance` | `number` | exchange/authority | Additional **simulated USD** allowed without new approval, ≥0 |
| `urgency` | server derived | buyer agent private | From deadline and priority; never asked |
| `buyer_id`, actual chat session | gateway derived | backend | Never submitted by card |

**Small UX simplification without removing semantics:** Render required shipment fields first and group private preferences in the same card where practical, or prefill sane demo defaults and provide a separate `form` for preferences (persist before negotiation). All source-document fields must be representable. `select` options use **actual seeded corridor/sailing data**. No fake 2026-10-23 date if the fixture has no such service.

## 3. Card builders and exact actions

Each builder returns `{narration, card_kind, payload, fallback_text}`. The gateway converts that into official `TextContent + MetadataContent`. Include `card_instance_id` in the `selection` of actionable CTAs, not as unsupported top-level schema keys.

| Function | Card | Input from backend | CTA selection |
|---|---|---|---|
| `cargo_setup_form(options, defaults, card_instance_id)` | form | corridor/port/date options and allowed cargo classes | `{action:"submit_cargo_request", card_instance_id, form_revision}` + captured field values |
| `final_offers_carousel(request_id, final_offers, card_instance_id)` | carousel | offer IDs, carrier, sailing, dates, total simulated USD, expiry, trace excerpts | `{action:"select_offer", request_id, offer_id, card_instance_id}` |
| `booking_review(booking_intent_id, terms, card_instance_id)` | review | exact selected offer; simulated freight; actual configured FET-testnet coordination fee; expiry | approve `{action:"confirm_booking", booking_intent_id, card_instance_id}`, reject `{action:"cancel_booking", ...}` |
| `booking_receipt(booking_id, ...)` | detail | confirmed booking, payment status, original offer | read-only; optional action with fresh ID if needed |
| `disruption_notice(booking_id, recovery_id, ...)` | detail/text | withdrawn sailing, initial status and source | optional `view_recovery` |
| `recovery_approval(approval_id, assignment, ...)` | review | original and replacement terms, extra simulated USD/delay, expiry | `{action:"approve_recovery", approval_id, ...}` / `decline_recovery` |
| `recovery_result(booking_id, old_id, new_id, ...)` | detail/text | `CONFIRMED`, `PENDING_APPROVAL`, `UNRECOVERED` etc. | none or read-only |

**Display:** explicitly label “Simulated freight: $X USD” and “Demo booking-service payment: Y testnet FET”. Do not display legacy USD $25 charge as *actually payable* at the same time as FET. Show deadlines and offer validity; prevent users from mistaking a proposed replacement for confirmed capacity.

## 4. Exact ASI form example (expand from Phase 1)

```json
{
  "title": "Set up your cargo shipment",
  "fields": [
    {"name":"origin_port","kind":"select","label":"Origin","required":true,
     "options":[{"value":"AEJEA","label":"Jebel Ali (AEJEA)"}]},
    {"name":"destination_port","kind":"select","label":"Destination","required":true,
     "options":[{"value":"INBOM","label":"Mumbai (INBOM)"}]},
    {"name":"quantity","kind":"number","label":"Number of 20GP containers","required":true},
    {"name":"latest_arrival","kind":"select","label":"Arrive no later than","required":true,
     "options":[{"value":"2026-10-23T23:59:00Z","label":"Oct 23"}]},
    {"name":"allow_split","kind":"checkbox","label":"Allow split across sailings"},
    {"name":"cargo_class","kind":"select","label":"Cargo class",
     "options":[{"value":"general","label":"General"},{"value":"high_value","label":"High value"}]},
    {"name":"max_total_price","kind":"number","label":"Max simulated freight price (USD)"},
    {"name":"priority","kind":"select","label":"Negotiation priority",
     "options":[{"value":"cheapest","label":"Cheapest"},{"value":"balanced","label":"Balanced"},{"value":"fastest","label":"Fastest"}]},
    {"name":"must_deliver","kind":"checkbox","label":"Mark as must deliver"},
    {"name":"recovery_allowance","kind":"number","label":"Auto-rebook extra cost limit (simulated USD)"}
  ],
  "submit_cta":{"label":"Find shipping offers","selection":{"action":"submit_cargo_request","card_instance_id":"generated-id"}}
}
```

Example dates are synthetic: production builder replaces select options with current active sailings and validated permitted deadlines; add optional `earliest_departure` to actual builder. All top-level keys above are official `form` schema fields; CTA data is a documented `selection` payload.

## 5. Event normalization and server-side action validation

**Procedure (never trust `action` alone):**

1. Derive sender address, gateway agent, actual chat session from authenticated transport.
2. For card `TextContent`, parse full JSON if valid; otherwise use the existing text intent parser to recover action + stable IDs, and request clarification if ambiguous. Do not assume planner narration has fully reliable action IDs.
3. Map action to **current expected pending state** for this buyer. Resolve `card_instance_id` and version where available; recover only from a single current pending action in planner-mediated path.
4. Server re-reads all offer/booking/approval records and verifies user ownership, expiry, state, sailing slots, latest deadline, required approval and allowed price. Never trust submitted totals or `buyer_id`.
5. Execute one idempotent business transition keyed by `buyer + action + resource id + revision`. Replayed click returns current existing state; cannot re-hold slots, re-charge, re-supersede or re-approve expired assignments.
6. Persist resulting event, enqueue next card/notice; return text only on malformed/unresolvable interaction.

**Critical rule:** a selected offer is not a hold; a booking-review confirm creates an intent, requests a hold and triggers Phase-3 payment protocol, **not** a committed booking. An approved recovery card can only authorize the exact pending replacement terms; if terms changed, issue a new approval ID/card.

## 6. Empty/error states

- Form invalid: re-display with constrained options + short error, do not create `cargo_requests` duplicates.
- No feasible carriers: textual unavailable result (optional detail card), not empty carousel.
- Quote expires: explain and refresh eligible offers, do not let stale CTA proceed.
- Capacity hold rejected: say capacity changed; retry bounded flow/requote, not paid.
- Payment not yet integrated in Phase 2: flag `PAYMENT_MODE=legacy-dev` and use only for developer regression; displayed receipt must say **test/development** and never imply Fetch protocol completed.
- Recovery awaiting consent: highlight amount/delay delta and 30-minute expiry; push to exact established chat.
- Card rendering failure: preserve equivalent text fallback, log wire validation error; **gate fails until real card renders**.

## 7. Tests (table-driven and live)

| Scenario | Expected |
|---|---|
| Submit valid setup card | One request, public/private properties split correctly |
| Submit invalid corridor/date/negative quantity | Rejected before any carrier messaging |
| Select 2 different offer IDs from stale card | Only one current valid selection |
| Submit same form twice | One request or intentionally separate user-confirmed request, never silent duplicates |
| Press Confirm then Cancel on old review | Deterministic state check; no contradictory payment |
| Unexpired 2-carrier carousel | Real IDs, totals, dates, provenance, carrier names |
| Planner says “second offer” without ID | Ask which offer, don't pick arbitrary one |
| Direct selection JSON | Correct action IDs and buyer authorization |
| Approve assignment belonging to another buyer | Forbidden |
| Duplicate recovery approval CTA | Exactly one accepted decision |
| Pending replacement appears | Must say `PENDING_APPROVAL`, not `RECOVERED` |
| Card render in ASI:One | All 4 predefined card kinds visible with action paths and fallback narration |

## 8. Commit slices and exit criteria

1. Freeze public→private buyer contract and validation; build dynamic form/options.
2. Implement signed-off card builders and wire them through `CardSeam` into gateway.
3. Normalize structured/narrated selections, add revision/expiry/idempotency.
4. Route selection/review to **existing** BookingGraph; keep payment adapter seam until Phase 3.
5. Add disruption/approval/result card builders and outbox delivery into established session.
6. Record live happy-path card screens plus negative/replayed CTA tests.

**Exit gate:** One ASI:One chat has a **real form, real offers carousel, real review**, and at least one working recovery approval/result card, all backed by Tiger; raw text fallback remains usable; tests validate no booking is marked paid before verified protocol completion. Do not begin redesigning the carrier dashboard.

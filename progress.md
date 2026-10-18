# Progress — Autonomous Freight Recovery

**Active phase:** [Parth lane] 04 — Booking & Stripe test payments (not started). Phase 03 PASSED.  
**Phase status:** Phase 01 CLOSED. Phase 02 PASSED. Phase 03 PASSED (AT-03-01..06; ASI:One evidence 2026-10-17). Phase 04 NOT STARTED.  
**Updated:** 2026-10-17  
**Scope:** Part 1. Part 2 blocked until Phase 8 passes.

## Verified completed
- P1.1 skeleton, P1.2 deterministic behavior, P1.3 local server via `langgraph-av` (no tunnel/registration yet). See checkpoint below.

## Tests / evidence
- Local only: T01, T02, T03, T09 pass; T08 local restart passes. T04–T07 + T08 (via ASI:One) NOT run. Do not infer Phase 1 completion.

## Blockers / unresolved decisions
- Phase 1 must be verified via real ASI:One → Agentverse → ACP → running code roundtrip.
- Stripe **test-only** Checkout is selected; whether it satisfies Fetch's separate Agent Payment Protocol bonus remains **unverified**.
- Other open decisions (container unit, shortage priority, recovery approval policy) are listed in PRD §21; do not silently turn suggestions into product commitments.

## Changed files
- Initialized Part 1 spec pack; no runtime application code was created by this pack.
- Phase 1 code added 2026-10-17 (see checkpoint).

## Exact next action
1. (Human) Agentverse → Launch an Agent → External Agent → LangGraph; name "Freight Recovery Exchange (Demo)"; keywords per README; copy Agent URI into `.env` as `AGENT_URI=` (secret — never paste elsewhere).
2. `uv run langgraph-av dev --tunnel --no-browser` → confirm log shows "Registered with agentverse", no init errors → click *Evaluate registration* (T04).
3. Agentverse chat: "What corridors do you currently support?" → match `Ref: req_…` to backend `request_handled` log (T05).
4. ASI:One: `@<handle> What corridors do you currently support?` (T06), then fresh chat natural-language discovery (T07, record separately), then restart + repeat (T08). Record handle, request IDs, screenshots here.
5. Begin Phase 2 **only after Phase 1 gate passes**.

## Checkpoints

### 2026-10-17 11:20 — Phase 01 (P1.1–P1.3)
- Done: uv project (Python 3.12) pinned via `uv.lock`: agentverse-sdk[langgraph] 0.2.1, langgraph 1.2.16, langgraph-cli 0.4.36, langgraph-api 0.9.1, uagents-core 0.4.12. Deterministic single-node `MessagesState` graph (no LLM) exported as `agent`; fixture `get_supported_corridors()` (`phase1-static-catalog`); keyword routing for capabilities / corridors / booking-declined / recovery-declined / unsupported; retry-safe error reply; JSON `request_handled` logs (request_id, route, status, duration_ms, agentverse sender/session; never message text or URI). Agentverse profile (description, README, starter prompts) passed to `agentverse_init` only when `AGENT_URI` is set.
- SDK facts verified from installed source (not guessed): SDK POSTs `{"messages":[{"role":"human","content":…}]}` to `/threads/{uuid(session)}/runs/wait`, assistant_id `agent`; reply = AI/tool messages after last human; registration URL comes from `LANGGRAPH_API_URL` (set by `--tunnel`); keywords are NOT in `AgentProfile` → set in UI; mailbox mode exists (`init(..., mailbox=True)`) as fallback only.
- **Decision change:** spec §6 says `AGENT_URI` "is an identifier, not a private seed" — WRONG for SDK 0.2.1: URI = `https://<handle>:<key>@host/<name>`, identity = `Identity.from_seed(key)`. Treat as secret.
- Tests / evidence: `uv run pytest -q` → 25 passed (T01 import, T02 fixture exact, T03 booking declined, SDK `extract_ai_content` contract, threaded follow-up, log correlation without message text, node-failure path). `langgraph-av dev --no-browser --port 2024` + curl to `/threads/{id}/runs/wait` with SDK payload: 4 required prompts returned fixture replies; log request_ids matched (e.g. req_c0e72a57f2a2 → route get_supported_corridors). Restart → `/ok` and corridor reply again. T09 grep: only placeholders. `.env`, `*.log`, `.langgraph_api/` confirmed gitignored.
- Files: pyproject.toml, uv.lock, .python-version, .gitignore, .env.example, langgraph.json, src/agent.py, src/freight_exchange/{__init__,capabilities,responses,profile,observability}.py, tests/{conftest,test_capabilities,test_responses,test_agent_import}.py, README.md (runbook appended).
- Blockers / decisions: P1.4+ needs a human Agentverse login + ASI:One account. Note: module is imported twice by langgraph-av (preload + server), so `agentverse_init` runs twice — same values, believed harmless; confirm in first registered run. Project dir is not its own git repo (enclosing repo is `/Users/parthdua`); nothing committed.
- Next action: "Exact next action" step 1 above.

### 2026-10-17 12:25 — Phase 01 (P1.4 registration)
- Done: Agentverse external LangGraph agent created by user. Handle `@freight-recovery-exc`, name "Freight Recovery Exchange", address `agent1q2h7svwmef55v9pnjdvuhlnd2t9gr6mc4jyp5w0v0akz7hvkduzh6h4wp52`. AGENT_URI stored only in `.env` (mode 600, gitignored) with `?log=INFO`. NOTE: user pasted the URI (incl. key) into the AI chat — rotate key if Agentverse allows.
- Evidence: SDK log `Registered with agentverse`; public Almanac `GET agentverse.ai/v1/almanac/agents/<address>` → `status: active`, type proxy, endpoint `agentverse.ai/v2/agents/proxy/submit`, chat protocol digest `30a801ed…`. First run logged `Failed to report agent start:` (empty msg, transient timeout); replaying `set_agent_status(active=True)` succeeded in 0.43s; second restart had no error.
- Blocker found + fixed: default cloudflared QUIC tunnel dropped every ~1 min ("timeout: no recent network activity"), then died after a network change (530/000). Restarted with `TUNNEL_TRANSPORT_PROTOCOL=http2` → `protocol=http2`, `/ok` 200 ×3. Current URL https://designed-pond-hunting-stages.trycloudflare.com (changes every restart).
- Files: README.md (runbook: http2 + ?log=INFO), progress.md. `.env` local only.
- Next action: user clicks Evaluate registration (T04); Agentverse chat + ASI:One `@freight-recovery-exc What corridors do you currently support?` (T05/T06); match `Ref: req_…` to `request_handled` log; then T07 discovery, T08 restart.

### 2026-10-17 13:35 — Phase 01 (DECISION: mailbox mode)
- **Decision (user-approved 2026-10-17):** switch from `--tunnel` to SDK mailbox mode (spec §12 fallback). Reason: quick tunnels died repeatedly — QUIC dropped every ~1 min; http2 tunnel lasted ~16 min then Cloudflare deleted it ("Unauthorized: Tunnel not found"), URL changes on each restart. Tunnel path kept as fallback via `AGENTVERSE_MAILBOX=false`.
- Done: `src/agent.py` passes `mailbox=` to `agentverse_init` (env `AGENTVERSE_MAILBOX`, default true); `.env.example`, README runbook updated. Tests: 25 passed.
- Evidence: `uv run langgraph-av dev --no-browser` → `Registered with agentverse`, `Mailbox client started for agent1q2h7…wp52`, no SDK errors. Almanac → `status: active`, `type: mailbox`, endpoint `https://agentverse.ai/v2/agents/mailbox/submit`, chat protocol digest unchanged.
- Next action: user runs Evaluate registration (T04), Agentverse chat (T05), ASI:One `@freight-recovery-exc What corridors do you currently support?` (T06); match `Ref: req_…` ↔ `request_handled` log.

### 2026-10-17 13:40 — Phase 01 (first inbound ACP message)
- Evidence (backend side): mailbox delivered a chat at 20:39:36Z → SDK POST `/threads/{session}/runs/wait` → `Background run succeeded`; log `request_handled` request_id `req_3436fb8e4050`, route `get_supported_corridors`, status ok, input_chars 103, sender `agent1qwh4wz3qqgwqqrh2kt2s3np8x4760rr82vzkddjrytxhkscv007tgcefg3w`, session `1f08a074-b3e8-4110-ad84-664d440f4119`. No SDK send-reply error logged.
- Pending: user confirmation of which surface sent it (Agentverse evaluation/chat vs ASI:One) and that the visible reply shows `Ref: req_3436fb8e4050` + catalog marker → then mark T04/T05 or T06.

### 2026-10-17 14:00 — Phase 01 CLOSED (user decision)
- User confirmed (2026-10-17) the visible reply for `req_3436fb8e4050` matched: ASI:One/Agentverse → mailbox → backend → reply works. Treated as T05/T06 pass (human-verified; no screenshot stored).
- Not formally run: T04 evaluation button result, T07 natural-language discovery, T08 restart-then-chat. User explicitly chose to proceed to Phase 2; retest T07/T08 opportunistically during Phase 2 (each restart + chat counts for T08).
- Phase 2 may assume: handle `@freight-recovery-exc`, address `agent1q2h7…wp52`, mailbox mode, start with `uv run langgraph-av dev --no-browser`, inbound ACP → `/threads/{session}/runs/wait` with `MessagesState`, run metadata includes `agentverse_sender`/`agentverse_session`.

### 2026-10-17 14:40 — Phase 02 (decisions + contracts)
- Decisions: `docs/decisions/0002-carrier-transport.md` (user chose local uAgents + sync request/reply + carrier-signed replies), `0003-demo-corridor.md` (AEJEA→INBOM 20GP, catalog from Tiger), `0001` (mailbox, retro-recorded). Tiger: user is creating a Tiger Cloud service and will put `TIGER_DATABASE_URL` in `.env` (not chat).
- Finding: `uagents` (via cosmpy) needs protobuf 5, `langgraph-api` 0.9.1 needs protobuf ≥6.32 → cannot share env. `uv add uagents` silently downgraded langgraph-api to 0.4.28; reverted and pinned `langgraph-api==0.9.1`. Carriers will get their own uv project `carriers/`. `uv pip check` clean; 25 tests pass.
- Finding: `uagents_core.models.Model` is **pydantic v1** — contracts use `pydantic.v1` validators. `uagents.Model` is the same class; agent identity = `Identity.from_seed(seed, 0)`.
- Done: `packages/freight_contracts` (RequestOffer, ProposeOffer, RejectOffer, AgentError, CargoSpec, typed RejectReason/ErrorCode, `parse_carrier_reply`, sign/verify). Smoke check: sign→parse→verify True; wrong address False; tampered price False; malformed/unknown/invalid payloads raise MalformedReply; same-port and naive timestamps rejected.
- Next: Tiger migrations + seed (WP1/WP4), carrier project (WP2/3), Exchange CarrierClient + graph route (WP5), tests (WP6).

### 2026-10-17 14:45 — Phase 02 (WP1–WP6 implemented, live evidence)
- Done: Tiger migration `001_core.sql` (ports, carriers, carrier_policies, vessels, sailings, capacity_slots, cargo_requests, offers, agent_messages, holds/hold_slots, bookings/booking_slots, payment_records, disruption_events, recovery_runs/assignments, market_events, market_snapshots; PK/FK incl. composite (sailing_id, carrier_id) ownership FKs, unique keys, CHECKs, `source_kind IN (simulated, external)`). Runner checks TimescaleDB version → `by_range` API. Idempotent seed from `fixtures/demo_carriers.json`. Carrier runtime `carriers/` (uAgents 0.26.1, 3 processes, Almanac API registration, startup check seed-address == Tiger). Exchange: `carrier_client` (uagents-core sync envelopes, Almanac→registry fallback, ordered validation), `quotes`, `repository` (events in same tx), `request_parser`, async graph node; Ref id == Tiger request_id. Removed Phase 1 static catalog (superseded by 0003).
- **AT-02-01 PASS:** Tiger (TimescaleDB 2.30.2) cold `scripts/db.py migrate`+`seed` ×2 → run 2 "none (already current)", slots_inserted 0, counts ports 2 / carriers 3 / policies 3 / vessels 3 / sailings 6 / slots 18; hypertables market_events, market_snapshots.
- **AT-02-02 + AT-02-04 PASS:** `scripts/quote_smoke.py` req_f8e16e7ff59b (flow_98a0f0df6704): atlas PROPOSE ATL-101 $5,200, beacon PROPOSE BEA-201 $5,500, cove REJECT INSUFFICIENT_CAPACITY; 3/3 signatures verified vs Tiger addresses (atlas agent1qvjf…7evk, beacon agent1q25t…x3tl, cove agent1q0d9…grlp); resolution=almanac. Tiger: 1 cargo_request QUOTED, 2 offers (FK-verified sailing ownership), 6 agent_messages, 8 market_events (CARGO_REQUEST_CREATED → 3×OFFER_REQUESTED → 2×OFFER_SUBMITTED + OFFER_DECLINED → QUOTE_ROUND_COMPLETED).
- **AT-02-03 PASS (live carriers + unit):** `scripts/acp_failure_smoke.py` → UNAUTHORIZED_REQUESTER, INVALID_CARGO_REQUEST, REQUEST_EXPIRED (carrier AGENT_ERRORs, signed), UNSUPPORTED_CONTAINER_TYPE (REJECT), CARRIER_UNREACHABLE (unknown carrier), unknown-schema payload → CARRIER_TIMEOUT ("Query envelope expired"; uAgents drops unknown schemas), control → offer. Unit (`tests/test_carrier_client.py`): MALFORMED_REPLY, UNKNOWN_CARRIER, INVALID_SIGNATURE (wrong key/unsigned/tampered), CORRELATION_MISMATCH, STALE_OFFER, OFFER_CONSTRAINT_VIOLATION ×4, timeout.
- **AT-02-05:** Exchange restarted (mailbox, `Registered with agentverse`, `Mailbox client started`); local SDK-shaped `/threads/{id}/runs/wait` with PRD prompt → live 3-carrier table, req_df13eeade8f3, carrier RTT 813–899 ms. **ASI:One run pending human.**
- Tests: `uv run pytest -q` 55 passed; `carriers: uv run pytest -q` 16 passed.
- Findings/decisions: macOS keeps re-setting `hidden` flag on `.venv/**/*.pth` (repo in iCloud-synced ~/Documents; iCloud " 2" duplicate seen) and Python skips hidden .pth → freight-contracts installed non-editable; CLIs in `scripts/` add `src` to sys.path. Tiger password via `PGPASSWORD` (URL has no password). Carriers bind 0.0.0.0 (uAgents default) but only serve signed requests from EXCHANGE_AGENT_ADDRESS. uAgents sync queries keyed by sender → concurrent rounds to same carrier may collide (Phase 3 risk).
- Repo: private GitHub https://github.com/Parth-Dua/freight-recovery-exchange (own git repo in project dir; parent home-dir repo untouched). Secret scan of staged files: none of AGENT_URI key / carrier seeds / PGPASSWORD present.
- Next action: user sends in ASI:One `@freight-recovery-exc Quote 2x20GP from Jebel Ali to Mumbai, arrive by Oct 23, budget $5,600, don't split` → match Ref ↔ `request_handled` + Tiger row → mark AT-02-05 and Phase 2 gate. Then Phase 3 spec.

### 2026-10-17 14:55 — Phase 02 (AT-02-05 via ASI:One)
- Bug found + fixed: ASI:One forwards the `@freight-recovery-exc` mention in the text; "recovery" in our handle routed quote requests to recovery (req_50ce5cf95abc, req_6d4369794841). Fix: `responses.strip_mentions` before routing/parsing; regression tests added (60 passed). Exchange restarted; commit "Ignore @handle mentions…" pushed.
- **AT-02-05 evidence:** ASI:One sender `agent1qwh4wz3q…cefg3w`, session 7316d2ec-…, text `@freight-recovery-exc Quote 2x20GP from Jebel Ali to Mumbai` → `request_handled` route request_quotes ok 7.9 s, **req_30692ecbd2cb** (flow_4b76e43673b5). Tiger: cargo_requests QUOTED, buyer_id = ASI:One sender; offers cove COV-302 500000, atlas ATL-101 520000, beacon BEA-201 550000 (PROPOSED); 6 agent_messages; 8 market_events. All 3 carriers resolution=almanac. Visible ASI:One table: awaiting user confirmation.
- **Finding (Phase 3 must fix before booking):** ASI:One delivers each message twice in the same session (~2.5–7 s apart): req_50ce…/req_6d43…, req_30692ecbd2cb/req_214a3d10faaf → duplicate quote rounds. The SDK passes no ACP msg_id to the graph (metadata: sender, target, session only). Need idempotency keyed on (session, normalized text, time window) or a pre-graph dedupe, before any state-changing action (holds/bookings/payments).
- Next action: user confirms ASI:One shows the 3-offer table ending `Ref: req_30692ecbd2cb` (or req_214a3d10faaf) → mark Phase 2 PASSED; then read `docs/specs/03-booking-negotiation.md`.

### 2026-10-17 15:00 — Phase 02 PASSED
- User confirmed the ASI:One reply showed the live 3-carrier offer table (req_30692ecbd2cb). Gate met: ASI:One → Exchange → 3 separately addressed carrier agents (uAgents sync, signed) → distinct offers persisted in Tiger.
- Phase 3 may assume: carriers run via `./scripts/run_carriers.sh`; `CarrierClient.request_offers`; offers/agent_messages/market_events schema; Ref == Tiger request_id; known risks: ASI:One duplicate delivery (needs idempotency), uAgents sync queries keyed by sender (concurrent rounds), SDK adapter returns only text/resources (Interactive Cards unverified — see Phase 3 WP5).
- User question (open, no decision): use an Interactive Card to collect cargo details instead of free text. Specs place cards in Phase 3 WP5 (offer selection cards mapped to real offer IDs) and Phase 7 (recovery outcome cards).
- Next action: read `docs/specs/03-booking-negotiation.md`; first verify what ASI:One card types exist (input form vs selection) and whether the agentverse-sdk LangGraph adapter can emit them.

### 2026-10-17 15:15 — Phase 03 pre-check: Interactive Cards (research only, no code)
- Docs (docs.agentverse.ai agent-driven-interactive-cards + predefined-card-schemas): card = one `MetadataContent` item in the agent's ChatMessage (+ TextContent narration). Keys: `card_protocol_version="1"`, `requires_card_interaction="true"`, `card_kind` ∈ carousel|detail|form|review|custom, `card_payload` (JSON string, ≤64 KB). Form field kinds: text, number, email, select, checkbox (**no date picker**). Submission returns `action` + field values keyed by name: as selection JSON text on direct @mention, as prose via the ASI:One planner (must handle both). Playground: asi1.ai/developer/card-playground.
- Installed `uagents-core` 0.4.12 ships `chat.cards` helpers (`FormCardPayload`, `CarouselCardPayload`, `ReviewCardPayload`, `create_card_content`, `extract_card`, `validate_card_payload`).
- Adapter check: agentverse-sdk 0.2.1 `extract_ai_content` emits only TextContent/ResourceContent → cards dropped as-is. Offline proof: patching module-level `agentverse_sdk.langgraph.content._convert_content_block` to map a `{"type":"card","metadata":…}` block → `MetadataContent` yields wire content `[text, metadata]`; a 7-field cargo form (972 B) passes the official validator and survives ChatMessage round-trip. Private-function patch → needs a version-pinned test.
- Inbound caveat: SDK passes only `msg.text()` to the graph; card responses sent purely as MetadataContent would arrive empty. Docs say ASI:One returns selections as text (JSON or prose) → expected OK, must verify live.
- NOT verified: that ASI:One renders a card coming from an external LangGraph agent (needs a live test). No decision taken on cards vs uAgent gateway.

### 2026-10-17 15:30 — Phase 03 implemented (decision 0004)
- Done: migration `002_negotiation_selection.sql` (cargo_requests.session_id + OFFER_SELECTED status; offers.supersedes_offer_id/display_rank + SELECTED status; offer_selections; carrier-owned carrier_offer_log with counters_used ≤ 1; chat_turns). Contracts: COUNTER_OFFER, ACCEPT_COUNTER, REVISED_OFFER, REJECT_COUNTER (+ reason codes). Carrier: offer log, `counter_precheck`, atomic `claim_counter`, `decide_counter` + `enforce_floor`; fixture concessions Atlas 10 / Beacon 4 / Cove 3 (policies v2). Exchange: `negotiation.counter_target`, `CarrierClient.counter_one` + `validate_counter_reply`, QuoteService negotiates in parallel and ranks finals (price, arrival, offer_id) → display_rank; `selection.resolve_choice`; graph now start(dedupe+route) → quote|requote|select|info → finish with pending clarification in thread state; quantity no longer assumed; unserved lane / container type clarified before carrier calls.
- Tests: `uv run pytest -q` **107 passed**; `carriers: uv run pytest -q` **54 passed** (floor never breached across policies × counter values, one-counter precheck, reply validation ×9, selection ×12, graph flows: clarification follow-up, select, expired select, requote, duplicate delivery).
- Live (`scripts/negotiation_smoke.py`, real carriers + Tiger): **req_ba06ddf3e8a8** round 10.9 s, no batching (AT-03-01): Atlas $5,200 → asked $4,940 → ACCEPT_COUNTER $4,940; Beacon $5,500 → REVISED_OFFER $5,280; Cove REJECT INSUFFICIENT_CAPACITY; 5/5 signatures verified. **AT-03-03**: 2nd counter on opening and counter on negotiated offers → REJECT_COUNTER COUNTER_LIMIT_REACHED (atlas, beacon); $1,000 counter → BELOW_POLICY (atlas, beacon); carrier_offer_log counters_used=1 on openings. Select → SELECTED, repeat → ALREADY_SELECTED; request OFFER_SELECTED; offers: openings SUPERSEDED, atlas round 2 SELECTED rank 1; 13 market_events incl. COUNTER_OFFER_SENT/COUNTER_ACCEPTED/OFFER_REVISED/OFFER_SELECTED.
- **AT-03-06**: Beacon process killed → req_621b1274b261: Atlas $5,200→$4,750 accepted, Cove $5,000→$4,850 revised, Beacon CARRIER_UNREACHABLE (typed); Beacon restarted.
- **Server HTTP flow** (SDK-shaped /threads/{id}/runs/wait, thread metadata): "Quote from Jebel Ali to Mumbai, arrive by Oct 23, budget $5,600" → asked for number of containers (AT-03-05) → "2" → negotiated table req_a68a77e95eec → "select 1" → OFFER_SELECTED (not a booking) req_bccba6c49103 → duplicate "select 1" → identical reply, same Ref (dedupe).
- Risks: round latency ~8–11 s (Tiger round-trips from laptop); dedupe window 120 s means an intentional identical repeat within 2 min returns the cached reply; ASI:One planner may paraphrase "select 1" (rank/carrier/cheapest parsing covers common forms).
- Next action: user runs ASI:One steps in README / chat (quote → select 1 → wait >10 min → select 2 expects OFFER_EXPIRED → requote) → record AT-03-02/04 → mark Phase 3 PASSED → Phase 4 spec.

### 2026-10-17 16:05 — [Parth] Phase 03 ASI:One evidence + Tisya's NEEDS-FROM-PARTH (branch parth/seams-and-disruption-status)
- AT-03-04 via ASI:One: session 1f0e6304-…, `select 2` after expiry (offers valid to 22:30 UTC) → reply OFFER_EXPIRED + requote hint (req_5479e247abde); Tiger: beacon round-2 offer EXPIRED, OFFER_EXPIRED event, 0 selections, request still QUOTED. Same session: clarification asked for destination + quantity; duplicate delivery req_8d38b409ffd4 answered from cache (no 2nd round). AT-03-02 (successful select in ASI:One) still pending: user to `requote` then `select 1` within 10 min.
- Tisya's requests done: (1) root `pyproject.toml` depends on `freight-seams` (non-editable path). (2) disruption/withdraw/recover/"my booking" messages → new graph node `status` → `TigerBookingSeam.recent_withdrawals()` + `bookings_for_buyer(sender)` → real Tiger reply (spec 05 WP6); recovery stated as not active; route name now `disruption_status`, checked before booking words. `TigerBookingSeam` satisfies `freight_seams.BookingSeam`; `commit_booking` raises NotImplementedError until Phase 4. (3) migration `003_disruption_command_fields.sql`: nullable `reason_risk_event_id`, `authorization_ref`, `idempotency_key` (+ partial unique index) on `disruption_events` — applied to Parth's Tiger.
- Tests: root 117 passed; carriers 54; freight_seams 12; console unit (`test_withdrawal_logic.py`, no DB) 13. Console live tests NOT run against Parth's Tiger (they withdraw sailings; seed does not un-withdraw). Live read on Parth's Tiger: "No sailings have been withdrawn", no bookings for the ASI:One sender (accurate).
- Risk for Phase 4: buyer identity = ASI:One `agentverse_sender`; the same address appeared across sessions, so it may be shared by ASI:One users — must be decided before bookings_for_buyer shows real bookings.
- NEEDS-FROM-TISYA: console can write the 3 new columns instead of packing them into `reason`; run `uv run python scripts/db.py migrate` on her Tiger.
- Next: PR for review (do not merge without user); user runs AT-03-02 in ASI:One; then Phase 4.

### 2026-10-17 16:05 — [Parth] Buyer identity check (Phase 4 input)
- Test: user messaged `@freight-recovery-exc` from a second ASI:One account → req_588966d2de21 at 16:00:17, sender `agent1q0zqghjhffthx27hku0uvr6q7wxapkvpcc0ghkm8t36dx0g5p6j6v2x8cc7` (session 769e19b5…). Main account in every earlier session: `agent1qwh4wz3qqgwqqrh2kt2s3np8x4760rr82vzkddjrytxhkscv007tgcefg3w`.
- Result: **`agentverse_sender` is per ASI:One account** (2 accounts → 2 addresses; stable across sessions). Resolves the PR #5 open question.
- Phase 4 decision (proposed): buyer_id = agentverse_sender; state-changing actions additionally bound to the session that created the request (already true for select); booking receipts carry booking ref + claim code for lookups outside the session.

### 2026-10-17 16:10 — [Parth] Phase 03 PASSED
- **AT-03-02 via ASI:One:** main account, session 1f0e6304-…: `requote` → req_a07d82db57e2 (8.9 s; duplicate delivery req_d16923a32ef4 served from cache, no 2nd round) → `select 2` → req_324807be325c: "Selected: option 2 — Beacon … $5,280 … not a booking". Tiger: cargo_requests req_a07d82db57e2 OFFER_SELECTED (buyer = main sender, session_id set); beacon round-2 offer SELECTED rank 2 (atlas round-2 PROPOSED rank 1); offer_selections off_beacon_e7b6a47b2209 source chat:rank; events COUNTER_ACCEPTED, OFFER_REVISED, OFFER_SELECTED; 0 holds, 0 bookings.
- Gate met: one buyer requested freight and selected a policy-valid, negotiated, expiring carrier offer from ASI:One; selection persisted, not called booked. AT-03-01/03/05/06 evidence in the 15:30 entry; AT-03-04 in the 16:05 entry.
- Phase 4 may assume: offer_selections + OFFER_SELECTED request state; buyer_id = agentverse_sender (per account); session-bound actions; selected offers expire (10 min) → Phase 4 must re-validate expiry before requesting a hold; TigerBookingSeam.commit_booking is the Phase 4 entry point (seam signature fixed).
- Next: read `docs/specs/04-booking-and-stripe-payments.md`; Stripe test key (`sk_test_…`) will be needed from the user.

## Checkpoint template (append concise updates)
### YYYY-MM-DD HH:MM — Phase NN
- Done:
- Tests / evidence:
- Files:
- Blockers / decisions:
- Next action (exact command/task):

### 2026-10-17 — Planning-only PRD review
- Done: Read active Phase 1 spec and PRD; assessed 24-hour feasibility and checked official Fetch hackpack/LangGraph documentation. No code or PRD changes.
- Tests / evidence: No tests run (planning only). Official hackpack lists Payment Protocol/cards as bonuses; no integration gates were verified.
- Files: progress.md (this required checkpoint only).
- Blockers / decisions: PRD native-payment requirement conflicts with selected Stripe test-only Checkout; PRD gateway recommendation differs from active direct-LangGraph spec. Proposed scope reductions remain recommendations, not approved changes. Phase 1 remains NOT VERIFIED.
- Next action: Team confirms demo scope, builder count, recovery authority/priority and documentation reconciliation; implementation still starts with Phase 1 real ASI:One roundtrip gate.

### 2026-10-17 — [Tisya] Proposed: recovery decision moves to Tisya (pending Parth's OK)
- Proposal (branch `tisya/recovery-split`, not merged): Parth talks to the carriers and makes bookings real; Tisya decides who goes where, checks carrier claims against Tiger, gets approval, explains. Step-by-step owners in `OWNERSHIP.md`.
- Phase 6 split: Parth = `REQUEST_RECOVERY_CAPACITY` over ACP + message validation; Tisya = claim checks + OR-Tools allocation (`AllocationSeam`, new `packages/freight_allocation/`). Phase 7 split: Parth = holds, confirm, supersede, re-solve trigger (`RecoverySeam`); Tisya = approval (`ApprovalSeam`) and recovery cards.
- Seams added: `AllocationSeam`, `ApprovalSeam`, types `RecoveryOffer`, `RecoveryProposal`, fakes `FakeAllocationSeam` (greedy, not the solver), `FakeApprovalSeam`, `demo_recovery_offers()`.
- Fixed: seams README wrongly said `console/pyproject.toml` depends on `freight-seams`. It does not yet.
- Open: `max_additional_cost_minor` (D5) is stored nowhere; defaults to 0 until phase 4 records it. Buyer identity (Parth's finding: shared `agentverse_sender`) is Tisya's to investigate.
- Real `DisruptionSeam` (Tiger-backed) does not exist yet: console's `withdraw_sailing` is not wired to the seam and `disrupted_bookings()` is unimplemented.

### 2026-10-17 — [Tisya] Recovery split approved
- Parth approved the split in person; merged as PR #6 (af583d4). Supersedes "pending Parth's OK" above. Tisya owns phase 6 allocation (claim checks + OR-Tools), approval (`ApprovalSeam`), recovery cards; Parth owns recovery offers over ACP and phase 7 commitments.
- Buyer identity (Parth, branch parth/seams-and-disruption-status): `agentverse_sender` is per ASI:One account and stable across sessions; no investigation needed from Tisya. Still open for Tisya: can the agent message a buyer unprompted (async closure update); test alongside the card spike.
- Fetch Agent Payment Protocol is Parth's (Phase 4, decision 0006).

### 2026-10-17 — [Tisya] Phase 06 allocation: in progress (branch tisya/allocation-solver)
- Done: `packages/freight_allocation/` (own uv project, ortools 9.15.6755, freight-seams non-editable). `verify.py` claim checks (trusted = min(claimed, sellable on sailing)); `capacity.py` (`TigerCapacityReader`: AVAILABLE slots on ACTIVE sailings; `StaticCapacity` for tests); `model.py` CP-SAT, 4 lexicographic stages (most recovered, fewest approvals, lowest cost, earliest arrival), single worker + fixed seed; `explain.py` per-booking buyer text; `seam.py` `SolverAllocationSeam` implements `AllocationSeam`.
- Evidence so far (unit, no DB): spec 06 WP5 fixture → BK-1001 (2, no split) on BEA-201, BK-1002 on COV-301, 0 approvals, cost 810000 minor, proven optimal.
- Decision to confirm with Parth: added "fewest approvals" as stage 2, between spec 06's "most recovered" and "lowest cost", so the solver prefers a plan the buyer doesn't have to sign off on.
- Next: AT-06-01..05 tests, claim-check and determinism tests, live Tiger reader test (skips without DB), README, PR. AT-06-06 (one-shot ACP, no recovery counteroffers) is Parth's side.

### 2026-10-17 — [Tisya] Phase 06 allocation: implemented, unit-tested (branch tisya/allocation-solver)
- Done: `SolverAllocationSeam` (real `AllocationSeam`) = claim checks → CP-SAT joint allocation → per-booking explanations; `TigerCapacityReader` for claim checks; README.
- Tests (`cd packages/freight_allocation && uv run pytest -q`): 26 passed, 1 skipped (live Tiger read, no DB URL). Covers AT-06-01 (spec baseline; same shape with renamed ids and reversed inputs), joint beats cheapest-first, AT-06-02 (honest partial without Cove; A cannot squeeze onto Cove without Beacon), AT-06-03 (cheaper second option changes the answer; equal price goes to earlier arrival), AT-06-04 (no-split never split; splittable may span sailings), AT-06-05 (one slot never used twice; offers on one ship share its slots), overstated claims, unknown sailings, approvals, fewest-approvals-over-cost, deadlines, expiry, currency, empty inputs, validation, determinism, input_version.
- Not evidence of: Tiger, ACP or ASI:One. All unit tests. AT-06-06 is Parth's side.
- Open for Parth: D4 (stage 1 counts bookings, not containers: Beacon alone saves B over A on cost); D5 still unstored (defaults to 0); root project needs `freight-allocation` (with ortools) only when phase 7 calls the real seam.
- Next (Tisya): own Tiger DB → run the live reader test and console live tests; `TigerDisruptionSeam`; then cards.

### 2026-10-17 — [Tisya] Own dev database + Phase 05 live evidence (branch tisya/dev-db)
- Local TimescaleDB 2.30.2 in Docker (`freight-tiger-dev`, 127.0.0.1:5433), same version as Parth's Tiger Cloud. `.env` local URL + generated password + generated carrier seeds (none printed). `console/scripts/dev_db.py up|reset` creates or wipes, migrates, seeds, preseeds B and C; refuses any non-local URL.
- Evidence: migrate → 001_core, 002_negotiation_selection; seed → ports 2, carriers 3, sailings 6, slots 18. Console `uv run pytest -q` with DB → **17 passed** (13 unit + 4 live: unknown sailing rejected, cross-carrier rejected (AT-05-04), idempotent withdrawal (AT-05-03), other sailings untouched (AT-05-02)). Withdrawal of ATL-101 and BEA-202 disrupted exactly the bookings on them; events SAILING_WITHDRAWN ×2, BOOKING_DISRUPTED ×2. Allocation live reader (branch tisya/allocation-solver): 1 passed; read BEA-201=2, COV-301=1.
- Fixed: console live tests errored at teardown ("Event loop is closed"): module-scoped pool cleanup across per-test event loops. Now per test.
- NEEDS-FROM-PARTH: `scripts/init_carrier_identities.py` treats keys present-but-empty (as copied from `.env.example`) as set, so all three carriers get the same identity from an empty seed and `db.py seed` then fails. Suggest treating an empty value as missing. Worked around in `dev_db.py`.
- Not covered: migrations 003/004 (Parth's open branches) not applied here yet; rerun `dev_db.py up` after they merge.

### 2026-10-17 — [Tisya] Real DisruptionSeam (branch tisya/disruption-seam)
- Done: new `packages/freight_disruption/` (Tisya). `withdraw_sailing` moved here from `console/` (console re-exports it, one copy of the logic) and now fills the migration 003 columns. `TigerDisruptionSeam` implements `DisruptionSeam`: `withdraw`, `disrupted_bookings(sailing_id)` (still-DISRUPTED bookings as `DisruptedBooking`), plus `pending_withdrawals()` (no `recovery_runs` row yet) as the recovery trigger's work queue.
- Fixed: preseeded buyers' `latest_arrival` equalled their original arrival, so every replacement sailing was "too late" and B/C could never be recovered. Now arrival + 2 days (`ARRIVAL_SLACK`).
- Evidence (local TimescaleDB 2.30.2, migrations 001–003): disruption 6 unit + 2 live pass (withdraw → stranded read with correct fields → in pending queue → 003 columns written → second withdraw is a no-op). End-to-end dry run: real withdrawal of ATL-101 → stranded buyer_b (deadline 24 Oct 08:00) → real sellable capacity → solver in 41 ms → BK→BEA-201; claim check caught Beacon claiming 4 slots on BEA-201 vs 2 sellable; flagged for approval (+USD 75, D5 = 0). Only the carrier offers were stand-ins (ACP recovery round not built). Console via HTTP: review + confirm withdrew COV-302 and wrote risk_sim_0002 / SIM-ADV-2026-015 / key. Suites: root 117, console 20 (unit+live), allocation 27, seams 18, disruption 7 (+1 skipped after console tests used the sailings).
- NEEDS-FROM-PARTH: root deps `freight-disruption` + `freight-allocation` when wiring recovery; recovery trigger can poll `pending_withdrawals()` and claim via `recovery_runs` (UNIQUE `withdrawal_event_id`). D5 still unstored, so recovery in the demo needs buyer approval.
- Next (Tisya): async-message spike, then `ApprovalSeam`.

### 2026-10-18 — [Tisya] Buyer notifications v1 (branch tisya/notify-buyer)
- Decision (Tisya + Parth): the agent notifies the buyer in ASI:One when their sailing is withdrawn, rather than waiting to be asked; ideally with a push on the ASI:One mobile app.
- Done: `src/freight_exchange/asi/notify.py` (Tisya's `asi/` folder, created). `notify_buyer(agent_uri, BuyerAddress, text)` sends a ChatMessage signed as the agent to the buyer's ASI:One address and session, via the same SDK function every reply uses (`agentverse_sdk._common.av.send_message_to_agent`; agentverse-sdk 0.2.1 `_app._send_reply`). `buyer_address_for_booking` finds where to reach a booking's buyer (booking_intents.session_id, then cargo_requests.session_id); demo buyers like `buyer_b` are skipped. CLI for the spike. Key read from `NOTIFY_AGENT_URI` first so a non-exchange machine can send as the agent without ever running a second copy.
- Tests: `tests/asi/test_notify.py` 8 passed (incl. a guard that fails if the SDK pin or the private function's signature changes); root 125 passed. Unit only; nothing sent.
- NOT verified yet: whether ASI:One displays an unprompted message (same session / new session / no session) and whether the mobile app pushes it. Live spike pending (needs the exchange URI in Tisya's .env as NOTIFY_AGENT_URI, and her sender + session from Parth's request_handled log).

### 2026-10-18 02:05 UTC — [Tisya] Notification spike: PASSED for same-session
- Parth ran `notify.py` from his machine (his AGENT_URI, worktree on tisya/notify-buyer) to Tisya's ASI:One sender `agent1qty00j…99nmc0`:
  - 02:02:18 `--session 0865becb-db3a-44e3-9eb4-6f06b5843bc5` (the conversation she had open, req_4ec0a565931e): **shown on asi1.ai AND pushed to her phone** (ASI:One app), msg 340ee108-….
  - 02:03:52 no session: accepted, **never shown**. 02:05:16 new random session: accepted, **never shown**.
- Conclusion: the exchange can notify a buyer unprompted, but only into a conversation ASI:One already knows. ASI:One accepts the other two silently, so `notify_buyer` now refuses to send without a stored session (`WouldNotDisplay`) and `buyer_address_for_booking` returns None without one. The ASI:One app cannot chat with custom agents ("isn't available in the ASI:One mobile app yet") but still delivers the push.
- Decision (Tisya + Parth): notifications are sent from the exchange process, not another machine. Acks and buyer replies return to the exchange's mailbox, so only the sender's logs can account for them; no AGENT_URI sharing.
- Tests: tests/asi 11 passed; root 128 passed.
- Unknown: whether a much older conversation still displays (demo gap is minutes, so low risk).

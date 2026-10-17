# Phase 1 Specification — Fetch ACP Connectivity & ASI:One Discovery

**Project:** Autonomous Freight Recovery Exchange (LA Hacks AI 2026)  
**Status:** Ready to implement; not yet verified  
**Priority:** P0; hard gate before Phase 2  
**Source of truth:** [Fetch LA Hacks AI hackpack](https://www.fetch.ai/events/hackathons/la-hacks-ai-hackathon-2026/hackpack), [Agentverse LangGraph integration](https://docs.agentverse.ai/documentation/launch-agents/agentverse-sdk/lang-graph), [Agent discovery setup](https://docs.agentverse.ai/documentation/agent-discovery/setup-guide), [README guidelines](https://docs.agentverse.ai/v-2/documentation/agent-discovery/readme-guidelines).

## 1. Objective and outcome

Build the **Exchange Agent** as a real running LangGraph-compatible service, register it on **Agentverse**, expose it through **Agent Chat Protocol (ACP)**, and prove a message sent through **ASI:One** reaches our backend and its response returns to the user.

**Done means observed evidence, not just deployed code:** Agentverse registration succeeds; the agent is active; a direct mention or discovery in ASI:One leads to a response containing information produced by our running code; the backend records the corresponding non-secret request ID. Do not declare natural-language discovery successful based solely on an ASI:One response discussing freight.

Phase 1 intentionally does **not** implement carrier agents, negotiation, inventory, Tiger Data, payment, OR-Tools, disruption, or recovery. It establishes the integration seam upon which all subsequent phases depend.

## 2. Scope

### In scope
- Minimal Python project with compatible LangGraph agent and Agentverse SDK integration.
- Agentverse external LangGraph registration using the persistent `AGENT_URI` created by Agentverse.
- ACP messaging supported by Agentverse SDK (do not create a second ad hoc ACP transport).
- Public local development endpoint via the documented tunnel (`langgraph-av dev --tunnel`); alternate mailbox mode is only a fallback with an explicit decision record.
- ASI:One agent-facing model/API configuration **if required by the selected LangGraph implementation**.
- A deterministic capability lookup: `get_supported_corridors()` returning a structured example corridor and a **source marker** (`phase1-static-catalog`).
- A concise Agentverse profile, README, keywords, and configured `@handle`.
- Health/startup checks, structured non-sensitive request/response logging, tests, and proof artifacts.
- Mandatory maintenance of `progress.md` for resumable work.

### Out of scope
- Buyer request parsing or binding quotes; bookings; carrier orchestration; costs; authorization; Stripe payments; Fetch Agent Payment Protocol; Interactive Cards; real maritime data; Tiger Data; Sentry; dashboards; production deployments.
- Any claim that this Phase 1 agent can actually book freight or recover shipments.

## 3. Architectural choice and uncertainty management

Follow the official **Agentverse SDK LangGraph** path rather than implementing ACP from scratch. The official guide uses `agentverse_sdk.langgraph.init` and registers the exported agent. Keep the graph tiny (even a single deterministic response/tool is sufficient), but preserve the exported object and directory structure for later BookingGraph/RecoveryGraph work.

**Do not assume** an Agentverse API key is required by this particular manual UI registration flow. The documented LangGraph example explicitly uses `AGENT_URI` and `ASI1_API_KEY`; other Agentverse management APIs may require `AGENTVERSE_API_KEY`. Only introduce additional credentials when required by an actual chosen API. No credential should be written into a spec, commit, log, or `progress.md`.

If tool invocation from a LangGraph agent cannot be made deterministic under the version of the framework installed, return the fixture from a deterministic graph node instead. Phase 1 must prove invocation, not agent reasoning quality.

## 4. Sequence and data flow

```mermaid
sequenceDiagram
    actor U as User
    participant A as ASI:One
    participant V as Agentverse / ACP
    participant E as Exchange Agent (LangGraph)
    U->>A: Find or @mention freight agent; ask capabilities
    A->>V: Route agent request via ACP
    V->>E: Deliver chat message
    E->>E: Route to capability response (static fixture)
    E-->>V: Respond with corridor + request marker
    V-->>A: Agent response
    A-->>U: Display result
```

**Proof boundary:** the actual request must appear in backend logs with a correlation marker, and the visible response must reflect the deterministic fixture rather than an invented ASI:One answer.

## 5. Required repository layout

```text
.
├── AGENTS.md                       # Agent working rules, kept concise
├── progress.md                     # Continuously updated checkpoint, never secrets
├── .env.example                    # Variable names only
├── .gitignore
├── pyproject.toml
├── langgraph.json                  # Maps exported graph object
├── src/
│   └── agent.py                    # Export the LangGraph `agent` object
├── tests/
│   ├── test_capabilities.py
│   └── test_agent_import.py
└── docs/
    └── specs/
        └── 01-fetch-connectivity.md
```

If the actual repo has a different structure, adapt paths rather than relocate unrelated files. Set `langgraph.json` module mapping to match the actual import path; the official docs' `agent:agent` example applies only if `agent.py` is importable at the configured module root.

## 6. Environment and operational setup

`.env.example` must contain **only** placeholders:

```dotenv
AGENT_URI=
ASI1_API_KEY=
# Only if an Agentverse management API used in this implementation requires it:
# AGENTVERSE_API_KEY=
LOG_LEVEL=INFO
```

- Obtain `AGENT_URI` from the Agentverse external LangGraph registration UI. **It is an identifier, not a private seed.**
- Obtain the ASI:One API key through the ASI:One portal if the chosen graph calls its model.
- Never commit `.env`, passwords, token contents, or tunnel credentials.
- For local development, the official guide describes `uv sync` and `langgraph-av dev --tunnel`. Verify commands against installed versions before execution.
- Keep the server and tunnel running during Agentverse evaluation and ASI:One tests.
- Record the selected package versions and working startup command in `progress.md` / README after verification.

## 7. Behavior and contracts

### 7.1 Capability fixture

The service exposes one deterministic, non-transactional capability. Implementation may use a function/tool or explicit graph node.

**Input (internal intent):** `get_supported_corridors()`; no user credentials or personal data required.

**Output shape (versioned, illustrative):**

```json
{
  "schema_version": "1.0",
  "capability": "get_supported_corridors",
  "source": "phase1-static-catalog",
  "corridors": [
    {"origin_port": "CNSHA", "destination_port": "USLAX", "mode": "ocean_freight"}
  ],
  "booking_enabled": false,
  "recovery_enabled": false
}
```

CNSHA and USLAX are UN/LOCODE-style identifiers selected as **simulation fixtures**, not a statement that live carrier capacity exists. Avoid invented prices, schedules, or real-world capacity claims. Only the currently supported example corridor should be returned.

### 7.2 User messages

Minimum supported prompts:

1. `What can you do?` → describe *future* booking/recovery product concept and current Phase 1 connectivity-only status.
2. `What shipping corridors can you show?` → invoke deterministic capability and list the fixture corridor.
3. `Can you book two containers tomorrow?` → clearly explain that booking is **not yet implemented**; never invent a booking or ask for a payment.
4. Nonsense/unsupported request → safely explain current scope without crashing.

Output should be short and conversational, but must visibly include a recognizable marker or stable fixture phrase for end-to-end verification (e.g., `Phase 1 catalog: Shanghai (CNSHA) → Los Angeles (USLAX)`). If including a request ID in user-visible output, sanitize and format it.

### 7.3 Observability and errors

For each handled message: emit a generated `request_id`, route/capability name, status, and execution duration. Never log API keys, full authorization headers, seed phrases, or payment information. On ACP delivery or LLM failure, surface a concise retry-safe error and retain enough non-sensitive diagnostics to debug it.

## 8. Agentverse registration and discovery metadata

After local agent is running:

1. Sign into [Agentverse](https://agentverse.ai) → Agents → Launch an Agent → External Agent → LangGraph (exact labels may change).
2. Provide an agent name, e.g., **Freight Recovery Exchange (Demo)**; choose an available handle at registration time rather than hardcode a claim it exists.
3. Set discoverability keywords: `ocean freight`, `cargo booking`, `shipping agent`, `shipment recovery`, `maritime disruption`.
4. Copy the generated `AGENT_URI` to local secrets, initialize the Agentverse SDK against it, and run the backend/tunnel.
5. Run Agentverse registration evaluation and confirm the profile is active and its chat test reaches the code.
6. Complete the Agentverse profile and README. State the purpose and future capabilities, while truthfully specifying Phase 1 is *discovery/capabilities only*.
7. From ASI:One, try the direct registered `@handle` first; then a fresh conversation with natural-language agent discovery. Discovery ranking and propagation are external factors; distinguish an agent being callable by handle from being independently found in generic search.

**Suggested Agentverse README content:**

```md
# Freight Recovery Exchange (Demo)
Agent-native ocean-freight coordination, designed to help cargo owners negotiate bookings and recover shipments after disruptions.

## Current demo capabilities
- Explain the product workflow.
- Return the Phase 1 sample corridor: Shanghai (CNSHA) to Los Angeles (USLAX).
- Confirm connectivity between ASI:One, Agentverse, and our live LangGraph backend.

**Not yet active:** live quotations, bookings, payments, or recovery.

## Example prompts
- "What can your freight agent do?"
- "What corridors do you currently support?"
```

The README and description should be updated in later phases when actual capabilities are delivered.

## 9. Work packages (strict sequence)

**P1.1 Repository skeleton.** Create Python project, dependency declarations, exportable graph/agent, `langgraph.json`, `.gitignore`, `.env.example`, starter tests, `progress.md`. **Verify:** imports succeed and tests pass locally.

**P1.2 Minimal deterministic behavior.** Implement capability fixture and scope-aware responses. **Verify:** unit tests confirm exact response data and unimplemented booking behavior.

**P1.3 ACP exposure.** Integrate Agentverse SDK per official LangGraph docs and run a reachable local server/tunnel. **Verify:** startup logs show expected port/endpoint and no registration or import errors; no keys leaked.

**P1.4 Agentverse registration.** Complete external LangGraph registration, configure `AGENT_URI`, evaluate, and verify an actual message round-trip in Agentverse. **Verify:** response comes from backend and matches fixture.

**P1.5 ASI:One connection.** Set profile metadata and README. Test direct invocation using assigned handle; verify actual backend log and visible output; then attempt natural-language discovery. **Verify:** screenshots/transcript and log request ID. Do not conflate unsuccessful broad search with failed direct invocation.

**P1.6 Hardening + handoff.** Test invalid inputs and server restart; update runbook, evidence, `progress.md`, and phase status. **Verify:** another teammate can reproduce from a clean checkout and local secrets.

**Mandatory progress checkpoint after every package:** Record completed work, tests run and pass/fail, current blocker, files changed, and next exact action in `progress.md`. Update it **before stopping work or switching coding agents/models**.

## 10. Acceptance tests

| ID | Test | Expected result | Evidence |
|---|---|---|---|
| T01 | Import exported agent | Import succeeds; app starts | Test/console output |
| T02 | Capability fixture unit test | Versioned corridor data and `booking_enabled=false` | Test output |
| T03 | Unimplemented booking request | Agent explicitly declines booking action | Test output |
| T04 | Public endpoint / ACP reachability | Agentverse can contact active external agent | Registration evaluation |
| T05 | Agentverse chat | Message reaches backend; fixture returned | Agentverse screenshot + matching log |
| T06 | ASI:One direct handle | Chat invokes registered agent and returns fixture | ASI:One transcript + matching log |
| T07 | Natural language discovery | Agent appears in results or is correctly identified | Screenshot or issue note; classify as distinct from T06 |
| T08 | Restart test | Service comes back and accepts chat | Repeated transcript/log |
| T09 | Secrets hygiene | No real keys in Git or logs | Git diff/grep |
| T10 | Resume readiness | `progress.md` identifies current status, blockers, next action | Reviewed file |

**Phase 1 passing gate:** T01–T06, T08–T10 pass. T07 is an important discoverability objective; if it fails despite working direct invocation, keep the phase open with an explicitly recorded ranking/metadata blocker and retest rather than inventing success. If ASI:One cannot directly invoke the agent at all, Phase 1 is blocked, regardless of whether Agentverse chat works.

## 11. Definition of Done and handoff

- [ ] Exported minimal LangGraph agent runs reliably.
- [ ] Agentverse registration evaluation succeeds.
- [ ] Agent profile contains truthful README, description, and keywords.
- [ ] ASI:One directly invokes the agent through ACP.
- [ ] Backend correlation logs prove the invocation.
- [ ] Generic discovery tested and result recorded accurately.
- [ ] No booking/payment/recovery behaviors are falsely advertised.
- [ ] Tests, run instructions, and version pins are documented.
- [ ] No secrets committed.
- [ ] `progress.md` updated with evidence, remaining risks, and next action.

**Handoff to Phase 2:** supply agent URI identifier, assigned `@handle`, tested startup instructions, public endpoint strategy (tunnel vs mailbox), SDK/package versions, and the known-good ASI:One test prompt. Never include secret values.

## 12. Open decisions: do not silently guess

- **Repository shape:** Is this a new repository or an existing monorepo? Inspect before creating/moving files.
- **Execution mode:** Default to the official tunnel integration for Phase 1. If unavailable, document and request approval before switching to mailbox/deployment.
- **LLM choice:** Official example uses ASI:One model. Keep it if needed for chat; deterministic capability must not depend on LLM inference correctness.
- **Agent brand/handle:** Use a temporary descriptive name until the team chooses the final name; never assume handle availability.

Do not halt implementation for harmless naming details. Halt and ask when a missing decision changes network architecture, security, irreversible external effects, or required hackpack compliance.

## 13. Notes on original sources

The official Agentverse LangGraph guide documents `agentverse_init(AGENT_URI)`, `AGENT_URI`/`ASI1_API_KEY`, `langgraph.json`, `uv sync`, and `langgraph-av dev --tunnel`. The Agent Discovery setup guide calls for ACP, README, handle, active availability, and matching metadata. The Fetch hackpack defines the broader competition goals; this phase deliberately proves one prerequisite rather than claiming end-to-end prize completeness.

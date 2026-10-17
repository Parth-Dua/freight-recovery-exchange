# Part 1 Spec-Driven Development Pack

Place `AGENTS.md` and `progress.md` in repository root. Place the full PRD in `docs/PRD.md` and phase specs in `docs/specs/`.

Follow phases 01→08, gated by observable acceptance evidence. Phase 01 was preserved from the earlier Phase 1 specification. Docs describe requirements, **not completed implementation**.

**Latest payment choice:** Stripe test-only API and official test card; no actual-money transfers. Stripe test Checkout is not automatically equivalent to Fetch's Agent Payment Protocol bonus; verify separately. Any conflicting payment recommendation in the historical PRD is superseded.

**Part 2 deferred:** real risk data, Sentry and the Market Command Center; schemas in Part 1 remain future-data-compatible.

---

## Phase 1 runbook — Exchange Agent (ACP connectivity)

**Stack (locked in `uv.lock`):** Python 3.12, `agentverse-sdk[langgraph]==0.2.1` (provides `langgraph-av`), langgraph 1.2.16, langgraph-cli 0.4.36, langgraph-api 0.9.1, uagents-core 0.4.12.

**Layout:** `src/agent.py` exports the `agent` graph (`langgraph.json` → `"agent": "agent:agent"`; `langgraph-av` puts `src/` on the import path). `src/freight_exchange/` holds the capability fixture, deterministic reply routing, Agentverse profile/README and structured logging. The graph is a single deterministic node — no LLM, so `ASI1_API_KEY` is not needed in Phase 1.

```bash
uv sync                    # install pinned deps
uv run pytest -q           # unit + SDK-contract tests

# Local only (no Agentverse): AGENT_URI unset → SDK init skipped
uv run langgraph-av dev --no-browser --port 2024
curl -s -X POST localhost:2024/runs/wait -H 'content-type: application/json' \
  -d '{"assistant_id":"agent","input":{"messages":[{"role":"human","content":"What corridors do you currently support?"}]}}'

# Agentverse: put AGENT_URI in .env (gitignored; append ?log=INFO to see SDK logs), then
# Default = mailbox mode (AGENTVERSE_MAILBOX=true): agent polls Agentverse, no tunnel needed
uv run langgraph-av dev --no-browser

# Fallback: official tunnel path (set AGENTVERSE_MAILBOX=false). Use http2 — the default
# QUIC tunnel dropped every ~1 min on campus Wi-Fi, and quick tunnels get deleted on disconnect.
TUNNEL_TRANSPORT_PROTOCOL=http2 uv run langgraph-av dev --tunnel --no-browser
```

The SDK logger is silent by default (level CRITICAL); add `?log=INFO` to the end of `AGENT_URI` to see `Registered with agentverse` and any suppressed init/registration errors. Healthy mailbox startup logs `Registered with agentverse` then `Mailbox client started for agent1q…`; the public Almanac (`https://agentverse.ai/v1/almanac/agents/<address>`) shows `"type":"mailbox"`. In tunnel mode each restart gets a new trycloudflare URL and the SDK re-registers it.

**`AGENT_URI` is a secret.** Its format is `https://<handle>:<key>@agentverse.ai/<name>` and the SDK derives the agent identity from `<key>` (`Identity.from_seed`). Never paste it into chat, logs, commits or `progress.md`.

**Registration (manual, Agentverse UI):** Agents → Launch an Agent → External Agent → LangGraph → name **Freight Recovery Exchange (Demo)** → keywords `ocean freight`, `cargo booking`, `shipping agent`, `shipment recovery`, `maritime disruption` → copy the Agent URI into `.env` → run with `--tunnel` → click *Evaluate registration*. Description, README and starter prompts are pushed from `src/freight_exchange/profile.py` at each startup.

**Verifying a round-trip:** every reply ends with `Ref: req_<12 hex>`; the backend logs one `request_handled` JSON line with the same `request_id`, the route, status, duration and the Agentverse sender/session. Message text is never logged. Known-good prompt: `What corridors do you currently support?` → contains `Phase 1 catalog: Shanghai (CNSHA) → Los Angeles (USLAX)`.

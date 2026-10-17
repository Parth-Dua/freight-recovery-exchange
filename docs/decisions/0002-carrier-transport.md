# 0002 — Exchange ↔ Carrier transport: local uAgents, sync request/reply, signed replies

- **Date:** 2026-10-17 · **Status:** Accepted (user chose "Option 1") · **Phase:** 2
- **Context:** The Exchange is an Agentverse-SDK LangGraph agent. Its mailbox handles only Chat Protocol messages and routes every chat message into the graph and auto-replies, so carrier replies cannot be received through it. Alternatives considered: carriers as Agentverse mailbox agents with a separate dispatcher identity (slower, more failure points); rebuilding the gateway as a uAgent (redoes Phase 1).
- **Decision:**
  - Carriers (Atlas, Beacon, Cove) are `uagents` 0.26.1 agents, one codebase, one process per company, each with its own seed/address, Almanac-registered with a local endpoint.
  - The Exchange sends typed `REQUEST_OFFER` envelopes signed with its own Agentverse identity using **uAgents sync messaging** (`x-uagents-connection: sync`), in parallel with per-carrier timeouts. Endpoint resolved via Almanac API, falling back to the endpoint stored in Tiger.
  - Sync reply envelopes are unsigned, so carriers sign the reply body (`freight_contracts.signing`); the Exchange verifies it against the carrier address registered in Tiger.
  - Messages are our own versioned models (`packages/freight_contracts`), not built-in ACP verbs.
- **Dependency constraint:** `uagents` → `cosmpy` requires protobuf 5; `langgraph-api` 0.9.1 requires protobuf ≥ 6.32. They cannot share an environment, so carriers run from their own uv project (`carriers/`). The Exchange only uses `uagents-core` (already present). `langgraph-api==0.9.1` is pinned to prevent silent downgrades.
- **Consequences:** Carriers must run on the same host as the Exchange (localhost endpoints). Carriers are not visible/chat-able on Agentverse; upgrading them later only swaps the transport behind `CarrierClient`. uAgents keys pending sync queries by sender, so concurrent quote rounds to the same carrier from the Exchange can collide — acceptable for the demo, revisit for Phase 3+.

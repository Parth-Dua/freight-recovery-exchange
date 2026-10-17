# 0001 — Exchange Agent reachability: Agentverse mailbox mode

- **Date:** 2026-10-17 · **Status:** Accepted (user-approved) · **Phase:** 1
- **Context:** Phase 1 spec defaults to `langgraph-av dev --tunnel`. Cloudflare quick tunnels dropped every ~1 min over QUIC, and an http2 tunnel was deleted by Cloudflare after a disconnect ("Tunnel not found"); each restart changes the URL.
- **Decision:** `agentverse_init(..., mailbox=True)` (env `AGENTVERSE_MAILBOX`, default true). The agent polls Agentverse; no inbound public URL.
- **Consequences:** Almanac shows `type: mailbox`. Tunnel path remains available (`AGENTVERSE_MAILBOX=false`). The SDK mailbox only accepts Chat Protocol messages (non-chat envelopes are skipped) — see 0002.

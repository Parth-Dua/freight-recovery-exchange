# Critical alerts and current testing steps

Updated as the v2 phases (`docs/specs_2/`) progress. Newest first. Only things that need **you** are here.

## Status

| Phase | Status |
|---|---|
| 1 — uAgent gateway | Built and running locally; both protocol manifests published. **Waiting on you: connect mailbox + live ASI:One check (below).** |
| 2 — Interactive Cards | Not started |
| 3 — Fetch payment | Not started |
| 4 — Two-sided negotiation | Not started |
| 5 — Recovery clearing + release | Not started |

## Needs you now

1. **Connect the gateway's Agentverse mailbox (one time, ~30 s).** The new buyer gateway is a separate agent
   from the old `@freight-recovery-exc`. Either:
   - open the inspector link and click **Connect → Mailbox**:
     https://agentverse.ai/inspect/?uri=http%3A//127.0.0.1%3A8110&address=agent1q0ktnrhtmerdle6tz9lmdj9v7c0vzrtnl8wdvuthj88cj9ydp7j3vmymxm8
   - **or** put an Agentverse API key in `.env` as `AGENTVERSE_API_KEY=` and restart the gateway; it connects itself.
   After connecting, `logs/gateway.log` stops saying "Agent mailbox not found".
2. **In Agentverse**, give the agent a handle/name you like (e.g. `hormuz-exchange`) so ASI:One can find it.
   Gateway address: `agent1q0ktnrhtmerdle6tz9lmdj9v7c0vzrtnl8wdvuthj88cj9ydp7j3vmymxm8`.
3. **Heads-up for Phase 3 (no action yet):** you will need a **testnet** FET wallet in ASI:One with a
   little test FET from the official faucet (details when Phase 3 starts). Never use mainnet FET.

## How to test right now (Phase 1)

Services: `./scripts/run_demo.sh` starts everything (carriers, old Exchange, recovery worker, console, internal
Exchange API on 127.0.0.1:8900, buyer gateway on :8110). Logs in `logs/`.

In ASI:One, start a **new** chat with the gateway agent (by address or handle) and send:

| Send | Expected |
|---|---|
| `I need to ship cargo` | A native **form card** (Origin, Destination, 20GP containers) — not just text |
| Fill and submit the form | Reply "Got it: N×20GP … action received" |
| `What corridors do you support?` | The live corridor list from Tiger (existing graph, via the gateway) |
| `notify test` | Reply "Queued…", then within ~5 s a **second** message in the same chat (background push) |
| `payment test` | A native **payment request** (0.1 testnet FET). Please **Reject** it — it's a smoke test and books nothing |

Tell me (or just leave a note here) what rendered and what didn't; I'll pick it up from the logs too.

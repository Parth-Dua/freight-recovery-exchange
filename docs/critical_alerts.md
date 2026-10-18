# Critical alerts and current testing steps

Updated as the v2 phases (`docs/specs_2/`) progress. Only things that need **you** are here.

## Status (2026-10-18 ~02:30 PDT)

| Phase | Built + tested locally | Live ASI:One check (you) |
|---|---|---|
| 1 — uAgent gateway | ✅ both protocol manifests published | ⏳ **blocked on mailbox connect (alert 1)** |
| 2 — Interactive Cards (form → carousel → review → receipt; recovery cards) | ✅ full journey vs live carriers | ⏳ |
| 3 — Fetch FET testnet fee | ✅ real testnet transfers verified on-chain, paid bookings confirmed | ⏳ needs ASI:One wallet (alert 2) |
| 4 — Two-sided negotiation | in progress | — |
| 5 — Recovery clearing + release | not started | — |

## Needs you

1. **Connect the gateway's Agentverse mailbox (one time, ~30 s) — blocks every live ASI:One test.**
   Open this link and click **Connect → Mailbox**:
   https://agentverse.ai/inspect/?uri=http%3A//127.0.0.1%3A8110&address=agent1q0ktnrhtmerdle6tz9lmdj9v7c0vzrtnl8wdvuthj88cj9ydp7j3vmymxm8
   (or add `AGENTVERSE_API_KEY=` to `.env` and restart the gateway; it connects itself). `logs/gateway.log`
   stops saying "Agent mailbox not found" once it works. Then, in Agentverse, give the agent a handle
   (e.g. `hormuz-exchange`).
2. **ASI:One testnet FET wallet.** To pay the 0.1 testnet FET fee from ASI:One you need its wallet on the
   Fetch **testnet** with a little test FET (official faucet; never mainnet). Phase 3's gate depends on
   ASI:One sending `CommitPayment` with `buyer_fet_wallet` metadata — if ASI:One doesn't show a FET payment
   step, tell me (write it here) and I'll document it as the blocker.

## How to test right now

Start everything: `./scripts/run_demo.sh` (or the services already running). Gateway address:
`agent1q0ktnrhtmerdle6tz9lmdj9v7c0vzrtnl8wdvuthj88cj9ydp7j3vmymxm8`.

In ASI:One, open a **new chat with the gateway agent** and:

1. `I need to ship cargo` → a **form card** (origin, destination, containers, arrive-by, ready-from, split,
   cargo class, private max price, priority, must-deliver, auto-rebook limit). Fill it (Jebel Ali → Mumbai,
   1–2 containers, a deadline 3–4 days out) and submit.
2. A text reply says the buyer agent is negotiating; ~15–30 s later an **offers carousel** arrives in the
   same chat. Click **Choose this offer**.
3. A **review card** shows simulated freight and the separate 0.1 testnet FET service fee. Click
   **Confirm and pay**.
4. ASI:One should show a **payment request** (0.1 testnet FET). Approve it with your testnet wallet.
5. Expect a **booking confirmed** card (booking ID, claim code, "testnet FET verified on-chain").

Other checks: `notify test` (background push), `payment test` (please **reject** — smoke only),
`What corridors do you support?` (text fallback).

Already proven without ASI:One (scripts you can re-run):
- `uv run python scripts/card_flow_smoke.py` — whole card journey via the internal API (+ replay/stale/other-buyer refusals).
- `uv run --project gateway python scripts/fet_booking_smoke.py` — real testnet fee → on-chain proof →
  carrier-confirmed booking; replayed commit idempotent; reused tx refused.

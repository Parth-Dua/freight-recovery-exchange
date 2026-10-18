# Critical alerts and current testing steps

Updated as the v2 phases (`docs/specs_2/`) progress. Only things that need **you** are here.

## Status (2026-10-18) — all five phases built and verified without ASI:One; gateway mailbox connected

| Phase | Built + verified (live carriers / Tiger / testnet) | Live ASI:One check (you) |
|---|---|---|
| 1 — uAgent gateway | ✅ Chat + Payment manifests published; mailbox connected (handle `@freight-disruption`) — a separate agent's chat message was delivered via Agentverse and answered with the form card | ⏳ your first ASI:One chat |
| 2 — Interactive Cards | ✅ form → carousel → review → receipt; recovery cards | ⏳ |
| 3 — Fetch FET testnet fee | ✅ real testnet transfers verified on-chain; paid bookings confirmed | ⏳ needs ASI:One wallet (alert 2) |
| 4 — Two-sided negotiation | ✅ 4 carrier agents, ≤2 counters, private policies | ⏳ |
| 5 — Recovery negotiation + 5-stage clearing | ✅ 3/3 recovered incl. must-deliver, approvals, confirmations | ⏳ push cards in a live chat |

## Needs you (in this order)

1. ✅ **Mailbox connected** (you, via the inspector). Handle: `@freight-disruption`. Check in Agentverse that the
   agent's profile shows the README and both protocols. Nothing else to do here.
2. **ASI:One testnet FET wallet** with a little test FET (official Fetch faucet; never mainnet). If ASI:One
   shows no FET payment step or the commit lacks `buyer_fet_wallet`, write that here — it is the documented
   P0 blocker (PRD §7) and I will record it.
3. **Submission (only you can):** Devpost + ASI:One Submission Agent, a 3–5 min video, and a shared ASI:One
   chat URL. README already has the badges, agent addresses and setup.

## Test it (≈5 min)

Services: `./scripts/run_demo.sh` (already running from my session). Gateway:
`agent1q0ktnrhtmerdle6tz9lmdj9v7c0vzrtnl8wdvuthj88cj9ydp7j3vmymxm8`.

**A. Book (ASI:One, new chat with the gateway — `@freight-disruption`):**
1. `@freight-disruption I need to ship cargo` → form card. Jebel Ali → Mumbai, 2 containers, arrive-by 3–4 days out,
   auto-rebook limit e.g. 300. Submit.
2. ~20–40 s later an offers **carousel** (with each carrier's negotiation trace) appears. Choose one.
3. **Review** card → **Confirm and pay** → approve the **0.1 testnet FET** payment → **receipt** card.

**B. Disrupt + recover (same chat stays open):**
4. Withdraw your sailing at http://localhost:8800 (or `uv run python scripts/demo.py withdraw <SAILING>`).
5. Within ~1 min: a **disruption** card, then either a **Rebooked** card or an **approval** card (approve it)
   followed by **Rebooked**. Other buyers on that sailing are recovered in the same joint clearing.

**Reproduce the scripted recovery scenario (no ASI:One needed):**
`uv run python scripts/demo.py reset && uv run python scripts/demo.py seed-v2 && uv run python scripts/demo.py withdraw ATL-101`,
then watch `logs/recovery_worker.log`; `uv run python scripts/demo.py evidence` shows sessions, clearing vs
baseline, holds and lineage. Buyers B and C go to AWAITING_APPROVAL (replacement above their allowance);
approve as them with `uv run python scripts/demo.py approve <BOOKING_ID>` (IDs are in the worker log /
evidence); the worker confirms within seconds → COMPLETED.

Smoke tests: `uv run python scripts/card_flow_smoke.py`, `uv run --project gateway python scripts/fet_booking_smoke.py`.

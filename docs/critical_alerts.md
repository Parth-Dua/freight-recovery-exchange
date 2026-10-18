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
2. ~~ASI:One testnet FET wallet~~ — replaced (your decision): payment is now **Stripe TEST card checkout over the
   Fetch Payment Protocol** (`PAYMENT_MODE=stripe_test`, default). No wallet needed; card `4242 4242 4242 4242`,
   any future expiry, any CVC. FET stays available as `PAYMENT_MODE=fet_testnet`.
3. **Submission (only you can):** Devpost + ASI:One Submission Agent, a 3–5 min video, and a shared ASI:One
   chat URL. README already has the badges, agent addresses and setup.

## Test it — the demo script (≈5 min)

Before each take: `uv run python scripts/demo.py reset && uv run python scripts/demo.py seed` (Buyer B: 1 container on
ATL-101, $300 auto-rebook limit; Buyer C: DHW-571, unaffected). Services: `./scripts/run_demo.sh`.

1. ASI:One, new chat: `@freight-disruption I need to ship cargo` → form: Jebel Ali → Mumbai, **2**, arrive no
   later than **Oct 23** (the date `seed` prints), cargo ready Any time, split **off**, General, budget blank,
   Balanced, must-deliver optional, auto-rebook limit **600**.
2. Carousel: Atlas ATL-101 and Beacon BEA-201 (Cove has 1 slot, Meridian arrives too late). Choose **Atlas ATL-101**.
3. Review → **Confirm and pay** → card `4242 4242 4242 4242` → **Booking confirmed** card.
4. Withdraw **ATL-101** at http://localhost:8800 → ⚠️ SAILING CANCELLATION ALERT card → ~20 s later 🔔 approval
   card: Beacon BEA-201, ≈$6,470 vs what you paid (≈+$1,200, over your $600) → **Approve** → ✅ REBOOKED card.
   Meanwhile Buyer B was auto-rebooked onto Cove COV-301 (+$95, within $300); Buyer C untouched.
   (Auto path instead: set the limit to 2000 → straight to ✅ REBOOKED. Verified: `demo.py rehearse` PASS.)

**Reproduce the scripted recovery scenario (no ASI:One needed):**
`uv run python scripts/demo.py reset && uv run python scripts/demo.py seed-v2 && uv run python scripts/demo.py withdraw ATL-101`,
then watch `logs/recovery_worker.log`; `uv run python scripts/demo.py evidence` shows sessions, clearing vs
baseline, holds and lineage. Buyers B and C go to AWAITING_APPROVAL (replacement above their allowance);
approve as them with `uv run python scripts/demo.py approve <BOOKING_ID>` (IDs are in the worker log /
evidence); the worker confirms within seconds → COMPLETED.

Smoke tests: `uv run python scripts/card_flow_smoke.py --pay` (pays the Stripe TEST link in headless Chrome), `uv run --project gateway python scripts/fet_booking_smoke.py`.

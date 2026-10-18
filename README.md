# Hormuz Exchange — two-sided autonomous freight market (LA Hacks AI 2026)

![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

**Agents negotiate the price, the exchange decides the match, and nobody has to reveal their secrets.**

A cargo buyer ships containers entirely inside **ASI:One**: a native form card collects the shipment, the
buyer's private agent negotiates in parallel with independent **carrier uAgents** (up to two counters each,
without revealing the budget), a carousel shows the final offers, a review card leads to a carrier **hold**
and a **Fetch Payment Protocol** fee in **testnet FET** that is verified on-chain, and the carrier confirms
the booking. When a carrier withdraws a sailing, the exchange negotiates replacements for every stranded
booking at once (60-second upper bound), clears them jointly with a five-stage **OR-Tools** objective,
compares with a first-come-first-served baseline, holds, asks buyers for approval when a replacement costs
more than they allowed, confirms, and pushes the outcome cards into each buyer's existing chat. Everything
is recorded in **Tiger (TimescaleDB)**.

> **Demo market.** Carriers (Atlas, Beacon, Cove, Meridian, Dhow), vessels, sailings, freight prices and
> withdrawals are **fictional simulations**, labelled as such. The only real transfer is a small **testnet**
> FET booking-coordination fee; no real freight is sold, settled or refunded.

## Agents

| Agent | Address | Role |
|---|---|---|
| **Hormuz Exchange buyer gateway** (talk to this one in ASI:One: `@freight-disruption`) | `agent1q0ktnrhtmerdle6tz9lmdj9v7c0vzrtnl8wdvuthj88cj9ydp7j3vmymxm8` | Mailbox uAgent: Chat Protocol + Interactive Cards + seller Payment Protocol (both manifests published) |
| Atlas Lines (fictional) | `agent1qvjf3yf8gnps47a5g6e5ca7ut5g0mul25nnwvvuuc6c64ndyundlz7g7evk` | Carrier uAgent, private `fill_ship` policy |
| Beacon Maritime (fictional) | `agent1q25t3ute3t7q4jnhkxx2p82a5ddkdrlg4wt364xv02e0w9te4360k6jx3tl` | Carrier uAgent, `protect_margin` |
| Cove Shipping (fictional) | `agent1q0d9kttp2thtehadr89n02rque7rga7pqqgpfw4glvq4cczn2dtlgx4grlp` | Carrier uAgent, `balanced` |
| Meridian Container Line (fictional) | `agent1q20dh3l4ahk7fuemhyuaeprdhxxjm06d3uxl22zcj79j6hca9ctvzs8q6dr` | Carrier uAgent, `protect_margin` |
| Dhow Feeder Lines (fictional) | `agent1qvs77v0zayx5dfpvzz0n89a8yztp5p8j92ttmxcj2wlaxh3vn5fz2wjxd0d` | Carrier uAgent, `fill_ship` |
| Legacy Exchange (`@freight-recovery-exc`) | `agent1q2h7svwmef55v9pnjdvuhlnd2t9gr6mc4jyp5w0v0akz7hvkduzh6h4wp52` | Part 1 LangGraph agent; text fallback only |

## How it fits together

```
ASI:One buyer <-> buyer gateway uAgent (gateway/)  -- cards, payment protocol, outbox pushes
                      | token-protected loopback API (src/freight_exchange/exchange_api.py)
                      v
   Exchange backend: cards, buyer agent + negotiation sessions, FET booking, recovery orchestration
        | signed uAgents messages (packages/freight_contracts)        | Tiger (bookings, holds, sessions,
        v                                                             |  payments, market_events hypertable)
   5 carrier uAgents (carriers/, private pricing policy) <-----------+
   carrier console (console/) -> withdrawal -> recovery worker -> negotiate -> OR-Tools (packages/freight_allocation)
```

Specs: `docs/PRD.md` (v2) and `docs/specs_2/`. Progress and evidence: `progress.md`. What needs a human
and current test steps: `docs/critical_alerts.md`.

## Setup (once)

Needs [uv](https://docs.astral.sh/uv/), Python 3.12, a Tiger/TimescaleDB database and an Agentverse account.

```bash
cp .env.example .env     # fill TIGER_DATABASE_URL, PGPASSWORD, AGENT_URI, EXCHANGE_AGENT_ADDRESS (never commit .env)
uv sync && (cd carriers && uv sync) && (cd console && uv sync) && (cd gateway && uv sync)
uv run python scripts/init_carrier_identities.py            # five carrier seeds
uv run --project gateway python scripts/init_gateway_identity.py   # gateway seed + backend token (prints the address)
uv run python scripts/db.py migrate && uv run python scripts/demo.py reset
./scripts/run_demo.sh                                       # carriers, Exchange API, gateway, recovery worker, console
```

Connect the gateway's mailbox once: open the inspector link printed in `logs/gateway.log` and click
**Connect → Mailbox** (or set `AGENTVERSE_API_KEY` in `.env`).

## Use it in ASI:One

1. Start a new chat with the gateway agent (`@freight-disruption`) and say **"I need to ship cargo"** → a form card.
2. Submit it → your buyer agent negotiates with every eligible carrier; a carousel of final offers (with the
   negotiation trace) arrives in the same chat.
3. Choose an offer → review card (simulated freight + `0.1` testnet FET service fee) → **Confirm and pay**
   → the carrier holds the space → approve the FET payment → on-chain verification → booking receipt card.
4. When a carrier withdraws your sailing (console at http://localhost:8800), you get a disruption card, then
   either a confirmed rebooking or an approval card if the replacement costs more than your auto-rebook limit.

## Verify

```bash
uv run pytest -q && (cd carriers && uv run pytest -q) && (cd gateway && uv run pytest -q)
for p in packages/freight_seams packages/freight_disruption packages/freight_allocation console; do (cd $p && uv run pytest -q); done
uv run python scripts/card_flow_smoke.py                    # card journey through the internal API (live carriers)
uv run --project gateway python scripts/fet_booking_smoke.py  # REAL testnet fee -> on-chain proof -> confirmed booking
uv run python scripts/demo.py reset && uv run python scripts/demo.py seed-v2 && uv run python scripts/demo.py withdraw ATL-101
uv run python scripts/demo.py audit && uv run python scripts/demo.py evidence
```

## Requirement status

| Item | Status | Evidence |
|---|---|---|
| Agentverse agent, Chat Protocol, usable in ASI:One | Gateway mailbox connected (`@freight-disruption`), manifests published, Agentverse delivery verified with a separate agent; live ASI:One run pending | `logs/gateway.log`, `docs/critical_alerts.md` |
| Interactive Cards (form, carousel, review, detail) | Built per official schemas; journey verified via the backend | `scripts/card_flow_smoke.py`; live render pending |
| Fetch Payment Protocol (FET testnet) | Seller protocol + on-chain verifier; real testnet transfers verified, paid bookings confirmed | tx `59ED6E70…F02B` → BK-79E93825 (`progress.md`); ASI:One wallet step pending |
| Two-sided negotiation (≤2 counters, private policies/limits) | Live with 4 carrier agents | `negotiation_sessions`, `negotiation_messages`; no private fields in events |
| Recovery negotiation (60 s max) + 5-stage OR-Tools + FCFS baseline | Live: 6 sessions closed early at 20.5 s, 3/3 recovered incl. must-deliver, approvals, confirmations | `rec_f6c589cc58bc`, `CLEARING_DONE` event |
| Tiger hypertable as runtime proof | `market_events` per-minute history | `scripts/demo.py evidence` |

## Limitations

- Simulated market and withdrawals; no real vessels, risk feeds or freight settlement. When a replacement costs more,
  the buyer is asked for the fare difference by card (Stripe TEST) after the carrier confirms; a cheaper replacement is not refunded.
- The buyer agent is a logical per-buyer actor in the backend, not a separately deployed uAgent per buyer.
- uAgents sync replies are matched per agent pair, so the Exchange keeps one message in flight per carrier.
- ASI:One may only show agent-initiated messages inside a conversation the buyer already has open.
- Multi-carrier recovery is a compensating saga (per-leg idempotent holds/confirmations), not a distributed transaction.

## Gotchas

- `AGENT_URI`, `GATEWAY_AGENT_SEED`, `GATEWAY_BACKEND_TOKEN`, carrier seeds, `PGPASSWORD` and Stripe keys are
  secrets; never commit `.env`; check a secret by its length, not its value.
- One machine per agent identity (mailbox mode delivers each message once).
- `freight-contracts` / `freight-seams` are installed non-editable: after editing them run
  `uv sync --reinstall-package freight-contracts` (and in `carriers/`), then restart carriers and the Exchange.
- `FET_USE_TESTNET` must stay `true`; the gateway refuses to start otherwise.

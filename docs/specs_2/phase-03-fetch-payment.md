# Phase 03 — Verified Fetch Payment Protocol for booking-service fee

**Priority:** before new negotiation logic · **Owner:** Parth (all work in this phase)  
**Depends on:** Phase 1 payment seller protocol and Phase 2 review→hold handshake  
**Hard exit gate:** An actual **ASI:One** buyer completes a small **FET stable-testnet** payment, the gateway verifies the real ledger transfer, publishes `CompletePayment`, the booking is confirmed against its hold, and Tiger records the reference/tx **exactly once**. Failed verification cannot book.

## 1. Why this is the simplest supported path

Your current Stripe TEST hosted-checkout link is not Fetch Payment Protocol. Fetch's official Stripe Payment Protocol example does combine ACP messages and Stripe, **but explicitly warns** ordinary ASI:One chat often will not render embedded Stripe checkout. Therefore the P0 path is the **Fetch-documented `fet-example`**: use your uAgent gateway as Payment **seller**, pay tiny testnet FET, query the Fetch ledger via `cosmpy`, and verify proof. This avoids a second payments onboarding integration and preserves the existing invoice/hold business code. The known risk is whether the ASI:One client supplies `buyer_fet_wallet` in `CommitPayment.metadata`—test early and treat it as a non-negotiable gate.

**Official references:**
- Protocol: https://innovationlab.fetch.ai/resources/docs/agent-transaction/agent-payment-protocol
- FET runnable implementation: https://innovationlab.fetch.ai/resources/docs/examples/agent-transaction/fet-image-agent-payment-protocol
- Example source: https://github.com/fetchai/innovation-lab-examples/tree/main/fet-example
- Stripe warning: https://innovationlab.fetch.ai/resources/docs/examples/agent-transaction/stripe-horoscope-payment-protocol
- Contingency USDC/Skyfire: https://innovationlab.fetch.ai/resources/docs/examples/agent-transaction/image-agent-payment-protocol

## 2. Product decision / money disclosure

**What buyer is paying for:** *Demo booking-coordination service*. Buyers are **not** buying real container freight. Their carrier negotiated rate (e.g. simulated `$5,250 USD`) is a contractual **simulation-only** ledger value.

**Primary demo charge:** fixed small FET stable-testnet amount (suggest `0.1 FET`), configurable as `BOOKING_SERVICE_FET_AMOUNT`. Never assert it is equivalent to `$25 USD`. Buyer review should read, for example:

```text
Carrier freight quote: $5,250 USD — simulated, not charged
Hormuz booking coordination service: 0.1 testnet FET — demonstration payment
No real freight is being purchased or settled.
```

The *old* `Stripe TEST: simulated freight + $25 fee` logic remains behind a **legacy development flag**, but must **not** also execute in the same booking as the FET flow. Do not mislabel that old charge as Fetch Payment Protocol.

**Recovery:** No second service fee in the demo. Extra simulated freight within an allowance or separately approved is *not a real FET/USD transfer*. Never assert real refunds, carrier settlement, or $25 collection. Testnet FET is not spendable mainnet FET.

## 3. Exact seller-side payment sequence

1. User confirms **native ASI:One booking review card**; validate current selected finalized offer, ownership and expiry.
2. BookingGraph asks owning carrier for a **real capacity hold**. Store `hold_id`, `hold_expires_at`, `booking_intent_id`; no hold = no payment request.
3. Create a durable `payment_records` row in Tiger (`REQUESTED`) with unique `payment_reference`, exact fee, `network=stable-testnet`, buyer sender and ASI session, booking intent and hold. TTL must be less than the verified remaining hold TTL (account for verification and confirmation); if a hold isn't long enough, extend it through the carrier or re-quote **before** requesting payment.
4. Gateway sends seller `RequestPayment(accepted_funds=[Funds(currency="FET", amount="0.1", payment_method="fet_direct")], recipient=<gateway wallet fetch1...>, deadline_seconds=<bounded>, reference=<payment_reference>, description=<plain fee disclosure>, metadata={"fet_network":"stable-testnet", "service":"booking_coordination"})`.
5. Client asks buyer to approve/reject. A buyer declining sends `RejectPayment`; mark `REJECTED` and release/expire hold. No booking.
6. Client paying sends `CommitPayment` with a transaction ID and buyer wallet metadata (`buyer_fet_wallet` or `buyer_fet_address`). Gateway checks **payment reference**, sender identity, accepted rail, expected amount, expected recipient, chain and tx hash, and verifies the transaction from the Fetch stable testnet via the SDK/`cosmpy` ledger. `CommitPayment` is **claim**, not proof.
7. If proof verifies and intent/hold remain valid, insert immutable tx record + transition to verified idempotently, send `CompletePayment(transaction_id=...)` to the exact sender, and continue the existing carrier confirmation path. Mark booking `COMMITTED` only after carrier's own confirm transaction.
8. If verification fails after `CommitPayment`, respond with **`CancelPayment`** (valid protocol transition), not `RejectPayment`. Keep booking uncommitted, release/expire hold as appropriate and surface a precise failure/status message.
9. If transfer is verified but the hold has expired, **do not discard payment**: record `RECONCILIATION_REQUIRED` with immutable tx proof, do not auto re-charge, do not confirm nonexistent capacity, and explain to buyer. Transaction does not magically reverse on `CancelPayment`.
10. Notify buyer with receipt card referencing booking ID, payment verification reference and clear distinction between simulated freight and actual **testnet** fee.

### Protocol transition constraints

```text
Seller RequestPayment ──> buyer CommitPayment or RejectPayment
CommitPayment ──> seller CompletePayment or CancelPayment
RejectPayment ──> application declined; no charge/booking
CompletePayment ──> business still needs carrier confirmation
```

`CancelPayment` does **not** mean funds were refunded. For invalid/uncertain payment evidence, never manufacture `CompletePayment` to satisfy a demo.

## 4. Exact engineering contracts

**Environment** (all illustrative, put real secrets in `.env` only):

```dotenv
GATEWAY_AGENT_SEED=<unique-private-seed>
FET_USE_TESTNET=true
BOOKING_SERVICE_FET_AMOUNT=0.1
PAYMENT_RAIL=fet_direct
LEGACY_STRIPE_CHECKOUT_ENABLED=false
# If separate services:
GATEWAY_BACKEND_URL=http://exchange-backend:8000
GATEWAY_BACKEND_TOKEN=<private-token>
```

`uagents==0.25.5`, `uagents-core==0.4.9` are the tutorial-tested pair; `cosmpy` is required for on-chain ledger verification. Use gateway's wallet address for `RequestPayment.recipient`, not the arbitrary agent chat address if they differ. Use `NetworkConfig.fetchai_stable_testnet()` and denom `atestfet` from the canonical example; set mainnet switch disabled/hard-fail in demo. **Do not use a float** for converting service amount to token base units; use `Decimal` and exact conversion (verify chain decimals against SDK).

```python
from uagents import Protocol
from uagents_core.contrib.protocols.payment import (
    payment_protocol_spec, Funds, RequestPayment, CommitPayment,
    CompletePayment, CancelPayment, RejectPayment,
)
payment_proto = Protocol(spec=payment_protocol_spec, role="seller")

# @payment_proto.on_message(CommitPayment)
# 1. resolve unique stored payment_reference and authenticated buyer sender
# 2. validate msg.funds/method/network/tx/reference against stored invoice
# 3. verify actual testnet tx from buyer wallet -> gateway wallet
# 4. check txn not previously bound to a different booking
# 5. persist verified result and continue booking idempotently
# 6. send CompletePayment ONLY on real verification
# on rejected/failed verify, use RejectPayment handler / CancelPayment respectively
```

**`payment_records` must store:** `payment_reference` UNIQUE, `booking_intent_id`, `hold_id`, gateway wallet, expected payer address (obtained from metadata), `sender_agent_address`, chat session, method `fet_direct`, chain `stable-testnet`, exact FET amount, issued/deadline times, provider tx ID UNIQUE, verified-at and status, actual checked transfer details, last error, complete-protocol-message sent flag (or outbox event), amount shown to user, and any reconciliation result. Never store a seed/private key in DB or telemetry.

**Verification predicate:** payment row exists, belongs to incoming seller agent/buyer session, and is currently `REQUESTED`; `msg.reference` matches; method/amount/currency match; wallet metadata is present and syntactically valid; ledger transaction is **successful and confirmed**, with `sender=buyer_fet_wallet`, `recipient=gateway_wallet`, correct `atestfet` denomination, `amount >= expected exact units` within intentional payment policy, and a txn identifier not bound to another payment. Recheck active hold and booking intent atomically. Make the verifier an independently unit-testable function with ledger client injected.

**Backend event**: `PAYMENT_VERIFIED` with `{reference, booking_intent_id, provider_tx_id, verified_at, verified_rail}`. Outbox booking continuation must be retry-safe; if process crashes after DB verify and before message send, resume without a second charge.

## 5. Payment smoke + live verification steps

1. Clone `fetchai/innovation-lab-examples/fet-example` for a reference, read its `payment.py`, `agent.py`, and `chat_proto.py`; **do not copy image generation functionality**.
2. Run gateway with unique seed/mailbox; register `payment_protocol_spec` seller role and check manifest in inspector.
3. In an established ASI:One buyer chat, show the **booking review**; confirm; ensure real hold is persisted and `RequestPayment` is sent to that same sender.
4. Observe whether ASI:One renders native FET approve/reject. Fund test wallet from the **official Fetch testnet faucet** linked in the FET example. Stay on testnet, never use mainnet real FET.
5. Approve payment and capture a **real** `CommitPayment` (inspect sender, `reference`, `metadata.buyer_fet_wallet`/`buyer_fet_address`, and tx hash). If the wallet metadata is absent, stop and treat P0 verification as blocked—not paid.
6. Query confirmed on-chain transfer and verify sender/recipient/amount/denomination; check `CompletePayment`; check Tiger `payment_records` and confirmed booking.
7. Replay the same commit: there must be no second booking or extra service fee. Send intentionally invalid tx in test harness: no booking.
8. Save logs/screenshot of ASI:One and testnet evidence with secrets redacted.

**Contingency if ASI:One FET metadata/UI fails:** trial documented Skyfire USDC seller path, with service id, API key/JWT verification and **real** provider charge. This alternative is only accepted after an end-to-end ASI:One pay verification test; it may involve real tokens/charges depending on configuration, so disclose and avoid production-money rails in demo. **Do not swap to Stripe embedded inspector-only and claim the primary workflow finished in ASI:One.**

## 6. Negative test matrix

| Scenario | Expected |
|---|---|
| `RequestPayment` issued without hold | Forbidden |
| User presses reject | Holds released/expired; no charge or booking |
| `CommitPayment` lacks `buyer_fet_wallet` | `CancelPayment` / verification failure; not booked |
| Tx hash malformed / not found / wrong network | Not booked |
| Tx sender or recipient differs | Not booked |
| Amount below expected; wrong currency; wrong `payment_method` | Not booked |
| Valid tx hash reused for another booking | Unique conflict; no second booking |
| Duplicate `CommitPayment` for same reference | Same verified transaction, idempotent response |
| Payment succeeds after hold expiry | Reconciliation, not fake booking |
| Payment verified but carrier rejects confirmation | Payment reconciliation, not auto-refunded |
| Gateway restart between verify and Complete | Rehydrate record/outbox; no new charge |
| Pending payment while sailing withdrawn | Prevent invalid confirmation; truthful status/reconciliation |
| FET mainnet flag accidentally set | Demo startup fails closed |
| Legacy Stripe checkout and FET both requested | Mutually exclusive payment modes, fail test |
| ASI:One doesn't render payment | Exit gate FAIL even if inspector works |

## 7. Phase exit

- [ ] One native ASI:One `RequestPayment`/`CommitPayment`/`CompletePayment` round trip.
- [ ] Wallet metadata and **confirmed** testnet chain tx independently verified.
- [ ] Carrier hold before charge; carrier confirmation after charge; receipt after actual business commit.
- [ ] Failed/late payments and replayed txs cannot generate confirmed bookings.
- [ ] Tiger records and market events are accurate, secrets not logged.
- [ ] Existing nonpayment booking/recovery regression suite green.

**Explicitly deferred:** USD carrier settlement, refunds and reconciliation automation, multiple payment rails in production, pricing dashboard, separate account wallets, chargebacks, real freight fee conversion.

# Phase 01 — Mailbox uAgent gateway and protocol proof

**Priority:** first · **Owner:** Parth (all work in this phase)  
**Depends on:** Part 1 Tiger, LangGraph API, Agentverse access  
**Definition of done:** ASI:One actually renders one form card and returns the input to the **new** gateway; a Payment Protocol request reaches a supported client and the seller handlers accept a reject/test callback; old carrier messaging and booking services still run.

## 1. Why this phase exists

Direct Agentverse/LangGraph SDK currently carries text but drops card metadata, and it cannot handle Fetch seller Payment Protocol messages in your current setup. The simplest integration is **one thin `uagents.Agent` with `mailbox=True`**, publishing **both** ACP Chat and Agent Payment Protocol manifests, and forwarding *only validated UI/business intents* to LangGraph. There is no need to introduce tunnel callbacks or patch the SDK's card serializer. The gateway is the **only** agent ASI:One buyers interact with; old LangGraph identity becomes backend/legacy, not a second buyer entrypoint.

Official dependencies: https://innovationlab.fetch.ai/resources/docs/interactive-cards/asi-interactive-cards and https://innovationlab.fetch.ai/resources/docs/agent-transaction/agent-payment-protocol .

## 2. Deliverables

- [ ] Dedicated gateway Python environment; pin compatible `uagents==0.25.5`, `uagents-core==0.4.9` (official examples' tested pair; adjust **only if** installed platform pins require it). Separate this environment from existing carrier protobuf-5 environment if needed.
- [ ] Stable unique `GATEWAY_AGENT_SEED`, `AGENT_NAME` and Agentverse mailbox connection; never commit seed.
- [ ] One agent publishes `AgentChatProtocol` via `chat_protocol_spec` and `AgentPaymentProtocol` seller role via `payment_protocol_spec` with `publish_manifest=True`.
- [ ] `ChatMessage` handler sends `ChatAcknowledgement`, reads `TextContent`, handles `StartSessionContent`/`EndSessionContent` as appropriate, dedupes by sender and `msg_id` and does **not** reply recursively to another agent's acknowledgements.
- [ ] Hard-coded **valid ASI:One form card** appears for “I need to ship cargo”; submitted action is logged as a structured command and comes back from the user into the **same live chat**. Demonstrate both direct `@mention` JSON and a planner-mediated text selection if planner path is part of final demo.
- [ ] Gateway POSTs a user-intent envelope to the existing backend and returns a real backend response (e.g., list of demo corridors); business logic remains in existing graph.
- [ ] Seller `RequestPayment` smoke test with `RejectPayment` receiver and `CommitPayment` receiver; do not mark **paid** in this phase, and do not send `CompletePayment` for unverified commitments.
- [ ] Correlation table/binding `sender_agent_address + actual_chat_session + gateway_address → internal buyer_id`; existing chat session recorded for later notifications.
- [ ] A basic authenticated, idempotent backend→gateway notification path tested in an **existing buyer chat** (not a fabricated fresh one).

## 3. Recommended code organization (suggestions, adjust to existing repo)

```text
apps/buyer_gateway/
  agent.py              # create Agent(mailbox=True), register protocols
  chat_protocol.py      # inbound ChatMessage / Ack, send_text, send_card
  payment_protocol.py   # seller protocol handlers only; verifier in Phase 3
  card_wire.py          # MetadataContent helper, serializes payload
  backend_client.py     # localhost/internal authenticated requests
  session_store.py      # DB-backed identity/session binding
services/exchange_api/   # existing service + thin internal routes
```

Do **not** move `BookingGraph`, recovery worker, Tiger operations or carrier signed messaging inside gateway code. One package may expose `CardSeam`, but it must return a protocol-neutral `{card_kind, payload, narration}` for the gateway to send.

## 4. Exact Chat/Card wire template

```python
import json
from datetime import datetime, timezone
from uuid import uuid4
from uagents import Agent, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatMessage, ChatAcknowledgement, TextContent, MetadataContent,
    chat_protocol_spec,
)
from uagents_core.contrib.protocols.payment import payment_protocol_spec

agent = Agent(name="HormuzBuyerGateway", seed=GATEWAY_AGENT_SEED, mailbox=True)
chat_proto = Protocol(spec=chat_protocol_spec)
payment_proto = Protocol(spec=payment_protocol_spec, role="seller")

def card_message(text: str, kind: str, payload: dict) -> ChatMessage:
    return ChatMessage(timestamp=datetime.now(timezone.utc), msg_id=uuid4(), content=[
        TextContent(type="text", text=text),
        MetadataContent(type="metadata", metadata={
            "card_protocol_version": "1",
            "requires_card_interaction": "true",
            "card_kind": kind,
            "card_payload": json.dumps(payload),
        }),
    ])

# Register @chat_proto.on_message(ChatMessage), ...payment handlers, etc.
agent.include(chat_proto, publish_manifest=True)
agent.include(payment_proto, publish_manifest=True)
```

This is a **wire-format reference**, not a complete runnable service: the handlers, auth, session identity, storage and runtime configuration must be implemented. `MetadataContent.metadata` values are **strings**, nested payload is serialized JSON. Do not wrap `card_payload` inside an extra `{ "data": ... }` envelope.

**Minimal form payload (valid card schema):**

```json
{
  "title": "Tell us about your cargo",
  "fields": [
    {"name":"origin_port", "kind":"select", "label":"Origin", "required":true,
     "options":[{"label":"Jebel Ali", "value":"AEJEA"}]},
    {"name":"destination_port", "kind":"select", "label":"Destination", "required":true,
     "options":[{"label":"Mumbai", "value":"INBOM"}]},
    {"name":"quantity", "kind":"number", "label":"20GP containers", "required":true}
  ],
  "submit_cta": {"label":"Find sailings", "selection":{"action":"submit_cargo_request", "card_version":"1"}}
}
```

## 5. Gateway/backend contracts

**Inbound message** (build `buyer_id` exclusively from verified gateway's sender/session binding):

```json
{
  "event_id": "incoming-message-uuid",
  "sender_agent_address": "agent1q...",
  "chat_session_id": "actual_context_session",
  "gateway_agent_address": "agent1q...",
  "kind": "CHAT_TEXT_OR_CARD_ACTION",
  "payload": {"action": "submit_cargo_request", "origin_port": "AEJEA", "destination_port": "INBOM", "quantity": 2}
}
```

**Backend result**: `{"kind":"CARD", "narration":"...", "card_kind":"form", "card_payload":{...}}` or `{"kind":"TEXT","text":"..."}` or `{"kind":"PENDING","correlation_id":"..."}`. `PENDING` means negotiation/background work proceeds outside the chat handler; write follow-up to the outbox and push into the same buyer session when complete.

**Security:** accept gateway requests only over private interface with shared secret or equivalent service identity. Never infer `buyer_id` from user-submitted form. Retry idempotently with stable `event_id`; no raw private policy data should be returned for display. Store session recipient and sender address separately from protocol metadata.

## 6. Test scenarios

| Test | Expected |
|---|---|
| Public Agentverse discoverability | Gateway address/protocol manifest appears and is routable in ASI:One |
| Buyer says “ship cargo” | Native form appears in ASI:One, not just explanatory text |
| Direct @mention submit | `TextContent` holds selection JSON; gateway normalizes action |
| Planner-mediated submit | Normalize narrated action; if ambiguous, ask instead of guessing |
| Wrong card metadata/payload | Local schema test fails; correct text fallback, no state change |
| Duplicate `msg_id` | Backend sees one logical command |
| Handler receives Ack | Does not reply with another ChatMessage |
| Backend temporarily unavailable | User sees pending/retry; no fake booking |
| Prior user chat session | Gateway can send follow-up into existing session, tested live |
| Payment smoke | `RequestPayment` reaches client; supported `RejectPayment` causes no booking; `CommitPayment` without verification cannot complete |
| Carrier 1→carrier 2 existing signed channel | Still functions independently of new gateway |

## 7. Implementation sequence (small commits)

1. Gateway venv + seed + stable Agentverse identity; include/publish both protocol manifests.
2. Ack-safe chat handler + simple native form (hard-coded), verify in ASI:One **before** deeper refactoring.
3. Parse returned card text; create/confirm session binding and backend HTTP message.
4. Add background delivery/outbox connection, prove it in live established gateway chat.
5. Add Payment Protocol seller handlers (smoke, no final payment verification yet); capture supported ASI:One UI response and environment constraints.
6. Add protocol/identity tests and demo screenshot/log evidence.

## 8. Failure policy / exit gate

**Stop and correct transport if form metadata is silently dropped.** Do not bypass this by rendering a React form outside ASI:One or by claiming the fallback text proves Cards. If `RequestPayment` is not rendered in ASI:One, preserve protocol capability but mark native payment UX **unverified**; Phase 3 must resolve it (try documented FET testnet first, Skyfire only if proven). A Stripe link is not evidence that Fetch Payment Protocol works.

**Exit evidence:** screenshot of actual ASI:One form; captured returned command + Tiger-backed backend request; gateway Agentverse profile; published chat/payment manifests; payment request and reject smoke logs; green transport tests.

**Out of scope:** complete UX cards, actual chain verification, carrier policies, negotiation engine, recovery optimization, carrier dashboard redesign.

-- 008 v2 phase 1: mailbox uAgent gateway (chat bindings, idempotent events, outbox) and the
-- Fetch Payment Protocol columns used from phase 3. Additive only.

-- Who is talking to us, in which ASI:One conversation. buyer_id is derived here, never from a card.
CREATE TABLE chat_bindings (
    sender_agent_address   TEXT NOT NULL,
    chat_session_id        TEXT NOT NULL,
    gateway_agent_address  TEXT NOT NULL,
    buyer_id               TEXT NOT NULL,
    first_seen             TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen              TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (sender_agent_address, chat_session_id)
);
CREATE INDEX chat_bindings_buyer_idx ON chat_bindings (buyer_id, last_seen DESC);

-- One logical command per incoming chat message (duplicate deliveries replay the stored answer).
CREATE TABLE gateway_events (
    event_id               TEXT PRIMARY KEY,
    sender_agent_address   TEXT NOT NULL,
    chat_session_id        TEXT NOT NULL,
    status                 TEXT NOT NULL CHECK (status IN ('PENDING', 'DONE')),
    response               JSONB,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at           TIMESTAMPTZ
);

-- Durable pushes into an existing buyer conversation (negotiation results, recovery notices).
CREATE TABLE chat_outbox (
    outbox_id              BIGSERIAL PRIMARY KEY,
    event_key              TEXT NOT NULL UNIQUE,
    buyer_id               TEXT NOT NULL,
    recipient              TEXT NOT NULL,
    chat_session_id        TEXT NOT NULL,
    gateway_agent_address  TEXT NOT NULL,
    payload                JSONB NOT NULL,
    status                 TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'SENT', 'FAILED')),
    attempts               INTEGER NOT NULL DEFAULT 0,
    last_error             TEXT,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at                TIMESTAMPTZ
);
CREATE INDEX chat_outbox_pending_idx ON chat_outbox (gateway_agent_address, outbox_id) WHERE status = 'PENDING';

-- Fetch Payment Protocol (FET stable-testnet) on the existing payment ledger.
ALTER TABLE payment_records ADD COLUMN method TEXT;
ALTER TABLE payment_records ADD COLUMN network TEXT;
ALTER TABLE payment_records ADD COLUMN funds_amount TEXT;          -- exact decimal as shown to the buyer
ALTER TABLE payment_records ADD COLUMN funds_currency TEXT;
ALTER TABLE payment_records ADD COLUMN provider_tx_id TEXT;
ALTER TABLE payment_records ADD COLUMN provider_verified_at TIMESTAMPTZ;
ALTER TABLE payment_records ADD COLUMN sender_agent_address TEXT;
ALTER TABLE payment_records ADD COLUMN chat_session_id TEXT;
ALTER TABLE payment_records ADD COLUMN gateway_wallet TEXT;
ALTER TABLE payment_records ADD COLUMN buyer_wallet TEXT;
ALTER TABLE payment_records ADD COLUMN hold_id TEXT;
ALTER TABLE payment_records ADD COLUMN deadline_at TIMESTAMPTZ;
ALTER TABLE payment_records ADD COLUMN last_error TEXT;
ALTER TABLE payment_records ADD COLUMN complete_sent BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE payment_records ADD COLUMN verification JSONB;       -- checked transfer details (no secrets)
CREATE UNIQUE INDEX payment_records_provider_tx ON payment_records (provider_tx_id) WHERE provider_tx_id IS NOT NULL;
ALTER TABLE payment_records DROP CONSTRAINT payment_records_status_check;
ALTER TABLE payment_records ADD CONSTRAINT payment_records_status_check
    CHECK (status IN ('REQUESTED', 'VERIFIED', 'FAILED', 'EXPIRED',
                      'PAYMENT_PENDING', 'PAYMENT_VERIFIED_TEST', 'PAYMENT_FAILED', 'PAYMENT_RECONCILIATION_REQUIRED',
                      'COMMIT_RECEIVED', 'VERIFICATION_FAILED', 'REJECTED', 'CANCELLED', 'RECONCILIATION_REQUIRED'));

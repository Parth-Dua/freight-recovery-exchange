-- 009 v2 phase 2: buyer card journey. Additive only.

-- Public cargo fields from the setup form, plus buyer-private settings that never go to carriers
-- (max_total_price_minor already exists; it is kept server-side from now on).
ALTER TABLE cargo_requests ADD COLUMN cargo_class TEXT NOT NULL DEFAULT 'general'
    CHECK (cargo_class IN ('general', 'high_value', 'hazardous'));
ALTER TABLE cargo_requests ADD COLUMN priority TEXT NOT NULL DEFAULT 'balanced'
    CHECK (priority IN ('cheapest', 'balanced', 'fastest'));
ALTER TABLE cargo_requests ADD COLUMN must_deliver BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE cargo_requests ADD COLUMN recovery_allowance_minor BIGINT NOT NULL DEFAULT 0
    CHECK (recovery_allowance_minor >= 0);
ALTER TABLE cargo_requests ADD COLUMN gateway_agent_address TEXT;
ALTER TABLE bookings ADD COLUMN must_deliver BOOLEAN NOT NULL DEFAULT FALSE;

-- Every actionable card we send. A CTA is honoured only if its card is ACTIVE and belongs to the
-- buyer clicking it; using it marks it USED, so stale or replayed clicks change nothing.
CREATE TABLE card_instances (
    card_instance_id  TEXT PRIMARY KEY,
    buyer_id          TEXT NOT NULL,
    chat_session_id   TEXT NOT NULL,
    card_kind         TEXT NOT NULL,
    purpose           TEXT NOT NULL,          -- cargo_form | offer_carousel | booking_review | recovery_approval
    resource_id       TEXT,                   -- request_id / approval_id the card is about
    status            TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'USED', 'SUPERSEDED')),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    used_at           TIMESTAMPTZ
);
CREATE INDEX card_instances_buyer_idx ON card_instances (buyer_id, purpose, created_at DESC);

-- booking_intents for the Fetch fee path: the FET fee is not a USD amount (fee_minor stays 0).
ALTER TABLE booking_intents ADD COLUMN payment_mode TEXT NOT NULL DEFAULT 'stripe_test'
    CHECK (payment_mode IN ('stripe_test', 'fet_testnet', 'dev'));

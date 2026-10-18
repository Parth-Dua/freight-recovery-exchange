-- 006 Phase 6: one-shot recovery offers and persisted proposals. Additive only:
-- nothing writes recovery_runs before this phase, and every new column is nullable or defaulted.

-- Recovery runs: one per withdrawal (withdrawal_event_id is already UNIQUE), spec 07 §5 states.
ALTER TABLE recovery_runs ADD COLUMN sailing_id TEXT REFERENCES sailings(sailing_id);
ALTER TABLE recovery_runs ADD COLUMN correlation_id TEXT;
ALTER TABLE recovery_runs ADD COLUMN input_version TEXT;   -- AllocationSeam fingerprint of what was decided on
ALTER TABLE recovery_runs ADD COLUMN proposal JSONB;       -- stranded snapshot + RecoveryProposal, never a booking
ALTER TABLE recovery_runs ADD COLUMN detail TEXT;
ALTER TABLE recovery_runs ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE recovery_runs ADD CONSTRAINT recovery_runs_status_check
    CHECK (status IN ('CREATED', 'OFFERS_REQUESTED', 'PROPOSED', 'HOLDING', 'AWAITING_APPROVAL', 'CONFIRMING',
                      'COMPLETED', 'PARTIAL', 'FAILED', 'RECONCILIATION_REQUIRED'));

-- Every recovery offer a carrier sent, with the Exchange's message-level verdict.
-- No FK on sailing_id: an offer naming an unknown sailing is stored as REJECTED evidence.
CREATE TABLE recovery_offers (
    offer_id              TEXT PRIMARY KEY,
    recovery_id           TEXT NOT NULL REFERENCES recovery_runs(recovery_id),
    message_id            TEXT NOT NULL,
    carrier_id            TEXT NOT NULL REFERENCES carriers(carrier_id),
    sailing_id            TEXT NOT NULL,
    container_type        TEXT NOT NULL,
    capacity_units        INTEGER NOT NULL CHECK (capacity_units >= 1),
    total_price_minor     BIGINT NOT NULL CHECK (total_price_minor > 0),
    currency              TEXT NOT NULL,
    departure_at          TIMESTAMPTZ NOT NULL,
    arrival_at            TIMESTAMPTZ NOT NULL,
    expires_at            TIMESTAMPTZ NOT NULL,
    eligible_booking_ids  TEXT[] NOT NULL,
    status                TEXT NOT NULL CHECK (status IN ('VALID', 'REJECTED')),
    rejection_code        TEXT,
    rejection_detail      TEXT,
    carrier_signature     TEXT NOT NULL,
    received_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX recovery_offers_run_idx ON recovery_offers (recovery_id);

-- Carrier-owned offer log: recovery offers are logged too, and are one-shot (never countered).
ALTER TABLE carrier_offer_log ADD COLUMN offer_kind TEXT NOT NULL DEFAULT 'quote'
    CHECK (offer_kind IN ('quote', 'recovery'));

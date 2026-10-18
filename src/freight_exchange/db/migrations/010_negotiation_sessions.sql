-- 010 v2 phase 4: durable two-sided negotiation sessions. Additive only.

-- Carrier offer log: a negotiation can now reach round 3 (opening + two buyer counters).
ALTER TABLE carrier_offer_log DROP CONSTRAINT carrier_offer_log_round_check;
ALTER TABLE carrier_offer_log ADD CONSTRAINT carrier_offer_log_round_check CHECK (round IN (1, 2, 3));

-- One session per buyer x carrier (normal booking) or displaced booking x carrier (recovery).
-- session_id is the negotiation_id carried in every signed message of that session.
CREATE TABLE negotiation_sessions (
    session_id             TEXT PRIMARY KEY,
    buyer_agent_key        TEXT NOT NULL,          -- isolated logical buyer agent (per buyer)
    carrier_id             TEXT NOT NULL REFERENCES carriers(carrier_id),
    carrier_agent_address  TEXT NOT NULL,
    request_id             TEXT REFERENCES cargo_requests(request_id),
    booking_id             TEXT REFERENCES bookings(booking_id),
    recovery_id            TEXT REFERENCES recovery_runs(recovery_id),
    kind                   TEXT NOT NULL CHECK (kind IN ('booking', 'recovery')),
    round                  INTEGER NOT NULL DEFAULT 0 CHECK (round BETWEEN 0 AND 2),
    rounds_max             INTEGER NOT NULL DEFAULT 2 CHECK (rounds_max BETWEEN 0 AND 2),
    status                 TEXT NOT NULL DEFAULT 'OPEN'
                           CHECK (status IN ('OPEN', 'ACCEPTED', 'REJECTED', 'EXHAUSTED', 'EXPIRED')),
    opened_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at             TIMESTAMPTZ NOT NULL,
    closed_at              TIMESTAMPTZ,
    final_offer_id         TEXT,                   -- offers.offer_id (booking) or recovery_offers.offer_id (recovery)
    final_total_minor      BIGINT,
    reason                 TEXT
);
CREATE UNIQUE INDEX negotiation_sessions_booking_pair ON negotiation_sessions (request_id, carrier_id)
    WHERE kind = 'booking';
CREATE UNIQUE INDEX negotiation_sessions_recovery_pair ON negotiation_sessions (recovery_id, booking_id, carrier_id)
    WHERE kind = 'recovery';
CREATE INDEX negotiation_sessions_recovery_idx ON negotiation_sessions (recovery_id, status);

-- Public negotiation trace (prices offered/countered are public terms; floors and budgets never appear).
CREATE TABLE negotiation_messages (
    session_id      TEXT NOT NULL REFERENCES negotiation_sessions(session_id),
    message_id      TEXT NOT NULL,
    sender_role     TEXT NOT NULL CHECK (sender_role IN ('buyer_agent', 'carrier')),
    sender_address  TEXT NOT NULL,
    round           INTEGER NOT NULL,
    message_type    TEXT NOT NULL,
    public_terms    JSONB NOT NULL DEFAULT '{}'::jsonb,
    ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (sender_address, message_id)
);
CREATE INDEX negotiation_messages_session_idx ON negotiation_messages (session_id, round);

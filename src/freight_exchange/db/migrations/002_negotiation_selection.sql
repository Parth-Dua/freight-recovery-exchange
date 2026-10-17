-- 002 Phase 3: bounded negotiation, offer selection, chat-turn idempotency.

-- Requests remember the chat session so "select 2" resolves to this buyer's latest quotes.
ALTER TABLE cargo_requests ADD COLUMN session_id TEXT;
CREATE INDEX cargo_requests_session_idx ON cargo_requests (session_id, created_at DESC);
ALTER TABLE cargo_requests DROP CONSTRAINT cargo_requests_status_check;
ALTER TABLE cargo_requests ADD CONSTRAINT cargo_requests_status_check
    CHECK (status IN ('OPEN', 'QUOTED', 'NO_OFFERS', 'OFFER_SELECTED', 'BOOKED', 'CANCELLED', 'EXPIRED'));

-- Offers: a counter outcome is a new offer row that supersedes the opening offer.
ALTER TABLE offers ADD COLUMN supersedes_offer_id TEXT REFERENCES offers(offer_id);
ALTER TABLE offers ADD COLUMN display_rank INTEGER CHECK (display_rank >= 1);
ALTER TABLE offers DROP CONSTRAINT offers_status_check;
ALTER TABLE offers ADD CONSTRAINT offers_status_check
    CHECK (status IN ('PROPOSED', 'SELECTED', 'EXPIRED', 'ACCEPTED', 'REJECTED', 'SUPERSEDED'));
CREATE UNIQUE INDEX offers_one_successor ON offers (supersedes_offer_id) WHERE supersedes_offer_id IS NOT NULL;
CREATE UNIQUE INDEX offers_request_rank ON offers (request_id, display_rank) WHERE display_rank IS NOT NULL;

-- Buyer selection: at most one per request; selecting is NOT a hold or a booking.
CREATE TABLE offer_selections (
    request_id   TEXT PRIMARY KEY REFERENCES cargo_requests(request_id),
    offer_id     TEXT NOT NULL UNIQUE REFERENCES offers(offer_id),
    session_id   TEXT,
    source       TEXT NOT NULL,   -- e.g. 'chat:rank', 'chat:offer_id', 'chat:carrier', 'card'
    selected_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Carrier-owned record of offers it issued (written only by the carrier agent).
-- counters_used <= 1 enforces "at most one counter per offer" in the database itself.
CREATE TABLE carrier_offer_log (
    offer_id           TEXT PRIMARY KEY,
    carrier_id         TEXT NOT NULL REFERENCES carriers(carrier_id),
    negotiation_id     TEXT NOT NULL,
    request_id         TEXT NOT NULL,
    sailing_id         TEXT NOT NULL,
    container_type     TEXT NOT NULL,
    quantity           INTEGER NOT NULL CHECK (quantity >= 1),
    total_price_minor  BIGINT NOT NULL CHECK (total_price_minor > 0),
    currency           TEXT NOT NULL,
    round              INTEGER NOT NULL CHECK (round IN (1, 2)),
    expires_at         TIMESTAMPTZ NOT NULL,
    counters_used      INTEGER NOT NULL DEFAULT 0 CHECK (counters_used BETWEEN 0 AND 1),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (sailing_id, carrier_id) REFERENCES sailings(sailing_id, carrier_id)
);
CREATE INDEX carrier_offer_log_neg_idx ON carrier_offer_log (carrier_id, negotiation_id);

-- Chat-turn idempotency: ASI:One can deliver the same message twice.
CREATE TABLE chat_turns (
    turn_key      TEXT PRIMARY KEY,          -- sha256(session | normalized text)
    session_id    TEXT NOT NULL,
    request_ref   TEXT NOT NULL,             -- the Ref shown to the user
    status        TEXT NOT NULL CHECK (status IN ('PENDING', 'DONE')),
    route         TEXT,
    reply         TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at  TIMESTAMPTZ
);

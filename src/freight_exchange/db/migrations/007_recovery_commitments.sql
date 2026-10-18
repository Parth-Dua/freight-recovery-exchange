-- 007 Phase 7: recovery holds, confirmations and booking lineage. Additive only.

-- Bookings: a replacement points at the booking it replaces; the original becomes SUPERSEDED
-- (never deleted). spec 07 §5.
ALTER TABLE bookings ADD COLUMN replaces_booking_id TEXT REFERENCES bookings(booking_id);
ALTER TABLE bookings DROP CONSTRAINT bookings_status_check;
ALTER TABLE bookings ADD CONSTRAINT bookings_status_check
    CHECK (status IN ('PENDING_PAYMENT', 'COMMITTED', 'DISRUPTED', 'RECOVERED', 'CANCELLED', 'SUPERSEDED'));
CREATE INDEX bookings_replaces_idx ON bookings (replaces_booking_id) WHERE replaces_booking_id IS NOT NULL;

-- Holds against a one-shot recovery offer (carrier-owned rows). The carrier caps the units held
-- under one recovery offer at the capacity it offered.
ALTER TABLE holds ADD COLUMN recovery_offer_id TEXT REFERENCES carrier_offer_log(offer_id);
CREATE INDEX holds_recovery_offer_idx ON holds (recovery_offer_id) WHERE recovery_offer_id IS NOT NULL;

-- Recovery runs: at most one fresh re-solve after a failed hold (attempt 2).
ALTER TABLE recovery_runs ADD COLUMN attempts INTEGER NOT NULL DEFAULT 1 CHECK (attempts BETWEEN 1 AND 2);

-- One row per proposed assignment ("leg"); a split booking has several legs.
-- spec 07 §5 assignment states: PROPOSED | HELD | CONFIRMED | RELEASED | FAILED.
CREATE TABLE recovery_legs (
    recovery_id             TEXT NOT NULL REFERENCES recovery_runs(recovery_id),
    attempt                 INTEGER NOT NULL CHECK (attempt BETWEEN 1 AND 2),
    booking_id              TEXT NOT NULL REFERENCES bookings(booking_id),
    recovery_offer_id       TEXT NOT NULL REFERENCES recovery_offers(offer_id),
    offer_id                TEXT NOT NULL REFERENCES offers(offer_id),   -- per-booking offer row
    carrier_id              TEXT NOT NULL REFERENCES carriers(carrier_id),
    sailing_id              TEXT NOT NULL REFERENCES sailings(sailing_id),
    quantity                INTEGER NOT NULL CHECK (quantity >= 1),
    quoted_price_minor      BIGINT NOT NULL CHECK (quoted_price_minor > 0),
    currency                TEXT NOT NULL,
    arrival_at              TIMESTAMPTZ NOT NULL,
    needs_approval          BOOLEAN NOT NULL DEFAULT FALSE,
    approval_id             TEXT,
    status                  TEXT NOT NULL CHECK (status IN ('PROPOSED', 'HELD', 'CONFIRMED', 'RELEASED', 'FAILED')),
    hold_id                 TEXT REFERENCES holds(hold_id),
    hold_expires_at         TIMESTAMPTZ,
    replacement_booking_id  TEXT REFERENCES bookings(booking_id),
    detail                  TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (recovery_id, attempt, booking_id, recovery_offer_id)
);
CREATE UNIQUE INDEX recovery_legs_one_replacement ON recovery_legs (replacement_booking_id)
    WHERE replacement_booking_id IS NOT NULL;

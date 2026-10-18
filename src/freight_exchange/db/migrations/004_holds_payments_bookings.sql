-- 004 Phase 4: carrier holds, Stripe TEST payments, booking intents. Additive only:
-- every new column is nullable so existing writers (console preseed/withdrawal) keep working.

-- Holds: what exactly is held (carrier-owned rows; written only by the carrier agent).
ALTER TABLE holds ADD COLUMN request_id TEXT REFERENCES cargo_requests(request_id);
ALTER TABLE holds ADD COLUMN sailing_id TEXT REFERENCES sailings(sailing_id);
ALTER TABLE holds ADD COLUMN container_type TEXT;
ALTER TABLE holds ADD COLUMN quantity INTEGER CHECK (quantity >= 1);
ALTER TABLE holds ADD COLUMN released_at TIMESTAMPTZ;
CREATE INDEX holds_active_expiry_idx ON holds (sailing_id, expires_at) WHERE status = 'ACTIVE';

-- Bookings: link to the intent/payment and a hashed claim code for lookups outside the chat.
ALTER TABLE bookings ADD COLUMN hold_id TEXT REFERENCES holds(hold_id);
ALTER TABLE bookings ADD COLUMN intent_id TEXT;
ALTER TABLE bookings ADD COLUMN payment_reference TEXT;
ALTER TABLE bookings ADD COLUMN claim_code_hash TEXT;
ALTER TABLE bookings ADD COLUMN payment_terms TEXT;
ALTER TABLE bookings ADD COLUMN confirmed_at TIMESTAMPTZ;
CREATE UNIQUE INDEX bookings_one_per_hold ON bookings (hold_id) WHERE hold_id IS NOT NULL;

-- Payments: spec 04 status vocabulary; Stripe test only.
ALTER TABLE payment_records DROP CONSTRAINT payment_records_status_check;
ALTER TABLE payment_records ADD CONSTRAINT payment_records_status_check
    CHECK (status IN ('REQUESTED', 'VERIFIED', 'FAILED', 'EXPIRED',
                      'PAYMENT_PENDING', 'PAYMENT_VERIFIED_TEST', 'PAYMENT_FAILED',
                      'PAYMENT_RECONCILIATION_REQUIRED'));
ALTER TABLE payment_records ADD COLUMN intent_id TEXT;
ALTER TABLE payment_records ADD COLUMN livemode BOOLEAN;
ALTER TABLE payment_records ADD COLUMN freight_minor BIGINT CHECK (freight_minor >= 0);
ALTER TABLE payment_records ADD COLUMN fee_minor BIGINT CHECK (fee_minor >= 0);
ALTER TABLE payment_records ADD COLUMN checkout_url TEXT;
ALTER TABLE payment_records ADD COLUMN provider_status TEXT;
ALTER TABLE payment_records ADD COLUMN updated_at TIMESTAMPTZ;

-- Booking intents: the Exchange-side state machine from "book" to receipt.
CREATE TABLE booking_intents (
    intent_id        TEXT PRIMARY KEY,
    request_id       TEXT NOT NULL REFERENCES cargo_requests(request_id),
    offer_id         TEXT NOT NULL REFERENCES offers(offer_id),
    buyer_id         TEXT NOT NULL,
    session_id       TEXT,
    carrier_id       TEXT NOT NULL REFERENCES carriers(carrier_id),
    hold_id          TEXT REFERENCES holds(hold_id),
    payment_id       TEXT REFERENCES payment_records(payment_id),
    booking_id       TEXT REFERENCES bookings(booking_id),
    status           TEXT NOT NULL CHECK (status IN (
                         'HOLD_REQUESTED', 'HOLD_REJECTED', 'PAYMENT_PENDING', 'PAYMENT_VERIFIED_TEST',
                         'BOOKING_CONFIRMED', 'BOOKING_PENDING', 'PAYMENT_FAILED',
                         'PAYMENT_RECONCILIATION_REQUIRED', 'RELEASED', 'EXPIRED')),
    freight_minor    BIGINT NOT NULL CHECK (freight_minor > 0),
    fee_minor        BIGINT NOT NULL CHECK (fee_minor >= 0),
    currency         TEXT NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    hold_expires_at  TIMESTAMPTZ,
    detail           TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- At most one live intent per request (re-booking after a terminal failure is allowed).
CREATE UNIQUE INDEX booking_intents_one_live ON booking_intents (request_id)
    WHERE status IN ('HOLD_REQUESTED', 'PAYMENT_PENDING', 'PAYMENT_VERIFIED_TEST', 'BOOKING_PENDING',
                     'PAYMENT_RECONCILIATION_REQUIRED', 'BOOKING_CONFIRMED');
CREATE INDEX booking_intents_session_idx ON booking_intents (session_id, created_at DESC);

-- 001 core transactional schema (Tiger / PostgreSQL).
-- Only Phase 2 fields are populated now; later-phase tables exist with their
-- key constraints so later phases do not need breaking changes.
-- source_kind: 'simulated' (seeded/demo fixtures) | 'external' (Part 2 live data).

CREATE TABLE ports (
    port_code     TEXT PRIMARY KEY CHECK (port_code ~ '^[A-Z]{5}$'),
    name          TEXT NOT NULL,
    country_code  TEXT NOT NULL CHECK (country_code ~ '^[A-Z]{2}$'),
    aliases       TEXT[] NOT NULL DEFAULT '{}',
    source_kind   TEXT NOT NULL CHECK (source_kind IN ('simulated', 'external'))
);

CREATE TABLE carriers (
    carrier_id     TEXT PRIMARY KEY,
    display_name   TEXT NOT NULL,
    agent_address  TEXT NOT NULL UNIQUE,
    endpoint_url   TEXT,
    active         BOOLEAN NOT NULL DEFAULT TRUE,
    source_kind    TEXT NOT NULL CHECK (source_kind IN ('simulated', 'external')),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Private commercial policy: read only by the owning carrier agent.
CREATE TABLE carrier_policies (
    carrier_id  TEXT PRIMARY KEY REFERENCES carriers(carrier_id),
    version     INTEGER NOT NULL CHECK (version >= 1),
    policy      JSONB NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE vessels (
    vessel_id    TEXT PRIMARY KEY,
    carrier_id   TEXT NOT NULL REFERENCES carriers(carrier_id),
    name         TEXT NOT NULL,
    source_kind  TEXT NOT NULL CHECK (source_kind IN ('simulated', 'external')),
    UNIQUE (vessel_id, carrier_id)
);

CREATE TABLE sailings (
    sailing_id        TEXT PRIMARY KEY,
    carrier_id        TEXT NOT NULL REFERENCES carriers(carrier_id),
    vessel_id         TEXT NOT NULL,
    origin_port       TEXT NOT NULL REFERENCES ports(port_code),
    destination_port  TEXT NOT NULL REFERENCES ports(port_code),
    corridor_id       TEXT NOT NULL,
    route_region_ids  TEXT[] NOT NULL DEFAULT '{}',
    departure_at      TIMESTAMPTZ NOT NULL,
    arrival_at        TIMESTAMPTZ NOT NULL,
    status            TEXT NOT NULL CHECK (status IN ('ACTIVE', 'WITHDRAWN', 'CANCELLED', 'DEPARTED')),
    source_kind       TEXT NOT NULL CHECK (source_kind IN ('simulated', 'external')),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (arrival_at > departure_at),
    CHECK (origin_port <> destination_port),
    -- a sailing's vessel must belong to the same carrier
    FOREIGN KEY (vessel_id, carrier_id) REFERENCES vessels(vessel_id, carrier_id),
    UNIQUE (sailing_id, carrier_id)
);
CREATE INDEX sailings_lane_idx ON sailings (origin_port, destination_port, departure_at) WHERE status = 'ACTIVE';

CREATE TABLE capacity_slots (
    slot_id         TEXT PRIMARY KEY,
    sailing_id      TEXT NOT NULL REFERENCES sailings(sailing_id),
    slot_no         INTEGER NOT NULL CHECK (slot_no >= 1),
    container_type  TEXT NOT NULL CHECK (container_type IN ('20GP', '40GP', '40HC')),
    status          TEXT NOT NULL CHECK (status IN ('AVAILABLE', 'HELD', 'COMMITTED', 'WITHDRAWN')),
    version         INTEGER NOT NULL DEFAULT 1,
    source_kind     TEXT NOT NULL CHECK (source_kind IN ('simulated', 'external')),
    UNIQUE (sailing_id, container_type, slot_no)
);
CREATE INDEX capacity_slots_avail_idx ON capacity_slots (sailing_id, container_type) WHERE status = 'AVAILABLE';

CREATE TABLE cargo_requests (
    request_id             TEXT PRIMARY KEY,
    buyer_id               TEXT NOT NULL,
    correlation_id         TEXT NOT NULL,
    origin_port            TEXT NOT NULL REFERENCES ports(port_code),
    destination_port       TEXT NOT NULL REFERENCES ports(port_code),
    container_type         TEXT NOT NULL CHECK (container_type IN ('20GP', '40GP', '40HC')),
    quantity               INTEGER NOT NULL CHECK (quantity BETWEEN 1 AND 50),
    earliest_departure     TIMESTAMPTZ,
    latest_arrival         TIMESTAMPTZ,
    max_total_price_minor  BIGINT CHECK (max_total_price_minor > 0),
    currency               TEXT NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    allow_split            BOOLEAN NOT NULL DEFAULT FALSE,
    requirements           JSONB NOT NULL DEFAULT '{}'::jsonb,
    status                 TEXT NOT NULL CHECK (status IN ('OPEN', 'QUOTED', 'NO_OFFERS', 'BOOKED', 'CANCELLED', 'EXPIRED')),
    source_kind            TEXT NOT NULL CHECK (source_kind IN ('simulated', 'external')),
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (origin_port <> destination_port)
);

CREATE TABLE offers (
    offer_id           TEXT PRIMARY KEY,
    request_id         TEXT NOT NULL REFERENCES cargo_requests(request_id),
    negotiation_id     TEXT NOT NULL,
    correlation_id     TEXT NOT NULL,
    message_id         TEXT NOT NULL UNIQUE,
    carrier_id         TEXT NOT NULL REFERENCES carriers(carrier_id),
    sailing_id         TEXT NOT NULL,
    container_type     TEXT NOT NULL CHECK (container_type IN ('20GP', '40GP', '40HC')),
    quantity           INTEGER NOT NULL CHECK (quantity >= 1),
    total_price_minor  BIGINT NOT NULL CHECK (total_price_minor > 0),
    currency           TEXT NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    departure_at       TIMESTAMPTZ NOT NULL,
    arrival_at         TIMESTAMPTZ NOT NULL,
    expires_at         TIMESTAMPTZ NOT NULL,
    round              INTEGER NOT NULL DEFAULT 1 CHECK (round >= 1),
    status             TEXT NOT NULL CHECK (status IN ('PROPOSED', 'EXPIRED', 'ACCEPTED', 'REJECTED', 'SUPERSEDED')),
    carrier_signature  TEXT NOT NULL,
    terms              TEXT NOT NULL,
    source_kind        TEXT NOT NULL CHECK (source_kind IN ('simulated', 'external')),
    created_at         TIMESTAMPTZ NOT NULL,
    received_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- the offered sailing must belong to the offering carrier
    FOREIGN KEY (sailing_id, carrier_id) REFERENCES sailings(sailing_id, carrier_id)
);
CREATE INDEX offers_request_idx ON offers (request_id);

-- ACP transport + negotiation audit: every request/reply/error, both directions.
CREATE TABLE agent_messages (
    message_id         TEXT PRIMARY KEY,
    correlation_id     TEXT NOT NULL,
    request_id         TEXT REFERENCES cargo_requests(request_id),
    negotiation_id     TEXT,
    in_reply_to        TEXT,
    direction          TEXT NOT NULL CHECK (direction IN ('OUTBOUND', 'INBOUND', 'LOCAL')),
    message_type       TEXT NOT NULL,
    carrier_id         TEXT REFERENCES carriers(carrier_id),
    sender_address     TEXT,
    recipient_address  TEXT,
    status             TEXT NOT NULL,
    latency_ms         INTEGER,
    payload            JSONB NOT NULL DEFAULT '{}'::jsonb,
    ts                 TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX agent_messages_corr_idx ON agent_messages (correlation_id);

-- Later-phase tables (keys/constraints now, populated in Phases 3–7).
CREATE TABLE holds (
    hold_id          TEXT PRIMARY KEY,
    buyer_id         TEXT NOT NULL,
    carrier_id       TEXT NOT NULL REFERENCES carriers(carrier_id),
    offer_id         TEXT REFERENCES offers(offer_id),
    status           TEXT NOT NULL CHECK (status IN ('ACTIVE', 'COMMITTED', 'RELEASED', 'EXPIRED', 'CANCELLED')),
    idempotency_key  TEXT NOT NULL UNIQUE,
    expires_at       TIMESTAMPTZ NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE hold_slots (
    hold_id  TEXT NOT NULL REFERENCES holds(hold_id),
    slot_id  TEXT NOT NULL REFERENCES capacity_slots(slot_id),
    status   TEXT NOT NULL CHECK (status IN ('ACTIVE', 'RELEASED', 'COMMITTED')),
    PRIMARY KEY (hold_id, slot_id)
);
-- INV-1: at most one active hold per slot
CREATE UNIQUE INDEX hold_slots_one_active ON hold_slots (slot_id) WHERE status = 'ACTIVE';

CREATE TABLE bookings (
    booking_id        TEXT PRIMARY KEY,
    buyer_id          TEXT NOT NULL,
    request_id        TEXT NOT NULL REFERENCES cargo_requests(request_id),
    offer_id          TEXT NOT NULL REFERENCES offers(offer_id),
    carrier_id        TEXT NOT NULL REFERENCES carriers(carrier_id),
    sailing_id        TEXT NOT NULL REFERENCES sailings(sailing_id),
    status            TEXT NOT NULL CHECK (status IN ('PENDING_PAYMENT', 'COMMITTED', 'DISRUPTED', 'RECOVERED', 'CANCELLED')),
    total_price_minor BIGINT NOT NULL CHECK (total_price_minor > 0),
    currency          TEXT NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    confirmation_key  TEXT NOT NULL UNIQUE,
    source_kind       TEXT NOT NULL CHECK (source_kind IN ('simulated', 'external')),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE booking_slots (
    booking_id  TEXT NOT NULL REFERENCES bookings(booking_id),
    slot_id     TEXT NOT NULL REFERENCES capacity_slots(slot_id),
    status      TEXT NOT NULL CHECK (status IN ('COMMITTED', 'RELEASED')),
    PRIMARY KEY (booking_id, slot_id)
);
CREATE UNIQUE INDEX booking_slots_one_committed ON booking_slots (slot_id) WHERE status = 'COMMITTED';

CREATE TABLE payment_records (
    payment_id   TEXT PRIMARY KEY,
    booking_id   TEXT REFERENCES bookings(booking_id),
    provider     TEXT NOT NULL,
    reference    TEXT NOT NULL UNIQUE,
    purpose      TEXT NOT NULL,
    amount_minor BIGINT NOT NULL CHECK (amount_minor > 0),
    currency     TEXT NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    status       TEXT NOT NULL CHECK (status IN ('REQUESTED', 'VERIFIED', 'FAILED', 'EXPIRED')),
    verified_at  TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE disruption_events (
    event_id         TEXT PRIMARY KEY,
    carrier_id       TEXT NOT NULL REFERENCES carriers(carrier_id),
    sailing_id       TEXT NOT NULL REFERENCES sailings(sailing_id),
    event_type       TEXT NOT NULL CHECK (event_type IN ('SAILING_WITHDRAWN', 'SAILING_REVIEW_REQUIRED')),
    authorized_by    TEXT,
    reason           TEXT NOT NULL,
    source_kind      TEXT NOT NULL CHECK (source_kind IN ('simulated', 'external')),
    occurred_at      TIMESTAMPTZ NOT NULL,
    UNIQUE (carrier_id, sailing_id, event_type)
);

CREATE TABLE recovery_runs (
    recovery_id          TEXT PRIMARY KEY,
    withdrawal_event_id  TEXT NOT NULL UNIQUE REFERENCES disruption_events(event_id),
    status               TEXT NOT NULL,
    solver_run_id        TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE recovery_assignments (
    recovery_id             TEXT NOT NULL REFERENCES recovery_runs(recovery_id),
    old_booking_id          TEXT NOT NULL REFERENCES bookings(booking_id),
    replacement_booking_id  TEXT REFERENCES bookings(booking_id),
    status                  TEXT NOT NULL,
    PRIMARY KEY (recovery_id, old_booking_id)
);

-- Append-only business history; converted to a hypertable by the migration runner
-- after checking the installed TimescaleDB version (see migrate.py).
CREATE TABLE market_events (
    ts              TIMESTAMPTZ NOT NULL,
    event_id        UUID NOT NULL,
    event_type      TEXT NOT NULL,
    entity_type     TEXT NOT NULL,
    entity_id       TEXT NOT NULL,
    correlation_id  TEXT,
    carrier_id      TEXT,
    sailing_id      TEXT,
    corridor_id     TEXT,
    source_kind     TEXT NOT NULL CHECK (source_kind IN ('simulated', 'external')),
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (ts, event_id)
);
CREATE INDEX market_events_corr_idx ON market_events (correlation_id, ts DESC);

CREATE TABLE market_snapshots (
    ts                      TIMESTAMPTZ NOT NULL,
    corridor_id             TEXT NOT NULL,
    departure_window        TEXT NOT NULL,
    requested_units         INTEGER NOT NULL,
    sellable_units          INTEGER NOT NULL,
    held_units              INTEGER NOT NULL,
    committed_units         INTEGER NOT NULL,
    avg_offered_unit_price  NUMERIC(12, 2),
    source_kind             TEXT NOT NULL CHECK (source_kind IN ('simulated', 'external')),
    PRIMARY KEY (ts, corridor_id, departure_window)
);

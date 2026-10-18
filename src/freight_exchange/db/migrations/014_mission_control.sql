-- 014 HX-OPS-001 Mission Control (read-only observer). Additive only; business tables are untouched.

-- Per-sailing capacity history, written by the ops observer every minute and on inventory transitions.
-- Simulated commercial slots, not vessel sensor readings. A withdrawn sailing has zero sellable units.
CREATE TABLE sailing_capacity_snapshots (
    ts              TIMESTAMPTZ NOT NULL,
    sailing_id      TEXT NOT NULL,
    carrier_id      TEXT NOT NULL,
    corridor_id     TEXT,
    status          TEXT NOT NULL,
    total_units     INTEGER NOT NULL,
    committed_units INTEGER NOT NULL,
    held_units      INTEGER NOT NULL,
    available_units INTEGER NOT NULL,
    trigger         TEXT NOT NULL,           -- 'interval' | event type that changed inventory
    source_kind     TEXT NOT NULL DEFAULT 'simulated'
);
SELECT create_hypertable('sailing_capacity_snapshots', 'ts', if_not_exists => TRUE);
CREATE INDEX sailing_capacity_snapshots_sailing_idx ON sailing_capacity_snapshots (sailing_id, ts DESC);

-- Time Machine checkpoints: a sanitized market projection plus the event cursor it already reflects.
CREATE TABLE market_state_checkpoints (
    snapshot_id         TEXT PRIMARY KEY,
    captured_at         TIMESTAMPTZ NOT NULL,
    cursor_ts           TIMESTAMPTZ,             -- every market event with ts <= cursor_ts is folded in
    projection_schema   INTEGER NOT NULL,
    reason              TEXT NOT NULL,
    projection          JSONB NOT NULL
);
CREATE INDEX market_state_checkpoints_at_idx ON market_state_checkpoints (captured_at DESC);

-- A real continuous aggregate (with a refresh policy) behind Market Intelligence's activity chart.
CREATE MATERIALIZED VIEW market_activity_1m
WITH (timescaledb.continuous) AS
SELECT time_bucket(INTERVAL '1 minute', ts) AS bucket,
       event_type,
       COUNT(*)::bigint AS event_count
FROM market_events
GROUP BY bucket, event_type
WITH NO DATA;

SELECT add_continuous_aggregate_policy('market_activity_1m',
    start_offset => INTERVAL '7 days',
    end_offset => INTERVAL '1 minute',
    schedule_interval => INTERVAL '1 minute');

-- 013 UX-NEG-001: pre-rendered, buyer-scoped negotiation transcript cards. Built from Tiger's persisted
-- negotiation_sessions / negotiation_messages / offers the moment a negotiation closes, so opening one is a
-- single owner-scoped read (no waiting, no recomputation). Read-only for the booking flow. Additive only.
CREATE TABLE negotiation_trace_cards (
    trace_key     TEXT PRIMARY KEY,               -- offer:<request>:<offer> | carousel:<request> | recovery:<run>:<booking>
    buyer_id      TEXT NOT NULL,
    kind          TEXT NOT NULL CHECK (kind IN ('offer', 'carousel', 'recovery')),
    request_id    TEXT,
    offer_id      TEXT,
    session_id    TEXT,
    recovery_id   TEXT,
    booking_id    TEXT,
    card          JSONB NOT NULL,                  -- the rendered chat result (card + text fallback)
    trace         JSONB,                           -- the validated display DTO it was rendered from
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX negotiation_trace_cards_owner_idx ON negotiation_trace_cards (buyer_id, request_id);

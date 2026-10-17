-- 003 WITHDRAW_SAILING command fields (spec 05 section 5) as real columns.
-- Additive and nullable: the console's current INSERT keeps working; it can start
-- filling these (NEEDS-FROM-TISYA) instead of packing them into `reason` / payload.
ALTER TABLE disruption_events ADD COLUMN reason_risk_event_id TEXT;
ALTER TABLE disruption_events ADD COLUMN authorization_ref TEXT;
ALTER TABLE disruption_events ADD COLUMN idempotency_key TEXT;
CREATE UNIQUE INDEX disruption_events_idempotency_key ON disruption_events (idempotency_key)
    WHERE idempotency_key IS NOT NULL;

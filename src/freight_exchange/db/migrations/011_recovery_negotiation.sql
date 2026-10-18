-- 011 v2 phase 5: timed recovery negotiation + five-stage clearing. Additive only.
ALTER TABLE recovery_runs DROP CONSTRAINT recovery_runs_status_check;
ALTER TABLE recovery_runs ADD CONSTRAINT recovery_runs_status_check
    CHECK (status IN ('CREATED', 'OFFERS_REQUESTED', 'NEGOTIATING', 'CLEARING', 'PROPOSED', 'HOLDING',
                      'AWAITING_APPROVAL', 'CONFIRMING', 'COMPLETED', 'PARTIAL', 'UNRESOLVED', 'FAILED',
                      'RECONCILIATION_REQUIRED'));
-- Upper bound for all recovery negotiation sessions of a run (opened_at + recovery_negotiation_timeout_seconds);
-- persisted so a restart never restarts the clock.
ALTER TABLE recovery_runs ADD COLUMN negotiation_deadline TIMESTAMPTZ;
ALTER TABLE recovery_runs ADD COLUMN negotiation_closed_at TIMESTAMPTZ;
-- A re-clear (attempt 2) negotiates again with the same carriers for the same booking.
DROP INDEX negotiation_sessions_recovery_pair;
CREATE INDEX negotiation_sessions_recovery_pair ON negotiation_sessions (recovery_id, booking_id, carrier_id)
    WHERE kind = 'recovery';

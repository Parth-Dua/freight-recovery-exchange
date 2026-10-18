-- 005 Buyer recovery allowance (PRD D5 / spec 03 §5 recovery_policy), recorded at booking time.
-- Default 0 = no blanket auto-authority: any pricier replacement needs the buyer's approval.
ALTER TABLE booking_intents ADD COLUMN max_additional_cost_minor BIGINT NOT NULL DEFAULT 0
    CHECK (max_additional_cost_minor >= 0);
ALTER TABLE bookings ADD COLUMN max_additional_cost_minor BIGINT NOT NULL DEFAULT 0
    CHECK (max_additional_cost_minor >= 0);

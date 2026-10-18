-- 012: Stripe TEST checkout carried over the Fetch Payment Protocol (seller RequestPayment with
-- metadata["stripe"]). Each payment has an embedded session (rendered by the client) and a hosted
-- fallback session (a link); whichever is paid first settles it, the other is expired. Additive only.
ALTER TABLE payment_records ADD COLUMN stripe_session_ids TEXT[];

-- 015: exactly one CompletePayment per payment, answering the client's own CommitPayment.
-- client_tx_id: the transaction id ASI:One put in its CommitPayment (echoed back in CompletePayment).
-- receipt_sent: the booking receipt was posted once (by the commit reply or the Stripe poller). Additive only.
ALTER TABLE payment_records ADD COLUMN client_tx_id TEXT;
ALTER TABLE payment_records ADD COLUMN receipt_sent BOOLEAN NOT NULL DEFAULT FALSE;

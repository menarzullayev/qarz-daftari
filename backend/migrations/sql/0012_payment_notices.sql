-- Payment notices and the files they carry (REQ-060, REQ-061; ADR-020).

-- Two more kinds of chat question: a customer who is telling the shop about a payment (the amount, then
-- the receipt), and a staff member writing the reason for declining a notice.
--
-- The list of kinds is extended, not restated: whatever kinds the constraint allows when this runs stay
-- allowed, so this migration does not depend on which other migration added a kind before it.
DO $$
DECLARE
  kinds text[];
BEGIN
  SELECT array_agg(DISTINCT quoted[1] ORDER BY quoted[1]) INTO kinds
  FROM pg_constraint c,
       LATERAL regexp_matches(pg_get_constraintdef(c.oid), '''([a-z_]+)''', 'g') AS quoted
  WHERE c.conname = 'chat_pending_kind_check' AND c.conrelid = 'chat_pending'::regclass;
  IF kinds IS NULL THEN
    RAISE EXCEPTION 'chat_pending_kind_check was not found';
  END IF;
  SELECT array_agg(DISTINCT kind ORDER BY kind) INTO kinds
  FROM unnest(kinds || ARRAY['notice', 'notice_decline']) AS kind;
  ALTER TABLE chat_pending DROP CONSTRAINT chat_pending_kind_check;
  -- Written as a plain list of literals, the form every other migration uses and this block can read again.
  EXECUTE format(
    'ALTER TABLE chat_pending ADD CONSTRAINT chat_pending_kind_check CHECK (kind IN (%s))',
    (SELECT string_agg(quote_literal(kind), ', ' ORDER BY kind) FROM unnest(kinds) AS kind)
  );
END $$;

CREATE INDEX payment_notice_open ON payment_notice (shop_id, created_at) WHERE status = 'sent';
CREATE INDEX payment_notice_customer ON payment_notice (customer_id, created_at DESC);
-- A receipt belongs to one notice.
CREATE UNIQUE INDEX payment_notice_file ON payment_notice (file_id) WHERE file_id IS NOT NULL;
-- What the retention cleanup looks for.
CREATE INDEX stored_file_due ON stored_file (shop_id, delete_after) WHERE delete_after IS NOT NULL;
-- No new grant or policy: the table-level grants to qd_app and the tenant policies of 0001 cover both tables.

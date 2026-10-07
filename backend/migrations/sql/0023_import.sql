-- Import of customers with opening balances (REQ-062, REQ-063).
--
-- No new table: the batch, its file and the mark on each entry are in the schema since 0001
-- (`import_batch`, `stored_file.purpose = 'import'`, `ledger_entry.import_batch_id`), and `erase_shop`
-- already deletes all three.

-- The list of a shop's imports, newest first.
CREATE INDEX import_batch_shop ON import_batch (shop_id, created_at DESC);
-- The entries an import created, for undoing it.
CREATE INDEX ledger_entry_import ON ledger_entry (import_batch_id) WHERE import_batch_id IS NOT NULL;
-- Whether a row of a file means a customer the shop already has.
CREATE INDEX customer_shop_norm ON customer (shop_id, name_norm);
CREATE INDEX customer_shop_phone ON customer (shop_id, phone) WHERE phone IS NOT NULL;

-- An import file is deleted like a receipt whose retention has run out: the hourly job now looks for both.
-- Redefined from its text in 0012; only the list of purposes is new.
CREATE OR REPLACE FUNCTION shops_with_receipt_work(p_stale_before timestamptz, p_now timestamptz)
RETURNS TABLE (shop_id uuid)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  SELECT n.shop_id FROM payment_notice n WHERE n.status = 'sent' AND n.created_at < p_stale_before
  UNION
  SELECT f.shop_id FROM stored_file f WHERE f.purpose IN ('payment_notice', 'import') AND f.delete_after <= p_now
  ORDER BY 1;
$$;

REVOKE ALL ON FUNCTION shops_with_receipt_work(timestamptz, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION shops_with_receipt_work(timestamptz, timestamptz) TO qd_app;

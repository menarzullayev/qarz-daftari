-- Import of customers with opening balances (REQ-062, REQ-063), with the heavy steps done by the worker.
--
-- No new table: the batch, its file and the mark on each entry are in the schema since 0001
-- (`import_batch`, `stored_file.purpose = 'import'`, `ledger_entry.import_batch_id`), and `erase_shop`
-- already deletes all three.

-- A batch waits for the worker three times: to be checked (`uploaded`), to be applied (`applying`) and to
-- be undone (`undoing`). `rejected` is a checked file with problems; `failed` one that could not be checked.
ALTER TABLE import_batch DROP CONSTRAINT import_batch_status_check;
ALTER TABLE import_batch ADD CONSTRAINT import_batch_status_check
  CHECK (status IN ('uploaded','validated','rejected','applying','applied','undoing','undone','discarded','failed'));

ALTER TABLE import_batch
  -- What applying would do, as the worker found it when it checked the file. Holds the rows; removed when
  -- the batch is applied or discarded and when its file is deleted.
  ADD COLUMN preview    jsonb,
  -- The fingerprint of that preview. Applying must name it.
  ADD COLUMN plan       text CHECK (plan IS NULL OR length(plan) <= 64),
  -- Who asked for the step the batch is waiting for, or for the last one done.
  ADD COLUMN step_by    uuid REFERENCES membership(id),
  ADD COLUMN queued_at  timestamptz,
  -- When a worker last took the waiting step, and how often it has been taken.
  ADD COLUMN started_at timestamptz,
  ADD COLUMN attempts   smallint NOT NULL DEFAULT 0;

-- The list of a shop's imports, newest first.
CREATE INDEX import_batch_shop ON import_batch (shop_id, created_at DESC);
-- What the worker looks for, across shops.
CREATE INDEX import_batch_waiting ON import_batch (queued_at) WHERE status IN ('uploaded','applying','undoing');
-- The entries an import created, for undoing it.
CREATE INDEX ledger_entry_import ON ledger_entry (import_batch_id) WHERE import_batch_id IS NOT NULL;
-- Whether a row of a file means a customer the shop already has.
CREATE INDEX customer_shop_norm ON customer (shop_id, name_norm);
CREATE INDEX customer_shop_phone ON customer (shop_id, phone) WHERE phone IS NOT NULL;

-- The worker has no tenant of its own. It takes the batch that has waited longest and that no worker has
-- taken, or whose worker has been silent since `p_stale_before` (it died), notes the start and learns only
-- which batch of which shop waits for which step. Two workers never get the same batch at once: the row is
-- locked while it is chosen, and a locked row is skipped. The same pattern as `claim_export_job` (0022).
CREATE FUNCTION claim_import_batch(p_now timestamptz, p_stale_before timestamptz)
RETURNS TABLE (batch_id uuid, shop_id uuid, step text, attempts smallint)
LANGUAGE sql
VOLATILE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  UPDATE import_batch b
     SET started_at = p_now, attempts = b.attempts + 1
   WHERE b.id = (
           SELECT w.id FROM import_batch w
            WHERE w.status IN ('uploaded','applying','undoing')
              AND (w.started_at IS NULL OR w.started_at < p_stale_before)
            ORDER BY w.queued_at, w.id
            LIMIT 1
              FOR UPDATE SKIP LOCKED)
  RETURNING b.id, b.shop_id, b.status, b.attempts;
$$;
REVOKE ALL ON FUNCTION claim_import_batch(timestamptz, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION claim_import_batch(timestamptz, timestamptz) TO qd_app;

-- An import file is deleted like a receipt or an export whose retention has run out: the hourly job now
-- looks for all three. Restated from its text in 0022; only the list of purposes is new.
CREATE OR REPLACE FUNCTION shops_with_receipt_work(p_stale_before timestamptz, p_now timestamptz)
RETURNS TABLE (shop_id uuid)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  SELECT n.shop_id FROM payment_notice n WHERE n.status = 'sent' AND n.created_at < p_stale_before
  UNION
  SELECT f.shop_id FROM stored_file f
   WHERE f.purpose IN ('payment_notice', 'export', 'subscription_receipt', 'import')
     AND f.delete_after <= p_now
  ORDER BY 1;
$$;

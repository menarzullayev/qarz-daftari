-- Exports as worker jobs with signed downloads (REQ-028; specification: "/exports: export jobs and
-- downloads", "larger exports run as jobs"; ADR-020).

-- One request for an export of a shop. The worker claims it, writes the workbook to the file store and
-- records the file here.
CREATE TABLE export_job (
  id            uuid PRIMARY KEY,
  shop_id       uuid NOT NULL REFERENCES shop(id),
  requested_by  uuid NOT NULL REFERENCES membership(id),
  status        text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','done','failed')),
  file_id       uuid,
  error         text CHECK (error IN ('interrupted','timeout','file_store','internal')),  -- a kind, never a text
  attempts      smallint NOT NULL DEFAULT 0,
  row_count     integer,
  created_at    timestamptz NOT NULL DEFAULT now(),
  started_at    timestamptz,
  finished_at   timestamptz,
  CHECK ((status = 'failed') = (error IS NOT NULL)),
  CHECK (file_id IS NULL OR status = 'done')
);
-- At most one export of a shop is waiting or being written at any time, whatever races the application loses.
CREATE UNIQUE INDEX one_active_export ON export_job (shop_id) WHERE status IN ('queued','running');
CREATE INDEX export_job_shop_time ON export_job (shop_id, created_at DESC);
-- What the worker looks for, across shops.
CREATE INDEX export_job_waiting ON export_job (created_at) WHERE status IN ('queued','running');

ALTER TABLE export_job ENABLE ROW LEVEL SECURITY;
ALTER TABLE export_job FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant ON export_job
  USING (shop_id = nullif(current_setting('qd.shop_id', true), '')::uuid)
  WITH CHECK (shop_id = nullif(current_setting('qd.shop_id', true), '')::uuid);
-- A job is never deleted by the application: its history is the shop's, and goes with the shop.
REVOKE ALL ON export_job FROM qd_app;
GRANT SELECT, INSERT, UPDATE ON export_job TO qd_app;

-- The file store keeps export workbooks too. The specification's schema lists three purposes; this adds
-- the fourth.
ALTER TABLE stored_file DROP CONSTRAINT stored_file_purpose_check;
ALTER TABLE stored_file ADD CONSTRAINT stored_file_purpose_check
  CHECK (purpose IN ('subscription_receipt','payment_notice','import','export'));

-- Customers of one shop in identifier order, a page at a time: the export reads them by key.
CREATE INDEX customer_shop_id ON customer (shop_id, id);

-- The worker has no tenant of its own. It takes the oldest job that is waiting, or one whose worker has
-- been silent since `p_stale_before` (it died), marks it as running and learns only which job of which
-- shop it is. Two workers never get the same job: the row is locked while it is chosen, and a locked
-- row is skipped.
CREATE FUNCTION claim_export_job(p_now timestamptz, p_stale_before timestamptz)
RETURNS TABLE (job_id uuid, shop_id uuid, attempts smallint)
LANGUAGE sql
VOLATILE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  UPDATE export_job j
     SET status = 'running', started_at = p_now, attempts = j.attempts + 1
   WHERE j.id = (
           SELECT w.id FROM export_job w
            WHERE w.status = 'queued' OR (w.status = 'running' AND w.started_at < p_stale_before)
            ORDER BY w.created_at, w.id
            LIMIT 1
              FOR UPDATE SKIP LOCKED)
  RETURNING j.id, j.shop_id, j.attempts;
$$;
REVOKE ALL ON FUNCTION claim_export_job(timestamptz, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION claim_export_job(timestamptz, timestamptz) TO qd_app;

-- The hourly cleanup of stored files now also finds export workbooks whose time has run out.
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
   WHERE f.purpose IN ('payment_notice', 'export') AND f.delete_after <= p_now
  ORDER BY 1;
$$;

-- Erasing a shop takes its export jobs with it. The function is restated from 0016 with the one new line.
CREATE OR REPLACE FUNCTION erase_shop(p_shop_id uuid) RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  people uuid[];
  person uuid;
BEGIN
  PERFORM 1 FROM shop
    WHERE id = p_shop_id AND status = 'deletion_pending' AND deletion_due IS NOT NULL AND deletion_due <= now()
    FOR UPDATE;
  IF NOT FOUND THEN
    RETURN false;
  END IF;

  -- Everyone the shop knew: its staff and the people linked as its customers.
  SELECT array_agg(DISTINCT user_id) INTO people FROM (
    SELECT user_id FROM membership WHERE shop_id = p_shop_id
    UNION
    SELECT user_id FROM customer_link WHERE shop_id = p_shop_id AND user_id IS NOT NULL
  ) known;

  -- Children before parents. Every table that carries a shop identifier is listed here;
  -- tests/db/test_shop_erasure.py fails when one is added and not listed.
  DELETE FROM goods_line WHERE shop_id = p_shop_id;
  DELETE FROM promise WHERE shop_id = p_shop_id;
  DELETE FROM dispute WHERE shop_id = p_shop_id;
  DELETE FROM date_change_request WHERE shop_id = p_shop_id;
  DELETE FROM payment_notice WHERE shop_id = p_shop_id;
  DELETE FROM export_job WHERE shop_id = p_shop_id;
  DELETE FROM reminder WHERE shop_id = p_shop_id;
  DELETE FROM removal_request WHERE shop_id = p_shop_id;
  DELETE FROM ledger_entry WHERE shop_id = p_shop_id;
  DELETE FROM import_batch WHERE shop_id = p_shop_id;
  DELETE FROM customer_link WHERE shop_id = p_shop_id;
  DELETE FROM customer WHERE shop_id = p_shop_id;
  DELETE FROM catalog_item WHERE shop_id = p_shop_id;
  DELETE FROM subscription_receipt WHERE shop_id = p_shop_id;
  DELETE FROM stored_file WHERE shop_id = p_shop_id;
  DELETE FROM support_access WHERE shop_id = p_shop_id;
  DELETE FROM ownership_transfer WHERE shop_id = p_shop_id;
  DELETE FROM invitation WHERE shop_id = p_shop_id;
  DELETE FROM activity WHERE shop_id = p_shop_id;
  DELETE FROM request_key WHERE shop_id = p_shop_id;
  DELETE FROM admin_request_key WHERE about_shop = p_shop_id;
  DELETE FROM outbox_message WHERE shop_id = p_shop_id;
  DELETE FROM online_payment WHERE shop_id = p_shop_id;
  DELETE FROM subscription WHERE shop_id = p_shop_id;
  DELETE FROM membership WHERE shop_id = p_shop_id;

  UPDATE app_user SET active_shop = NULL WHERE active_shop = p_shop_id;
  -- The row stays as a tombstone with nothing of the shop left in it.
  UPDATE shop
     SET status = 'erased', name = 'erased', deletion_due = NULL, reminders_on = false, sms_on = false,
         default_credit_limit = NULL
   WHERE id = p_shop_id;

  IF people IS NOT NULL THEN
    FOREACH person IN ARRAY people LOOP
      PERFORM forget_user_if_unused(person);
    END LOOP;
  END IF;
  RETURN true;
END $$;

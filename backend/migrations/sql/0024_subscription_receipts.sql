-- Subscription receipts: an owner's proof of a card transfer, decided by an administrator
-- (REQ-054, REQ-055; ADR-019; domain object DOM-019).

-- What the owner says they paid for. The administrator records the months that count when approving.
ALTER TABLE subscription_receipt
  ADD COLUMN stated_months smallint CHECK (stated_months BETWEEN 1 AND 36);

-- A file belongs to one receipt. When its retention runs out the file's row is deleted and the receipt,
-- which stays as the record of the payment, simply has no file any more.
ALTER TABLE subscription_receipt DROP CONSTRAINT subscription_receipt_file_id_fkey;
ALTER TABLE subscription_receipt
  ADD CONSTRAINT subscription_receipt_file_id_fkey FOREIGN KEY (file_id) REFERENCES stored_file(id) ON DELETE SET NULL;
CREATE UNIQUE INDEX subscription_receipt_file ON subscription_receipt (file_id) WHERE file_id IS NOT NULL;
-- The queue the administrator works through, and the age of its oldest item.
CREATE INDEX subscription_receipt_waiting ON subscription_receipt (created_at, id) WHERE status = 'submitted';
CREATE INDEX subscription_receipt_shop ON subscription_receipt (shop_id, created_at DESC);
-- The same content sent again, by any shop (ADR-019: "duplicates are flagged").
CREATE INDEX stored_file_receipt_hash ON stored_file (sha256) WHERE purpose = 'subscription_receipt';

-- A shop sends receipts and reads its own. It never changes one: the decision is the administrator's and
-- is written only by admin_decide_receipt below (INV-16), and rows go only when the shop is erased.
REVOKE UPDATE, DELETE, TRUNCATE ON subscription_receipt FROM qd_app;

-- The admin audit names one more kind of thing an administrator acts on.
ALTER TABLE admin_audit DROP CONSTRAINT admin_audit_target_type_check;
ALTER TABLE admin_audit
  ADD CONSTRAINT admin_audit_target_type_check CHECK (target_type IN ('admin', 'shop', 'setting', 'receipt'));

-- Two more kinds of chat question: an owner who chose how many months they are paying for and is about
-- to send the receipt, and an administrator writing the reason for rejecting one. The list of kinds is
-- extended, not restated, as in 0012.
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
  FROM unnest(kinds || ARRAY['sub_receipt', 'receipt_reject']) AS kind;
  ALTER TABLE chat_pending DROP CONSTRAINT chat_pending_kind_check;
  EXECUTE format(
    'ALTER TABLE chat_pending ADD CONSTRAINT chat_pending_kind_check CHECK (kind IN (%s))',
    (SELECT string_agg(quote_literal(kind), ', ' ORDER BY kind) FROM unnest(kinds) AS kind)
  );
END $$;

-- ---------------------------------------------------------------------------
-- Cross-tenant functions, as in 0016: each of the administrator's does nothing unless the account is
-- active; the allow-list and the second factor are checked by the application before it calls them.
-- ---------------------------------------------------------------------------

-- How many other subscription receipts, of any shop, carry a file with the same content as this one.
-- A shop calls this for its own new receipt so that the reviewers can be warned; it returns a number and
-- nothing about the other receipts. A file that is not this shop's subscription receipt counts nothing.
CREATE FUNCTION subscription_receipt_copies(p_file uuid) RETURNS integer
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  SELECT count(*)::integer
    FROM stored_file mine
    JOIN stored_file other
      ON other.sha256 = mine.sha256 AND other.purpose = 'subscription_receipt' AND other.id <> mine.id
    JOIN subscription_receipt r ON r.file_id = other.id
   WHERE mine.id = p_file
     AND mine.purpose = 'subscription_receipt'
     AND mine.shop_id = nullif(current_setting('qd.shop_id', true), '')::uuid;
$$;

-- Receipts of one status across all shops, oldest first: the queue. `copies` counts the other receipts
-- with the same file content. No card detail is stored in any column returned here.
CREATE FUNCTION admin_receipts(
  p_admin uuid, p_status text, p_after_created timestamptz, p_after_id uuid, p_limit integer
)
RETURNS TABLE (
  receipt_id uuid, shop_id uuid, shop_name text, stated_amount bigint, stated_months smallint, status text,
  months smallint, reject_reason text, created_at timestamptz, decided_at timestamptz, decided_by uuid,
  has_file boolean, copies integer
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  SELECT r.id, r.shop_id, s.name, r.stated_amount, r.stated_months, r.status, r.months, r.reject_reason,
         r.created_at, r.decided_at, r.decided_by, r.file_id IS NOT NULL,
         (SELECT count(*)::integer
            FROM stored_file mine
            JOIN stored_file other
              ON other.sha256 = mine.sha256 AND other.purpose = 'subscription_receipt' AND other.id <> mine.id
            JOIN subscription_receipt o ON o.file_id = other.id
           WHERE mine.id = r.file_id)
    FROM subscription_receipt r
    JOIN shop s ON s.id = r.shop_id
   WHERE r.status = p_status
     AND s.status <> 'erased'
     AND EXISTS (SELECT 1 FROM admin_account a WHERE a.user_id = p_admin AND a.status = 'active')
     AND (p_after_created IS NULL OR (r.created_at, r.id) > (p_after_created, p_after_id))
   ORDER BY r.created_at, r.id
   LIMIT least(greatest(p_limit, 1), 101);
$$;

-- One receipt with where its file is kept. With `p_lock` the row stays locked until the transaction
-- ends, so two decisions cannot both find it waiting.
CREATE FUNCTION admin_receipt(p_admin uuid, p_receipt uuid, p_lock boolean)
RETURNS TABLE (
  receipt_id uuid, shop_id uuid, shop_name text, stated_amount bigint, stated_months smallint, status text,
  months smallint, reject_reason text, created_at timestamptz, decided_at timestamptz, decided_by uuid,
  file_id uuid, object_key text, sha256 bytea, size_bytes integer, mime text, delete_after timestamptz
)
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM admin_account a WHERE a.user_id = p_admin AND a.status = 'active') THEN
    RETURN;
  END IF;
  IF p_lock THEN
    PERFORM 1 FROM subscription_receipt r WHERE r.id = p_receipt FOR UPDATE;
  END IF;
  RETURN QUERY
    SELECT r.id, r.shop_id, s.name, r.stated_amount, r.stated_months, r.status, r.months, r.reject_reason,
           r.created_at, r.decided_at, r.decided_by,
           f.id, f.object_key, f.sha256, f.size_bytes, f.mime, f.delete_after
      FROM subscription_receipt r
      JOIN shop s ON s.id = r.shop_id
      LEFT JOIN stored_file f ON f.id = r.file_id
     WHERE r.id = p_receipt AND s.status <> 'erased';
END $$;

-- The other receipts whose file has the same content as this one's: what the reviewer compares with.
CREATE FUNCTION admin_receipt_copies(p_admin uuid, p_receipt uuid)
RETURNS TABLE (
  receipt_id uuid, shop_id uuid, shop_name text, stated_amount bigint, status text, created_at timestamptz
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  SELECT o.id, o.shop_id, s.name, o.stated_amount, o.status, o.created_at
    FROM subscription_receipt r
    JOIN stored_file mine ON mine.id = r.file_id
    JOIN stored_file other
      ON other.sha256 = mine.sha256 AND other.purpose = 'subscription_receipt' AND other.id <> mine.id
    JOIN subscription_receipt o ON o.file_id = other.id
    JOIN shop s ON s.id = o.shop_id
   WHERE r.id = p_receipt
     AND EXISTS (SELECT 1 FROM admin_account a WHERE a.user_id = p_admin AND a.status = 'active')
   ORDER BY o.created_at, o.id
   LIMIT 50;
$$;

-- Writes the decision. A receipt is decided once: only one that is still waiting changes, and the table
-- itself refuses an approval without months. True when a row was changed.
CREATE FUNCTION admin_decide_receipt(
  p_admin uuid, p_receipt uuid, p_status text, p_months smallint, p_reason text, p_now timestamptz
)
RETURNS boolean
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  changed integer;
BEGIN
  IF p_status NOT IN ('approved', 'rejected') THEN
    RAISE EXCEPTION 'a receipt is approved or rejected';
  END IF;
  UPDATE subscription_receipt r
     SET status = p_status,
         months = CASE WHEN p_status = 'approved' THEN p_months END,
         reject_reason = CASE WHEN p_status = 'rejected' THEN p_reason END,
         decided_by = p_admin,
         decided_at = p_now
   WHERE r.id = p_receipt
     AND r.status = 'submitted'
     AND EXISTS (SELECT 1 FROM shop s WHERE s.id = r.shop_id AND s.status <> 'erased')
     AND EXISTS (SELECT 1 FROM admin_account a WHERE a.user_id = p_admin AND a.status = 'active');
  GET DIAGNOSTICS changed = ROW_COUNT;
  RETURN changed = 1;
END $$;

-- A line in the shop's own activity for what an administrator did to it. The administrator is not named
-- to the shop; who it was is in the admin audit.
CREATE FUNCTION admin_shop_activity(p_admin uuid, p_shop uuid, p_action text, p_subject uuid)
RETURNS boolean
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM admin_account a WHERE a.user_id = p_admin AND a.status = 'active')
     OR NOT EXISTS (SELECT 1 FROM shop s WHERE s.id = p_shop AND s.status <> 'erased') THEN
    RETURN false;
  END IF;
  INSERT INTO activity (id, shop_id, actor_kind, actor_id, action, subject_type, subject_id)
  VALUES (gen_random_uuid(), p_shop, 'admin', NULL, p_action, 'subscription_receipt', p_subject);
  RETURN true;
END $$;

-- When the oldest receipt still awaiting a decision was sent; null when none waits. For monitoring
-- ("receipts awaiting decision older than 24 hours"); it tells nothing but a time.
CREATE FUNCTION oldest_waiting_receipt() RETURNS timestamptz
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  SELECT min(r.created_at)
    FROM subscription_receipt r JOIN shop s ON s.id = r.shop_id
   WHERE r.status = 'submitted' AND s.status <> 'erased';
$$;

-- The hourly cleanup (0012, restated in 0022 for export files) now also finds shops with subscription
-- receipts past their retention.
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
   WHERE f.purpose IN ('payment_notice', 'export', 'subscription_receipt') AND f.delete_after <= p_now
  ORDER BY 1;
$$;

REVOKE ALL ON FUNCTION subscription_receipt_copies(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION admin_receipts(uuid, text, timestamptz, uuid, integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION admin_receipt(uuid, uuid, boolean) FROM PUBLIC;
REVOKE ALL ON FUNCTION admin_receipt_copies(uuid, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION admin_decide_receipt(uuid, uuid, text, smallint, text, timestamptz) FROM PUBLIC;
REVOKE ALL ON FUNCTION admin_shop_activity(uuid, uuid, text, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION oldest_waiting_receipt() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION subscription_receipt_copies(uuid) TO qd_app;
GRANT EXECUTE ON FUNCTION admin_receipts(uuid, text, timestamptz, uuid, integer) TO qd_app;
GRANT EXECUTE ON FUNCTION admin_receipt(uuid, uuid, boolean) TO qd_app;
GRANT EXECUTE ON FUNCTION admin_receipt_copies(uuid, uuid) TO qd_app;
GRANT EXECUTE ON FUNCTION admin_decide_receipt(uuid, uuid, text, smallint, text, timestamptz) TO qd_app;
GRANT EXECUTE ON FUNCTION admin_shop_activity(uuid, uuid, text, uuid) TO qd_app;
GRANT EXECUTE ON FUNCTION oldest_waiting_receipt() TO qd_app;

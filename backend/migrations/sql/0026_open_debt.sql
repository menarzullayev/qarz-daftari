-- Open debts, stored (the founder's decision of 2026-10-07 under launch criterion 6).
--
-- The overview and the list of debtors added up every entry of a shop on each call; for a shop with
-- 200 000 entries that took longer than NFR-005 allows (load test, EVID-063). What they need is small:
-- for each debt that is not yet fully paid, how much of it is left and when it was promised. That is kept
-- here, one row per such entry, and is rewritten for a customer whenever an entry or a promise of theirs
-- is added. The ledger stays the only truth: this table can be rebuilt from it at any time, and
-- `open_debt_mismatches()` says where the two differ (nowhere, or something is broken).

CREATE TABLE open_debt (
  entry_id      uuid PRIMARY KEY REFERENCES ledger_entry(id),
  shop_id       uuid NOT NULL REFERENCES shop(id),
  customer_id   uuid NOT NULL REFERENCES customer(id),
  remaining     bigint NOT NULL CHECK (remaining > 0),   -- what the oldest-first allocation (BR-3) leaves unpaid
  promised_date date                                      -- the entry's current promised date
);
CREATE INDEX open_debt_shop_customer ON open_debt (shop_id, customer_id);

-- Read by the application; written only by the functions below, which run with their owner's rights.
GRANT SELECT ON open_debt TO qd_app;
ALTER TABLE open_debt ENABLE ROW LEVEL SECURITY;
ALTER TABLE open_debt FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant ON open_debt
  USING (shop_id = nullif(current_setting('qd.shop_id', true), '')::uuid)
  WITH CHECK (shop_id = nullif(current_setting('qd.shop_id', true), '')::uuid);

-- What the ledger says is open for the given customers. The same allocation as the application's
-- (qarz.domain.ledger.allocate): a debt's unpaid part is what its running total exceeds the customer's
-- payments by, capped at its own amount; reversed entries do not count (INV-2), and a reversal is neither
-- a debt nor a payment.
CREATE FUNCTION open_debts_of(p_customers uuid[])
RETURNS TABLE (shop_id uuid, customer_id uuid, entry_id uuid, remaining bigint, promised_date date)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
AS $$
  WITH live AS (
    SELECT e.id, e.shop_id, e.customer_id, e.seq, e.kind, e.amount
      FROM ledger_entry e
     WHERE e.customer_id = ANY (p_customers)
       AND NOT EXISTS (SELECT 1 FROM ledger_entry r WHERE r.reverses_id = e.id)),
  paid AS (SELECT customer_id, sum(amount) AS paid FROM live WHERE kind = 'payment' GROUP BY customer_id),
  debt AS (
    SELECT l.id, l.shop_id, l.customer_id, l.amount,
           sum(l.amount) OVER (PARTITION BY l.customer_id ORDER BY l.seq) AS running
      FROM live l WHERE l.kind IN ('credit', 'opening')),
  uncovered AS (
    SELECT d.id, d.shop_id, d.customer_id,
           least(d.amount, greatest(0, d.running - coalesce(p.paid, 0))) AS remaining
      FROM debt d LEFT JOIN paid p ON p.customer_id = d.customer_id)
  SELECT u.shop_id, u.customer_id, u.id AS entry_id, u.remaining::bigint AS remaining,
         (SELECT p.promised_date FROM promise p WHERE p.entry_id = u.id
           ORDER BY p.created_at DESC, p.id DESC LIMIT 1) AS promised_date
    FROM uncovered u WHERE u.remaining > 0;
$$;

CREATE FUNCTION refresh_open_debts(p_customers uuid[]) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp
AS $$
BEGIN
  DELETE FROM open_debt d WHERE d.customer_id = ANY (p_customers);
  INSERT INTO open_debt (shop_id, customer_id, entry_id, remaining, promised_date)
  SELECT o.shop_id, o.customer_id, o.entry_id, o.remaining, o.promised_date FROM open_debts_of(p_customers) o;
END $$;

-- After entries are added: once per statement, for the customers it touched. A reversal names the
-- customer of the entry it reverses, like every entry.
CREATE FUNCTION open_debt_after_entries() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp
AS $$
BEGIN
  PERFORM refresh_open_debts(array(SELECT DISTINCT i.customer_id FROM inserted i));
  RETURN NULL;
END $$;

CREATE TRIGGER open_debt_after_entries
  AFTER INSERT ON ledger_entry
  REFERENCING NEW TABLE AS inserted
  FOR EACH STATEMENT EXECUTE FUNCTION open_debt_after_entries();

-- After promises are added: the promised date of an entry changed, or was first set.
CREATE FUNCTION open_debt_after_promises() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp
AS $$
BEGIN
  PERFORM refresh_open_debts(array(
    SELECT DISTINCT e.customer_id FROM inserted i JOIN ledger_entry e ON e.id = i.entry_id));
  RETURN NULL;
END $$;

CREATE TRIGGER open_debt_after_promises
  AFTER INSERT ON promise
  REFERENCING NEW TABLE AS inserted
  FOR EACH STATEMENT EXECUTE FUNCTION open_debt_after_promises();

-- Where the stored rows and the ledger differ, for one shop or for all (NULL). Empty when all is well.
-- For tests, for the load test's check after a run, and for an operator who doubts a figure.
CREATE FUNCTION open_debt_mismatches(p_shop uuid)
RETURNS TABLE (entry_id uuid, stored_remaining bigint, ledger_remaining bigint,
               stored_promised date, ledger_promised date)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
AS $$
  WITH ledger AS (
    WITH live AS (
      SELECT e.id, e.shop_id, e.customer_id, e.seq, e.kind, e.amount
        FROM ledger_entry e
       WHERE (p_shop IS NULL OR e.shop_id = p_shop)
         AND NOT EXISTS (SELECT 1 FROM ledger_entry r WHERE r.reverses_id = e.id)),
    paid AS (SELECT customer_id, sum(amount) AS paid FROM live WHERE kind = 'payment' GROUP BY customer_id),
    debt AS (
      SELECT l.id, l.shop_id, l.customer_id, l.amount,
             sum(l.amount) OVER (PARTITION BY l.customer_id ORDER BY l.seq) AS running
        FROM live l WHERE l.kind IN ('credit', 'opening')),
    uncovered AS (
      SELECT d.id, d.shop_id, d.customer_id,
             least(d.amount, greatest(0, d.running - coalesce(p.paid, 0))) AS remaining
        FROM debt d LEFT JOIN paid p ON p.customer_id = d.customer_id)
    SELECT u.shop_id, u.customer_id, u.id AS entry_id, u.remaining::bigint AS remaining,
           (SELECT p.promised_date FROM promise p WHERE p.entry_id = u.id
             ORDER BY p.created_at DESC, p.id DESC LIMIT 1) AS promised_date
      FROM uncovered u WHERE u.remaining > 0)
  SELECT coalesce(s.entry_id, l.entry_id), s.remaining, l.remaining, s.promised_date, l.promised_date
    FROM (SELECT * FROM open_debt d WHERE p_shop IS NULL OR d.shop_id = p_shop) s
    FULL JOIN ledger l ON l.entry_id = s.entry_id
   WHERE s.entry_id IS NULL OR l.entry_id IS NULL
      OR s.remaining <> l.remaining
      OR s.promised_date IS DISTINCT FROM l.promised_date
      OR s.customer_id <> l.customer_id OR s.shop_id <> l.shop_id;
$$;

REVOKE ALL ON FUNCTION open_debts_of(uuid[]) FROM PUBLIC;
REVOKE ALL ON FUNCTION refresh_open_debts(uuid[]) FROM PUBLIC;
REVOKE ALL ON FUNCTION open_debt_after_entries() FROM PUBLIC;
REVOKE ALL ON FUNCTION open_debt_after_promises() FROM PUBLIC;
REVOKE ALL ON FUNCTION open_debt_mismatches(uuid) FROM PUBLIC;
-- None of these is granted to the application. The table is rewritten only by the triggers, so no
-- request can store a figure the ledger does not give; and the comparison crosses shops, so it is for the
-- migration owner (tests, the load test's check, an operator) and not for a request.

-- Everything recorded so far.
SELECT refresh_open_debts(array(SELECT c.id FROM customer c));

-- Erasing a shop erases its stored open debts too: the function of 0022 with one more table.
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
  DELETE FROM open_debt WHERE shop_id = p_shop_id;
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

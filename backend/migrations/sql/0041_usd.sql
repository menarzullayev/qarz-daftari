-- US dollars beside Uzbek so'm (the founder's decision 8 of 2026-10-09: separate balances, no conversion).
--
-- Every row that holds an amount of a customer's debt says which currency it is in. The default is so'm,
-- so every row written before this migration, and every statement that does not name a currency, means
-- what it meant before. Dollars are whole cents in the same bigint columns (qarz.domain.money): nothing
-- is a fraction, and no statement ever adds a so'm amount to a dollar one.
--
-- A customer's so'm and dollars are two books in one account: one sequence of entries, and within it
-- the payments of a currency cover the debts of that currency, oldest first (BR-3).

-- A shop works in dollars when its owner says so (and the platform switch `usd_on` allows it; that half
-- is the application's). Dollars have a credit limit of their own; the existing one stays so'm.
ALTER TABLE shop
  ADD COLUMN usd_on boolean NOT NULL DEFAULT false,
  ADD COLUMN default_credit_limit_usd bigint CHECK (default_credit_limit_usd > 0);    -- whole cents
ALTER TABLE customer
  ADD COLUMN credit_limit_usd bigint CHECK (credit_limit_usd > 0);                    -- whole cents

-- A constant default: PostgreSQL stores it once and rewrites no row, so this is quick on a large ledger.
ALTER TABLE ledger_entry
  ADD COLUMN currency text NOT NULL DEFAULT 'UZS' CONSTRAINT ledger_entry_currency CHECK (currency IN ('UZS', 'USD'));
ALTER TABLE open_debt
  ADD COLUMN currency text NOT NULL DEFAULT 'UZS' CONSTRAINT open_debt_currency CHECK (currency IN ('UZS', 'USD'));
ALTER TABLE payment_notice
  ADD COLUMN currency text NOT NULL DEFAULT 'UZS' CONSTRAINT payment_notice_currency CHECK (currency IN ('UZS', 'USD'));
-- Measurement events keep their currency so that a sum is never of two; the weekly figures count so'm.
ALTER TABLE measure.event
  ADD COLUMN currency text NOT NULL DEFAULT 'UZS' CONSTRAINT event_currency CHECK (currency IN ('UZS', 'USD'));

-- A reminder states what is owed in each currency. One of the two is above zero.
ALTER TABLE reminder DROP CONSTRAINT reminder_amount_check;
ALTER TABLE reminder
  ADD COLUMN amount_usd bigint NOT NULL DEFAULT 0,                                    -- whole cents
  ADD CONSTRAINT reminder_amounts CHECK (amount >= 0 AND amount_usd >= 0 AND (amount > 0 OR amount_usd > 0));

-- A reversal undoes an entry of its own currency (INV-6 says it carries the same amount; an amount is
-- only the same in the same currency). Checked for reversals alone, so other entries pay nothing for it.
CREATE FUNCTION ledger_reversal_currency() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_temp
AS $$
BEGIN
  IF NEW.currency IS DISTINCT FROM (SELECT e.currency FROM ledger_entry e WHERE e.id = NEW.reverses_id) THEN
    RAISE EXCEPTION 'a reversal carries the currency of the entry it reverses'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'ledger_reversal_currency';
  END IF;
  RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION ledger_reversal_currency() FROM PUBLIC;

CREATE TRIGGER ledger_reversal_currency
  BEFORE INSERT ON ledger_entry
  FOR EACH ROW WHEN (NEW.reverses_id IS NOT NULL)
  EXECUTE FUNCTION ledger_reversal_currency();

-- Open debts (0026), per currency: a debt's unpaid part is what its running total, among the debts of
-- its currency, exceeds the customer's payments in that currency by. The result gains a column, and a
-- function's result cannot be changed in place, so it is dropped and created again; like before it is
-- granted to no role.
DROP FUNCTION open_debts_of(uuid[]);

CREATE FUNCTION open_debts_of(p_customers uuid[])
RETURNS TABLE (shop_id uuid, customer_id uuid, entry_id uuid, currency text, remaining bigint, promised_date date)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
AS $$
  WITH live AS (
    SELECT e.id, e.shop_id, e.customer_id, e.currency, e.seq, e.kind, e.amount
      FROM ledger_entry e
     WHERE e.customer_id = ANY (p_customers)
       AND NOT EXISTS (SELECT 1 FROM ledger_entry r WHERE r.reverses_id = e.id)),
  paid AS (
    SELECT customer_id, currency, sum(amount) AS paid FROM live WHERE kind = 'payment'
     GROUP BY customer_id, currency),
  debt AS (
    SELECT l.id, l.shop_id, l.customer_id, l.currency, l.amount,
           sum(l.amount) OVER (PARTITION BY l.customer_id, l.currency ORDER BY l.seq) AS running
      FROM live l WHERE l.kind IN ('credit', 'opening')),
  uncovered AS (
    SELECT d.id, d.shop_id, d.customer_id, d.currency,
           least(d.amount, greatest(0, d.running - coalesce(p.paid, 0))) AS remaining
      FROM debt d LEFT JOIN paid p ON p.customer_id = d.customer_id AND p.currency = d.currency)
  SELECT u.shop_id, u.customer_id, u.id AS entry_id, u.currency, u.remaining::bigint AS remaining,
         (SELECT p.promised_date FROM promise p WHERE p.entry_id = u.id
           ORDER BY p.created_at DESC, p.id DESC LIMIT 1) AS promised_date
    FROM uncovered u WHERE u.remaining > 0;
$$;
REVOKE ALL ON FUNCTION open_debts_of(uuid[]) FROM PUBLIC;

CREATE OR REPLACE FUNCTION refresh_open_debts(p_customers uuid[]) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp
AS $$
BEGIN
  DELETE FROM open_debt d WHERE d.customer_id = ANY (p_customers);
  INSERT INTO open_debt (shop_id, customer_id, entry_id, currency, remaining, promised_date)
  SELECT o.shop_id, o.customer_id, o.entry_id, o.currency, o.remaining, o.promised_date
    FROM open_debts_of(p_customers) o;
END $$;

-- The comparison with the ledger, with the currency compared too. Its result is unchanged.
CREATE OR REPLACE FUNCTION open_debt_mismatches(p_shop uuid)
RETURNS TABLE (entry_id uuid, stored_remaining bigint, ledger_remaining bigint,
               stored_promised date, ledger_promised date)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
AS $$
  WITH ledger AS (
    WITH live AS (
      SELECT e.id, e.shop_id, e.customer_id, e.currency, e.seq, e.kind, e.amount
        FROM ledger_entry e
       WHERE (p_shop IS NULL OR e.shop_id = p_shop)
         AND NOT EXISTS (SELECT 1 FROM ledger_entry r WHERE r.reverses_id = e.id)),
    paid AS (
      SELECT customer_id, currency, sum(amount) AS paid FROM live WHERE kind = 'payment'
       GROUP BY customer_id, currency),
    debt AS (
      SELECT l.id, l.shop_id, l.customer_id, l.currency, l.amount,
             sum(l.amount) OVER (PARTITION BY l.customer_id, l.currency ORDER BY l.seq) AS running
        FROM live l WHERE l.kind IN ('credit', 'opening')),
    uncovered AS (
      SELECT d.id, d.shop_id, d.customer_id, d.currency,
             least(d.amount, greatest(0, d.running - coalesce(p.paid, 0))) AS remaining
        FROM debt d LEFT JOIN paid p ON p.customer_id = d.customer_id AND p.currency = d.currency)
    SELECT u.shop_id, u.customer_id, u.id AS entry_id, u.currency, u.remaining::bigint AS remaining,
           (SELECT p.promised_date FROM promise p WHERE p.entry_id = u.id
             ORDER BY p.created_at DESC, p.id DESC LIMIT 1) AS promised_date
      FROM uncovered u WHERE u.remaining > 0)
  SELECT coalesce(s.entry_id, l.entry_id), s.remaining, l.remaining, s.promised_date, l.promised_date
    FROM (SELECT * FROM open_debt d WHERE p_shop IS NULL OR d.shop_id = p_shop) s
    FULL JOIN ledger l ON l.entry_id = s.entry_id
   WHERE s.entry_id IS NULL OR l.entry_id IS NULL
      OR s.remaining <> l.remaining
      OR s.currency <> l.currency
      OR s.promised_date IS DISTINCT FROM l.promised_date
      OR s.customer_id <> l.customer_id OR s.shop_id <> l.shop_id;
$$;

-- A person's own accounts (0009) added up every entry of the account. Now the so'm balance and the
-- dollar balance are two columns, with whether the shop works in dollars. The result changes, so the
-- function is dropped and created again and stays the application's alone.
DROP FUNCTION my_accounts(uuid);

CREATE FUNCTION my_accounts(p_user_id uuid)
RETURNS TABLE (link_id uuid, shop_id uuid, shop_name text, customer_id uuid, display_name text,
               balance bigint, balance_usd bigint, usd_on boolean)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  SELECT l.id, s.id, s.name, c.id, c.display_name,
         coalesce(b.uzs, 0)::bigint, coalesce(b.usd, 0)::bigint, s.usd_on
    FROM customer_link l
    JOIN shop s ON s.id = l.shop_id
    JOIN customer c ON c.id = l.customer_id
    LEFT JOIN LATERAL (
      SELECT sum(CASE WHEN e.kind IN ('credit', 'opening') THEN e.amount ELSE -e.amount END)
               FILTER (WHERE e.currency = 'UZS') AS uzs,
             sum(CASE WHEN e.kind IN ('credit', 'opening') THEN e.amount ELSE -e.amount END)
               FILTER (WHERE e.currency = 'USD') AS usd
        FROM ledger_entry e
       WHERE e.customer_id = c.id
         AND e.kind <> 'reversal'
         AND NOT EXISTS (SELECT 1 FROM ledger_entry r WHERE r.reverses_id = e.id)
    ) b ON true
   WHERE l.user_id = p_user_id
     AND l.status IN ('active', 'unreachable')
     AND s.status <> 'erased'
   ORDER BY l.created_at, l.id;
$$;
REVOKE ALL ON FUNCTION my_accounts(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION my_accounts(uuid) TO qd_app;

-- Erasing a shop leaves a tombstone with nothing of the shop in it: the function of 0026 with the two
-- new settings cleared as well.
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
         default_credit_limit = NULL, usd_on = false, default_credit_limit_usd = NULL
   WHERE id = p_shop_id;

  IF people IS NOT NULL THEN
    FOREACH person IN ARRAY people LOOP
      PERFORM forget_user_if_unused(person);
    END LOOP;
  END IF;
  RETURN true;
END $$;

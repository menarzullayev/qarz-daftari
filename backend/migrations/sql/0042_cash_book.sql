-- The cash book (the founder's decision 5 of 2026-10-09: income, expense, categories, cash and card;
-- expansion module H).
--
-- Until now the service knew what customers owe and nothing about the money in the till. A shop can now
-- keep a cash book: every amount that came in or went out, by which way it was paid (cash, card,
-- transfer), in which currency, under which category, on which day.
--
-- All of it is behind the platform switch `cash_book_on`, which is off unless an administrator turns it
-- on. While it is off no row is written to either table below.
--
-- An entry is never changed and never deleted: a wrong one is cancelled with a reason and stays, the
-- same principle as the ledger's (ADR-005). The database holds that itself: the application may insert a
-- row and may set the three columns of a cancellation, once, and nothing else.

CREATE TABLE cash_category (
  id           uuid PRIMARY KEY,
  shop_id      uuid NOT NULL REFERENCES shop(id),
  direction    text NOT NULL CHECK (direction IN ('income', 'expense')),
  name         text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 60),
  name_norm    text NOT NULL CHECK (char_length(name_norm) BETWEEN 1 AND 60),
  -- Set on a category the service itself writes to and looks up by this key, whatever the shop has
  -- renamed it to: 'debt_repaid' is where a customer's payment in the ledger lands. Null on every other.
  system_key   text CHECK (system_key ~ '^[a-z_]{1,32}$'),
  archived_at  timestamptz,                          -- an archived category takes no new entry
  created_at   timestamptz NOT NULL DEFAULT now(),
  -- Income and expense have separate lists: the same name may be in both.
  CONSTRAINT cash_category_name UNIQUE (shop_id, direction, name_norm),
  CONSTRAINT cash_category_system_key UNIQUE (shop_id, system_key),
  -- What an entry's foreign key points at: its category is of its own shop and of its own direction.
  CONSTRAINT cash_category_of_direction UNIQUE (id, shop_id, direction),
  -- The service's own category cannot be put away: payments would have nowhere to land.
  CONSTRAINT cash_category_system_stays CHECK (system_key IS NULL OR archived_at IS NULL)
);

CREATE TABLE cash_entry (
  id               uuid PRIMARY KEY,
  shop_id          uuid NOT NULL REFERENCES shop(id),
  direction        text NOT NULL CHECK (direction IN ('income', 'expense')),
  method           text NOT NULL CHECK (method IN ('cash', 'card', 'transfer')),
  -- The convention of migration 0041: whole so'm, or whole cents; never a sum of the two.
  currency         text NOT NULL DEFAULT 'UZS' CONSTRAINT cash_entry_currency CHECK (currency IN ('UZS', 'USD')),
  amount           bigint NOT NULL CHECK (amount > 0),
  category_id      uuid NOT NULL,
  note             text CHECK (char_length(note) BETWEEN 1 AND 200),
  day              date NOT NULL,                    -- the Tashkent day the money belongs to
  created_at       timestamptz NOT NULL DEFAULT now(),   -- when it was written
  author_id        uuid NOT NULL REFERENCES membership(id),
  -- Set on an entry the ledger wrote: the customer's payment this money is. Such an entry is cancelled
  -- by reversing that payment and in no other way.
  ledger_entry_id  uuid REFERENCES ledger_entry(id),
  cancelled_at     timestamptz,
  cancelled_by     uuid REFERENCES membership(id),
  cancel_reason    text CHECK (char_length(cancel_reason) BETWEEN 1 AND 300),
  CONSTRAINT cash_entry_category FOREIGN KEY (category_id, shop_id, direction)
    REFERENCES cash_category (id, shop_id, direction),
  -- A payment is money received; nothing the ledger holds is money paid out.
  CONSTRAINT cash_entry_ledger_is_income CHECK (ledger_entry_id IS NULL OR direction = 'income'),
  -- A cancellation says when and who. It says why as well, unless the ledger did it: the reason is
  -- then the reversal itself.
  CONSTRAINT cash_entry_cancellation CHECK (
    (cancelled_at IS NULL AND cancelled_by IS NULL AND cancel_reason IS NULL)
    OR (cancelled_at IS NOT NULL AND cancelled_by IS NOT NULL
        AND (cancel_reason IS NOT NULL OR ledger_entry_id IS NOT NULL))
  )
);
-- A payment of the ledger is in the cash book once, ever: recorded twice, or copied again by the
-- backfill, it is refused here.
CREATE UNIQUE INDEX cash_entry_one_per_payment ON cash_entry (ledger_entry_id) WHERE ledger_entry_id IS NOT NULL;
-- A day's book, newest first, and a page of it by key.
CREATE INDEX cash_entry_day ON cash_entry (shop_id, day, created_at, id);
-- Every total and balance: the entries that stand, by day, answered from the index alone.
CREATE INDEX cash_entry_standing ON cash_entry (shop_id, day)
  INCLUDE (direction, method, currency, amount, category_id)
  WHERE cancelled_at IS NULL;
-- Whether a category is used (it cannot be deleted while it is).
CREATE INDEX cash_entry_category ON cash_entry (category_id);

-- A cancellation is written once and never taken back. The application's right to update is on the
-- three cancellation columns alone (below), so this is all that is left to refuse.
CREATE FUNCTION cash_entry_cancel_once() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_temp
AS $$
BEGIN
  IF OLD.cancelled_at IS NOT NULL OR NEW.cancelled_at IS NULL THEN
    RAISE EXCEPTION 'a cash book entry is cancelled once and stays cancelled'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'cash_entry_cancel_once';
  END IF;
  RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION cash_entry_cancel_once() FROM PUBLIC;

CREATE TRIGGER cash_entry_cancel_once
  BEFORE UPDATE ON cash_entry
  FOR EACH ROW EXECUTE FUNCTION cash_entry_cancel_once();

-- An entry the ledger wrote is that payment: its amount and currency, in the same shop. Checked when
-- such a row is written, so other entries pay nothing for it.
CREATE FUNCTION cash_entry_matches_payment() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_temp
AS $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM ledger_entry e
     WHERE e.id = NEW.ledger_entry_id AND e.shop_id = NEW.shop_id AND e.kind = 'payment'
       AND e.amount = NEW.amount AND e.currency = NEW.currency
  ) THEN
    RAISE EXCEPTION 'a cash book entry of the ledger carries the amount and currency of a payment of its shop'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'cash_entry_matches_payment';
  END IF;
  RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION cash_entry_matches_payment() FROM PUBLIC;

CREATE TRIGGER cash_entry_matches_payment
  BEFORE INSERT ON cash_entry
  FOR EACH ROW WHEN (NEW.ledger_entry_id IS NOT NULL)
  EXECUTE FUNCTION cash_entry_matches_payment();

ALTER TABLE cash_category ENABLE ROW LEVEL SECURITY;
ALTER TABLE cash_category FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant ON cash_category
  USING (shop_id = nullif(current_setting('qd.shop_id', true), '')::uuid)
  WITH CHECK (shop_id = nullif(current_setting('qd.shop_id', true), '')::uuid);

ALTER TABLE cash_entry ENABLE ROW LEVEL SECURITY;
ALTER TABLE cash_entry FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant ON cash_entry
  USING (shop_id = nullif(current_setting('qd.shop_id', true), '')::uuid)
  WITH CHECK (shop_id = nullif(current_setting('qd.shop_id', true), '')::uuid);

-- The ordinary application writes an entry and cancels one; it never deletes one and never rewrites what
-- it says. A category it may rename, archive and, while nothing uses it (the foreign key above), delete.
-- The worker reads both to write the shop's export; it writes neither. The administrators' side has
-- nothing to do here.
GRANT SELECT, INSERT ON cash_entry TO qd_app;
GRANT UPDATE (cancelled_at, cancelled_by, cancel_reason) ON cash_entry TO qd_app;
GRANT SELECT, INSERT, DELETE ON cash_category TO qd_app;
GRANT UPDATE (name, name_norm, archived_at) ON cash_category TO qd_app;
GRANT SELECT ON cash_entry, cash_category TO qd_worker;

-- Erasing a shop erases its cash book: the function of 0041 with two more tables. The entries go before
-- the ledger entries and the members they point at, the categories after the entries.
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
  DELETE FROM cash_entry WHERE shop_id = p_shop_id;
  DELETE FROM cash_category WHERE shop_id = p_shop_id;
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
  DELETE FROM customer_share WHERE shop_id = p_shop_id;
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
         default_credit_limit = NULL, share_phone = NULL, usd_on = false, default_credit_limit_usd = NULL
   WHERE id = p_shop_id;

  IF people IS NOT NULL THEN
    FOREACH person IN ARRAY people LOOP
      PERFORM forget_user_if_unused(person);
    END LOOP;
  END IF;
  RETURN true;
END $$;

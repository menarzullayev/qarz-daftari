-- Online payment of the subscription through Payme and Click (REQ-056, ADR-019).
-- Built and switched off: the platform setting `online_pay_on` is absent, which means off, and the
-- provider endpoints answer "disabled" until an administrator turns it on and the keys are configured.

-- One order to pay for a number of months. Its identifier is what the provider is given as the account
-- (Payme) or the merchant transaction (Click). The approved schema has no table for it.
CREATE TABLE online_payment (
  id            uuid PRIMARY KEY,
  prepare_id    bigint GENERATED ALWAYS AS IDENTITY UNIQUE,   -- Click wants an integer of ours back
  shop_id       uuid NOT NULL REFERENCES shop(id),
  months        smallint NOT NULL CHECK (months BETWEEN 1 AND 36),
  amount        bigint NOT NULL CHECK (amount > 0),            -- whole UZS, fixed when the order is made
  state         text NOT NULL DEFAULT 'created' CHECK (state IN ('created', 'pending', 'paid', 'cancelled')),
  provider      text CHECK (provider IN ('payme', 'click')),
  provider_txn  text,
  provider_time bigint,                                        -- Payme: the time it gave, in milliseconds
  cancel_reason smallint,
  created_at    timestamptz NOT NULL DEFAULT now(),
  started_at    timestamptz,
  paid_at       timestamptz,
  cancelled_at  timestamptz,
  UNIQUE (provider, provider_txn),
  CHECK ((state = 'created') = (provider IS NULL)),
  CHECK ((state = 'paid') = (paid_at IS NOT NULL))
);
CREATE INDEX online_payment_shop ON online_payment (shop_id, created_at DESC);

GRANT SELECT, INSERT, UPDATE ON online_payment TO qd_app;
ALTER TABLE online_payment ENABLE ROW LEVEL SECURITY;
ALTER TABLE online_payment FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant ON online_payment
  USING (shop_id = nullif(current_setting('qd.shop_id', true), '')::uuid)
  WITH CHECK (shop_id = nullif(current_setting('qd.shop_id', true), '')::uuid);

-- A provider names an order or its own transaction, not a shop. These two answer only "which shop",
-- so that the rest is done inside that shop's row-level security like everything else.
CREATE FUNCTION online_payment_shop(p_order_id uuid) RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
AS $$ SELECT shop_id FROM online_payment WHERE id = p_order_id; $$;

CREATE FUNCTION online_payment_shop_by_txn(p_provider text, p_txn text) RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
AS $$ SELECT shop_id FROM online_payment WHERE provider = p_provider AND provider_txn = p_txn; $$;

-- Payme asks for its transactions in a period, across shops. Nothing but what Payme itself sent or was
-- told is returned.
CREATE FUNCTION payme_statement(p_from bigint, p_to bigint)
RETURNS TABLE (id uuid, provider_txn text, provider_time bigint, amount bigint, state text,
               cancel_reason smallint, started_at timestamptz, paid_at timestamptz, cancelled_at timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
AS $$
  SELECT id, provider_txn, provider_time, amount, state, cancel_reason, started_at, paid_at, cancelled_at
    FROM online_payment
   WHERE provider = 'payme' AND provider_time BETWEEN p_from AND p_to
   ORDER BY provider_time, id;
$$;

REVOKE ALL ON FUNCTION online_payment_shop(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION online_payment_shop_by_txn(text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION payme_statement(bigint, bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION online_payment_shop(uuid) TO qd_app;
GRANT EXECUTE ON FUNCTION online_payment_shop_by_txn(text, text) TO qd_app;
GRANT EXECUTE ON FUNCTION payme_statement(bigint, bigint) TO qd_app;

-- Erasing a shop erases its orders too: the function of 0017 with one more table.
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

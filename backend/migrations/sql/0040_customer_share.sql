-- A customer's secret read-only link (the expansion of 2026-10-09, decision 12; module B).
--
-- Until now a customer saw what they owe only by connecting their Telegram account to the shop through
-- the bot. A customer without Telegram saw nothing. A member of staff can now hand a customer a link that
-- shows that one account, read-only, to whoever holds it. The link is a secret: only the SHA-256 of its
-- token is stored, so neither a copy of the database nor anyone who reads it can open an account.
--
-- All of it is behind the platform switch `customer_links_on`, which is off unless an administrator
-- turns it on. While it is off no row is written here and the lookup below finds nothing.

CREATE TABLE customer_share (
  id              uuid PRIMARY KEY,
  shop_id         uuid NOT NULL REFERENCES shop(id),
  customer_id     uuid NOT NULL REFERENCES customer(id),
  token_hash      bytea NOT NULL UNIQUE CHECK (octet_length(token_hash) = 32),   -- SHA-256 of the token
  created_by      uuid NOT NULL REFERENCES membership(id),
  created_at      timestamptz NOT NULL DEFAULT now(),
  expires_at      timestamptz NOT NULL,
  revoked_at      timestamptz,                         -- by staff, by a newer link, or with the customer's data
  last_opened_at  timestamptz,
  opened_on       date,                                -- the last (Tashkent) day an opening was put in the activity log
  CHECK (expires_at > created_at)
);
-- One link per customer can be alive: making a new one ends the one before it in the same transaction.
CREATE UNIQUE INDEX one_live_share_per_customer ON customer_share (customer_id) WHERE revoked_at IS NULL;
CREATE INDEX customer_share_shop ON customer_share (shop_id);

ALTER TABLE customer_share ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer_share FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant ON customer_share
  USING (shop_id = nullif(current_setting('qd.shop_id', true), '')::uuid)
  WITH CHECK (shop_id = nullif(current_setting('qd.shop_id', true), '')::uuid);

-- The ordinary application makes a link, ends it, and notes that it was opened; it never deletes one and
-- never rewrites whose it is, its token or its expiry. The worker ends a customer's links when it
-- completes the removal of their data (undoing an import can settle the debt that removal waited for),
-- and for that it needs to find the rows by customer. The administrators' side has nothing to do here.
GRANT SELECT, INSERT ON customer_share TO qd_app;
GRANT UPDATE (revoked_at, last_opened_at, opened_on) ON customer_share TO qd_app;
GRANT SELECT (customer_id, revoked_at), UPDATE (revoked_at) ON customer_share TO qd_worker;

-- The phone a shop wants its customers to call, shown on the page behind a link. A shop had no phone of
-- its own until now. Null: the page shows none.
ALTER TABLE shop ADD COLUMN share_phone text CHECK (share_phone ~ '^\+[0-9]{8,15}$');

-- Whoever opens a link is nobody to the service: not a member, not a signed-in user. So the question
-- "which shop and which customer is behind this token" is answered here, outside any shop, the way
-- `my_link` answers it for a connected customer. It answers for a link that is alive and nothing else:
-- an unknown token, an ended link, an expired one, a customer whose data was removed, a shop that is
-- being deleted or is erased, and the switch being off all give the same empty result, so the caller
-- cannot tell them apart. The application then reads inside that shop's row-level security and narrows
-- to the one customer itself.
--
-- The moment is the application's clock, like every other moment it compares with.
CREATE FUNCTION customer_share_lookup(p_token_hash bytea, p_now timestamptz)
RETURNS TABLE (share_id uuid, shop_id uuid, customer_id uuid, token_hash bytea)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  SELECT cs.id, cs.shop_id, cs.customer_id, cs.token_hash
    FROM customer_share cs
    JOIN shop s ON s.id = cs.shop_id
    JOIN customer c ON c.id = cs.customer_id
   WHERE cs.token_hash = p_token_hash
     AND cs.revoked_at IS NULL
     AND cs.expires_at > p_now
     AND s.status = 'active'
     AND c.status <> 'anonymized'
     AND EXISTS (
       SELECT 1 FROM platform_setting p WHERE p.key = 'customer_links_on' AND p.value = 'true'::jsonb
     );
$$;

REVOKE ALL ON FUNCTION customer_share_lookup(bytea, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION customer_share_lookup(bytea, timestamptz) TO qd_app;

-- Erasing a shop erases its links and the phone: the function of 0026 with one more table and one more
-- column of the tombstone.
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
         default_credit_limit = NULL, share_phone = NULL
   WHERE id = p_shop_id;

  IF people IS NOT NULL THEN
    FOREACH person IN ARRAY people LOOP
      PERFORM forget_user_if_unused(person);
    END LOOP;
  END IF;
  RETURN true;
END $$;

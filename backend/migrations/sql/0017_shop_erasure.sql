-- Erasing a shop after its waiting period (REQ-048; domain rule BR-25).
--
-- The application role cannot delete ledger entries, goods lines, promises or activity: the ledger is
-- insert-only for it, and stays so. Erasure is the one exception, and it is a function of its own that
-- checks for itself that the shop was asked to be deleted and that its waiting period is over. It cannot
-- be used to erase any other shop, whatever the application passes. The clock is the database's own, so
-- the application cannot shorten the waiting period either.

-- Shops whose waiting period has ended, with where to tell the owner that it is done.
CREATE FUNCTION shops_to_erase()
RETURNS TABLE (shop_id uuid, shop_name text, owner_tg bigint, owner_lang text)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT s.id, s.name, u.tg_id, u.lang
    FROM shop s
    LEFT JOIN membership m ON m.shop_id = s.id AND m.role = 'owner' AND m.status = 'active'
    LEFT JOIN app_user u ON u.id = m.user_id
   WHERE s.status = 'deletion_pending' AND s.deletion_due <= now()
   ORDER BY s.deletion_due, s.id;
$$;

-- Returns true when the shop was erased by this call.
CREATE FUNCTION erase_shop(p_shop_id uuid) RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
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

REVOKE ALL ON FUNCTION shops_to_erase() FROM PUBLIC;
REVOKE ALL ON FUNCTION erase_shop(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION shops_to_erase() TO qd_app;
GRANT EXECUTE ON FUNCTION erase_shop(uuid) TO qd_app;

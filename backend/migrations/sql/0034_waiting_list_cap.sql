-- A cap on the people waiting at one shop's counter (security review, finding 13).
--
-- Anyone who holds a shop's counter code could put any number of accounts on its waiting list, each under
-- a profile name of their choosing. The list is now full at 100 people: those who came within the last
-- 24 hours, which is as long as an entry stays on the list the staff see (BR-16). An older entry is on
-- nobody's list and takes no place. A hundred is one page of any other list of the service, and far more
-- than a counter leaves unattached in a day; staff make room by attaching or dismissing, and a new
-- counter code stops whoever was filling the list.
--
-- The number is held here and not handed in by the caller: the application role calls this function and
-- cannot raise the cap.

-- Make the link after the person agreed. Outcomes:
--   linked   a personal link attached them to their record
--   waiting  a counter code put them on the shop's waiting list
--   already  they are already linked to, or waiting in, this shop
--   taken    the record is linked to someone else
--   full     the shop's waiting list is full; nothing was stored
--   invalid  the code is unknown, used, cancelled or expired, or the customer is archived
CREATE OR REPLACE FUNCTION link_customer(p_token_hash bytea, p_user_id uuid, p_consent_v smallint, p_name text)
RETURNS TABLE (outcome text, shop_id uuid, customer_id uuid)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  inv invitation%ROWTYPE;
  cust customer%ROWTYPE;
  waiting_cap CONSTANT integer := 100;
BEGIN
  SELECT i.* INTO inv FROM invitation i JOIN shop s ON s.id = i.shop_id
   WHERE i.token_hash = p_token_hash AND i.kind IN ('counter', 'customer') AND i.status = 'issued'
     AND (i.expires_at IS NULL OR i.expires_at > now()) AND s.status = 'active'
   FOR UPDATE OF i;
  IF NOT FOUND THEN
    RETURN QUERY SELECT 'invalid'::text, NULL::uuid, NULL::uuid;
    RETURN;
  END IF;

  IF EXISTS (SELECT 1 FROM customer_link l
              WHERE l.shop_id = inv.shop_id AND l.user_id = p_user_id
                AND l.status IN ('waiting', 'active', 'unreachable')) THEN
    RETURN QUERY SELECT 'already'::text, inv.shop_id, NULL::uuid;
    RETURN;
  END IF;

  IF inv.kind = 'counter' THEN
    -- One at a time for a shop, whichever code brought them: two people arriving at once must not both
    -- find the last place free. Held until the transaction ends.
    PERFORM pg_advisory_xact_lock(hashtextextended('waiting-list:' || inv.shop_id::text, 0));
    IF (SELECT count(*) FROM customer_link l
         WHERE l.shop_id = inv.shop_id AND l.status = 'waiting'
           AND l.created_at > now() - interval '24 hours') >= waiting_cap THEN
      RETURN QUERY SELECT 'full'::text, inv.shop_id, NULL::uuid;
      RETURN;
    END IF;
    INSERT INTO customer_link (id, shop_id, customer_id, user_id, status, consent_text_v, consent_at, waiting_name)
    VALUES (gen_random_uuid(), inv.shop_id, NULL, p_user_id, 'waiting', p_consent_v, now(),
            nullif(left(btrim(coalesce(p_name, '')), 80), ''));
    RETURN QUERY SELECT 'waiting'::text, inv.shop_id, NULL::uuid;
    RETURN;
  END IF;

  SELECT * INTO cust FROM customer c WHERE c.id = inv.customer_id AND c.shop_id = inv.shop_id FOR UPDATE;
  IF NOT FOUND OR cust.status <> 'active' THEN
    RETURN QUERY SELECT 'invalid'::text, NULL::uuid, NULL::uuid;
    RETURN;
  END IF;
  IF EXISTS (SELECT 1 FROM customer_link l
              WHERE l.customer_id = cust.id AND l.status IN ('active', 'unreachable')) THEN
    RETURN QUERY SELECT 'taken'::text, inv.shop_id, NULL::uuid;
    RETURN;
  END IF;

  INSERT INTO customer_link (id, shop_id, customer_id, user_id, status, consent_text_v, consent_at)
  VALUES (gen_random_uuid(), inv.shop_id, cust.id, p_user_id, 'active', p_consent_v, now());
  UPDATE invitation SET status = 'used' WHERE token_hash = p_token_hash;
  INSERT INTO activity (id, shop_id, actor_kind, actor_id, action, subject_type, subject_id)
  VALUES (gen_random_uuid(), inv.shop_id, 'customer', NULL, 'customer.linked', 'customer', cust.id);
  RETURN QUERY SELECT 'linked'::text, inv.shop_id, cust.id;
END $$;

-- Replacing a function keeps its owner and who may execute it; said again so that this file can be read
-- alone.
REVOKE ALL ON FUNCTION link_customer(bytea, uuid, smallint, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION link_customer(bytea, uuid, smallint, text) TO qd_app;

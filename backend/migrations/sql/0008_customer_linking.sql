-- Connecting a customer to their record (REQ-013, REQ-014, REQ-021; domain rule BR-16).
--
-- A customer is not a member of the shop, so what they do cannot run under the shop's row-level security
-- context. As with staff invitations, each such step is one SECURITY DEFINER function that takes the
-- authenticated user's identifier and does exactly one thing. The functions' owner must be able to bypass
-- row-level security (see 0002).

-- The waiting list shows the name from the person's Telegram profile (BR-16). It is kept only while
-- the person waits.
ALTER TABLE customer_link ADD COLUMN waiting_name text;
ALTER TABLE customer_link ADD CONSTRAINT waiting_name_length
  CHECK (waiting_name IS NULL OR length(waiting_name) BETWEEN 1 AND 80);
ALTER TABLE customer_link ADD CONSTRAINT waiting_name_only_while_waiting
  CHECK (status = 'waiting' OR waiting_name IS NULL);
-- Nothing is stored about a person before they agree, the waiting entry included.
ALTER TABLE customer_link ADD CONSTRAINT waiting_needs_consent
  CHECK (status <> 'waiting' OR consent_at IS NOT NULL);

-- A consent question waits in the chat like any other question.
ALTER TABLE chat_pending DROP CONSTRAINT chat_pending_kind_check;
ALTER TABLE chat_pending ADD CONSTRAINT chat_pending_kind_check
  CHECK (kind IN ('shop_name', 'entry', 'promise_date', 'consent'));

-- What a counter code or a personal link leads to. Shown to the person before they agree.
CREATE FUNCTION customer_token_info(p_token_hash bytea)
RETURNS TABLE (kind text, shop_id uuid, shop_name text)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT i.kind, i.shop_id, s.name
    FROM invitation i
    JOIN shop s ON s.id = i.shop_id
   WHERE i.token_hash = p_token_hash
     AND i.kind IN ('counter', 'customer')
     AND i.status = 'issued'
     AND (i.expires_at IS NULL OR i.expires_at > now())
     AND s.status = 'active';
$$;

-- Make the link after the person agreed. Outcomes:
--   linked   a personal link attached them to their record
--   waiting  a counter code put them on the shop's waiting list
--   already  they are already linked to, or waiting in, this shop
--   taken    the record is linked to someone else
--   invalid  the code is unknown, used, cancelled or expired, or the customer is archived
CREATE FUNCTION link_customer(p_token_hash bytea, p_user_id uuid, p_consent_v smallint, p_name text)
RETURNS TABLE (outcome text, shop_id uuid, customer_id uuid)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  inv invitation%ROWTYPE;
  cust customer%ROWTYPE;
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

-- The shops a person is linked to as a customer, with what they owe in each (REQ-019).
CREATE FUNCTION my_accounts(p_user_id uuid)
RETURNS TABLE (shop_id uuid, shop_name text, customer_id uuid, display_name text, balance bigint)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT s.id, s.name, c.id, c.display_name,
         coalesce((
           SELECT sum(CASE WHEN e.kind IN ('credit', 'opening') THEN e.amount ELSE -e.amount END)
             FROM ledger_entry e
            WHERE e.customer_id = c.id
              AND e.kind <> 'reversal'
              AND NOT EXISTS (SELECT 1 FROM ledger_entry r WHERE r.reverses_id = e.id)
         ), 0)::bigint
    FROM customer_link l
    JOIN shop s ON s.id = l.shop_id
    JOIN customer c ON c.id = l.customer_id
   WHERE l.user_id = p_user_id
     AND l.status IN ('active', 'unreachable')
     AND s.status <> 'erased'
   ORDER BY l.created_at, l.id;
$$;

-- A customer disconnects, or stops waiting (REQ-021). The ledger keeps its entries.
CREATE FUNCTION end_my_link(p_user_id uuid, p_shop_id uuid) RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  ended customer_link%ROWTYPE;
BEGIN
  UPDATE customer_link
     SET status = 'ended', ended_at = now(), waiting_name = NULL
   WHERE user_id = p_user_id AND shop_id = p_shop_id AND status IN ('waiting', 'active', 'unreachable')
  RETURNING * INTO ended;
  IF NOT FOUND THEN
    RETURN 0;
  END IF;
  IF ended.customer_id IS NOT NULL THEN
    INSERT INTO activity (id, shop_id, actor_kind, actor_id, action, subject_type, subject_id)
    VALUES (gen_random_uuid(), p_shop_id, 'customer', NULL, 'customer.unlinked', 'customer', ended.customer_id);
  END IF;
  RETURN 1;
END $$;

-- A person who had blocked the bot writes to it again: their links can be notified again.
CREATE FUNCTION mark_recipient_reachable(p_user_id uuid) RETURNS integer
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
  WITH changed AS (
    UPDATE customer_link SET status = 'active'
     WHERE user_id = p_user_id AND status = 'unreachable'
    RETURNING 1
  )
  SELECT count(*)::integer FROM changed;
$$;

REVOKE ALL ON FUNCTION customer_token_info(bytea) FROM PUBLIC;
REVOKE ALL ON FUNCTION link_customer(bytea, uuid, smallint, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION my_accounts(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION end_my_link(uuid, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION mark_recipient_reachable(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION customer_token_info(bytea) TO qd_app;
GRANT EXECUTE ON FUNCTION link_customer(bytea, uuid, smallint, text) TO qd_app;
GRANT EXECUTE ON FUNCTION my_accounts(uuid) TO qd_app;
GRANT EXECUTE ON FUNCTION end_my_link(uuid, uuid) TO qd_app;
GRANT EXECUTE ON FUNCTION mark_recipient_reachable(uuid) TO qd_app;

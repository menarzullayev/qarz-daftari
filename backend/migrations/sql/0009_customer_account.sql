-- The customer's own page and the removal of their identifying data (REQ-019, REQ-020, REQ-029; BR-32).
--
-- A customer reaches their account through their link. `my_link` answers one question for the application:
-- does this link belong to this user, and which shop and record is behind it. The application then reads
-- inside that shop's row-level security context and narrows to that one record itself.

DROP FUNCTION my_accounts(uuid);

CREATE FUNCTION my_accounts(p_user_id uuid)
RETURNS TABLE (link_id uuid, shop_id uuid, shop_name text, customer_id uuid, display_name text, balance bigint)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT l.id, s.id, s.name, c.id, c.display_name,
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

CREATE FUNCTION my_link(p_user_id uuid, p_link_id uuid)
RETURNS TABLE (shop_id uuid, customer_id uuid)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT l.shop_id, l.customer_id
    FROM customer_link l
    JOIN shop s ON s.id = l.shop_id
   WHERE l.id = p_link_id
     AND l.user_id = p_user_id
     AND l.status IN ('active', 'unreachable')
     AND s.status <> 'erased';
$$;

-- After a customer's data was removed: if nothing else refers to the person (no membership of any shop,
-- no other link, not an administrator), forget their Telegram identity too and end their sessions.
CREATE FUNCTION forget_user_if_unused(p_user_id uuid) RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM membership m WHERE m.user_id = p_user_id)
     OR EXISTS (SELECT 1 FROM customer_link l WHERE l.user_id = p_user_id)
     OR EXISTS (SELECT 1 FROM admin_account a WHERE a.user_id = p_user_id) THEN
    RETURN false;
  END IF;
  UPDATE app_user SET tg_id = NULL, active_shop = NULL WHERE id = p_user_id AND tg_id IS NOT NULL;
  IF NOT FOUND THEN
    RETURN false;
  END IF;
  UPDATE user_session SET revoked_at = now() WHERE user_id = p_user_id AND revoked_at IS NULL;
  DELETE FROM chat_pending WHERE user_id = p_user_id;
  RETURN true;
END $$;

-- One open removal request per customer.
CREATE UNIQUE INDEX one_waiting_removal_per_customer ON removal_request (customer_id) WHERE status = 'waiting';

REVOKE ALL ON FUNCTION my_accounts(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION my_link(uuid, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION forget_user_if_unused(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION my_accounts(uuid) TO qd_app;
GRANT EXECUTE ON FUNCTION my_link(uuid, uuid) TO qd_app;
GRANT EXECUTE ON FUNCTION forget_user_if_unused(uuid) TO qd_app;

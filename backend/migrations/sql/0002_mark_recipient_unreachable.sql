-- Cross-tenant function for the outbox dispatcher (technical specification, "Cross-tenant functions").
--
-- When Telegram reports that a person blocked the bot, every active customer link of that account must
-- become unreachable, in whichever shops they are. The dispatcher runs as qd_app with no tenant set and
-- therefore sees no rows of customer_link; this narrow function does that one update on its behalf.
--
-- It runs with its owner's rights. The owner (the migration role) must be able to bypass row-level
-- security: tenant tables use FORCE ROW LEVEL SECURITY, which binds a non-superuser owner as well.

CREATE FUNCTION mark_recipient_unreachable(p_tg_id bigint) RETURNS integer
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
  WITH changed AS (
    UPDATE customer_link l
       SET status = 'unreachable'
      FROM app_user u
     WHERE u.tg_id = p_tg_id
       AND l.user_id = u.id
       AND l.status = 'active'
    RETURNING 1
  )
  SELECT count(*)::integer FROM changed;
$$;

REVOKE ALL ON FUNCTION mark_recipient_unreachable(bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION mark_recipient_unreachable(bigint) TO qd_app;

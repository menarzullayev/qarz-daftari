-- A month paid for during the trial is counted from the trial's last day (the founder's decision of
-- 2026-10-09).
--
-- Until now the months were counted from the day of payment unless a paid period was still running, so
-- an owner who paid on the first day of a thirty-day trial was paid through the very day the trial would
-- have ended, and the payment bought nothing. The rule is the domain layer's
-- (qarz.domain.subscription.after_payment); it needs the trial's last day, which the administrator's side
-- and an online payment already read. The review group's reading of a receipt (0030) did not return it.
-- A function's result columns cannot be changed in place, so it is dropped and created again; nothing
-- else in it is changed, and it stays the application's alone (0031).
DROP FUNCTION review_group_receipt(bigint, uuid, boolean);

CREATE FUNCTION review_group_receipt(p_group bigint, p_receipt uuid, p_lock boolean)
RETURNS TABLE (
  receipt_id uuid, shop_id uuid, shop_name text, stated_amount bigint, stated_months smallint, status text,
  state text, trial_ends date, paid_through date, owner_tg bigint, owner_lang text
)
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
#variable_conflict use_column
BEGIN
  IF p_group IS NULL OR NOT EXISTS (
    SELECT 1 FROM platform_setting p
     WHERE p.key = 'review_group' AND jsonb_typeof(p.value) = 'number' AND p.value = to_jsonb(p_group)
  ) THEN
    RETURN;
  END IF;
  IF p_lock THEN
    PERFORM 1 FROM subscription_receipt r WHERE r.id = p_receipt FOR UPDATE;
    PERFORM 1 FROM subscription sub
      WHERE sub.shop_id = (SELECT r.shop_id FROM subscription_receipt r WHERE r.id = p_receipt)
        FOR UPDATE;
  END IF;
  RETURN QUERY
    SELECT r.id, r.shop_id, s.name, r.stated_amount, r.stated_months, r.status,
           sub.state, sub.trial_ends, sub.paid_through, o.tg_id, o.lang
      FROM subscription_receipt r
      JOIN shop s ON s.id = r.shop_id
      LEFT JOIN subscription sub ON sub.shop_id = r.shop_id
      LEFT JOIN LATERAL (
        SELECT u.tg_id, u.lang FROM membership m JOIN app_user u ON u.id = m.user_id
         WHERE m.shop_id = s.id AND m.role = 'owner' AND m.status = 'active'
      ) o ON true
     WHERE r.id = p_receipt AND s.status <> 'erased';
END $$;

REVOKE ALL ON FUNCTION review_group_receipt(bigint, uuid, boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION review_group_receipt(bigint, uuid, boolean) TO qd_app;

-- The daily review of subscriptions (REQ-057; BR-28; technical specification, "Worker schedule").

-- Shops whose trial or paid period ends in seven days or tomorrow (the owner is warned), or has ended
-- (the shop becomes limited), with where to reach the owner. Identifiers and the owner's chat only.
CREATE FUNCTION subscriptions_to_review(p_today date)
RETURNS TABLE (shop_id uuid, shop_name text, state text, ends_on date, owner_tg bigint, owner_lang text)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT s.id, s.name, sub.state, e.ends_on, u.tg_id, u.lang
    FROM subscription sub
    JOIN shop s ON s.id = sub.shop_id
    CROSS JOIN LATERAL (
      SELECT CASE sub.state WHEN 'trial' THEN sub.trial_ends ELSE sub.paid_through END AS ends_on
    ) e
    LEFT JOIN membership m ON m.shop_id = s.id AND m.role = 'owner' AND m.status = 'active'
    LEFT JOIN app_user u ON u.id = m.user_id
   WHERE s.status = 'active'
     AND sub.state IN ('trial', 'active')
     AND (e.ends_on IS NULL OR e.ends_on < p_today OR e.ends_on - p_today IN (7, 1))
   ORDER BY s.id;
$$;

REVOKE ALL ON FUNCTION subscriptions_to_review(date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION subscriptions_to_review(date) TO qd_app;

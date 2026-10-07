-- Support access (REQ-059; BR-31; INV-10): a logged, time-limited permission for one administrator to
-- see one shop's customers and entries, opened with a reason and shown to the shop's owner.
--
-- The approved table holds who, which shop, why, and from when until when (at most 24 hours, by its
-- check constraint). Ending one early needs to be remembered too.
ALTER TABLE support_access
  ADD COLUMN closed_at  timestamptz,                   -- ended before its time
  ADD COLUMN closed_by  text CHECK (closed_by IN ('owner', 'admin')),
  ADD CHECK ((closed_at IS NULL) = (closed_by IS NULL)),
  ADD CHECK (length(reason) BETWEEN 3 AND 500);
CREATE INDEX support_access_shop ON support_access (shop_id, starts_at DESC);
CREATE INDEX support_access_admin ON support_access (admin_id, ends_at DESC);

-- A row is never removed by the application and only its ending is ever written: the history the owner
-- reads must not be something the application can rewrite.
REVOKE ALL ON support_access FROM qd_app;
GRANT SELECT, INSERT ON support_access TO qd_app;
GRANT UPDATE (closed_at, closed_by) ON support_access TO qd_app;

-- ---------------------------------------------------------------------------
-- The administrator's side of it. `support_access` is a tenant table, and an administrator has no tenant,
-- so opening, closing and listing go through these. Each does nothing unless the given user is an active
-- administrator; the allow-list and the second factor are checked by the application first.
-- ---------------------------------------------------------------------------

-- Whether this administrator may see this shop's data now: their support access that has started, has
-- not run out and was not closed. The specification names this function; every read of a shop's data by
-- an administrator starts with it.
CREATE FUNCTION admin_open_shop(p_admin uuid, p_shop uuid, p_now timestamptz)
RETURNS TABLE (access_id uuid, ends_at timestamptz)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  SELECT sa.id, sa.ends_at
    FROM support_access sa
    JOIN shop s ON s.id = sa.shop_id
   WHERE sa.shop_id = p_shop
     AND sa.admin_id = p_admin
     AND sa.closed_at IS NULL
     AND sa.starts_at <= p_now
     AND sa.ends_at > p_now
     AND s.status <> 'erased'
     AND EXISTS (SELECT 1 FROM admin_account a WHERE a.user_id = p_admin AND a.status = 'active')
   ORDER BY sa.ends_at DESC
   LIMIT 1;
$$;

-- Opens a support access. `outcome` is 'opened', or 'already_open' when this administrator already has
-- one for the shop; no row at all when the caller is not an active administrator or the shop does not
-- exist or was erased. The shop's row is locked, so two requests cannot both find none open. Opening and
-- closing are written to the shop's own activity log here, where the owner reads them.
CREATE FUNCTION admin_support_open(
  p_admin uuid, p_shop uuid, p_id uuid, p_reason text, p_now timestamptz, p_ends timestamptz
)
RETURNS TABLE (outcome text, access_id uuid, shop_name text, owner_tg bigint, owner_lang text)
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  v_name text;
  v_open uuid;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM admin_account a WHERE a.user_id = p_admin AND a.status = 'active') THEN
    RETURN;
  END IF;
  SELECT s.name INTO v_name FROM shop s WHERE s.id = p_shop AND s.status <> 'erased' FOR UPDATE;
  IF NOT FOUND THEN
    RETURN;
  END IF;
  SELECT sa.id INTO v_open FROM support_access sa
   WHERE sa.shop_id = p_shop AND sa.admin_id = p_admin AND sa.closed_at IS NULL AND sa.ends_at > p_now
   LIMIT 1;
  IF v_open IS NULL THEN
    INSERT INTO support_access (id, shop_id, admin_id, reason, starts_at, ends_at)
    VALUES (p_id, p_shop, p_admin, p_reason, p_now, p_ends);
    INSERT INTO activity (id, shop_id, at, actor_kind, actor_id, action, subject_type, subject_id)
    VALUES (gen_random_uuid(), p_shop, p_now, 'admin', p_admin, 'support_access.opened', 'support_access', p_id);
  END IF;
  RETURN QUERY
    SELECT CASE WHEN v_open IS NULL THEN 'opened' ELSE 'already_open' END, coalesce(v_open, p_id), v_name,
           o.tg_id, o.lang
      FROM (SELECT 1) one
      LEFT JOIN LATERAL (
        SELECT u.tg_id, u.lang FROM membership m JOIN app_user u ON u.id = m.user_id
         WHERE m.shop_id = p_shop AND m.role = 'owner' AND m.status = 'active'
      ) o ON true;
END $$;

-- Closes this administrator's open support access for the shop. No row when there was none.
CREATE FUNCTION admin_support_close(p_admin uuid, p_shop uuid, p_now timestamptz)
RETURNS TABLE (access_id uuid, shop_name text, owner_tg bigint, owner_lang text)
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  v_closed uuid;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM admin_account a WHERE a.user_id = p_admin AND a.status = 'active') THEN
    RETURN;
  END IF;
  UPDATE support_access sa
     SET closed_at = p_now, closed_by = 'admin'
   WHERE sa.shop_id = p_shop AND sa.admin_id = p_admin AND sa.closed_at IS NULL AND sa.ends_at > p_now
  RETURNING sa.id INTO v_closed;
  IF v_closed IS NULL THEN
    RETURN;
  END IF;
  INSERT INTO activity (id, shop_id, at, actor_kind, actor_id, action, subject_type, subject_id)
  VALUES (gen_random_uuid(), p_shop, p_now, 'admin', p_admin, 'support_access.closed', 'support_access', v_closed);
  RETURN QUERY
    SELECT v_closed, s.name, o.tg_id, o.lang
      FROM shop s
      LEFT JOIN LATERAL (
        SELECT u.tg_id, u.lang FROM membership m JOIN app_user u ON u.id = m.user_id
         WHERE m.shop_id = s.id AND m.role = 'owner' AND m.status = 'active'
      ) o ON true
     WHERE s.id = p_shop;
END $$;

-- Support accesses across shops, newest first, for the administrators' own overview: every
-- administrator sees what every administrator opened. `p_open_only` keeps those open at `p_now`.
CREATE FUNCTION admin_support_list(
  p_admin uuid, p_shop uuid, p_open_only boolean, p_now timestamptz,
  p_after_start timestamptz, p_after_id uuid, p_limit integer
)
RETURNS TABLE (
  access_id uuid, shop_id uuid, shop_name text, admin_id uuid, reason text,
  starts_at timestamptz, ends_at timestamptz, closed_at timestamptz, closed_by text
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  SELECT sa.id, sa.shop_id, s.name, sa.admin_id, sa.reason, sa.starts_at, sa.ends_at, sa.closed_at, sa.closed_by
    FROM support_access sa
    JOIN shop s ON s.id = sa.shop_id
   WHERE EXISTS (SELECT 1 FROM admin_account a WHERE a.user_id = p_admin AND a.status = 'active')
     AND (p_shop IS NULL OR sa.shop_id = p_shop)
     AND (NOT p_open_only OR (sa.closed_at IS NULL AND sa.ends_at > p_now))
     AND (p_after_start IS NULL OR (sa.starts_at, sa.id) < (p_after_start, p_after_id))
   ORDER BY sa.starts_at DESC, sa.id DESC
   LIMIT least(greatest(p_limit, 1), 101);
$$;

REVOKE ALL ON FUNCTION admin_open_shop(uuid, uuid, timestamptz) FROM PUBLIC;
REVOKE ALL ON FUNCTION admin_support_open(uuid, uuid, uuid, text, timestamptz, timestamptz) FROM PUBLIC;
REVOKE ALL ON FUNCTION admin_support_close(uuid, uuid, timestamptz) FROM PUBLIC;
REVOKE ALL ON FUNCTION admin_support_list(uuid, uuid, boolean, timestamptz, timestamptz, uuid, integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION admin_open_shop(uuid, uuid, timestamptz) TO qd_app;
GRANT EXECUTE ON FUNCTION admin_support_open(uuid, uuid, uuid, text, timestamptz, timestamptz) TO qd_app;
GRANT EXECUTE ON FUNCTION admin_support_close(uuid, uuid, timestamptz) TO qd_app;
GRANT EXECUTE ON FUNCTION admin_support_list(uuid, uuid, boolean, timestamptz, timestamptz, uuid, integer) TO qd_app;

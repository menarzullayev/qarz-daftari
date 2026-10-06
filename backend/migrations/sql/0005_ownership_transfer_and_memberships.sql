-- Ownership transfer (REQ-036, domain rule BR-22) and a user's own memberships (REQ-064).

-- A transfer needs both people to confirm, at different times, so its pending state must be stored. The
-- approved schema has no table for it.
CREATE TABLE ownership_transfer (
  id              uuid PRIMARY KEY,
  shop_id         uuid NOT NULL REFERENCES shop(id),
  from_membership uuid NOT NULL REFERENCES membership(id),
  to_membership   uuid NOT NULL REFERENCES membership(id),
  status          text NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'accepted', 'declined', 'cancelled', 'expired')),
  created_at      timestamptz NOT NULL DEFAULT now(),
  expires_at      timestamptz NOT NULL,
  decided_at      timestamptz,
  CHECK (from_membership <> to_membership),
  CHECK ((status = 'pending') = (decided_at IS NULL))
);
CREATE UNIQUE INDEX one_pending_transfer_per_shop ON ownership_transfer (shop_id) WHERE status = 'pending';

GRANT SELECT, INSERT, UPDATE ON ownership_transfer TO qd_app;
ALTER TABLE ownership_transfer ENABLE ROW LEVEL SECURITY;
ALTER TABLE ownership_transfer FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant ON ownership_transfer
  USING (shop_id = nullif(current_setting('qd.shop_id', true), '')::uuid)
  WITH CHECK (shop_id = nullif(current_setting('qd.shop_id', true), '')::uuid);

-- Cross-tenant read listed in the technical specification: the shops a user is an active member of.
-- The application passes the authenticated user's identifier; the function returns nothing else.
CREATE FUNCTION my_memberships(p_user_id uuid)
RETURNS TABLE (shop_id uuid, shop_name text, role text, membership_id uuid)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT s.id, s.name, m.role, m.id
    FROM membership m
    JOIN shop s ON s.id = m.shop_id
   WHERE m.user_id = p_user_id
     AND m.status = 'active'
     AND s.status <> 'erased'
   ORDER BY s.created_at, s.id;
$$;

REVOKE ALL ON FUNCTION my_memberships(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION my_memberships(uuid) TO qd_app;

-- Cross-tenant function: accepting a staff invitation (REQ-032).
--
-- The invited person holds only a token. They are not yet a member of any shop, so no tenant can be set
-- for them, and the application role cannot look the invitation up. This function does exactly that one
-- lookup and join. It reveals nothing unless the token hash matches an issued, unexpired staff invitation.
--
-- Returns the shop the user joined, or NULL when the invitation cannot be used. Raises 'already_member'
-- when the user is already an active or suspended member of that shop.
-- Like 0002, it runs with its owner's rights and the owner must be able to bypass row-level security.

CREATE FUNCTION accept_staff_invitation(p_token_hash bytea, p_user_id uuid) RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  inv invitation%ROWTYPE;
  existing membership%ROWTYPE;
  new_membership uuid;
BEGIN
  SELECT * INTO inv FROM invitation
   WHERE token_hash = p_token_hash AND kind = 'staff' AND status = 'issued'
     AND (expires_at IS NULL OR expires_at > now())
   FOR UPDATE;
  IF NOT FOUND THEN
    RETURN NULL;
  END IF;

  SELECT * INTO existing FROM membership WHERE shop_id = inv.shop_id AND user_id = p_user_id FOR UPDATE;
  IF FOUND AND existing.status IN ('active', 'suspended', 'invited') THEN
    RAISE EXCEPTION 'already_member' USING ERRCODE = 'unique_violation';
  END IF;

  IF FOUND THEN
    -- A person removed earlier and invited again keeps their membership row, so that the entries they
    -- recorded before stay attributed to them.
    UPDATE membership SET role = inv.role, status = 'active' WHERE id = existing.id;
    new_membership := existing.id;
  ELSE
    new_membership := gen_random_uuid();
    INSERT INTO membership (id, shop_id, user_id, role, status)
    VALUES (new_membership, inv.shop_id, p_user_id, inv.role, 'active');
  END IF;

  UPDATE invitation SET status = 'used' WHERE token_hash = p_token_hash;
  INSERT INTO activity (id, shop_id, actor_kind, actor_id, action, subject_type, subject_id)
  VALUES (gen_random_uuid(), inv.shop_id, 'staff', new_membership, 'staff.joined', 'membership', new_membership);
  UPDATE app_user SET active_shop = inv.shop_id WHERE id = p_user_id AND active_shop IS NULL;
  RETURN inv.shop_id;
END $$;

REVOKE ALL ON FUNCTION accept_staff_invitation(bytea, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accept_staff_invitation(bytea, uuid) TO qd_app;

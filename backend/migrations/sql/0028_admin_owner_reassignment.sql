-- An administrator gives a shop to another person (operations runbook 7: the owner lost the Telegram
-- account, and Telegram is the only way to sign in, ADR-017). The ordinary transfer (REQ-036, BR-22) is
-- started by the old owner, who can no longer do it.

-- Everything in one call and so in one transaction: the old owner's membership, the new owner's, a
-- waiting transfer, the audit row and the line in the shop's own activity. `membership` and
-- `ownership_transfer` are tenant tables and an administrator has no tenant, so the application role
-- cannot write them here itself.
--
-- It does nothing unless the caller's administrator account is active, has proved its second factor and
-- holds a session that is open at p_now; the allow-list and the fresh code are the application's. The
-- answer is one row whose `outcome` is:
--   'refused'         not such an administrator, or no reason given;
--   'no_shop'         no such shop, or it was erased;
--   'no_user'         nobody with that Telegram identifier has started the bot;
--   'same_owner'      that person is the owner already;
--   'too_many_shops'  that person already owns five shops (security review, finding 13);
--   'reassigned'      done.
-- Only 'reassigned' writes anything. Afterwards the shop has exactly one active owner (INV-11): the old
-- owner is demoted before the new one is raised, as the index one_owner_per_shop requires at every
-- moment. The former owner becomes a manager, as after a transfer (BR-22), but a suspended one: the lost
-- account may be in someone else's hands, and a manager reads and writes the shop's ledger. The new
-- owner can reinstate or remove that membership like any other. A shop that waits for deletion keeps
-- waiting with the same due time; a suspended shop stays suspended. No customer, entry or amount is
-- read or returned (REQ-059).
CREATE FUNCTION admin_reassign_owner(
  p_admin uuid, p_shop uuid, p_new_owner_tg bigint, p_reason text, p_now timestamptz
) RETURNS TABLE (
  outcome text, audit_id uuid, shop_name text, shop_lang text, deletion_due timestamptz,
  previous_owner uuid, previous_owner_tg bigint, previous_owner_lang text,
  new_owner uuid, new_owner_lang text, transfer_cancelled boolean
)
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
#variable_conflict use_column
DECLARE
  v_shop shop%ROWTYPE;
  v_new app_user%ROWTYPE;
  v_old_membership uuid;
  v_old_user uuid;
  v_old_tg bigint;
  v_old_lang text;
  v_new_membership uuid;
  v_cancelled boolean;
  v_audit uuid := gen_random_uuid();
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM admin_account a
     WHERE a.user_id = p_admin AND a.status = 'active' AND a.confirmed_at IS NOT NULL
  ) OR NOT EXISTS (
    SELECT 1 FROM admin_session s
     WHERE s.user_id = p_admin AND s.revoked_at IS NULL AND s.expires_at > p_now
  ) OR p_reason IS NULL OR length(btrim(p_reason)) < 3 THEN
    RETURN QUERY SELECT 'refused'::text, NULL::uuid, NULL::text, NULL::text, NULL::timestamptz,
                        NULL::uuid, NULL::bigint, NULL::text, NULL::uuid, NULL::text, false;
    RETURN;
  END IF;

  -- The shop's row is held until the transaction ends: two reassignments, or a reassignment and the
  -- shop's erasure, happen one after the other.
  SELECT * INTO v_shop FROM shop s WHERE s.id = p_shop AND s.status <> 'erased' FOR UPDATE;
  IF NOT FOUND THEN
    RETURN QUERY SELECT 'no_shop'::text, NULL::uuid, NULL::text, NULL::text, NULL::timestamptz,
                        NULL::uuid, NULL::bigint, NULL::text, NULL::uuid, NULL::text, false;
    RETURN;
  END IF;

  SELECT * INTO v_new FROM app_user u WHERE u.tg_id = p_new_owner_tg;
  IF NOT FOUND THEN
    RETURN QUERY SELECT 'no_user'::text, NULL::uuid, v_shop.name, v_shop.lang, v_shop.deletion_due,
                        NULL::uuid, NULL::bigint, NULL::text, NULL::uuid, NULL::text, false;
    RETURN;
  END IF;

  SELECT m.id, m.user_id, u.tg_id, u.lang INTO v_old_membership, v_old_user, v_old_tg, v_old_lang
    FROM membership m JOIN app_user u ON u.id = m.user_id
   WHERE m.shop_id = p_shop AND m.role = 'owner' AND m.status = 'active'
     FOR UPDATE OF m;
  IF v_old_user = v_new.id THEN
    RETURN QUERY SELECT 'same_owner'::text, NULL::uuid, v_shop.name, v_shop.lang, v_shop.deletion_due,
                        v_old_user, v_old_tg, v_old_lang, v_new.id, v_new.lang, false;
    RETURN;
  END IF;

  -- The same lock and the same count as claim_owned_shop: a person owns at most five shops, however
  -- they come by them.
  PERFORM pg_advisory_xact_lock(hashtextextended('owned_shops:' || v_new.id::text, 0));
  IF (SELECT count(*) FROM membership m JOIN shop s ON s.id = m.shop_id
       WHERE m.user_id = v_new.id AND m.role = 'owner' AND m.status = 'active' AND s.status <> 'erased') >= 5
  THEN
    RETURN QUERY SELECT 'too_many_shops'::text, NULL::uuid, v_shop.name, v_shop.lang, v_shop.deletion_due,
                        v_old_user, v_old_tg, v_old_lang, v_new.id, v_new.lang, false;
    RETURN;
  END IF;

  -- Demote first: the database allows only one active owner per shop at any moment.
  UPDATE membership m SET role = 'manager', status = 'suspended' WHERE m.id = v_old_membership;
  INSERT INTO membership AS m (id, shop_id, user_id, role, status)
  VALUES (gen_random_uuid(), p_shop, v_new.id, 'owner', 'active')
  ON CONFLICT (shop_id, user_id) DO UPDATE SET role = 'owner', status = 'active'
  RETURNING m.id INTO v_new_membership;

  -- A transfer the old owner offered can no longer be accepted: its giver is not the owner.
  UPDATE ownership_transfer t SET status = 'cancelled', decided_at = p_now
   WHERE t.shop_id = p_shop AND t.status = 'pending';
  v_cancelled := FOUND;

  -- Users are named by their identifiers only: the audit outlives the shop.
  INSERT INTO admin_audit (id, at, admin_id, action, target_type, target_id, target_shop, reason, detail)
  VALUES (v_audit, p_now, p_admin, 'shop.owner_reassigned', 'shop', p_shop::text, p_shop, btrim(p_reason),
          jsonb_build_object(
            'previous_owner', v_old_user,
            'new_owner', v_new.id,
            'previous_owner_membership', CASE WHEN v_old_membership IS NULL THEN NULL ELSE 'manager, suspended' END,
            'transfer_cancelled', v_cancelled,
            'shop_status', v_shop.status));
  -- The administrator is not named to the shop; who it was is in the admin audit.
  INSERT INTO activity (id, shop_id, actor_kind, actor_id, action, subject_type, subject_id)
  VALUES (gen_random_uuid(), p_shop, 'admin', NULL, 'ownership.reassigned_by_admin', 'membership', v_new_membership);

  RETURN QUERY SELECT 'reassigned'::text, v_audit, v_shop.name, v_shop.lang, v_shop.deletion_due,
                      v_old_user, v_old_tg, v_old_lang, v_new.id, v_new.lang, v_cancelled;
END $$;

REVOKE ALL ON FUNCTION admin_reassign_owner(uuid, uuid, bigint, text, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION admin_reassign_owner(uuid, uuid, bigint, text, timestamptz) TO qd_app;

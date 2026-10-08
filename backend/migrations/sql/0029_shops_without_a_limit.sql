-- No limit on the number of shops a person owns (the founder's decision of 2026-10-08, DEC-065, which
-- changes that part of DEC-061 and DEC-062). Migration 0027 refused a sixth shop and 0028 applied the
-- same limit when an administrator reassigns a shop's owner. Both limits go. What stays: a person gets
-- one trial, and every later shop starts in limited mode until it is paid for.

-- Called in the transaction that creates a shop, before the shop is written. Answers:
--   'trial'    the shop may start a trial, and the person's one trial is now used;
--   'limited'  the shop starts without a trial.
-- It no longer counts the person's shops, so it no longer takes the lock that made two requests count
-- one after the other. The one trial needs none: the UPDATE below changes the person's row only while
-- no trial is recorded on it, a second request at the same moment waits for that row and then finds the
-- trial used, and a transaction that fails gives the trial back.
CREATE OR REPLACE FUNCTION claim_owned_shop(p_user uuid, p_wants_trial boolean) RETURNS text
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  IF NOT p_wants_trial THEN
    RETURN 'limited';
  END IF;
  UPDATE app_user SET trial_used_at = now() WHERE id = p_user AND trial_used_at IS NULL;
  IF FOUND THEN
    RETURN 'trial';
  END IF;
  RETURN 'limited';
END $$;

REVOKE ALL ON FUNCTION claim_owned_shop(uuid, boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION claim_owned_shop(uuid, boolean) TO qd_app;

-- The administrator's reassignment of a shop's owner (0028), restated without the limit: the outcome
-- 'too_many_shops' no longer exists. Nothing else is changed. The answer is one row whose `outcome` is:
--   'refused'     not such an administrator, or no reason given;
--   'no_shop'     no such shop, or it was erased;
--   'no_user'     nobody with that Telegram identifier has started the bot;
--   'same_owner'  that person is the owner already;
--   'reassigned'  done.
CREATE OR REPLACE FUNCTION admin_reassign_owner(
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

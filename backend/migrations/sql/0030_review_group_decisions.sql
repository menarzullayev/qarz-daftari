-- A Telegram administrator of the review group decides a subscription receipt from the buttons on the
-- group's announcement (the founder's decision of 2026-10-08, DEC-064, which changes DEC-051).
--
-- Such a person need not be a platform administrator: they have no administrator account, no session
-- and no place in the panel, and none is made up for them. A decision of theirs is therefore recorded
-- by their Telegram user identifier, in the receipt and in the admin audit, beside the reference to an
-- administrator that stays empty. Whether the person administers the group is asked of Telegram by the
-- application at the moment of the press; that cannot be checked here. What the database checks is that
-- the group named is the configured review group.

-- Who decided a receipt: an administrator (`decided_by`) or a review-group administrator known only by
-- the Telegram identifier (`decided_by_tg`), never both.
ALTER TABLE subscription_receipt
  ADD COLUMN decided_by_tg bigint CHECK (decided_by_tg > 0);
ALTER TABLE subscription_receipt
  ADD CONSTRAINT subscription_receipt_one_decider CHECK (decided_by IS NULL OR decided_by_tg IS NULL);

-- The audit names who acted: an administrator, or the Telegram identifier of a review-group
-- administrator. Exactly one of the two, so no row is ever without its actor.
ALTER TABLE admin_audit ALTER COLUMN admin_id DROP NOT NULL;
ALTER TABLE admin_audit
  ADD COLUMN actor_tg bigint CHECK (actor_tg > 0);
ALTER TABLE admin_audit
  ADD CONSTRAINT admin_audit_one_actor CHECK ((admin_id IS NULL) <> (actor_tg IS NULL));

-- One receipt as the review group decides it, with its shop's subscription and where to reach the
-- owner. Nothing is returned unless `p_group` is the configured review group. With `p_lock` the receipt
-- and the subscription stay locked until the transaction ends, in the order the administrator's side
-- locks them, so two decisions cannot both find the receipt waiting. A shop without a subscription row
-- has no `state`: its receipt can be rejected, and cannot be approved.
CREATE FUNCTION review_group_receipt(p_group bigint, p_receipt uuid, p_lock boolean)
RETURNS TABLE (
  receipt_id uuid, shop_id uuid, shop_name text, stated_amount bigint, stated_months smallint, status text,
  state text, paid_through date, owner_tg bigint, owner_lang text
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
           sub.state, sub.paid_through, o.tg_id, o.lang
      FROM subscription_receipt r
      JOIN shop s ON s.id = r.shop_id
      LEFT JOIN subscription sub ON sub.shop_id = r.shop_id
      LEFT JOIN LATERAL (
        SELECT u.tg_id, u.lang FROM membership m JOIN app_user u ON u.id = m.user_id
         WHERE m.shop_id = s.id AND m.role = 'owner' AND m.status = 'active'
      ) o ON true
     WHERE r.id = p_receipt AND s.status <> 'erased';
END $$;

-- Writes a review-group administrator's decision: the receipt, the subscription the domain layer worked
-- out when it is an approval, the audit row and the line in the shop's own activity, all or nothing.
-- True when the receipt was still waiting and is now decided. False, and nothing written, when `p_group`
-- is not the configured review group, when no Telegram identifier is given, or when the receipt does
-- not wait any more. A receipt is decided once.
CREATE FUNCTION review_group_decide_receipt(
  p_group bigint, p_decider_tg bigint, p_receipt uuid, p_status text, p_months smallint, p_reason text,
  p_state text, p_paid_through date, p_prior_state text, p_detail jsonb, p_now timestamptz
)
RETURNS boolean
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  v_shop uuid;
  v_amount bigint;
  v_months smallint;
BEGIN
  IF p_status NOT IN ('approved', 'rejected') THEN
    RAISE EXCEPTION 'a receipt is approved or rejected';
  END IF;
  IF p_decider_tg IS NULL OR p_decider_tg <= 0 OR p_group IS NULL OR NOT EXISTS (
    SELECT 1 FROM platform_setting p
     WHERE p.key = 'review_group' AND jsonb_typeof(p.value) = 'number' AND p.value = to_jsonb(p_group)
  ) THEN
    RETURN false;
  END IF;
  UPDATE subscription_receipt r
     SET status = p_status,
         months = CASE WHEN p_status = 'approved' THEN p_months END,
         reject_reason = CASE WHEN p_status = 'rejected' THEN p_reason END,
         decided_by = NULL,
         decided_by_tg = p_decider_tg,
         decided_at = p_now
   WHERE r.id = p_receipt
     AND r.status = 'submitted'
     AND EXISTS (SELECT 1 FROM shop s WHERE s.id = r.shop_id AND s.status <> 'erased')
  RETURNING r.shop_id, r.stated_amount, r.stated_months INTO v_shop, v_amount, v_months;
  IF NOT FOUND THEN
    RETURN false;
  END IF;
  IF p_status = 'approved' THEN
    UPDATE subscription sub
       SET state = p_state, paid_through = p_paid_through, prior_state = p_prior_state, updated_at = p_now
     WHERE sub.shop_id = v_shop;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'the shop of an approved receipt has no subscription';
    END IF;
  END IF;
  -- Under "subscription.", as the administrator's own decisions are, so the shop's page in the panel
  -- shows it with the other changes to its subscription.
  INSERT INTO admin_audit (id, at, admin_id, actor_tg, action, target_type, target_id, target_shop, reason, detail)
  VALUES (gen_random_uuid(), p_now, NULL, p_decider_tg, 'subscription.receipt_' || p_status, 'receipt',
          p_receipt::text, v_shop, p_reason,
          jsonb_build_object('stated_amount', v_amount, 'stated_months', v_months, 'group', p_group)
            || coalesce(p_detail, '{}'::jsonb));
  -- The person is not named to the shop; who it was is in the admin audit.
  INSERT INTO activity (id, shop_id, actor_kind, actor_id, action, subject_type, subject_id)
  VALUES (gen_random_uuid(), v_shop, 'admin', NULL, 'subscription.receipt_' || p_status, 'subscription_receipt',
          p_receipt);
  RETURN true;
END $$;

REVOKE ALL ON FUNCTION review_group_receipt(bigint, uuid, boolean) FROM PUBLIC;
REVOKE ALL ON FUNCTION review_group_decide_receipt(
  bigint, bigint, uuid, text, smallint, text, text, date, text, jsonb, timestamptz
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION review_group_receipt(bigint, uuid, boolean) TO qd_app;
GRANT EXECUTE ON FUNCTION review_group_decide_receipt(
  bigint, bigint, uuid, text, smallint, text, text, date, text, jsonb, timestamptz
) TO qd_app;

-- The administrator's two readings of receipts (0024) also say which review-group administrator
-- decided, when it was one. A function's result columns cannot be changed in place, so both are dropped
-- and created again; nothing else in them is changed.
DROP FUNCTION admin_receipts(uuid, text, timestamptz, uuid, integer);
DROP FUNCTION admin_receipt(uuid, uuid, boolean);

CREATE FUNCTION admin_receipts(
  p_admin uuid, p_status text, p_after_created timestamptz, p_after_id uuid, p_limit integer
)
RETURNS TABLE (
  receipt_id uuid, shop_id uuid, shop_name text, stated_amount bigint, stated_months smallint, status text,
  months smallint, reject_reason text, created_at timestamptz, decided_at timestamptz, decided_by uuid,
  decided_by_tg bigint, has_file boolean, copies integer
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  SELECT r.id, r.shop_id, s.name, r.stated_amount, r.stated_months, r.status, r.months, r.reject_reason,
         r.created_at, r.decided_at, r.decided_by, r.decided_by_tg, r.file_id IS NOT NULL,
         (SELECT count(*)::integer
            FROM stored_file mine
            JOIN stored_file other
              ON other.sha256 = mine.sha256 AND other.purpose = 'subscription_receipt' AND other.id <> mine.id
            JOIN subscription_receipt o ON o.file_id = other.id
           WHERE mine.id = r.file_id)
    FROM subscription_receipt r
    JOIN shop s ON s.id = r.shop_id
   WHERE r.status = p_status
     AND s.status <> 'erased'
     AND EXISTS (SELECT 1 FROM admin_account a WHERE a.user_id = p_admin AND a.status = 'active')
     AND (p_after_created IS NULL OR (r.created_at, r.id) > (p_after_created, p_after_id))
   ORDER BY r.created_at, r.id
   LIMIT least(greatest(p_limit, 1), 101);
$$;

CREATE FUNCTION admin_receipt(p_admin uuid, p_receipt uuid, p_lock boolean)
RETURNS TABLE (
  receipt_id uuid, shop_id uuid, shop_name text, stated_amount bigint, stated_months smallint, status text,
  months smallint, reject_reason text, created_at timestamptz, decided_at timestamptz, decided_by uuid,
  decided_by_tg bigint,
  file_id uuid, object_key text, sha256 bytea, size_bytes integer, mime text, delete_after timestamptz
)
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM admin_account a WHERE a.user_id = p_admin AND a.status = 'active') THEN
    RETURN;
  END IF;
  IF p_lock THEN
    PERFORM 1 FROM subscription_receipt r WHERE r.id = p_receipt FOR UPDATE;
  END IF;
  RETURN QUERY
    SELECT r.id, r.shop_id, s.name, r.stated_amount, r.stated_months, r.status, r.months, r.reject_reason,
           r.created_at, r.decided_at, r.decided_by, r.decided_by_tg,
           f.id, f.object_key, f.sha256, f.size_bytes, f.mime, f.delete_after
      FROM subscription_receipt r
      JOIN shop s ON s.id = r.shop_id
      LEFT JOIN stored_file f ON f.id = r.file_id
     WHERE r.id = p_receipt AND s.status <> 'erased';
END $$;

REVOKE ALL ON FUNCTION admin_receipts(uuid, text, timestamptz, uuid, integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION admin_receipt(uuid, uuid, boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION admin_receipts(uuid, text, timestamptz, uuid, integer) TO qd_app;
GRANT EXECUTE ON FUNCTION admin_receipt(uuid, uuid, boolean) TO qd_app;

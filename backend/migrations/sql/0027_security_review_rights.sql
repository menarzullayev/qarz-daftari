-- Security review, findings 9, 11 and 13 (docs/10-operations/security-review.md).
--
-- 9:  signed Telegram sign-in data is accepted once; expired sign-in records and sessions are purged.
-- 11: the application role keeps, table by table, only what the application does with each platform table.
-- 13: a person owns at most five shops and gets one trial.

-- ---------------------------------------------------------------------------
-- Finding 9: one use of each signed sign-in payload
-- ---------------------------------------------------------------------------

-- One row for each accepted payload, kept until the payload would be refused for its age anyway. Only a
-- hash of the signature is stored. The application adds a row in the transaction that creates the session
-- and can never take one back: there is no UPDATE and no DELETE for it.
CREATE TABLE signin_replay (
  payload_hash  bytea PRIMARY KEY CHECK (octet_length(payload_hash) = 32),
  expires_at    timestamptz NOT NULL
);
CREATE INDEX signin_replay_expiry ON signin_replay (expires_at);
REVOKE ALL ON signin_replay FROM qd_app;
GRANT SELECT, INSERT ON signin_replay TO qd_app;

-- Removes what can no longer be used: sign-in records past their expiry, sessions and administrator
-- sessions that expired or were revoked, and administrator request keys older than 30 days. The time is
-- the database's own, so the caller cannot name an instant that would end everybody's session.
CREATE FUNCTION purge_expired_sign_ins() RETURNS integer
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  v_total integer := 0;
  v_rows integer;
BEGIN
  DELETE FROM signin_replay WHERE expires_at <= now();
  GET DIAGNOSTICS v_rows = ROW_COUNT;
  v_total := v_total + v_rows;
  DELETE FROM user_session WHERE expires_at <= now() OR revoked_at IS NOT NULL;
  GET DIAGNOSTICS v_rows = ROW_COUNT;
  v_total := v_total + v_rows;
  DELETE FROM admin_session WHERE expires_at <= now() OR revoked_at IS NOT NULL;
  GET DIAGNOSTICS v_rows = ROW_COUNT;
  v_total := v_total + v_rows;
  DELETE FROM admin_request_key WHERE created_at <= now() - interval '30 days';
  GET DIAGNOSTICS v_rows = ROW_COUNT;
  RETURN v_total + v_rows;
END $$;

REVOKE ALL ON FUNCTION purge_expired_sign_ins() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION purge_expired_sign_ins() TO qd_app;

-- ---------------------------------------------------------------------------
-- Finding 13: shops and trials per person
-- ---------------------------------------------------------------------------

ALTER TABLE app_user ADD COLUMN trial_used_at timestamptz;

-- Whoever owns or owned a shop that was given a trial has had theirs.
UPDATE app_user u
   SET trial_used_at = now()
 WHERE EXISTS (
   SELECT 1 FROM membership m JOIN subscription s ON s.shop_id = m.shop_id
    WHERE m.user_id = u.id AND m.role = 'owner' AND s.trial_ends IS NOT NULL
 );

-- Called in the transaction that creates a shop, before the shop is written. The lock is held until that
-- transaction ends, so two requests of one person are counted one after the other. Answers:
--   'refused'  the person already owns five shops that are not erased;
--   'trial'    the shop may start a trial, and the person's one trial is now used;
--   'limited'  the shop starts without a trial.
-- The limit is written here and not passed in, so the caller cannot raise it.
CREATE FUNCTION claim_owned_shop(p_user uuid, p_wants_trial boolean) RETURNS text
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  v_owned integer;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtextextended('owned_shops:' || p_user::text, 0));
  SELECT count(*) INTO v_owned
    FROM membership m JOIN shop s ON s.id = m.shop_id
   WHERE m.user_id = p_user AND m.role = 'owner' AND m.status = 'active' AND s.status <> 'erased';
  IF v_owned >= 5 THEN
    RETURN 'refused';
  END IF;
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

-- ---------------------------------------------------------------------------
-- Finding 11: platform settings are changed only through the administrator's function
-- ---------------------------------------------------------------------------

-- Stores one setting and its audit row together, for an administrator whose account is active, who has
-- proved the second factor, and who holds a session that is open at p_now. False, and nothing written,
-- for anybody else. The allow-list of administrators is the application's; this is the part the database
-- can check. p_detail is what the audit shows as before and after, already masked by the application.
CREATE FUNCTION admin_set_platform_setting(
  p_admin uuid, p_key text, p_value jsonb, p_reason text, p_detail jsonb, p_now timestamptz
) RETURNS boolean
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM admin_account a
     WHERE a.user_id = p_admin AND a.status = 'active' AND a.confirmed_at IS NOT NULL
  ) OR NOT EXISTS (
    SELECT 1 FROM admin_session s
     WHERE s.user_id = p_admin AND s.revoked_at IS NULL AND s.expires_at > p_now
  ) THEN
    RETURN false;
  END IF;
  INSERT INTO platform_setting (key, value, updated_by, updated_at)
  VALUES (p_key, p_value, p_admin::text, p_now)
  ON CONFLICT (key) DO UPDATE
    SET value = EXCLUDED.value, updated_by = EXCLUDED.updated_by, updated_at = EXCLUDED.updated_at;
  INSERT INTO admin_audit (id, at, admin_id, action, target_type, target_id, reason, detail)
  VALUES (gen_random_uuid(), p_now, p_admin, 'setting.changed', 'setting', p_key, p_reason,
          coalesce(p_detail, '{}'::jsonb));
  RETURN true;
END $$;

REVOKE ALL ON FUNCTION admin_set_platform_setting(uuid, text, jsonb, text, jsonb, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION admin_set_platform_setting(uuid, text, jsonb, text, jsonb, timestamptz) TO qd_app;

-- ---------------------------------------------------------------------------
-- Finding 11: per table, what the application does and nothing else
-- ---------------------------------------------------------------------------

-- Settings are read everywhere (the price, the card number owners are told to pay to, the switches) and
-- written only by the function above.
REVOKE ALL ON platform_setting FROM qd_app;
GRANT SELECT ON platform_setting TO qd_app;

-- People: created at first sight, then only the language and the chosen shop change. The Telegram
-- identifier is what binds a person to an account, and the trial mark is what finding 13 stands on;
-- neither can be rewritten, and no person can be deleted.
REVOKE ALL ON app_user FROM qd_app;
GRANT SELECT, INSERT ON app_user TO qd_app;
GRANT UPDATE (lang, active_shop) ON app_user TO qd_app;

-- Sessions: opened, looked up, revoked. Expiry, owner and token cannot be changed afterwards; removal is
-- the purge function's.
REVOKE ALL ON user_session FROM qd_app;
GRANT SELECT, INSERT ON user_session TO qd_app;
GRANT UPDATE (revoked_at) ON user_session TO qd_app;

REVOKE ALL ON admin_session FROM qd_app;
GRANT SELECT, INSERT ON admin_session TO qd_app;
GRANT UPDATE (revoked_at) ON admin_session TO qd_app;

-- Administrator accounts: enrolment and the second factor's counters. Not the status (who is disabled is
-- decided outside the application), and no deletion.
REVOKE ALL ON admin_account FROM qd_app;
GRANT SELECT, INSERT ON admin_account TO qd_app;
GRANT UPDATE (totp_secret, confirmed_at, failed_codes, locked_until, last_step) ON admin_account TO qd_app;

-- Administrator request keys are written once and read; old ones go with the purge function.
REVOKE ALL ON admin_request_key FROM qd_app;
GRANT SELECT, INSERT ON admin_request_key TO qd_app;

-- The outbox: queued, claimed, marked. A queued message's recipient and text cannot be rewritten, and a
-- message cannot be removed (a shop's messages go with the shop, in erase_shop).
REVOKE ALL ON outbox_message FROM qd_app;
GRANT SELECT, INSERT ON outbox_message TO qd_app;
GRANT UPDATE (status, attempts, next_try_at, sent_at) ON outbox_message TO qd_app;

-- Telegram updates already handled: remembered, never forgotten.
REVOKE ALL ON processed_update FROM qd_app;
GRANT SELECT, INSERT ON processed_update TO qd_app;

-- The migration tool's own table was caught by the first migration's grant on every table. The
-- application never reads it and must not be able to rewrite which migrations count as applied.
DO $$ BEGIN
  IF to_regclass('public.alembic_version') IS NOT NULL THEN
    REVOKE ALL ON alembic_version FROM qd_app;
  END IF;
END $$;

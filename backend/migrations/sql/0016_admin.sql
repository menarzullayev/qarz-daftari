-- The administrator's side (REQ-058, REQ-059, REQ-N14; ADR-017, ADR-018; technical specification,
-- "Authentication / authorization" and "Security": allow-list, time-based second factor with the secret
-- encrypted at rest, every action in an audit log the administrator cannot alter).

-- The second factor's state. The approved table holds only the secret and the status; the application
-- also has to remember failed codes, a lock, and the last code it accepted.
ALTER TABLE admin_account
  ADD COLUMN confirmed_at  timestamptz,                  -- first accepted code; until then enrolment may be repeated
  ADD COLUMN failed_codes  smallint NOT NULL DEFAULT 0 CHECK (failed_codes >= 0),
  ADD COLUMN locked_until  timestamptz,                  -- no code is accepted before this instant
  ADD COLUMN last_step     bigint;                       -- time step of the last accepted code; never accepted again

-- The elevation an administrator gets by passing the second factor. A table of its own, so a token of an
-- ordinary session can never be found here and the other way round. Only hashes are stored.
CREATE TABLE admin_session (
  id          uuid PRIMARY KEY,
  token_hash  bytea NOT NULL UNIQUE,                     -- SHA-256 of the token
  user_id     uuid NOT NULL REFERENCES admin_account(user_id),
  created_at  timestamptz NOT NULL DEFAULT now(),
  expires_at  timestamptz NOT NULL,
  revoked_at  timestamptz,
  CHECK (expires_at > created_at),
  CHECK (expires_at <= created_at + interval '8 hours')  -- specification: "session valid 8 hours"
);
CREATE INDEX admin_session_user ON admin_session (user_id);
REVOKE ALL ON admin_session FROM qd_app;
GRANT SELECT, INSERT, UPDATE ON admin_session TO qd_app;

-- Every administrator action (REQ-058). Insert-only for the application, like the ledger: the role that
-- serves the administrator can add and read rows but can never change or remove one.
CREATE TABLE admin_audit (
  id           uuid PRIMARY KEY,
  at           timestamptz NOT NULL DEFAULT now(),
  admin_id     uuid NOT NULL REFERENCES app_user(id),
  action       text NOT NULL CHECK (length(action) BETWEEN 1 AND 60),
  target_type  text NOT NULL CHECK (target_type IN ('admin', 'shop', 'setting')),
  target_id    text,                                     -- a shop or user identifier, or a setting's key
  -- Set when the target is a shop. Not named shop_id: this is a platform table with no tenant policy,
  -- and a schema test requires every table with a shop_id column to have one.
  target_shop  uuid,
  reason       text CHECK (length(reason) <= 500),
  detail       jsonb NOT NULL DEFAULT '{}'::jsonb        -- before and after; never a secret, code or token
);
CREATE INDEX admin_audit_time ON admin_audit (at DESC, id DESC);
CREATE INDEX admin_audit_shop ON admin_audit (target_shop, at DESC) WHERE target_shop IS NOT NULL;
REVOKE ALL ON admin_audit FROM qd_app;
GRANT SELECT, INSERT ON admin_audit TO qd_app;

-- Idempotency for administrator writes (ADR-006). `request_key` belongs to a shop; these belong to the
-- administrator who made the request.
CREATE TABLE admin_request_key (
  admin_id    uuid NOT NULL REFERENCES app_user(id),
  key         text NOT NULL,
  response    jsonb NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (admin_id, key)
);
REVOKE ALL ON admin_request_key FROM qd_app;
GRANT SELECT, INSERT, DELETE ON admin_request_key TO qd_app;

-- ---------------------------------------------------------------------------
-- Cross-tenant functions. The application role sees no shop without a tenant, so what an administrator
-- may see or change across shops goes through these. Each takes the administrator's user identifier
-- and does nothing unless that account is active; the allow-list and the second factor are checked by
-- the application before it calls them. None returns a customer, an entry or an amount owed (REQ-059).
-- ---------------------------------------------------------------------------

-- Shops with their subscription, the owner's Telegram identifier and two counts. `p_state` filters by
-- what applies on `p_today` (a period includes its last day), which must agree with
-- qarz.domain.subscription.effective_state; tests/db/test_admin_functions.py compares the two.
-- `p_shop` narrows to one shop. Newest first, paged by the last row's (created_at, shop_id).
CREATE FUNCTION admin_shop_search(
  p_admin uuid, p_today date, p_query text, p_state text, p_shop uuid,
  p_after_created timestamptz, p_after_id uuid, p_limit integer
)
RETURNS TABLE (
  shop_id uuid, name text, lang text, status text, created_at timestamptz, deletion_due timestamptz,
  state text, effective_state text, trial_ends date, paid_through date, prior_state text,
  owner_tg bigint, staff_count bigint, customer_count bigint
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT s.id, s.name, s.lang, s.status, s.created_at, s.deletion_due,
         sub.state, e.effective, sub.trial_ends, sub.paid_through, sub.prior_state,
         (SELECT u.tg_id FROM membership m JOIN app_user u ON u.id = m.user_id
           WHERE m.shop_id = s.id AND m.role = 'owner' AND m.status = 'active'),
         (SELECT count(*) FROM membership m WHERE m.shop_id = s.id AND m.status = 'active'),
         (SELECT count(*) FROM customer c WHERE c.shop_id = s.id AND c.status <> 'anonymized')
    FROM shop s
    LEFT JOIN subscription sub ON sub.shop_id = s.id
    CROSS JOIN LATERAL (
      SELECT CASE
               WHEN sub.state = 'suspended' THEN 'suspended'
               WHEN sub.state = 'trial' AND sub.trial_ends >= p_today THEN 'trial'
               WHEN sub.state = 'active' AND sub.paid_through >= p_today THEN 'active'
               ELSE 'limited'
             END AS effective
    ) e
   WHERE EXISTS (SELECT 1 FROM admin_account a WHERE a.user_id = p_admin AND a.status = 'active')
     AND (p_shop IS NULL OR s.id = p_shop)
     AND (p_state IS NULL OR e.effective = p_state)
     AND (p_query IS NULL OR s.name ILIKE
          '%' || replace(replace(replace(p_query, '\', '\\'), '%', '\%'), '_', '\_') || '%')
     AND (p_after_created IS NULL OR (s.created_at, s.id) < (p_after_created, p_after_id))
   ORDER BY s.created_at DESC, s.id DESC
   LIMIT least(greatest(p_limit, 1), 101);
$$;

-- Payment history of one shop: its subscription receipts, newest first, without the file.
CREATE FUNCTION admin_shop_receipts(p_admin uuid, p_shop uuid)
RETURNS TABLE (
  receipt_id uuid, stated_amount bigint, status text, months smallint, reject_reason text,
  created_at timestamptz, decided_at timestamptz
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT r.id, r.stated_amount, r.status, r.months, r.reject_reason, r.created_at, r.decided_at
    FROM subscription_receipt r
   WHERE r.shop_id = p_shop
     AND EXISTS (SELECT 1 FROM admin_account a WHERE a.user_id = p_admin AND a.status = 'active')
   ORDER BY r.created_at DESC, r.id DESC
   LIMIT 200;
$$;

-- One shop's subscription row, locked until the transaction ends, with where to reach the owner. The
-- rules for changing it live in the domain layer; this only reads.
CREATE FUNCTION admin_lock_subscription(p_admin uuid, p_shop uuid)
RETURNS TABLE (
  state text, trial_ends date, paid_through date, prior_state text,
  shop_name text, owner_tg bigint, owner_lang text
)
LANGUAGE sql
VOLATILE
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT sub.state, sub.trial_ends, sub.paid_through, sub.prior_state, s.name, o.tg_id, o.lang
    FROM subscription sub
    JOIN shop s ON s.id = sub.shop_id
    LEFT JOIN LATERAL (
      SELECT u.tg_id, u.lang FROM membership m JOIN app_user u ON u.id = m.user_id
       WHERE m.shop_id = s.id AND m.role = 'owner' AND m.status = 'active'
    ) o ON true
   WHERE sub.shop_id = p_shop
     AND s.status <> 'erased'
     AND EXISTS (SELECT 1 FROM admin_account a WHERE a.user_id = p_admin AND a.status = 'active')
     FOR UPDATE OF sub;
$$;

-- Stores the subscription the domain layer worked out. True when a row was changed.
CREATE FUNCTION admin_store_subscription(
  p_admin uuid, p_shop uuid, p_state text, p_trial_ends date, p_paid_through date, p_prior_state text,
  p_now timestamptz
)
RETURNS boolean
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  changed integer;
BEGIN
  UPDATE subscription sub
     SET state = p_state, trial_ends = p_trial_ends, paid_through = p_paid_through,
         prior_state = p_prior_state, updated_at = p_now
   WHERE sub.shop_id = p_shop
     AND EXISTS (SELECT 1 FROM shop s WHERE s.id = p_shop AND s.status <> 'erased')
     AND EXISTS (SELECT 1 FROM admin_account a WHERE a.user_id = p_admin AND a.status = 'active');
  GET DIAGNOSTICS changed = ROW_COUNT;
  RETURN changed = 1;
END;
$$;

REVOKE ALL ON FUNCTION admin_shop_search(uuid, date, text, text, uuid, timestamptz, uuid, integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION admin_shop_receipts(uuid, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION admin_lock_subscription(uuid, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION admin_store_subscription(uuid, uuid, text, date, date, text, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION admin_shop_search(uuid, date, text, text, uuid, timestamptz, uuid, integer) TO qd_app;
GRANT EXECUTE ON FUNCTION admin_shop_receipts(uuid, uuid) TO qd_app;
GRANT EXECUTE ON FUNCTION admin_lock_subscription(uuid, uuid) TO qd_app;
GRANT EXECUTE ON FUNCTION admin_store_subscription(uuid, uuid, text, date, date, text, timestamptz) TO qd_app;

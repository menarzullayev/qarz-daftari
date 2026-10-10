-- An administrator's passkeys (ADR-017, amended 2026-10-10): the third further way in.
--
-- A row is a credential a device made for this site: its identifier, its public key as the browser gave
-- it (DER, SubjectPublicKeyInfo) and the algorithm's COSE number. The private key never leaves the
-- device. Who is an administrator is decided where it always was; this table decides nothing about that.
CREATE TABLE admin_passkey (
  id             uuid PRIMARY KEY,
  -- Goes with the account, like the password: no role may delete an account.
  user_id        uuid NOT NULL REFERENCES admin_account(user_id) ON DELETE CASCADE,
  credential_id  bytea NOT NULL UNIQUE CHECK (length(credential_id) BETWEEN 1 AND 1023),
  public_key     bytea NOT NULL CHECK (length(public_key) BETWEEN 32 AND 1024),
  algorithm      integer NOT NULL CHECK (algorithm IN (-7, -257, -8)),
  -- The device's own count of its signatures; zero for a device that does not count.
  sign_count     bigint NOT NULL DEFAULT 0 CHECK (sign_count >= 0),
  label          text NOT NULL CHECK (length(label) BETWEEN 1 AND 60),
  created_at     timestamptz NOT NULL DEFAULT now(),
  last_used_at   timestamptz,
  revoked_at     timestamptz
);
CREATE INDEX admin_passkey_user ON admin_passkey (user_id) WHERE revoked_at IS NULL;

-- A passkey is ended, never deleted: the audit names it by its identifier.
GRANT SELECT, INSERT ON admin_passkey TO qd_admin;
GRANT UPDATE (sign_count, last_used_at, revoked_at) ON admin_passkey TO qd_admin;

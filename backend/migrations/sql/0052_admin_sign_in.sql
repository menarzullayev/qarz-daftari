-- More ways for an administrator to come in (ADR-017, amended 2026-10-10): a service key and a password.
--
-- Both end where the Telegram sign-in ends: in a row of user_session for the administrator's own user.
-- Who is an administrator is decided where it always was (the allow-list and admin_account); these
-- tables decide nothing about that.

-- A service key is a session of its own kind: a bearer token that does not run out, named by a label,
-- ended by revoking it. Only its hash is stored, like every session's.
ALTER TABLE user_session DROP CONSTRAINT user_session_kind_check;
ALTER TABLE user_session ADD CONSTRAINT user_session_kind_check CHECK (kind IN ('webapp', 'web', 'service'));
ALTER TABLE user_session ADD COLUMN label text;
ALTER TABLE user_session ADD CONSTRAINT user_session_label_check
  CHECK ((kind = 'service') = (label IS NOT NULL) AND (label IS NULL OR label ~ '^[a-z0-9][a-z0-9-]{0,39}$'));
-- One live key a label: a label names the key that is revoked.
CREATE UNIQUE INDEX user_session_service_label ON user_session (label) WHERE kind = 'service' AND revoked_at IS NULL;

-- A key's row is written and ended by the ordinary role, which alone may touch user_session; the
-- administrators' role says who the key is for and keeps the audit (the command uses both).

-- An administrator's password. Set from the server's command line only; the hash is scrypt's, with a
-- salt of its own. Five wrong attempts lock the login for a quarter of an hour.
CREATE TABLE admin_password (
  -- Goes with the account: no role may delete an account, so this is for whoever owns the database.
  user_id       uuid PRIMARY KEY REFERENCES admin_account(user_id) ON DELETE CASCADE,
  login         text NOT NULL UNIQUE CHECK (login ~ '^[a-z0-9][a-z0-9._-]{2,39}$'),
  salt          bytea NOT NULL CHECK (length(salt) = 16),
  hash          bytea NOT NULL CHECK (length(hash) = 64),
  failures      smallint NOT NULL DEFAULT 0 CHECK (failures BETWEEN 0 AND 5),
  locked_until  timestamptz,
  updated_at    timestamptz NOT NULL DEFAULT now()
);
GRANT SELECT, INSERT ON admin_password TO qd_admin;
GRANT UPDATE (login, salt, hash, failures, locked_until, updated_at) ON admin_password TO qd_admin;

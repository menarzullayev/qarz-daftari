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

-- Keys are made and ended by the administrators' role, from the server's command line. The ordinary
-- role keeps the rights it had; it reads the row when a request brings the key.
GRANT SELECT, INSERT ON user_session TO qd_admin;
GRANT UPDATE (revoked_at) ON user_session TO qd_admin;

-- An administrator's password. Set from the server's command line only; the hash is scrypt's, with a
-- salt of its own. Five wrong attempts lock the login for a quarter of an hour.
CREATE TABLE admin_password (
  user_id       uuid PRIMARY KEY REFERENCES admin_account(user_id),
  login         text NOT NULL UNIQUE CHECK (login ~ '^[a-z0-9][a-z0-9._-]{2,39}$'),
  salt          bytea NOT NULL CHECK (length(salt) = 16),
  hash          bytea NOT NULL CHECK (length(hash) = 64),
  failures      smallint NOT NULL DEFAULT 0 CHECK (failures BETWEEN 0 AND 5),
  locked_until  timestamptz,
  updated_at    timestamptz NOT NULL DEFAULT now()
);
GRANT SELECT, INSERT ON admin_password TO qd_admin;
GRANT UPDATE (login, salt, hash, failures, locked_until, updated_at) ON admin_password TO qd_admin;

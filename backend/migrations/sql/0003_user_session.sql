-- Server-side sessions (ADR-017; technical specification, Security: "random 256-bit identifiers stored
-- hashed; revocable"). The approved schema names sessions but defines no table for them.
--
-- Platform-level: a session belongs to a user, not to a shop. Only hashes are stored, so a copy of this
-- table cannot be used to sign in.

CREATE TABLE user_session (
  id          uuid PRIMARY KEY,
  token_hash  bytea NOT NULL UNIQUE,                 -- SHA-256 of the session token
  user_id     uuid NOT NULL REFERENCES app_user(id),
  kind        text NOT NULL CHECK (kind IN ('webapp', 'web')),
  csrf_hash   bytea,                                 -- SHA-256 of the CSRF token; cookie sessions only
  created_at  timestamptz NOT NULL DEFAULT now(),
  expires_at  timestamptz NOT NULL,
  revoked_at  timestamptz,
  CHECK ((kind = 'web') = (csrf_hash IS NOT NULL)),
  CHECK (expires_at > created_at)
);
CREATE INDEX user_session_user ON user_session (user_id);

GRANT SELECT, INSERT, UPDATE, DELETE ON user_session TO qd_app;

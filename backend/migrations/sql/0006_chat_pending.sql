-- What an unanswered chat question was about (technical specification, "Chat contract").
--
-- A button's callback data is limited to 64 bytes, so it carries only this row's identifier. The row holds
-- what the seller typed (a customer name, an amount) until the button is pressed or 15 minutes pass.
-- Like user_session this is not a tenant table: a row belongs to one person, is read only together with
-- that person's identifier, and every action taken from it is authorized again in the shop it names.

CREATE TABLE chat_pending (
  id          uuid PRIMARY KEY,
  user_id     uuid NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
  kind        text NOT NULL CHECK (kind IN ('shop_name', 'entry', 'promise_date')),
  payload     jsonb NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  expires_at  timestamptz NOT NULL,
  CHECK (expires_at > created_at)
);
CREATE INDEX chat_pending_user ON chat_pending (user_id, kind);

-- Rows are written once and removed; nothing updates them.
REVOKE ALL ON chat_pending FROM qd_app;
GRANT SELECT, INSERT, DELETE ON chat_pending TO qd_app;

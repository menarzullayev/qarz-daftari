-- Date change requests (REQ-066, REQ-067): the reason a manager may give when declining one, the chat
-- question in which a customer writes the date they ask for, and an index of open requests.
ALTER TABLE date_change_request
  ADD COLUMN decline_reason text CHECK (decline_reason IS NULL OR length(decline_reason) BETWEEN 1 AND 300);
ALTER TABLE date_change_request
  ADD CONSTRAINT date_change_request_reason_check CHECK (reason IS NULL OR length(reason) BETWEEN 1 AND 300);
ALTER TABLE promise
  ADD CONSTRAINT promise_reason_check CHECK (reason IS NULL OR length(reason) BETWEEN 1 AND 300);

ALTER TABLE chat_pending DROP CONSTRAINT chat_pending_kind_check;
ALTER TABLE chat_pending ADD CONSTRAINT chat_pending_kind_check
  CHECK (kind IN ('shop_name', 'entry', 'promise_date', 'consent', 'dispute', 'decline', 'date_request'));

CREATE INDEX date_change_request_open ON date_change_request (shop_id, created_at) WHERE status = 'open';

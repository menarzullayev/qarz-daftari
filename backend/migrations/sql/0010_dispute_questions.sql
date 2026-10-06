-- Two more kinds of chat question: a customer writing the reason of a dispute, and a manager writing
-- the reason for declining one (REQ-016, REQ-017).
ALTER TABLE chat_pending DROP CONSTRAINT chat_pending_kind_check;
ALTER TABLE chat_pending ADD CONSTRAINT chat_pending_kind_check
  CHECK (kind IN ('shop_name', 'entry', 'promise_date', 'consent', 'dispute', 'decline'));

CREATE INDEX dispute_open ON dispute (shop_id, created_at) WHERE status = 'open';

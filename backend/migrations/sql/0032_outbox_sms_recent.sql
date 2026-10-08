-- SMS in the health figures (qd_sms_messages_last_day): how many of the last 24 hours were sent, failed, or
-- wait after an attempt that did not succeed. The monitoring system reads them every time it reads
-- /metrics, and the outbox keeps every Telegram message ever queued: without an index of its own the
-- count would read the whole table each time. SMS are a small part of it, so this index stays small.
-- No right changes: the roles read and write the same columns as before (migration 0031).

CREATE INDEX outbox_sms_recent ON outbox_message (next_try_at) WHERE channel = 'sms';

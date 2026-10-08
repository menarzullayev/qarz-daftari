-- Separate database roles (security review, finding 11; docs/10-operations/security-review.md).
--
-- Until now one role, qd_app, served the API, the administrators' side and the worker, so whoever got
-- into one of them held the rights of all three. From here on each connects as its own role:
--
--   qd_app     the ordinary application: what shop members and customers do, sign-in, the bot's chat;
--   qd_admin   the administrators' side: the admin_* tables and functions, and reading a shop under a
--              support access;
--   qd_worker  the worker: the outbox, scheduled jobs, exports and imports, erasure, the purges.
--
-- Each is granted what its code runs and nothing else; tests/db/test_database_roles.py lists it all and
-- fails for a table or a function that is not listed. Like qd_app (DEC-021), the new roles are created
-- without a login and without a password; both are set at deploy.

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'qd_admin') THEN
    CREATE ROLE qd_admin NOLOGIN NOBYPASSRLS;  -- login and password are set at deploy
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'qd_worker') THEN
    CREATE ROLE qd_worker NOLOGIN NOBYPASSRLS;  -- login and password are set at deploy
  END IF;
END $$;

-- A role is shared by every database of the server. One that existed before this migration, in a
-- database of another kind, holds nothing here until it is granted below; whatever it was given in this
-- database by hand is taken back first, so that the lists below are the whole of it.
REVOKE ALL ON ALL TABLES IN SCHEMA public, measure FROM qd_admin, qd_worker;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public, measure FROM qd_admin, qd_worker;
GRANT USAGE ON SCHEMA public, measure TO qd_admin, qd_worker;

-- ---------------------------------------------------------------------------
-- Who is told that a subscription receipt waits
-- ---------------------------------------------------------------------------

-- The ordinary application announces a new receipt to the administrators. It used to read
-- admin_account for that, the table that also holds the second-factor secrets. This gives it the two
-- columns it needs, of active and confirmed administrators only, and nothing else of the table.
CREATE FUNCTION admin_notice_recipients() RETURNS TABLE (tg_id bigint, lang text)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  SELECT u.tg_id, u.lang
    FROM admin_account a JOIN app_user u ON u.id = a.user_id
   WHERE a.status = 'active' AND a.confirmed_at IS NOT NULL AND u.tg_id IS NOT NULL
   ORDER BY u.tg_id
$$;

REVOKE ALL ON FUNCTION admin_notice_recipients() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION admin_notice_recipients() TO qd_app;

-- ---------------------------------------------------------------------------
-- qd_app: what it loses
-- ---------------------------------------------------------------------------

-- The administrators' tables: accounts with their second-factor secrets, admin sessions, the audit, and
-- the administrators' request keys. The ordinary application touches none of them any more.
REVOKE ALL ON admin_account, admin_session, admin_audit, admin_request_key FROM qd_app;

-- The outbox: the ordinary application only queues. It can no longer read a queued message's recipient
-- or text (they are other shops' too: the table is outside row-level security), nor mark, reschedule or
-- claim one. What it may still read is what queueing itself needs (the row it wrote and the key that
-- makes a message unique) and what /metrics reports (how long the oldest due message has waited).
REVOKE ALL ON outbox_message FROM qd_app;
GRANT INSERT ON outbox_message TO qd_app;
GRANT SELECT (id, dedupe_key, channel, status, next_try_at) ON outbox_message TO qd_app;

-- Which periods of which jobs are done is written by the worker; /metrics reads when each last ran.
REVOKE ALL ON job_run FROM qd_app;
GRANT SELECT ON job_run TO qd_app;

-- The weekly figures are computed and stored by the worker.
REVOKE ALL ON measure.weekly FROM qd_app;

-- Functions only the administrators' side calls.
REVOKE ALL ON FUNCTION admin_shop_search(uuid, date, text, text, uuid, timestamptz, uuid, integer) FROM qd_app;
REVOKE ALL ON FUNCTION admin_shop_receipts(uuid, uuid) FROM qd_app;
REVOKE ALL ON FUNCTION admin_lock_subscription(uuid, uuid) FROM qd_app;
REVOKE ALL ON FUNCTION admin_store_subscription(uuid, uuid, text, date, date, text, timestamptz) FROM qd_app;
REVOKE ALL ON FUNCTION admin_receipts(uuid, text, timestamptz, uuid, integer) FROM qd_app;
REVOKE ALL ON FUNCTION admin_receipt(uuid, uuid, boolean) FROM qd_app;
REVOKE ALL ON FUNCTION admin_receipt_copies(uuid, uuid) FROM qd_app;
REVOKE ALL ON FUNCTION admin_decide_receipt(uuid, uuid, text, smallint, text, timestamptz) FROM qd_app;
REVOKE ALL ON FUNCTION admin_shop_activity(uuid, uuid, text, uuid) FROM qd_app;
REVOKE ALL ON FUNCTION admin_open_shop(uuid, uuid, timestamptz) FROM qd_app;
REVOKE ALL ON FUNCTION admin_support_open(uuid, uuid, uuid, text, timestamptz, timestamptz) FROM qd_app;
REVOKE ALL ON FUNCTION admin_support_close(uuid, uuid, timestamptz) FROM qd_app;
REVOKE ALL ON FUNCTION admin_support_list(uuid, uuid, boolean, timestamptz, timestamptz, uuid, integer) FROM qd_app;
REVOKE ALL ON FUNCTION admin_reassign_owner(uuid, uuid, bigint, text, timestamptz) FROM qd_app;
REVOKE ALL ON FUNCTION admin_set_platform_setting(uuid, text, jsonb, text, jsonb, timestamptz) FROM qd_app;

-- Functions only the worker calls.
REVOKE ALL ON FUNCTION mark_recipient_unreachable(bigint) FROM qd_app;
REVOKE ALL ON FUNCTION shops_due_for_reminders(smallint) FROM qd_app;
REVOKE ALL ON FUNCTION subscriptions_to_review(date) FROM qd_app;
REVOKE ALL ON FUNCTION shops_to_erase() FROM qd_app;
REVOKE ALL ON FUNCTION erase_shop(uuid) FROM qd_app;
REVOKE ALL ON FUNCTION claim_export_job(timestamptz, timestamptz) FROM qd_app;
REVOKE ALL ON FUNCTION claim_import_batch(timestamptz, timestamptz) FROM qd_app;
REVOKE ALL ON FUNCTION shops_with_receipt_work(timestamptz, timestamptz) FROM qd_app;
REVOKE ALL ON FUNCTION purge_expired_sign_ins() FROM qd_app;

-- ---------------------------------------------------------------------------
-- qd_admin: the administrators' side
-- ---------------------------------------------------------------------------

-- Its own tables, with the limits migration 0027 set: an account's status, a session's owner and
-- lifetime cannot be rewritten, and nothing here can be deleted.
GRANT SELECT, INSERT ON admin_account TO qd_admin;
GRANT UPDATE (totp_secret, confirmed_at, failed_codes, locked_until, last_step) ON admin_account TO qd_admin;
GRANT SELECT, INSERT ON admin_session TO qd_admin;
GRANT UPDATE (revoked_at) ON admin_session TO qd_admin;
GRANT SELECT, INSERT ON admin_audit TO qd_admin;
GRANT SELECT, INSERT ON admin_request_key TO qd_admin;

-- Settings are read here and written through admin_set_platform_setting.
GRANT SELECT ON platform_setting TO qd_admin;
-- Of a person, only what the allow-list is checked against.
GRANT SELECT (id, tg_id) ON app_user TO qd_admin;
-- It tells an owner what was decided: it queues, and reads nothing of the queue but its own new row.
GRANT INSERT ON outbox_message TO qd_admin;
GRANT SELECT (id, dedupe_key) ON outbox_message TO qd_admin;
GRANT INSERT ON measure.event TO qd_admin;

-- Under a support access (REQ-059) an administrator reads a shop's customers and their ledgers, and
-- the look is written into the shop's own activity. Read only, and only these tables; row-level
-- security applies to this role as to any other. That a support access is open is checked by
-- admin_open_shop before anything is read.
GRANT SELECT ON customer, ledger_entry, promise, goods_line, dispute, date_change_request, payment_notice,
  stored_file TO qd_admin;
GRANT INSERT ON activity TO qd_admin;

GRANT EXECUTE ON FUNCTION admin_shop_search(uuid, date, text, text, uuid, timestamptz, uuid, integer) TO qd_admin;
GRANT EXECUTE ON FUNCTION admin_shop_receipts(uuid, uuid) TO qd_admin;
GRANT EXECUTE ON FUNCTION admin_lock_subscription(uuid, uuid) TO qd_admin;
GRANT EXECUTE ON FUNCTION admin_store_subscription(uuid, uuid, text, date, date, text, timestamptz) TO qd_admin;
GRANT EXECUTE ON FUNCTION admin_receipts(uuid, text, timestamptz, uuid, integer) TO qd_admin;
GRANT EXECUTE ON FUNCTION admin_receipt(uuid, uuid, boolean) TO qd_admin;
GRANT EXECUTE ON FUNCTION admin_receipt_copies(uuid, uuid) TO qd_admin;
GRANT EXECUTE ON FUNCTION admin_decide_receipt(uuid, uuid, text, smallint, text, timestamptz) TO qd_admin;
GRANT EXECUTE ON FUNCTION admin_shop_activity(uuid, uuid, text, uuid) TO qd_admin;
GRANT EXECUTE ON FUNCTION admin_open_shop(uuid, uuid, timestamptz) TO qd_admin;
GRANT EXECUTE ON FUNCTION admin_support_open(uuid, uuid, uuid, text, timestamptz, timestamptz) TO qd_admin;
GRANT EXECUTE ON FUNCTION admin_support_close(uuid, uuid, timestamptz) TO qd_admin;
GRANT EXECUTE ON FUNCTION admin_support_list(uuid, uuid, boolean, timestamptz, timestamptz, uuid, integer) TO qd_admin;
GRANT EXECUTE ON FUNCTION admin_reassign_owner(uuid, uuid, bigint, text, timestamptz) TO qd_admin;
GRANT EXECUTE ON FUNCTION admin_set_platform_setting(uuid, text, jsonb, text, jsonb, timestamptz) TO qd_admin;

-- ---------------------------------------------------------------------------
-- qd_worker: the worker
-- ---------------------------------------------------------------------------

-- The outbox is its to deliver: claim, mark, reschedule. As for qd_app before, a queued message's
-- recipient and text cannot be rewritten and a message cannot be removed.
GRANT SELECT, INSERT ON outbox_message TO qd_worker;
GRANT UPDATE (status, attempts, next_try_at, sent_at) ON outbox_message TO qd_worker;
GRANT SELECT, INSERT ON job_run TO qd_worker;
GRANT SELECT, INSERT ON measure.event TO qd_worker;
GRANT SELECT, INSERT, UPDATE ON measure.weekly TO qd_worker;
GRANT SELECT ON platform_setting TO qd_worker;
-- Whom a reminder or a finished file goes to.
GRANT SELECT (id, tg_id, lang) ON app_user TO qd_worker;

-- A shop's rows, one shop at a time under row-level security: reminders, the daily subscription
-- review, exports, imports and their undoing, receipts past their retention. The ledger stays
-- insert-only for this role too.
GRANT SELECT ON shop, membership, goods_line TO qd_worker;
GRANT SELECT, UPDATE ON subscription, customer_link, date_change_request, dispute, export_job, import_batch,
  payment_notice, invitation TO qd_worker;
GRANT SELECT, INSERT, UPDATE ON customer, removal_request TO qd_worker;
GRANT SELECT, INSERT ON ledger_entry, promise, reminder TO qd_worker;
GRANT SELECT, INSERT, UPDATE, DELETE ON stored_file TO qd_worker;
GRANT INSERT ON activity TO qd_worker;

GRANT EXECUTE ON FUNCTION mark_recipient_unreachable(bigint) TO qd_worker;
GRANT EXECUTE ON FUNCTION shops_due_for_reminders(smallint) TO qd_worker;
GRANT EXECUTE ON FUNCTION subscriptions_to_review(date) TO qd_worker;
GRANT EXECUTE ON FUNCTION shops_to_erase() TO qd_worker;
GRANT EXECUTE ON FUNCTION erase_shop(uuid) TO qd_worker;
GRANT EXECUTE ON FUNCTION claim_export_job(timestamptz, timestamptz) TO qd_worker;
GRANT EXECUTE ON FUNCTION claim_import_batch(timestamptz, timestamptz) TO qd_worker;
GRANT EXECUTE ON FUNCTION shops_with_receipt_work(timestamptz, timestamptz) TO qd_worker;
GRANT EXECUTE ON FUNCTION purge_expired_sign_ins() TO qd_worker;
-- Undoing an import can complete a customer's removal, which forgets a person nobody else knows. The
-- ordinary application completes removals too, so this one function is held by both.
GRANT EXECUTE ON FUNCTION forget_user_if_unused(uuid) TO qd_worker;

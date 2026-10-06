-- Scheduled work and the reminder schedule (REQ-023, REQ-042; technical specification, "Worker schedule").

-- One row per job and period: a period that has a row has been done. The worker writes the row after the
-- work, so a crash in the middle repeats the period; every job must therefore be safe to repeat.
CREATE TABLE job_run (
  job         text NOT NULL,
  period      text NOT NULL,
  finished_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (job, period)
);
REVOKE ALL ON job_run FROM qd_app;
GRANT SELECT, INSERT ON job_run TO qd_app;

-- Which shops send reminders at this hour. The worker has no tenant of its own, so this is the one
-- cross-shop read it needs; it returns identifiers only.
CREATE FUNCTION shops_due_for_reminders(p_hour smallint)
RETURNS TABLE (shop_id uuid)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT s.id
    FROM shop s
    JOIN subscription sub ON sub.shop_id = s.id
   WHERE s.status = 'active'
     AND s.reminders_on
     AND s.reminder_hour = p_hour
     AND sub.state <> 'suspended'
   ORDER BY s.id;
$$;

REVOKE ALL ON FUNCTION shops_due_for_reminders(smallint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION shops_due_for_reminders(smallint) TO qd_app;

CREATE INDEX reminder_customer_auto ON reminder (customer_id, sent_on DESC) WHERE kind = 'auto';

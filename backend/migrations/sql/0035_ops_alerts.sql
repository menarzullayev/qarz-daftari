-- The operations watch (the founder's decision of 2026-10-09, DEC-078; launch criterion 9).
--
-- The worker evaluates a small set of conditions every minute and tells the operators' Telegram chat
-- when one starts to hold and when it stops. What is firing, since when, and when somebody was last
-- told lives here and not in the worker's memory: the worker restarts, and a restart must neither
-- repeat every alert nor forget one.
--
-- Both tables are platform tables like job_run: no tenant, no row-level security, and nothing of a
-- shop or a person in them. A key is the name of a rule and a label from the code (a channel, a job, a
-- backup type, a path of the container); a value is a number. The worker alone holds rights on them.

-- One row for each condition that holds now, or that stopped and whose "resolved" has not been said.
CREATE TABLE ops_alert (
  key             text PRIMARY KEY CHECK (length(key) BETWEEN 1 AND 120),
  since           timestamptz NOT NULL,          -- when the condition began to hold
  firing_since    timestamptz,                   -- null while it has not held for the rule's time yet
  value           double precision,              -- the last figure, for the message
  notified_at     timestamptz,                   -- when the operators were last told that it is firing
  resolved_at     timestamptz,                   -- set when it stopped; the row goes once that is said
  attempts        integer NOT NULL DEFAULT 0,    -- sends that failed since the last one that succeeded
  last_attempt_at timestamptz,
  last_outcome    text CHECK (last_outcome IN ('sent', 'unconfigured', 'unreachable', 'refused', 'rejected'))
);

-- Figures the watch compares over time: the API's counters (they are in the API's memory and only an
-- increase means anything), and the result of the nightly check of the ledger. A handful of series, one
-- row a minute each; the worker deletes what is older than the longest window, keeping the newest of
-- every series.
CREATE TABLE ops_sample (
  series    text NOT NULL CHECK (length(series) BETWEEN 1 AND 120),
  taken_at  timestamptz NOT NULL,
  value     double precision NOT NULL,
  PRIMARY KEY (series, taken_at)
);

REVOKE ALL ON ops_alert, ops_sample FROM PUBLIC, qd_app, qd_admin, qd_worker;
GRANT SELECT, INSERT, UPDATE, DELETE ON ops_alert TO qd_worker;
GRANT SELECT, INSERT, DELETE ON ops_sample TO qd_worker;

-- In how many places the stored open debts differ from the ledger: a number and nothing else. The
-- comparison itself (open_debt_mismatches) reads every shop and returns identifiers and amounts, and
-- stays closed to every role; the worker is given the count alone, for its nightly check.
CREATE FUNCTION open_debt_mismatch_count()
RETURNS bigint
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  SELECT count(*) FROM open_debt_mismatches(NULL);
$$;
REVOKE ALL ON FUNCTION open_debt_mismatch_count() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION open_debt_mismatch_count() TO qd_worker;

-- Since when the oldest subscription receipt has awaited a decision: one moment, nothing of the
-- receipt. The API already reads it for /metrics; the watch reads it for the same rule.
GRANT EXECUTE ON FUNCTION oldest_waiting_receipt() TO qd_worker;

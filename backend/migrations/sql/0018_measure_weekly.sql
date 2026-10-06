-- The weekly figures computed from the measurement events (REQ-030, ADR-010).
-- Like the events they hold no name, phone, Telegram identity or shop identifier: only totals.
CREATE TABLE measure.weekly (
  week_start  date NOT NULL,               -- a Monday, Tashkent time
  metric      text NOT NULL,
  value       numeric,                     -- NULL when the week gives no ground for the figure
  computed_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (week_start, metric),
  CHECK (extract(isodow FROM week_start) = 1)
);
GRANT SELECT, INSERT, UPDATE ON measure.weekly TO qd_app;

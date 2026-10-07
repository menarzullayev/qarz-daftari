-- Ledger indexes that start with the shop (S19.1 load test, REQ-N13, NFR-005).
--
-- Row-level security adds "shop_id = <the current shop>" to every query, and the planner cannot see which
-- shop that is: it plans for an average shop of a few hundred entries. Measured with 5,000 shops, one of
-- them holding 200,000 entries, two of its choices made a request cost what the platform or the whole
-- shop holds instead of what the request needs. Each index below gives it a direct path instead.

-- The reversals of one shop. Every list and total leaves out reversed entries with
-- NOT EXISTS (... r.reverses_id = e.id); for a whole shop that was answered by walking the unique index
-- on reverses_id, which holds the reversals of all shops, and visiting each row to see whose it is.
CREATE INDEX ledger_reversal_shop ON ledger_entry (shop_id, reverses_id) WHERE reverses_id IS NOT NULL;

-- The entries of one customer of one shop. With only (customer_id, seq) and (shop_id, created_at) to
-- choose from, reading one customer's entries was planned as the intersection of the two, which reads
-- the index entries of the whole shop once per customer: a page of 50 customers in the large shop read
-- ten million of them.
CREATE INDEX ledger_shop_customer ON ledger_entry (shop_id, customer_id, seq);

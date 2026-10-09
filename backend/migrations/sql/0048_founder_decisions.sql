-- Three decisions of the founder (2026-10-10). The first and the second change the database; the third
-- (switching the free plan off is previewed and announced) needs nothing here.

-- =====================================================================================================
-- Decision 1: an advance is accepted
-- =====================================================================================================
--
-- A customer may pay more than they owe. The excess is their advance: the balance of that currency's
-- book stands below zero until later credit sales use it up (INV-3 as rewritten). It is the shop's
-- choice, off by default and changed by the owner, so a shop that never takes advances keeps the
-- refusal that catches a mistyped payment.
--
-- Nothing is stored about an advance in the ledger itself: it is what the payments exceed the debts by.
-- The oldest-first allocation of 0026/0041 (`open_debts_of`) already gives a debt recorded after such a
-- payment no unpaid part until the debts outgrow the payments, so `open_debt` is right as it is. What is
-- new is the other side, kept the same way and for the same reason (an overview must not add up every
-- entry of a shop): one row per customer and currency in credit, rewritten with the customer's open
-- debts whenever an entry of theirs is added.

ALTER TABLE shop ADD COLUMN accept_advances boolean NOT NULL DEFAULT false;

CREATE TABLE customer_advance (
  customer_id uuid NOT NULL REFERENCES customer(id),
  currency    text NOT NULL CONSTRAINT customer_advance_currency CHECK (currency IN ('UZS', 'USD')),
  shop_id     uuid NOT NULL REFERENCES shop(id),
  amount      bigint NOT NULL CHECK (amount > 0),   -- what the payments exceed the debts by, in minor units
  PRIMARY KEY (customer_id, currency)
);
CREATE INDEX customer_advance_shop ON customer_advance (shop_id, currency);

-- Read by the application; written only by the function below, which runs with its owner's rights.
REVOKE ALL ON customer_advance FROM PUBLIC;
GRANT SELECT ON customer_advance TO qd_app;
ALTER TABLE customer_advance ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer_advance FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant ON customer_advance
  USING (shop_id = nullif(current_setting('qd.shop_id', true), '')::uuid)
  WITH CHECK (shop_id = nullif(current_setting('qd.shop_id', true), '')::uuid);

-- What the ledger says each of the given customers is in credit by, per currency. The balance of 0041:
-- debts minus payments among the entries that stand; a reversal is neither.
CREATE FUNCTION customer_advances_of(p_customers uuid[])
RETURNS TABLE (shop_id uuid, customer_id uuid, currency text, amount bigint)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
AS $$
  SELECT e.shop_id, e.customer_id, e.currency,
         (-sum(CASE WHEN e.kind IN ('credit', 'opening') THEN e.amount ELSE -e.amount END))::bigint
    FROM ledger_entry e
   WHERE e.customer_id = ANY (p_customers) AND e.kind <> 'reversal'
     AND NOT EXISTS (SELECT 1 FROM ledger_entry r WHERE r.reverses_id = e.id)
   GROUP BY e.shop_id, e.customer_id, e.currency
  HAVING sum(CASE WHEN e.kind IN ('credit', 'opening') THEN e.amount ELSE -e.amount END) < 0;
$$;
REVOKE ALL ON FUNCTION customer_advances_of(uuid[]) FROM PUBLIC;

-- The refresh of 0041 with the advances beside the open debts: both from the same entries, in the same
-- statement of the same transaction, so a reader never sees one without the other.
CREATE OR REPLACE FUNCTION refresh_open_debts(p_customers uuid[]) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp
AS $$
BEGIN
  DELETE FROM open_debt d WHERE d.customer_id = ANY (p_customers);
  INSERT INTO open_debt (shop_id, customer_id, entry_id, currency, remaining, promised_date)
  SELECT o.shop_id, o.customer_id, o.entry_id, o.currency, o.remaining, o.promised_date
    FROM open_debts_of(p_customers) o;
  DELETE FROM customer_advance a WHERE a.customer_id = ANY (p_customers);
  INSERT INTO customer_advance (shop_id, customer_id, currency, amount)
  SELECT o.shop_id, o.customer_id, o.currency, o.amount FROM customer_advances_of(p_customers) o;
END $$;

-- INV-3, held by the database for every writer: after entries are added, none of the customers they
-- touched is in credit in a shop that does not accept advances. The application refuses such an entry
-- first and by name (EXCEEDS_BALANCE, WOULD_GO_NEGATIVE); this is what stops a writer that did not ask.
-- Only here, after entries: the backfill below and a change of a promised date add nothing to a book.
CREATE OR REPLACE FUNCTION open_debt_after_entries() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp
AS $$
DECLARE
  touched uuid[] := array(SELECT DISTINCT i.customer_id FROM inserted i);
BEGIN
  PERFORM refresh_open_debts(touched);
  IF EXISTS (SELECT 1 FROM customer_advance a JOIN shop s ON s.id = a.shop_id
              WHERE a.customer_id = ANY (touched) AND NOT s.accept_advances) THEN
    RAISE EXCEPTION 'a balance goes below zero only in a shop that accepts advances'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'ledger_advance_accepted';
  END IF;
  RETURN NULL;
END $$;

-- The same rule from the other side: a shop stops accepting advances only when none stands. The
-- application says so first (ADVANCES_STAND); a payment that is making an advance holds the shop's row
-- shared until it commits, so the two cannot pass each other.
CREATE FUNCTION shop_advances_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp
AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM customer_advance a WHERE a.shop_id = NEW.id) THEN
    RAISE EXCEPTION 'advances stand in this shop: it cannot stop accepting them'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'shop_advances_stand';
  END IF;
  RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION shop_advances_guard() FROM PUBLIC;

CREATE TRIGGER shop_advances_guard
  BEFORE UPDATE OF accept_advances ON shop
  FOR EACH ROW WHEN (OLD.accept_advances AND NOT NEW.accept_advances)
  EXECUTE FUNCTION shop_advances_guard();

-- Where the stored advances and the ledger differ, for one shop or for all (NULL). Empty when all is
-- well. Closed to every role, like `open_debt_mismatches`.
CREATE FUNCTION customer_advance_mismatches(p_shop uuid)
RETURNS TABLE (customer_id uuid, currency text, stored_amount bigint, ledger_amount bigint)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
AS $$
  WITH ledger AS (
    SELECT e.shop_id, e.customer_id, e.currency,
           (-sum(CASE WHEN e.kind IN ('credit', 'opening') THEN e.amount ELSE -e.amount END))::bigint AS amount
      FROM ledger_entry e
     WHERE (p_shop IS NULL OR e.shop_id = p_shop) AND e.kind <> 'reversal'
       AND NOT EXISTS (SELECT 1 FROM ledger_entry r WHERE r.reverses_id = e.id)
     GROUP BY e.shop_id, e.customer_id, e.currency
    HAVING sum(CASE WHEN e.kind IN ('credit', 'opening') THEN e.amount ELSE -e.amount END) < 0)
  SELECT coalesce(s.customer_id, l.customer_id), coalesce(s.currency, l.currency), s.amount, l.amount
    FROM (SELECT * FROM customer_advance a WHERE p_shop IS NULL OR a.shop_id = p_shop) s
    FULL JOIN ledger l ON l.customer_id = s.customer_id AND l.currency = s.currency
   WHERE s.customer_id IS NULL OR l.customer_id IS NULL OR s.amount <> l.amount OR s.shop_id <> l.shop_id;
$$;
REVOKE ALL ON FUNCTION customer_advance_mismatches(uuid) FROM PUBLIC;

-- The worker's nightly count (0035) now counts both kept figures of the customer ledger: a difference
-- in either is the same alarm. Still a number and nothing else, under the same name and right.
CREATE OR REPLACE FUNCTION open_debt_mismatch_count()
RETURNS bigint
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  SELECT (SELECT count(*) FROM open_debt_mismatches(NULL)) + (SELECT count(*) FROM customer_advance_mismatches(NULL));
$$;

-- Everything recorded so far. No account is below zero today (the application refused it), so this
-- writes no row; it is here so that the table is right whatever it finds.
INSERT INTO customer_advance (shop_id, customer_id, currency, amount)
SELECT o.shop_id, o.customer_id, o.currency, o.amount
  FROM customer_advances_of(array(SELECT c.id FROM customer c)) o;

-- Erasing a shop erases its stored advances too, and its tombstone does not accept advances. The
-- function is not written out again here: two other migrations of these days touch what is around
-- it, and a copy of its hundred lines would silently undo whichever of them ran first. The two lines
-- are added to the function as it stands, and the migration fails if it could not add them.
DO $$
DECLARE
  body text := pg_get_functiondef('erase_shop(uuid)'::regprocedure);
  open_debts constant text := 'DELETE FROM open_debt WHERE shop_id = p_shop_id;';
  tombstone constant text := 'stock_refuse_negative = false';
BEGIN
  IF position(open_debts IN body) = 0 OR position(tombstone IN body) = 0 THEN
    RAISE EXCEPTION 'erase_shop is not written as migration 0048 expects';
  END IF;
  body := replace(body, open_debts, open_debts || E'\n  DELETE FROM customer_advance WHERE shop_id = p_shop_id;');
  body := replace(body, tombstone, tombstone || ', accept_advances = false');
  EXECUTE body;
END $$;

-- =====================================================================================================
-- Decision 2: a delivery note whose issuer has left is posted in the owner's name
--
-- When the buyer confirms a delivery note, the supplier's sale is written with the authority of the note
-- itself: no member of the supplier is present. Until now it was always written in the name of the member
-- who issued the note, whatever had become of that member since. The founder decided: the sale is still
-- written (the note is the shop's commitment), but it is in the issuer's name only while the issuer is
-- still an active member of the shop who holds `credits.record`, the permission issuing a note asks for;
-- otherwise it is in the name of the shop's owner.
--
-- `network_note_author` is that rule, and `network_receipt_finish` now compares the author of the
-- supplier's entries with it: the issuer while active and permitted, the owner otherwise, nobody else.
--
-- "Holds `credits.record`" is the domain's `effective` (qarz.domain.permissions), said again in SQL for
-- this one key. Every role holds `credits.record` by default and the key is not a fixed one, so:
--   * the owner holds it, always;
--   * with the platform switch `permissions_on` off, every active member holds it;
--   * with the switch on, an active member holds it unless the owner denied it to that member
--     (`membership.permissions_denied`); a grant adds nothing to a default.
-- backend/tests/db/test_network_note_author.py holds the function to the application's own answer for
-- every role, status, switch and override, and backend/tests/test_network_note_author.py fails if the
-- catalogue stops giving `credits.record` to every role, which is what the SQL below takes for granted.
--
-- Called only from inside `network_receipt_finish` (SECURITY DEFINER), so it runs with that function's
-- rights and nobody is granted the right to call it: closed to PUBLIC, like `network_log`.
CREATE FUNCTION network_note_author(p_shop uuid, p_issuer uuid) RETURNS uuid
LANGUAGE sql STABLE SET search_path = public, pg_temp
AS $$
  SELECT coalesce(
    (SELECT m.id FROM membership m
      WHERE m.shop_id = p_shop AND m.id = p_issuer AND m.status = 'active'
        AND (m.role = 'owner'
             OR NOT (coalesce((SELECT s.value = 'true'::jsonb FROM platform_setting s
                                WHERE s.key = 'permissions_on'), false)
                     AND 'credits.record' = ANY (m.permissions_denied)))),
    (SELECT m.id FROM membership m WHERE m.shop_id = p_shop AND m.role = 'owner' AND m.status = 'active'));
$$;
REVOKE ALL ON FUNCTION network_note_author(uuid, uuid) FROM PUBLIC;

-- `network_receipt_finish` again, as migration 0047 left it, with one change made in two places (the
-- credit sale and the payment of what was paid on delivery):
--     e.author_id = theirs.issued_by   ->   e.author_id = network_note_author(p_peer, theirs.issued_by)
-- Same name, same arguments, same refusal (`NETWORK_BOOKS_MISMATCH`), same rights: replaced, so it stays
-- closed to PUBLIC and granted to `qd_app` alone (said again below).
CREATE OR REPLACE FUNCTION network_receipt_finish(
  p_shop uuid, p_peer uuid, p_note uuid, p_member uuid, p_document uuid, p_entry uuid, p_paid_entry uuid,
  p_now timestamptz
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp
AS $$
DECLARE
  own network_note%ROWTYPE;
  theirs network_note%ROWTYPE;
  link network_link%ROWTYPE;
BEGIN
  PERFORM network_guard(p_shop);
  PERFORM network_member(p_shop, p_member);
  own := network_note_pair(p_shop, p_peer, p_note);
  SELECT * INTO link FROM network_link l WHERE l.shop_id = p_shop AND l.id = own.link_id;
  SELECT * INTO theirs FROM network_note n WHERE n.shop_id = p_peer AND n.id = p_note;
  IF own.role <> 'buyer' OR own.status <> 'issued' OR theirs.status <> 'issued' OR link.state <> 'active' THEN
    PERFORM network_refuse('NETWORK_STATE');
  END IF;
  PERFORM 1 FROM stock_document d
    WHERE d.id = p_document AND d.shop_id = p_shop AND d.kind = 'receipt' AND d.status = 'posted'
      AND d.origin_ref = p_note AND d.supplier_id = link.supplier_id
      AND d.currency = own.currency AND d.total = own.total AND d.paid = own.paid;
  IF NOT FOUND THEN
    PERFORM network_refuse('NETWORK_BOOKS_MISMATCH');
  END IF;

  -- The buyer's receipt against the note, line by line. A receipt numbers its lines 1, 2, 3 and a note
  -- keeps the numbers its lines had on the order, so the two are matched by their place in that order.
  -- The buyer chose which of its own items each line is; what is held here is that the item is counted
  -- in the line's unit, and that quantity, price and line total are the note's.
  PERFORM 1
    FROM (SELECT row_number() OVER (ORDER BY n.line_no) AS place, n.unit, n.qty, n.unit_price, n.line_total
            FROM network_note_line n WHERE n.shop_id = p_shop AND n.note_id = p_note) said
    FULL JOIN
         (SELECT row_number() OVER (ORDER BY l.line_no) AS place, i.unit, l.qty, l.unit_cost, l.line_total
            FROM stock_document_line l
            JOIN catalog_item i ON i.shop_id = l.shop_id AND i.id = l.item_id
           WHERE l.shop_id = p_shop AND l.document_id = p_document) posted USING (place)
   WHERE said.qty IS NULL OR posted.qty IS NULL
      OR said.qty <> posted.qty
      OR said.unit_price IS DISTINCT FROM posted.unit_cost
      OR said.line_total IS DISTINCT FROM posted.line_total
      OR said.unit <> posted.unit;
  IF FOUND THEN
    PERFORM network_refuse('NETWORK_BOOKS_MISMATCH');
  END IF;
  -- Each line of the receipt came into the buyer's stock: one standing movement, that quantity, that price.
  PERFORM 1 FROM stock_document_line l
    WHERE l.shop_id = p_shop AND l.document_id = p_document
      AND (SELECT count(*) FROM stock_movement m
            WHERE m.shop_id = p_shop AND m.document_id = p_document AND m.line_no = l.line_no
              AND m.item_id = l.item_id AND m.kind = 'receipt' AND m.qty = l.qty
              AND m.unit_cost IS NOT DISTINCT FROM l.unit_cost
              AND NOT EXISTS (SELECT 1 FROM stock_movement r WHERE r.reverses_id = m.id)) <> 1;
  IF FOUND THEN
    PERFORM network_refuse('NETWORK_BOOKS_MISMATCH');
  END IF;

  PERFORM 1 FROM ledger_entry e
    WHERE e.id = p_entry AND e.shop_id = p_peer AND e.kind = 'credit' AND e.customer_id = theirs.customer_id
      AND e.amount = theirs.total AND e.currency = theirs.currency AND e.author_id = network_note_author(p_peer, theirs.issued_by)
      AND NOT EXISTS (SELECT 1 FROM ledger_entry r WHERE r.reverses_id = e.id);
  IF NOT FOUND THEN
    PERFORM network_refuse('NETWORK_BOOKS_MISMATCH');
  END IF;
  IF (theirs.paid > 0) <> (p_paid_entry IS NOT NULL) THEN
    PERFORM network_refuse('NETWORK_BOOKS_MISMATCH');
  END IF;
  IF p_paid_entry IS NOT NULL THEN
    PERFORM 1 FROM ledger_entry e
      WHERE e.id = p_paid_entry AND e.shop_id = p_peer AND e.kind = 'payment' AND e.customer_id = theirs.customer_id
        AND e.amount = theirs.paid AND e.currency = theirs.currency AND e.author_id = network_note_author(p_peer, theirs.issued_by);
    IF NOT FOUND THEN
      PERFORM network_refuse('NETWORK_BOOKS_MISMATCH');
    END IF;
  END IF;

  -- The supplier's stock against the note. A line that is one of the supplier's own items, counted, in
  -- the line's unit, left its stock with this sale: one standing movement of the credit entry for that
  -- line, item and quantity (and, in so'm, sold for the line's total). A line that is no counted item of
  -- the supplier's moves nothing, and the sale moves nothing that is not such a line.
  PERFORM 1
    FROM (SELECT n.line_no, n.item_id, n.qty,
                 CASE WHEN theirs.currency = 'UZS' AND n.line_total > 0 THEN n.line_total END AS sold_for
            FROM network_note_line n
            JOIN catalog_item i ON i.shop_id = n.shop_id AND i.id = n.item_id
           WHERE n.shop_id = p_peer AND n.note_id = p_note AND i.tracked AND i.unit = n.unit) said
    FULL JOIN
         (SELECT m.line_no, m.item_id, m.qty, m.kind, m.sale_total,
                 count(*) OVER (PARTITION BY m.line_no, m.item_id) AS times
            FROM stock_movement m
           WHERE m.shop_id = p_peer AND m.ledger_entry_id = p_entry AND m.kind <> 'reversal'
             AND NOT EXISTS (SELECT 1 FROM stock_movement r WHERE r.reverses_id = m.id)) moved
      ON moved.line_no = said.line_no AND moved.item_id = said.item_id
   WHERE said.line_no IS NULL OR moved.item_id IS NULL
      OR moved.times <> 1
      OR moved.kind <> 'sale'
      OR moved.qty <> -said.qty
      OR moved.sale_total IS DISTINCT FROM said.sold_for;
  IF FOUND THEN
    PERFORM network_refuse('NETWORK_BOOKS_MISMATCH');
  END IF;

  UPDATE network_note n
     SET status = 'received', decided_at = p_now,
         decided_by = CASE WHEN n.shop_id = p_shop THEN p_member END,
         document_id = CASE WHEN n.shop_id = p_shop THEN p_document END,
         ledger_entry_id = CASE WHEN n.shop_id = p_peer THEN p_entry END
   WHERE n.id = p_note AND n.shop_id IN (p_shop, p_peer);
  UPDATE network_order o SET status = 'received', updated_at = p_now
   WHERE o.id = own.order_id AND o.shop_id IN (p_shop, p_peer);
  PERFORM network_log(p_shop, p_peer, own.link_id, 'note', p_note, 'note_received', p_member, p_now,
                      jsonb_build_object('number', own.number, 'currency', own.currency, 'total', own.total,
                                         'paid', own.paid));
END $$;
REVOKE ALL ON FUNCTION network_receipt_finish(uuid, uuid, uuid, uuid, uuid, uuid, uuid, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION network_receipt_finish(uuid, uuid, uuid, uuid, uuid, uuid, uuid, timestamptz) TO qd_app;
-- End of decision 2.

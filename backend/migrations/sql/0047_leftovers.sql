-- A leftover of the expansion that needs the database.
--
-- 1. `network_receipt_finish` (migration 0045) marked a delivery note received when both shops' books
--    held its totals, currency, parties and author. That the LINES were the note's was the application's
--    doing alone. It is now the database's as well: the note is not marked received unless
--      - the buyer's posted receipt has the note's lines, in the note's order, each with the note's
--        quantity, price and line total, on an item counted in the line's unit;
--      - each of those lines moved the buyer's stock in by that quantity at that price (one standing
--        movement of the receipt);
--      - each line of the note that is one of the supplier's own counted items, in the line's unit, moved
--        the supplier's stock out by that quantity with the sale (one standing movement of the credit
--        entry), and the sale moved nothing else.
--    Same name, same arguments, same refusal (`NETWORK_BOOKS_MISMATCH`), same rights: the function is
--    replaced, so it stays closed to PUBLIC and granted to `qd_app` alone (said again below).
--
-- Nothing else changes: no table, no column, no policy, no right on a table.

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
      AND e.amount = theirs.total AND e.currency = theirs.currency AND e.author_id = theirs.issued_by
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
        AND e.amount = theirs.paid AND e.currency = theirs.currency AND e.author_id = theirs.issued_by;
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

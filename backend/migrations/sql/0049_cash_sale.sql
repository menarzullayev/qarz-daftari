-- A sale for cash, without a customer (the founder's decision of 2026-10-09, open question 7 of the
-- expansion).
--
-- Until now the stock was drawn by a credit sale with goods lines and by stock documents. Goods sold for
-- cash stayed on the books, so a shop that counted its stock saw the figures drift from the shelf. A
-- cash sale is a sixth kind of stock document, `sale`: goods lines, each sold at a price; the counted
-- items leave the stock through the ordinary movements of kind `sale`; the money is income of the cash
-- book while that is on. It names no customer and no supplier and never touches a ledger of either.
--
-- It is written and posted in one transaction, so a sale is never seen as a draft, and it is taken back
-- like any other document: cancelled with a reason, its movements reversed and its cash entry cancelled.
--
-- No table is added: the document, its lines and its movements are the tables of migration 0043, with
-- their row-level security, their rights and their place in `erase_shop` as they are. All of it is behind
-- the platform switch `stock_on`; while that is off no row of the kind is written.

-- ---------------------------------------------------------------------------
-- The document
-- ---------------------------------------------------------------------------

ALTER TABLE stock_document
  DROP CONSTRAINT stock_document_kind_check,
  ADD CONSTRAINT stock_document_kind_check CHECK (
    kind IN ('receipt', 'customer_return', 'supplier_return', 'write_off', 'stocktake', 'sale')),
  -- How the buyer paid: kept on the sale itself, so it is known with the cash book off as well.
  ADD COLUMN method text CONSTRAINT stock_document_method_check CHECK (method IN ('cash', 'card', 'transfer')),
  ADD CONSTRAINT stock_document_sale_method CHECK ((kind = 'sale') = (method IS NOT NULL)),
  -- A cash sale is paid in full when it is made, is worth something, and is in so'm: selling prices are
  -- so'm everywhere (the `sale_total` of a movement, the goods lines of a credit sale).
  ADD CONSTRAINT stock_document_sale_paid CHECK (kind <> 'sale' OR (paid = total AND total > 0 AND currency = 'UZS'));

-- `method` is written with the document and never afterwards: the application's right to update
-- (migration 0043) names the columns it may change, and this one is not among them.

-- The day's sales, and a seller's: the list reads `stock_document_by_kind` (shop, kind, newest first),
-- which already exists. An item's sales are found from its lines, through the lines' own index.

-- ---------------------------------------------------------------------------
-- The money
-- ---------------------------------------------------------------------------

-- Until now every cash entry of a stock document was money paid out. The entry of a sale is money
-- received: the check that said "expense" is replaced by one that asks the document what it is. A
-- supplier's payment stays an expense.
ALTER TABLE cash_entry
  DROP CONSTRAINT cash_entry_stock_is_expense,
  ADD CONSTRAINT cash_entry_supplier_is_expense CHECK (supplier_entry_id IS NULL OR direction = 'expense');

-- The entry of a document is of the document's shop, and its direction is the document's: income for a
-- sale, whose whole total it carries in the sale's currency; an expense for every other kind (what was
-- paid at once on a receipt, what was handed back to a customer). One entry per document at most is the
-- unique index of migration 0043.
CREATE FUNCTION cash_entry_matches_stock_document() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_temp
AS $$
DECLARE
  document stock_document%ROWTYPE;
BEGIN
  SELECT * INTO document FROM stock_document d WHERE d.id = NEW.stock_document_id AND d.shop_id = NEW.shop_id;
  IF NOT FOUND
     OR (document.kind = 'sale' AND (NEW.direction <> 'income' OR NEW.amount <> document.total
                                     OR NEW.currency <> document.currency OR NEW.method <> document.method))
     OR (document.kind <> 'sale' AND NEW.direction <> 'expense') THEN
    RAISE EXCEPTION 'a cash entry of a stock document is of its shop: income of the whole total for a sale, an expense otherwise'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'cash_entry_matches_stock_document';
  END IF;
  RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION cash_entry_matches_stock_document() FROM PUBLIC;
CREATE TRIGGER cash_entry_matches_stock_document
  BEFORE INSERT ON cash_entry
  FOR EACH ROW WHEN (NEW.stock_document_id IS NOT NULL)
  EXECUTE FUNCTION cash_entry_matches_stock_document();

-- ---------------------------------------------------------------------------
-- Reading what was sold
-- ---------------------------------------------------------------------------

-- "What sold in the last N days": the standing sales of a shop since a moment, added up per item. The
-- index holds only sales and everything the sum needs, so the report reads neither the other movements
-- nor the table.
CREATE INDEX stock_movement_sold ON stock_movement (shop_id, created_at)
  INCLUDE (id, item_id, qty, sale_total, cost_total, currency, document_id)
  WHERE kind = 'sale';

-- The lists of documents never show a sale (it has lists of its own), and a shop that sells for cash
-- writes hundreds of sales between two receipts. The two indexes those lists walk, newest first and by
-- state (migrations 0043 and 0047), are therefore rebuilt to hold every kind but that one: a page of
-- documents costs what it holds, whatever was sold. Nothing else read them: a sale is found through
-- `stock_document_by_kind`, which leads with the shop like these.
DROP INDEX stock_document_recent;
CREATE INDEX stock_document_recent ON stock_document (shop_id, created_at DESC, id DESC) WHERE kind <> 'sale';
DROP INDEX stock_document_by_status;
CREATE INDEX stock_document_by_status ON stock_document (shop_id, status, created_at DESC, id DESC)
  WHERE kind <> 'sale';

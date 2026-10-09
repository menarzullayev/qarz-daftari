-- Stock, purchases and suppliers (the founder's decision 6 of 2026-10-09; module I of the expansion).
--
-- Until now the catalogue was a list of names and prices and "nothing here counts stock". This adds the
-- counting: what came in, what went out, what it cost, and what the shop owes the people it buys from.
-- All of it is behind the platform switch `stock_on`; while that is off no row is written to any table
-- below and no existing statement reads one.
--
-- The model in one paragraph. The stock is a ledger: `stock_movement` is insert-only, one row for each
-- time a quantity of one item came in or went out, and a wrong movement is undone by an opposite one.
-- What is on hand and what it is worth (`stock_level`) is the sum of the movements, kept by a trigger so
-- that reading it costs one row; nobody can write it directly. A document (`stock_document`: a purchase
-- receipt, a return, a write-off, a stocktake) is what a person fills in; posting it writes its lines
-- and its movements, and cancelling it reverses them. A supplier's account (`supplier_entry`) is the
-- same kind of ledger as a customer's, seen from the other side, with its balance kept the same way.
--
-- Quantities are numeric(14,3): three decimal places, the scale goods lines have always had. Money is
-- whole minor units of its currency in bigint, as in the rest of the schema; no column here is a float.

-- ---------------------------------------------------------------------------
-- The catalogue item, extended
-- ---------------------------------------------------------------------------

-- An item is counted in stock only when the shop says so. Every item that exists is not counted, so a
-- shop that only uses the catalogue sees no difference. A counted item has one of the units of
-- qarz.domain.stock.UNITS (tests/db/test_stock_schema.py holds the two lists equal).
ALTER TABLE catalog_item
  ADD COLUMN tracked boolean NOT NULL DEFAULT false,
  ADD COLUMN low_stock numeric(14,3) CHECK (low_stock >= 0),
  ADD CONSTRAINT catalog_item_tracked_unit CHECK (
    NOT tracked OR unit IN ('dona', 'kg', 'g', 'l', 'ml', 'm', 'quti', 'paket', 'juft', 'qop', 'blok')),
  -- An alias stands for another item and a learned item is not reviewed yet: neither is counted.
  ADD CONSTRAINT catalog_item_tracked_reviewed CHECK (NOT tracked OR (merged_into IS NULL AND NOT learned));

-- The stock list is the counted items by name, and the low-stock list those of them with a threshold.
CREATE INDEX catalog_item_tracked ON catalog_item (shop_id, name_norm, id) WHERE tracked;
CREATE INDEX catalog_item_low ON catalog_item (shop_id, name_norm, id) WHERE tracked AND low_stock IS NOT NULL;

-- A barcode names one item of the shop. The code is validated by the application (EAN-8, UPC-A and
-- EAN-13 by their check digit; anything else as the text of a Code-128 label).
CREATE TABLE catalog_barcode (
  shop_id     uuid NOT NULL REFERENCES shop(id),
  code        text NOT NULL CHECK (length(code) BETWEEN 1 AND 48),
  item_id     uuid NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (shop_id, code),
  FOREIGN KEY (shop_id, item_id) REFERENCES catalog_item (shop_id, id)
);
CREATE INDEX catalog_barcode_item ON catalog_barcode (item_id);

-- "Refuse sales beyond stock": off, a sale may take an item below zero and the seller is warned.
ALTER TABLE shop ADD COLUMN stock_refuse_negative boolean NOT NULL DEFAULT false;

-- ---------------------------------------------------------------------------
-- Suppliers and what the shop owes them
-- ---------------------------------------------------------------------------

CREATE TABLE supplier (
  id              uuid PRIMARY KEY,
  shop_id         uuid NOT NULL REFERENCES shop(id),
  name            text NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
  name_norm       text NOT NULL,
  phone           text CHECK (phone ~ '^\+[0-9]{8,15}$'),
  note            text CHECK (length(note) <= 200),
  status          text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
  -- For the network between shops (module J): the shop on this platform that this supplier is. Nothing
  -- writes it yet, and the application cannot change it on a supplier that exists.
  linked_shop_id  uuid REFERENCES shop(id),
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (shop_id, name_norm),
  UNIQUE (shop_id, id)
);

-- What a person fills in. `draft` holds the lines of a document that is not posted yet; posting writes
-- them to stock_document_line and clears it.
CREATE TABLE stock_document (
  id             uuid PRIMARY KEY,
  shop_id        uuid NOT NULL REFERENCES shop(id),
  kind           text NOT NULL CHECK (kind IN ('receipt', 'customer_return', 'supplier_return', 'write_off', 'stocktake')),
  number         integer NOT NULL CHECK (number > 0),          -- counted per shop and kind
  status         text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'posted', 'cancelled')),
  doc_date       date NOT NULL,
  supplier_id    uuid,
  customer_id    uuid REFERENCES customer(id),
  currency       text NOT NULL DEFAULT 'UZS' CHECK (currency IN ('UZS', 'USD')),
  total          bigint NOT NULL DEFAULT 0 CHECK (total >= 0),  -- of the lines, in minor units
  paid           bigint NOT NULL DEFAULT 0 CHECK (paid >= 0),   -- handed over at once, in money
  reason         text CHECK (reason IN ('damaged', 'expired', 'lost', 'own_use')),
  note           text CHECK (length(note) <= 200),
  draft          jsonb,
  ledger_entry_id uuid REFERENCES ledger_entry(id),             -- a customer's return: the entry that lowered the debt
  -- For module J: the order or delivery note of the other shop that this document answers.
  origin_ref     uuid,
  created_by     uuid NOT NULL REFERENCES membership(id),
  created_at     timestamptz NOT NULL DEFAULT now(),
  posted_by      uuid REFERENCES membership(id),
  posted_at      timestamptz,
  cancelled_by   uuid REFERENCES membership(id),
  cancelled_at   timestamptz,
  cancel_reason  text CHECK (length(cancel_reason) BETWEEN 1 AND 200),
  UNIQUE (shop_id, kind, number),
  UNIQUE (shop_id, id),
  FOREIGN KEY (shop_id, supplier_id) REFERENCES supplier (shop_id, id),
  CHECK (paid <= total),
  CHECK ((kind = 'write_off') = (reason IS NOT NULL)),
  CHECK (supplier_id IS NULL OR kind IN ('receipt', 'supplier_return')),
  CHECK (kind <> 'supplier_return' OR supplier_id IS NOT NULL),
  CHECK ((kind = 'customer_return') = (customer_id IS NOT NULL)),
  CHECK ((status = 'draft') = (draft IS NOT NULL) OR status = 'cancelled'),
  CHECK (status <> 'posted' OR (posted_at IS NOT NULL AND posted_by IS NOT NULL)),
  CHECK ((status = 'cancelled') = (cancelled_at IS NOT NULL)),
  CHECK ((cancelled_at IS NULL) = (cancelled_by IS NULL) AND (cancelled_at IS NULL) = (cancel_reason IS NULL))
);
CREATE INDEX stock_document_recent ON stock_document (shop_id, created_at DESC, id DESC);
CREATE INDEX stock_document_by_kind ON stock_document (shop_id, kind, created_at DESC, id DESC);
CREATE INDEX stock_document_supplier ON stock_document (supplier_id, created_at DESC) WHERE supplier_id IS NOT NULL;
CREATE INDEX stock_document_ledger_entry ON stock_document (ledger_entry_id) WHERE ledger_entry_id IS NOT NULL;

-- A document moves one way: draft, then posted, then cancelled; a draft may also be cancelled (thrown
-- away). Once posted, nothing of what it says may change: only the cancellation is added.
CREATE FUNCTION stock_document_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_temp
AS $$
BEGIN
  IF OLD.status = 'cancelled' THEN
    RAISE EXCEPTION 'a cancelled document does not change'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'stock_document_guard';
  END IF;
  IF NEW.status = 'draft' AND OLD.status <> 'draft' THEN
    RAISE EXCEPTION 'a document does not go back to a draft'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'stock_document_guard';
  END IF;
  IF (NEW.id, NEW.shop_id, NEW.kind, NEW.number, NEW.created_by, NEW.created_at)
     IS DISTINCT FROM (OLD.id, OLD.shop_id, OLD.kind, OLD.number, OLD.created_by, OLD.created_at) THEN
    RAISE EXCEPTION 'what a document is does not change'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'stock_document_guard';
  END IF;
  IF OLD.status = 'posted' AND (
       NEW.status <> 'cancelled'
       OR (NEW.doc_date, NEW.supplier_id, NEW.customer_id, NEW.currency, NEW.total, NEW.paid, NEW.reason, NEW.note,
           NEW.ledger_entry_id, NEW.origin_ref, NEW.posted_by, NEW.posted_at)
          IS DISTINCT FROM
          (OLD.doc_date, OLD.supplier_id, OLD.customer_id, OLD.currency, OLD.total, OLD.paid, OLD.reason, OLD.note,
           OLD.ledger_entry_id, OLD.origin_ref, OLD.posted_by, OLD.posted_at)) THEN
    RAISE EXCEPTION 'a posted document can only be cancelled'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'stock_document_guard';
  END IF;
  RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION stock_document_guard() FROM PUBLIC;
CREATE TRIGGER stock_document_guard BEFORE UPDATE ON stock_document
  FOR EACH ROW EXECUTE FUNCTION stock_document_guard();

-- The lines of a posted document: insert-only, written in the transaction that posts it.
CREATE TABLE stock_document_line (
  shop_id      uuid NOT NULL REFERENCES shop(id),
  document_id  uuid NOT NULL,
  line_no      smallint NOT NULL CHECK (line_no > 0),
  item_id      uuid NOT NULL,
  qty          numeric(14,3) NOT NULL CHECK (qty >= 0),    -- a stocktake: what was counted, which may be nothing
  unit_cost    bigint CHECK (unit_cost >= 0),              -- per unit, in the document's currency
  line_total   bigint CHECK (line_total >= 0),
  expected     numeric(14,3),                              -- a stocktake: what the books held when it was posted
  PRIMARY KEY (document_id, line_no),
  FOREIGN KEY (shop_id, document_id) REFERENCES stock_document (shop_id, id),
  FOREIGN KEY (shop_id, item_id) REFERENCES catalog_item (shop_id, id)
);
CREATE INDEX stock_document_line_item ON stock_document_line (item_id);

CREATE FUNCTION stock_document_line_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_temp
AS $$
BEGIN
  IF (SELECT d.status FROM stock_document d WHERE d.id = NEW.document_id) IS DISTINCT FROM 'draft' THEN
    RAISE EXCEPTION 'lines are written while the document is being posted, and never afterwards'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'stock_document_line_guard';
  END IF;
  RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION stock_document_line_guard() FROM PUBLIC;
CREATE TRIGGER stock_document_line_guard BEFORE INSERT ON stock_document_line
  FOR EACH ROW EXECUTE FUNCTION stock_document_line_guard();

-- A supplier's account. Insert-only; an entry is cancelled by a reversal whose note says why.
CREATE TABLE supplier_entry (
  id           uuid PRIMARY KEY,
  shop_id      uuid NOT NULL REFERENCES shop(id),
  supplier_id  uuid NOT NULL,
  seq          integer NOT NULL CHECK (seq > 0),
  kind         text NOT NULL CHECK (kind IN ('purchase', 'opening', 'payment', 'return', 'reversal')),
  amount       bigint NOT NULL CHECK (amount > 0),
  currency     text NOT NULL DEFAULT 'UZS' CHECK (currency IN ('UZS', 'USD')),
  note         text CHECK (length(note) <= 200),
  reverses_id  uuid REFERENCES supplier_entry(id),
  document_id  uuid,                                   -- the receipt or the return that wrote it
  author_id    uuid NOT NULL REFERENCES membership(id),
  created_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (supplier_id, seq),
  UNIQUE (reverses_id),                                -- an entry is reversed at most once
  CHECK ((kind = 'reversal') = (reverses_id IS NOT NULL)),
  FOREIGN KEY (shop_id, supplier_id) REFERENCES supplier (shop_id, id),
  FOREIGN KEY (shop_id, document_id) REFERENCES stock_document (shop_id, id)
);
CREATE INDEX supplier_entry_document ON supplier_entry (document_id) WHERE document_id IS NOT NULL;
CREATE INDEX supplier_entry_shop_time ON supplier_entry (shop_id, created_at);

-- What the shop owes each supplier in each currency: the sum of the entries that stand, kept by the
-- trigger below. Above zero the shop owes; below zero it paid in advance.
CREATE TABLE supplier_balance (
  shop_id      uuid NOT NULL REFERENCES shop(id),
  supplier_id  uuid NOT NULL,
  currency     text NOT NULL CHECK (currency IN ('UZS', 'USD')),
  balance      bigint NOT NULL DEFAULT 0,
  PRIMARY KEY (supplier_id, currency),
  FOREIGN KEY (shop_id, supplier_id) REFERENCES supplier (shop_id, id)
);
CREATE INDEX supplier_balance_shop ON supplier_balance (shop_id, currency);

CREATE FUNCTION supplier_entry_apply() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp
AS $$
DECLARE
  original supplier_entry%ROWTYPE;
  change bigint;
BEGIN
  IF NEW.reverses_id IS NOT NULL THEN
    SELECT * INTO original FROM supplier_entry e WHERE e.id = NEW.reverses_id;
    IF NOT FOUND OR original.kind = 'reversal' OR original.supplier_id <> NEW.supplier_id
       OR original.shop_id <> NEW.shop_id OR original.amount <> NEW.amount OR original.currency <> NEW.currency THEN
      RAISE EXCEPTION 'a reversal repeats the supplier, the amount and the currency of the entry it reverses'
        USING ERRCODE = 'check_violation', CONSTRAINT = 'supplier_entry_reversal';
    END IF;
    change := CASE WHEN original.kind IN ('purchase', 'opening') THEN -NEW.amount ELSE NEW.amount END;
  ELSE
    change := CASE WHEN NEW.kind IN ('purchase', 'opening') THEN NEW.amount ELSE -NEW.amount END;
  END IF;
  INSERT INTO supplier_balance (shop_id, supplier_id, currency, balance)
  VALUES (NEW.shop_id, NEW.supplier_id, NEW.currency, change)
  ON CONFLICT (supplier_id, currency) DO UPDATE SET balance = supplier_balance.balance + EXCLUDED.balance;
  RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION supplier_entry_apply() FROM PUBLIC;
CREATE TRIGGER supplier_entry_apply AFTER INSERT ON supplier_entry
  FOR EACH ROW EXECUTE FUNCTION supplier_entry_apply();

-- ---------------------------------------------------------------------------
-- The stock ledger
-- ---------------------------------------------------------------------------

-- What is on hand of an item and what that is worth at cost. Written only by the trigger of
-- stock_movement; the application reads it.
CREATE TABLE stock_level (
  shop_id        uuid NOT NULL REFERENCES shop(id),
  item_id        uuid PRIMARY KEY,
  on_hand        numeric(14,3) NOT NULL DEFAULT 0,
  cost_value     bigint NOT NULL DEFAULT 0 CHECK (cost_value >= 0),   -- of what is on hand, in cost_currency
  cost_currency  text CHECK (cost_currency IN ('UZS', 'USD')),
  last_cost      numeric(18,4) CHECK (last_cost >= 0),                -- the average the last time something was on hand
  last_seq       integer NOT NULL DEFAULT 0,
  last_sale_at   timestamptz,
  updated_at     timestamptz NOT NULL DEFAULT now(),
  CHECK (on_hand > 0 OR cost_value = 0),                              -- nothing on hand is worth nothing
  FOREIGN KEY (shop_id, item_id) REFERENCES catalog_item (shop_id, id)
);
-- The report's "not sold for N days" reads what is on hand, longest unsold first, and its totals read
-- the same rows.
CREATE INDEX stock_level_idle ON stock_level (shop_id, last_sale_at NULLS FIRST, item_id) WHERE on_hand > 0;

CREATE TABLE stock_movement (
  id              uuid PRIMARY KEY,
  shop_id         uuid NOT NULL REFERENCES shop(id),
  item_id         uuid NOT NULL,
  item_seq        integer NOT NULL CHECK (item_seq > 0),               -- 1, 2, 3 ... for each item
  kind            text NOT NULL CHECK (kind IN (
                    'receipt', 'sale', 'customer_return', 'supplier_return', 'write_off', 'correction', 'reversal')),
  qty             numeric(14,3) NOT NULL CHECK (qty <> 0),             -- above zero came in, below zero went out
  unit_cost       bigint CHECK (unit_cost >= 0),                       -- as written on a receipt line
  cost_total      bigint CHECK (cost_total >= 0),                      -- the cost of the quantity that moved
  sale_total      bigint CHECK (sale_total > 0),                       -- a sale: what its line was sold for, in so'm
  value_delta     bigint NOT NULL,                                     -- what it did to the value of the stock
  currency        text CHECK (currency IN ('UZS', 'USD')),             -- of the cost figures
  on_hand_after   numeric(14,3) NOT NULL,
  value_after     bigint NOT NULL CHECK (value_after >= 0),
  reason          text CHECK (reason IN ('damaged', 'expired', 'lost', 'own_use')),
  document_id     uuid,
  line_no         smallint,
  ledger_entry_id uuid REFERENCES ledger_entry(id),                    -- the sale or the return that caused it
  reverses_id     uuid REFERENCES stock_movement(id),
  author_id       uuid NOT NULL REFERENCES membership(id),
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (item_id, item_seq),
  UNIQUE (reverses_id),                                                -- a movement is reversed at most once
  CHECK ((kind = 'reversal') = (reverses_id IS NOT NULL)),
  CHECK ((kind = 'write_off') = (reason IS NOT NULL) OR kind = 'reversal'),
  CHECK (on_hand_after > 0 OR value_after = 0),
  FOREIGN KEY (shop_id, item_id) REFERENCES catalog_item (shop_id, id),
  FOREIGN KEY (shop_id, document_id) REFERENCES stock_document (shop_id, id)
);
CREATE INDEX stock_movement_shop_time ON stock_movement (shop_id, created_at DESC, id DESC);
CREATE INDEX stock_movement_entry ON stock_movement (ledger_entry_id) WHERE ledger_entry_id IS NOT NULL;
CREATE INDEX stock_movement_document ON stock_movement (document_id, line_no) WHERE document_id IS NOT NULL;
-- The report's "sold below cost" reads only the sales that were.
CREATE INDEX stock_movement_below_cost ON stock_movement (shop_id, created_at DESC, id DESC)
  WHERE kind = 'sale' AND currency = 'UZS' AND sale_total < cost_total;

-- Each movement must continue its item's level: the next number, and the figures after it equal to the
-- level plus what it moved. The application computes them (qarz.domain.stock); a movement computed from
-- a level that is no longer current, or computed wrongly, is refused here, and the level is then set
-- from the movement. So the level cannot differ from the sum of the movements.
CREATE FUNCTION stock_movement_apply() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp
AS $$
DECLARE
  level stock_level%ROWTYPE;
BEGIN
  INSERT INTO stock_level (shop_id, item_id) VALUES (NEW.shop_id, NEW.item_id) ON CONFLICT (item_id) DO NOTHING;
  SELECT * INTO level FROM stock_level l WHERE l.item_id = NEW.item_id FOR UPDATE;
  IF NEW.item_seq <> level.last_seq + 1
     OR NEW.on_hand_after <> level.on_hand + NEW.qty
     OR NEW.value_after <> level.cost_value + NEW.value_delta THEN
    RAISE EXCEPTION 'a stock movement continues the level of its item'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'stock_movement_continues_level';
  END IF;
  IF (NEW.value_after > 0 AND NEW.currency IS NULL)
     OR (level.cost_value > 0 AND NEW.currency IS DISTINCT FROM level.cost_currency) THEN
    RAISE EXCEPTION 'the cost of an item is kept in one currency while something of it is on hand'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'stock_movement_cost_currency';
  END IF;
  IF NEW.reverses_id IS NOT NULL THEN
    PERFORM 1 FROM stock_movement o
      WHERE o.id = NEW.reverses_id AND o.item_id = NEW.item_id AND o.kind <> 'reversal' AND o.qty = -NEW.qty;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'a reversal moves the same item by the same quantity the other way'
        USING ERRCODE = 'check_violation', CONSTRAINT = 'stock_movement_reversal';
    END IF;
  END IF;
  UPDATE stock_level l
     SET on_hand = NEW.on_hand_after,
         cost_value = NEW.value_after,
         cost_currency = coalesce(NEW.currency, l.cost_currency),
         last_cost = CASE
           WHEN NEW.on_hand_after > 0 AND NEW.currency IS NOT NULL THEN round(NEW.value_after / NEW.on_hand_after, 4)
           WHEN NEW.currency IS DISTINCT FROM l.cost_currency THEN NULL
           ELSE l.last_cost END,
         last_seq = NEW.item_seq,
         last_sale_at = CASE WHEN NEW.kind = 'sale' THEN NEW.created_at ELSE l.last_sale_at END,
         updated_at = NEW.created_at
   WHERE l.item_id = NEW.item_id;
  RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION stock_movement_apply() FROM PUBLIC;
CREATE TRIGGER stock_movement_apply AFTER INSERT ON stock_movement
  FOR EACH ROW EXECUTE FUNCTION stock_movement_apply();

-- Where the kept figures differ from the ledgers they are the sums of. Empty when all is well; for the
-- tests and for whoever looks into a doubt. Granted to no role.
CREATE FUNCTION stock_level_mismatches(p_shop uuid)
RETURNS TABLE (item_id uuid, stored_on_hand numeric, ledger_on_hand numeric, stored_value bigint, ledger_value bigint)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
AS $$
  WITH ledger AS (
    SELECT m.item_id, sum(m.qty) AS on_hand, sum(m.value_delta)::bigint AS value, max(m.item_seq) AS last_seq
      FROM stock_movement m WHERE p_shop IS NULL OR m.shop_id = p_shop GROUP BY m.item_id)
  SELECT coalesce(l.item_id, g.item_id), l.on_hand, g.on_hand, l.cost_value, g.value
    FROM (SELECT * FROM stock_level s WHERE p_shop IS NULL OR s.shop_id = p_shop) l
    FULL JOIN ledger g ON g.item_id = l.item_id
   WHERE l.item_id IS NULL OR g.item_id IS NULL
      OR l.on_hand <> g.on_hand OR l.cost_value <> g.value OR l.last_seq <> g.last_seq;
$$;
REVOKE ALL ON FUNCTION stock_level_mismatches(uuid) FROM PUBLIC;

CREATE FUNCTION supplier_balance_mismatches(p_shop uuid)
RETURNS TABLE (supplier_id uuid, currency text, stored_balance bigint, ledger_balance bigint)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
AS $$
  WITH ledger AS (
    SELECT e.supplier_id, e.currency,
           sum(CASE WHEN e.kind IN ('purchase', 'opening') THEN e.amount ELSE -e.amount END)::bigint AS balance
      FROM supplier_entry e
     WHERE (p_shop IS NULL OR e.shop_id = p_shop)
       AND e.kind <> 'reversal'
       AND NOT EXISTS (SELECT 1 FROM supplier_entry r WHERE r.reverses_id = e.id)
     GROUP BY e.supplier_id, e.currency)
  SELECT coalesce(b.supplier_id, g.supplier_id), coalesce(b.currency, g.currency), b.balance, g.balance
    FROM (SELECT * FROM supplier_balance s WHERE p_shop IS NULL OR s.shop_id = p_shop) b
    FULL JOIN ledger g ON g.supplier_id = b.supplier_id AND g.currency = b.currency
   WHERE coalesce(b.balance, 0) <> coalesce(g.balance, 0);
$$;
REVOKE ALL ON FUNCTION supplier_balance_mismatches(uuid) FROM PUBLIC;

-- ---------------------------------------------------------------------------
-- The cash book (migration 0042)
-- ---------------------------------------------------------------------------

-- Money that leaves the till for goods, or back to a customer for goods returned, is an expense of the
-- cash book written by the stock in the same transaction, while the cash book is on. Such an entry says
-- where it came from, the way a customer's payment does with `ledger_entry_id`: the payment on a
-- supplier's account, or the document whose money it is (a purchase for cash, a customer's refund). It
-- is cancelled where it was written, and in no other way.
ALTER TABLE cash_entry
  ADD COLUMN supplier_entry_id uuid REFERENCES supplier_entry(id),
  ADD COLUMN stock_document_id uuid REFERENCES stock_document(id),
  ADD CONSTRAINT cash_entry_one_source CHECK (num_nonnulls(ledger_entry_id, supplier_entry_id, stock_document_id) <= 1),
  ADD CONSTRAINT cash_entry_stock_is_expense CHECK (
    (supplier_entry_id IS NULL AND stock_document_id IS NULL) OR direction = 'expense');
-- A payment to a supplier, and the money of a document, are in the cash book once.
CREATE UNIQUE INDEX cash_entry_one_per_supplier_entry ON cash_entry (supplier_entry_id)
  WHERE supplier_entry_id IS NOT NULL;
CREATE UNIQUE INDEX cash_entry_one_per_stock_document ON cash_entry (stock_document_id)
  WHERE stock_document_id IS NOT NULL;

-- The entry repeats what it stands for: the shop, the amount and the currency of the supplier's payment.
CREATE FUNCTION cash_entry_matches_supplier_payment() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_temp
AS $$
BEGIN
  PERFORM 1 FROM supplier_entry e
    WHERE e.id = NEW.supplier_entry_id AND e.shop_id = NEW.shop_id AND e.kind = 'payment'
      AND e.amount = NEW.amount AND e.currency = NEW.currency;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'a cash entry of a supplier payment repeats its shop, amount and currency'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'cash_entry_matches_supplier_payment';
  END IF;
  RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION cash_entry_matches_supplier_payment() FROM PUBLIC;
CREATE TRIGGER cash_entry_matches_supplier_payment
  BEFORE INSERT ON cash_entry
  FOR EACH ROW WHEN (NEW.supplier_entry_id IS NOT NULL)
  EXECUTE FUNCTION cash_entry_matches_supplier_payment();

-- ---------------------------------------------------------------------------
-- Row-level security and rights
-- ---------------------------------------------------------------------------

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'catalog_barcode', 'supplier', 'supplier_entry', 'supplier_balance', 'stock_document', 'stock_document_line',
    'stock_movement', 'stock_level']
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format(
      'CREATE POLICY tenant ON %I USING (shop_id = nullif(current_setting(''qd.shop_id'', true), '''')::uuid) '
      'WITH CHECK (shop_id = nullif(current_setting(''qd.shop_id'', true), '''')::uuid)', t);
  END LOOP;
END $$;

-- The ordinary application. The two ledgers and the lines of a posted document are insert-only for it,
-- like the customers' ledger; the kept figures are read-only; a supplier's link to another shop is not
-- its to change; and a document changes through the columns the guard above watches.
GRANT SELECT, INSERT, DELETE ON catalog_barcode TO qd_app;
GRANT SELECT, INSERT ON supplier TO qd_app;
GRANT UPDATE (name, name_norm, phone, note, status) ON supplier TO qd_app;
GRANT SELECT, INSERT ON supplier_entry TO qd_app;
GRANT SELECT ON supplier_balance TO qd_app;
GRANT SELECT, INSERT ON stock_document TO qd_app;
GRANT UPDATE (status, doc_date, supplier_id, customer_id, currency, total, paid, reason, note, draft,
              ledger_entry_id, posted_by, posted_at, cancelled_by, cancelled_at, cancel_reason)
  ON stock_document TO qd_app;
GRANT SELECT, INSERT ON stock_document_line TO qd_app;
GRANT SELECT, INSERT ON stock_movement TO qd_app;
GRANT SELECT ON stock_level TO qd_app;

-- The worker writes the owner's export of the shop's own data, which now includes these books. It reads
-- and nothing else. The administrators' side has nothing to do here.
GRANT SELECT ON catalog_item, catalog_barcode, supplier, supplier_entry, supplier_balance, stock_document,
  stock_document_line, stock_movement, stock_level TO qd_worker;

-- ---------------------------------------------------------------------------
-- Erasing a shop
-- ---------------------------------------------------------------------------

-- The function of the migration before this one (0042) with the eight tables above, children before
-- parents: the cash entries that point at them go first, as they already did.
-- and the new setting of the tombstone. A supplier of another shop that pointed at this one keeps
-- pointing at the tombstone, which says nothing.
CREATE OR REPLACE FUNCTION erase_shop(p_shop_id uuid) RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  people uuid[];
  person uuid;
BEGIN
  PERFORM 1 FROM shop
    WHERE id = p_shop_id AND status = 'deletion_pending' AND deletion_due IS NOT NULL AND deletion_due <= now()
    FOR UPDATE;
  IF NOT FOUND THEN
    RETURN false;
  END IF;

  -- Everyone the shop knew: its staff and the people linked as its customers.
  SELECT array_agg(DISTINCT user_id) INTO people FROM (
    SELECT user_id FROM membership WHERE shop_id = p_shop_id
    UNION
    SELECT user_id FROM customer_link WHERE shop_id = p_shop_id AND user_id IS NOT NULL
  ) known;

  -- Children before parents. Every table that carries a shop identifier is listed here;
  -- tests/db/test_shop_erasure.py fails when one is added and not listed.
  DELETE FROM cash_entry WHERE shop_id = p_shop_id;
  DELETE FROM stock_movement WHERE shop_id = p_shop_id;
  DELETE FROM stock_level WHERE shop_id = p_shop_id;
  DELETE FROM stock_document_line WHERE shop_id = p_shop_id;
  DELETE FROM supplier_entry WHERE shop_id = p_shop_id;
  DELETE FROM supplier_balance WHERE shop_id = p_shop_id;
  DELETE FROM stock_document WHERE shop_id = p_shop_id;
  DELETE FROM supplier WHERE shop_id = p_shop_id;
  DELETE FROM catalog_barcode WHERE shop_id = p_shop_id;
  DELETE FROM cash_category WHERE shop_id = p_shop_id;
  DELETE FROM goods_line WHERE shop_id = p_shop_id;
  DELETE FROM open_debt WHERE shop_id = p_shop_id;
  DELETE FROM promise WHERE shop_id = p_shop_id;
  DELETE FROM dispute WHERE shop_id = p_shop_id;
  DELETE FROM date_change_request WHERE shop_id = p_shop_id;
  DELETE FROM payment_notice WHERE shop_id = p_shop_id;
  DELETE FROM export_job WHERE shop_id = p_shop_id;
  DELETE FROM reminder WHERE shop_id = p_shop_id;
  DELETE FROM removal_request WHERE shop_id = p_shop_id;
  DELETE FROM ledger_entry WHERE shop_id = p_shop_id;
  DELETE FROM import_batch WHERE shop_id = p_shop_id;
  DELETE FROM customer_link WHERE shop_id = p_shop_id;
  DELETE FROM customer_share WHERE shop_id = p_shop_id;
  DELETE FROM customer WHERE shop_id = p_shop_id;
  DELETE FROM catalog_item WHERE shop_id = p_shop_id;
  DELETE FROM subscription_receipt WHERE shop_id = p_shop_id;
  DELETE FROM stored_file WHERE shop_id = p_shop_id;
  DELETE FROM support_access WHERE shop_id = p_shop_id;
  DELETE FROM ownership_transfer WHERE shop_id = p_shop_id;
  DELETE FROM invitation WHERE shop_id = p_shop_id;
  DELETE FROM activity WHERE shop_id = p_shop_id;
  DELETE FROM request_key WHERE shop_id = p_shop_id;
  DELETE FROM admin_request_key WHERE about_shop = p_shop_id;
  DELETE FROM outbox_message WHERE shop_id = p_shop_id;
  DELETE FROM online_payment WHERE shop_id = p_shop_id;
  DELETE FROM subscription WHERE shop_id = p_shop_id;
  DELETE FROM membership WHERE shop_id = p_shop_id;

  UPDATE app_user SET active_shop = NULL WHERE active_shop = p_shop_id;
  -- The row stays as a tombstone with nothing of the shop left in it.
  UPDATE shop
     SET status = 'erased', name = 'erased', deletion_due = NULL, reminders_on = false, sms_on = false,
         default_credit_limit = NULL, share_phone = NULL, usd_on = false, default_credit_limit_usd = NULL,
         stock_refuse_negative = false
   WHERE id = p_shop_id;

  IF people IS NOT NULL THEN
    FOREACH person IN ARRAY people LOOP
      PERFORM forget_user_if_unused(person);
    END LOOP;
  END IF;
  RETURN true;
END $$;

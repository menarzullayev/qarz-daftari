-- The shared product catalogue (the founder's seven decisions of 2026-10-10).
--
-- Until now every shop typed every item itself. This adds one catalogue for the whole platform: a shop
-- searches it, picks an item and types only its own price. All of it is behind the platform switch
-- `catalog_on`; while that is off no row is written to any table below and no existing statement reads
-- one.
--
-- These are the first tables that belong to no shop and are still read by every shop, so they do not
-- sit under the tenant policy the way everything a shop owns does:
--
--   * `shared_item` and `shared_barcode` hold nothing of any shop: names, a unit, a package size, an
--     approximate price, a photo. The ordinary application may read them and nothing else. They change
--     in two ways only: the import command, which connects as the administrators' role, and an
--     administrator's approval, which goes through `admin_shared_decide` below.
--   * `shared_suggestion` is what a shop proposes, and it is a shop's row like any other: it carries
--     `shop_id` under forced row-level security, so a shop reads its own proposals and nobody else's.
--     The application may add one that waits and may not change or delete one; the decision is the
--     administrators'.
--
-- What a suggestion holds is the whole of what leaves a shop: the item's name, its unit and a barcode.
-- Never a price, a quantity, a cost, a supplier or a customer. The administrators' queue is not told
-- which shop a suggestion came from.

-- ---------------------------------------------------------------------------
-- The catalogue
-- ---------------------------------------------------------------------------

CREATE TABLE shared_item (
  id           uuid PRIMARY KEY,
  -- The stable key of a row of the seed file: what makes the import safe to run again.
  source_key   text UNIQUE CHECK (source_key ~ '^[0-9a-f]{16}$'),
  name_ru      text CHECK (length(name_ru) BETWEEN 1 AND 300),
  name_uz      text CHECK (length(name_uz) BETWEEN 1 AND 300),   -- Uzbek, Latin; Cyrillic is generated
  -- Both names and the package size in the matching form of qarz.domain.names: what a search reads.
  search_norm  text NOT NULL CHECK (length(search_norm) >= 1),
  category     text NOT NULL DEFAULT 'other' CHECK (category ~ '^[a-z]{2,16}$'),
  subcategory  text CHECK (length(subcategory) BETWEEN 1 AND 120),
  amount       text CHECK (length(amount) BETWEEN 1 AND 24),     -- the package: "200 г", "1 л"
  unit         text NOT NULL DEFAULT 'dona' CHECK (length(unit) BETWEEN 1 AND 12),
  -- An approximate retail price in Tashkent, in so'm. Advice shown under the price field; never a price.
  price_hint   bigint CHECK (price_hint > 0),
  -- The SHA-256 of the photo, which is also where it is kept in the file store.
  image_key    text CHECK (image_key ~ '^[0-9a-f]{64}$'),
  status       text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'hidden')),
  created_at   timestamptz NOT NULL DEFAULT now(),
  CHECK (name_ru IS NOT NULL OR name_uz IS NOT NULL)
);
-- A search is "every typed word is somewhere in the names": trigrams answer that without reading
-- thirteen thousand rows. The list without a search, and within a category, is by name.
CREATE INDEX shared_item_search ON shared_item USING gin (search_norm gin_trgm_ops) WHERE status = 'active';
CREATE INDEX shared_item_by_name ON shared_item (search_norm, id) WHERE status = 'active';
CREATE INDEX shared_item_by_category ON shared_item (category, search_norm, id) WHERE status = 'active';

-- A barcode names one item of the catalogue. The catalogue starts without any: shops fill them in, and
-- each one is here only after an administrator approved it.
CREATE TABLE shared_barcode (
  code        text PRIMARY KEY CHECK (length(code) BETWEEN 1 AND 48),
  item_id     uuid NOT NULL REFERENCES shared_item(id),
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX shared_barcode_item ON shared_barcode (item_id);

-- What a shop proposes for the catalogue: an item it added by hand, or a barcode it attached to an item
-- it had picked from the catalogue.
CREATE TABLE shared_suggestion (
  id              uuid PRIMARY KEY,
  shop_id         uuid NOT NULL REFERENCES shop(id),
  kind            text NOT NULL CHECK (kind IN ('item', 'barcode')),
  name            text CHECK (length(name) BETWEEN 1 AND 80),
  name_norm       text,
  unit            text CHECK (length(unit) BETWEEN 1 AND 12),
  barcode         text CHECK (length(barcode) BETWEEN 1 AND 48),
  -- A barcode: the catalogue item it is proposed for. An item: the catalogue item its approval made.
  shared_item_id  uuid REFERENCES shared_item(id),
  -- The shop's own item, so that an approval can tie it to the catalogue item it became.
  item_id         uuid,
  status          text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
  decided_by      uuid,
  decided_at      timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now(),
  CHECK ((kind = 'item') = (name IS NOT NULL AND name_norm IS NOT NULL AND unit IS NOT NULL)),
  CHECK (kind <> 'barcode' OR (barcode IS NOT NULL AND shared_item_id IS NOT NULL)),
  CHECK (kind <> 'item' OR status = 'approved' OR shared_item_id IS NULL),
  CHECK ((status = 'pending') = (decided_at IS NULL)),
  CHECK ((decided_at IS NULL) = (decided_by IS NULL))
);
-- A shop proposes the same thing once, whatever was decided about it.
CREATE UNIQUE INDEX shared_suggestion_item_once ON shared_suggestion (shop_id, name_norm) WHERE kind = 'item';
CREATE UNIQUE INDEX shared_suggestion_barcode_once ON shared_suggestion (shop_id, barcode, shared_item_id)
  WHERE kind = 'barcode';
-- The administrators' queue: one status, oldest first.
CREATE INDEX shared_suggestion_queue ON shared_suggestion (status, created_at, id);
CREATE INDEX shared_suggestion_same_name ON shared_suggestion (name_norm) WHERE kind = 'item' AND status = 'pending';
CREATE INDEX shared_suggestion_same_code ON shared_suggestion (barcode) WHERE status = 'pending';

-- The shop's item says which catalogue item it was picked from. A shop holds a catalogue item once:
-- picking it again finds the item it already has.
ALTER TABLE catalog_item ADD COLUMN shared_item_id uuid REFERENCES shared_item(id);
CREATE UNIQUE INDEX catalog_item_shared_once ON catalog_item (shop_id, shared_item_id)
  WHERE shared_item_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Row-level security and rights
-- ---------------------------------------------------------------------------

ALTER TABLE shared_suggestion ENABLE ROW LEVEL SECURITY;
ALTER TABLE shared_suggestion FORCE ROW LEVEL SECURITY;
-- A shop reads its own proposals, and writes one only as a proposal: waiting, decided by nobody.
CREATE POLICY tenant ON shared_suggestion
  USING (shop_id = nullif(current_setting('qd.shop_id', true), '')::uuid)
  WITH CHECK (shop_id = nullif(current_setting('qd.shop_id', true), '')::uuid
              AND status = 'pending' AND decided_at IS NULL AND decided_by IS NULL);

-- The ordinary application reads the catalogue and proposes. The policy above lets it add a proposal
-- that waits and nothing else, and it may neither change nor delete one: the decision is not its to write.
GRANT SELECT ON shared_item, shared_barcode TO qd_app;
GRANT SELECT, INSERT ON shared_suggestion TO qd_app;

-- The administrators' role loads the catalogue (the import command) and may correct an item. The
-- suggestions it reaches through the two functions below alone, which ask who the administrator is.
GRANT SELECT, INSERT, UPDATE ON shared_item TO qd_admin;
GRANT SELECT ON shared_barcode TO qd_admin;

-- ---------------------------------------------------------------------------
-- The administrators' queue
-- ---------------------------------------------------------------------------

-- Suggestions of one status across all shops, oldest first. Which shop proposed one is not returned;
-- `same` counts the other shops that wait with the same name, or the same barcode.
CREATE FUNCTION admin_shared_suggestions(
  p_admin uuid, p_status text, p_after_created timestamptz, p_after_id uuid, p_limit integer
)
RETURNS TABLE (
  suggestion_id uuid, kind text, name text, unit text, barcode text, shared_item_id uuid,
  item_name_ru text, item_name_uz text, item_amount text, status text, created_at timestamptz,
  decided_at timestamptz, same integer
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  SELECT s.id, s.kind, s.name, s.unit, s.barcode, s.shared_item_id, i.name_ru, i.name_uz, i.amount, s.status,
         s.created_at, s.decided_at,
         (SELECT count(*)::integer FROM shared_suggestion o
           WHERE o.status = 'pending' AND o.id <> s.id AND o.shop_id <> s.shop_id
             AND ((s.kind = 'item' AND o.kind = 'item' AND o.name_norm = s.name_norm)
                  OR (s.kind = 'barcode' AND o.barcode = s.barcode)))
    FROM shared_suggestion s
    LEFT JOIN shared_item i ON i.id = s.shared_item_id
   WHERE s.status = p_status
     AND EXISTS (SELECT 1 FROM admin_account a WHERE a.user_id = p_admin AND a.status = 'active')
     AND (p_after_created IS NULL OR (s.created_at, s.id) > (p_after_created, p_after_id))
   ORDER BY s.created_at, s.id
   LIMIT least(greatest(p_limit, 1), 101);
$$;

-- Decides one suggestion, once: the row is locked, and only one that still waits changes.
--
-- Approving an item writes it to the catalogue under the names and the category the administrator
-- gives (`p_item` is the new item's identifier), with its barcode when it has one that is free, and
-- ties the proposing shop's own item to it. Approving a barcode attaches it to its catalogue item, and
-- settles every other shop's wait for the same code on the same item. A code that already names
-- another catalogue item is not moved: the answer is 'barcode_taken' and nothing changes.
--
-- Answers: 'approved', 'rejected', 'decided' (it was decided before), 'barcode_taken', 'unnamed' (an
-- item approved without a name: nothing changes), or 'missing' (no such suggestion, or the caller is
-- not an active administrator).
CREATE FUNCTION admin_shared_decide(
  p_admin uuid, p_suggestion uuid, p_approve boolean, p_item uuid, p_name_ru text, p_name_uz text,
  p_search_norm text, p_category text, p_now timestamptz
)
RETURNS TABLE (outcome text, kind text, shared_item_id uuid)
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  s shared_suggestion%ROWTYPE;
  holder uuid;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM admin_account a WHERE a.user_id = p_admin AND a.status = 'active') THEN
    RETURN QUERY SELECT 'missing'::text, NULL::text, NULL::uuid;
    RETURN;
  END IF;
  SELECT * INTO s FROM shared_suggestion g WHERE g.id = p_suggestion FOR UPDATE;
  IF NOT FOUND THEN
    RETURN QUERY SELECT 'missing'::text, NULL::text, NULL::uuid;
    RETURN;
  END IF;
  IF s.status <> 'pending' THEN
    RETURN QUERY SELECT 'decided'::text, s.kind, s.shared_item_id;
    RETURN;
  END IF;
  IF NOT p_approve THEN
    UPDATE shared_suggestion g SET status = 'rejected', decided_by = p_admin, decided_at = p_now
     WHERE g.id = s.id;
    RETURN QUERY SELECT 'rejected'::text, s.kind, s.shared_item_id;
    RETURN;
  END IF;

  IF s.kind = 'item' THEN
    IF p_search_norm IS NULL OR (p_name_ru IS NULL AND p_name_uz IS NULL) THEN
      RETURN QUERY SELECT 'unnamed'::text, s.kind, NULL::uuid;
      RETURN;
    END IF;
    INSERT INTO shared_item (id, name_ru, name_uz, search_norm, category, unit, created_at)
    VALUES (p_item, p_name_ru, p_name_uz, p_search_norm, p_category, s.unit, p_now);
    IF s.barcode IS NOT NULL THEN
      INSERT INTO shared_barcode (code, item_id, created_at) VALUES (s.barcode, p_item, p_now)
      ON CONFLICT (code) DO NOTHING;
    END IF;
    UPDATE catalog_item c SET shared_item_id = p_item
     WHERE c.shop_id = s.shop_id AND c.id = s.item_id AND c.shared_item_id IS NULL;
    UPDATE shared_suggestion g
       SET status = 'approved', decided_by = p_admin, decided_at = p_now, shared_item_id = p_item
     WHERE g.id = s.id;
    RETURN QUERY SELECT 'approved'::text, s.kind, p_item;
    RETURN;
  END IF;

  SELECT b.item_id INTO holder FROM shared_barcode b WHERE b.code = s.barcode;
  IF holder IS NOT NULL AND holder <> s.shared_item_id THEN
    RETURN QUERY SELECT 'barcode_taken'::text, s.kind, holder;
    RETURN;
  END IF;
  INSERT INTO shared_barcode (code, item_id, created_at) VALUES (s.barcode, s.shared_item_id, p_now)
  ON CONFLICT (code) DO NOTHING;
  UPDATE shared_suggestion g SET status = 'approved', decided_by = p_admin, decided_at = p_now
   WHERE g.status = 'pending' AND g.kind = 'barcode' AND g.barcode = s.barcode
     AND g.shared_item_id = s.shared_item_id;
  RETURN QUERY SELECT 'approved'::text, s.kind, s.shared_item_id;
END $$;

-- Each decision is in the administrators' audit, as a decision about a suggestion: the audit, like the
-- queue, does not say which shop it came from.
ALTER TABLE admin_audit DROP CONSTRAINT admin_audit_target_type_check;
ALTER TABLE admin_audit
  ADD CONSTRAINT admin_audit_target_type_check
  CHECK (target_type IN ('admin', 'shop', 'setting', 'receipt', 'suggestion'));

REVOKE ALL ON FUNCTION admin_shared_suggestions(uuid, text, timestamptz, uuid, integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION admin_shared_decide(uuid, uuid, boolean, uuid, text, text, text, text, timestamptz)
  FROM PUBLIC;
GRANT EXECUTE ON FUNCTION admin_shared_suggestions(uuid, text, timestamptz, uuid, integer) TO qd_admin;
GRANT EXECUTE ON FUNCTION admin_shared_decide(uuid, uuid, boolean, uuid, text, text, text, text, timestamptz)
  TO qd_admin;

-- ---------------------------------------------------------------------------
-- Erasing a shop
-- ---------------------------------------------------------------------------

-- A shop's proposals go with the shop; what an administrator already approved stays in the catalogue,
-- and says nothing of where it came from. The function is not written out again (see migration 0048):
-- the line is added to it as it stands, and the migration fails if it could not be.
DO $$
DECLARE
  body text := pg_get_functiondef('erase_shop(uuid)'::regprocedure);
  barcodes constant text := 'DELETE FROM catalog_barcode WHERE shop_id = p_shop_id;';
BEGIN
  IF position(barcodes IN body) = 0 THEN
    RAISE EXCEPTION 'erase_shop is not written as migration 0050 expects';
  END IF;
  body := replace(body, barcodes, 'DELETE FROM shared_suggestion WHERE shop_id = p_shop_id;' || E'\n  ' || barcodes);
  EXECUTE body;
END $$;

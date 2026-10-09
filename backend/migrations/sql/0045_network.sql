-- The network between shops (the founder's decision 7 of 2026-10-09; module J of the expansion).
--
-- Two shops of the platform connect as supplier and buyer: orders, a delivery note, and payments that
-- take effect on the other side only when that side confirms them. All of it is behind the platform
-- switch `network_on` (and needs `stock_on`); while that is off no row is written to any table below and
-- every function below refuses.
--
-- THE TRUST MODEL. This is the first place where what one shop does reaches another shop's rows, and
-- tenant isolation (forced row-level security by `qd.shop_id`) is the product's central guarantee. So:
--
-- 1. No policy is widened. Every table below is an ordinary tenant table: `shop_id`, forced row-level
--    security, the same policy as everywhere. A shop reads its own rows and nobody else's, with no
--    function in between.
-- 2. What two shops share is stored twice: each side holds its own copy, under its own `shop_id`, with
--    the same identifier. A copy holds what that side may know and nothing else: the partner's name and
--    contact phone, the lines of an order, the figures of a note. Never the partner's customers,
--    balances, staff, stock, catalogue or cost. A member of staff of the partner is never named.
-- 3. The application role cannot write the shared tables at all (SELECT only). Every change is made by
--    one of the SECURITY DEFINER functions below, which writes both copies in one statement of the
--    caller's transaction. Each function takes the acting shop and the partner by identifier and
--    verifies, before it changes anything: that the acting shop is the tenant of the transaction
--    (`qd.shop_id`), that the switches are on, that the acting member is an active member of the acting
--    shop, that a link joins exactly these two shops, that the acting side has the role the step
--    belongs to, and that the object is in the state the step starts from. A shop that is no party gets
--    the refusal a missing object gets.
-- 4. Each function locks the two copies of the link first, lower shop identifier first, so every step of
--    one link takes its turn and no two steps can wait for each other in a ring.
-- 5. Books are never written here. A confirmed delivery posts a stock receipt on the buyer's side and a
--    credit sale on the supplier's, through the application's own ledger and stock code, under each
--    shop's own tenant setting. The one function that lets a transaction name a second tenant is
--    `network_enter_peer`, and it does so only for the supplier of a delivery note that this shop, the
--    buyer, is confirming right now. `network_receipt_finish` then refuses to mark the note received
--    unless both postings exist and say what the note says.

-- ---------------------------------------------------------------------------
-- Tables
-- ---------------------------------------------------------------------------

-- An invitation: the code one shop hands to another outside the service. Stored as its SHA-256; single
-- use; short-lived. There is no directory of shops and no search: the code is the only way in.
CREATE TABLE network_invite (
  id          uuid PRIMARY KEY,
  shop_id     uuid NOT NULL REFERENCES shop(id),
  code_hash   bytea NOT NULL UNIQUE CHECK (length(code_hash) = 32),
  as_role     text NOT NULL CHECK (as_role IN ('buyer', 'supplier')),   -- what the inviting shop will be
  created_by  uuid NOT NULL REFERENCES membership(id),
  created_at  timestamptz NOT NULL DEFAULT now(),
  expires_at  timestamptz NOT NULL,
  used_at     timestamptz,
  revoked_at  timestamptz,
  CHECK (expires_at > created_at AND expires_at <= created_at + interval '7 days')
);
CREATE INDEX network_invite_open ON network_invite (shop_id, created_at DESC) WHERE used_at IS NULL AND revoked_at IS NULL;

-- One side of a link. The other side's row has the same `id` under its own shop.
CREATE TABLE network_link (
  shop_id           uuid NOT NULL REFERENCES shop(id),
  id                uuid NOT NULL,
  peer_shop_id      uuid NOT NULL REFERENCES shop(id),
  role              text NOT NULL CHECK (role IN ('buyer', 'supplier')),   -- what this shop is in the link
  state             text NOT NULL CHECK (state IN ('requested', 'active', 'declined', 'ended')),
  invited           boolean NOT NULL,       -- this shop made the invitation, so it answers the request
  peer_name         text CHECK (length(peer_name) BETWEEN 1 AND 80),       -- null once the partner is erased
  peer_phone        text CHECK (peer_phone ~ '^\+[0-9]{8,15}$'),           -- known from the moment the link is active
  peer_removed      boolean NOT NULL DEFAULT false,
  -- The row of this shop's own books the partner is: a supplier for the buyer, a customer for the supplier.
  supplier_id       uuid,
  customer_id       uuid REFERENCES customer(id),
  counterpart_made  boolean NOT NULL DEFAULT false,                        -- the network created that row
  order_seq         integer NOT NULL DEFAULT 0,
  note_seq          integer NOT NULL DEFAULT 0,
  requested_at      timestamptz NOT NULL,
  requested_by      uuid REFERENCES membership(id),                        -- this shop's member, when it asked
  decided_at        timestamptz,
  decided_by        uuid REFERENCES membership(id),
  ended_at          timestamptz,
  ended_by          uuid REFERENCES membership(id),
  ended_by_peer     boolean NOT NULL DEFAULT false,
  PRIMARY KEY (shop_id, id),
  FOREIGN KEY (shop_id, supplier_id) REFERENCES supplier (shop_id, id),
  CHECK (shop_id <> peer_shop_id),
  CHECK (CASE role WHEN 'buyer' THEN customer_id IS NULL ELSE supplier_id IS NULL END)
);
-- Two shops have at most one live link in each direction.
CREATE UNIQUE INDEX network_link_one_live ON network_link (shop_id, peer_shop_id, role)
  WHERE state IN ('requested', 'active');
-- A supplier row or a customer row stands for one partner at a time.
CREATE UNIQUE INDEX network_link_supplier ON network_link (supplier_id) WHERE supplier_id IS NOT NULL AND state = 'active';
CREATE UNIQUE INDEX network_link_customer ON network_link (customer_id) WHERE customer_id IS NOT NULL AND state = 'active';
CREATE INDEX network_link_peer ON network_link (peer_shop_id);
CREATE INDEX network_link_recent ON network_link (shop_id, requested_at DESC, id);

-- An order the buyer is still writing: its own, and nobody else ever sees it.
CREATE TABLE network_order_draft (
  id           uuid PRIMARY KEY,
  shop_id      uuid NOT NULL REFERENCES shop(id),
  link_id      uuid NOT NULL,
  note         text CHECK (length(note) <= 200),
  wanted_date  date,
  lines        jsonb NOT NULL,
  created_by   uuid NOT NULL REFERENCES membership(id),
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (shop_id, link_id) REFERENCES network_link (shop_id, id)
);
CREATE INDEX network_order_draft_recent ON network_order_draft (shop_id, updated_at DESC, id);

CREATE TABLE network_order (
  shop_id        uuid NOT NULL REFERENCES shop(id),
  id             uuid NOT NULL,
  link_id        uuid NOT NULL,
  role           text NOT NULL CHECK (role IN ('buyer', 'supplier')),
  number         integer NOT NULL CHECK (number > 0),                      -- counted per link, the same on both sides
  status         text NOT NULL CHECK (status IN ('sent', 'accepted', 'delivered', 'received', 'declined', 'cancelled')),
  note           text CHECK (length(note) <= 200),
  wanted_date    date,
  currency       text CHECK (currency IN ('UZS', 'USD')),                  -- named by the supplier when it accepts
  total          bigint CHECK (total > 0),
  sent_at        timestamptz NOT NULL,
  sent_by        uuid REFERENCES membership(id),                           -- the buyer's member, on the buyer's copy
  closed_reason  text CHECK (length(closed_reason) BETWEEN 1 AND 300),
  updated_at     timestamptz NOT NULL,
  PRIMARY KEY (shop_id, id),
  FOREIGN KEY (shop_id, link_id) REFERENCES network_link (shop_id, id),
  CHECK ((status = 'sent') = (currency IS NULL) OR status IN ('declined', 'cancelled')),
  CHECK ((currency IS NULL) = (total IS NULL))
);
CREATE INDEX network_order_recent ON network_order (shop_id, role, sent_at DESC, id DESC);
CREATE INDEX network_order_link ON network_order (shop_id, link_id, sent_at DESC, id DESC);

CREATE TABLE network_order_line (
  shop_id       uuid NOT NULL REFERENCES shop(id),
  order_id      uuid NOT NULL,
  line_no       smallint NOT NULL CHECK (line_no > 0),
  name          text NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
  unit          text NOT NULL CHECK (unit IN ('dona', 'kg', 'g', 'l', 'ml', 'm', 'quti', 'paket', 'juft', 'qop', 'blok')),
  qty           numeric(14,3) NOT NULL CHECK (qty > 0),
  -- This side's own catalogue item for the line, if it chose one. Never the partner's.
  item_id       uuid,
  accepted_qty  numeric(14,3) CHECK (accepted_qty >= 0),
  unit_price    bigint CHECK (unit_price >= 0),
  PRIMARY KEY (shop_id, order_id, line_no),
  FOREIGN KEY (shop_id, order_id) REFERENCES network_order (shop_id, id),
  FOREIGN KEY (shop_id, item_id) REFERENCES catalog_item (shop_id, id),
  CHECK ((accepted_qty IS NULL) = (unit_price IS NULL))
);

-- A delivery note. What it says never changes once it is issued; a correction is a new note that
-- supersedes it (`network_note_guard`).
CREATE TABLE network_note (
  shop_id           uuid NOT NULL REFERENCES shop(id),
  id                uuid NOT NULL,
  link_id           uuid NOT NULL,
  order_id          uuid NOT NULL,
  role              text NOT NULL CHECK (role IN ('buyer', 'supplier')),
  number            integer NOT NULL CHECK (number > 0),
  status            text NOT NULL CHECK (status IN ('issued', 'received', 'rejected', 'superseded', 'void')),
  currency          text NOT NULL CHECK (currency IN ('UZS', 'USD')),
  total             bigint NOT NULL CHECK (total > 0),
  paid              bigint NOT NULL CHECK (paid >= 0),                     -- handed over on delivery; the rest is on credit
  supersedes_id     uuid,
  supersede_reason  text CHECK (length(supersede_reason) BETWEEN 1 AND 300),
  issued_at         timestamptz NOT NULL,
  issued_by         uuid REFERENCES membership(id),                        -- the supplier's member, on the supplier's copy
  customer_id       uuid REFERENCES customer(id),                          -- the supplier's copy: whose account it joins
  decided_at        timestamptz,
  decided_by        uuid REFERENCES membership(id),                        -- the buyer's member, on the buyer's copy
  reject_reason     text CHECK (length(reject_reason) BETWEEN 1 AND 300),
  -- What confirming it posted on this side: the buyer's receipt, the supplier's credit sale.
  document_id       uuid,
  ledger_entry_id   uuid REFERENCES ledger_entry(id),
  PRIMARY KEY (shop_id, id),
  FOREIGN KEY (shop_id, link_id) REFERENCES network_link (shop_id, id),
  FOREIGN KEY (shop_id, order_id) REFERENCES network_order (shop_id, id),
  FOREIGN KEY (shop_id, document_id) REFERENCES stock_document (shop_id, id),
  CHECK (paid <= total),
  CHECK ((supersedes_id IS NULL) = (supersede_reason IS NULL)),
  CHECK ((status = 'rejected') = (reject_reason IS NOT NULL) OR status IN ('superseded', 'void')),
  CHECK (status = 'received' OR (document_id IS NULL AND ledger_entry_id IS NULL))
);
-- An order has one note that counts at a time: the others were superseded or voided.
CREATE UNIQUE INDEX network_note_current ON network_note (shop_id, order_id) WHERE status IN ('issued', 'received', 'rejected');
CREATE INDEX network_note_recent ON network_note (shop_id, role, issued_at DESC, id DESC);
CREATE INDEX network_note_link ON network_note (shop_id, link_id, issued_at DESC, id DESC);
CREATE UNIQUE INDEX network_note_document ON network_note (document_id) WHERE document_id IS NOT NULL;
CREATE UNIQUE INDEX network_note_entry ON network_note (ledger_entry_id) WHERE ledger_entry_id IS NOT NULL;

CREATE TABLE network_note_line (
  shop_id       uuid NOT NULL REFERENCES shop(id),
  note_id       uuid NOT NULL,
  line_no       smallint NOT NULL CHECK (line_no > 0),
  name          text NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
  unit          text NOT NULL CHECK (unit IN ('dona', 'kg', 'g', 'l', 'ml', 'm', 'quti', 'paket', 'juft', 'qop', 'blok')),
  qty           numeric(14,3) NOT NULL CHECK (qty > 0),
  unit_price    bigint NOT NULL CHECK (unit_price >= 0),
  line_total    bigint NOT NULL CHECK (line_total >= 0),
  item_id       uuid,                                                      -- this side's own item, as on the order
  received_qty  numeric(14,3) CHECK (received_qty >= 0),                   -- what the buyer says arrived, when it rejects
  PRIMARY KEY (shop_id, note_id, line_no),
  FOREIGN KEY (shop_id, note_id) REFERENCES network_note (shop_id, id),
  FOREIGN KEY (shop_id, item_id) REFERENCES catalog_item (shop_id, id)
);

CREATE FUNCTION network_note_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_temp
AS $$
BEGIN
  IF (NEW.shop_id, NEW.id, NEW.link_id, NEW.order_id, NEW.role, NEW.number, NEW.currency, NEW.total, NEW.paid,
      NEW.supersedes_id, NEW.supersede_reason, NEW.issued_at, NEW.issued_by, NEW.customer_id)
     IS DISTINCT FROM
     (OLD.shop_id, OLD.id, OLD.link_id, OLD.order_id, OLD.role, OLD.number, OLD.currency, OLD.total, OLD.paid,
      OLD.supersedes_id, OLD.supersede_reason, OLD.issued_at, OLD.issued_by, OLD.customer_id) THEN
    RAISE EXCEPTION 'what a delivery note says does not change once it is issued'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'network_note_guard';
  END IF;
  IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
       (OLD.status = 'issued' AND NEW.status IN ('received', 'rejected', 'superseded', 'void'))
       OR (OLD.status = 'rejected' AND NEW.status IN ('superseded', 'void'))) THEN
    RAISE EXCEPTION 'a delivery note does not go from % to %', OLD.status, NEW.status
      USING ERRCODE = 'check_violation', CONSTRAINT = 'network_note_guard';
  END IF;
  IF OLD.status <> 'issued' AND (NEW.decided_at, NEW.decided_by, NEW.reject_reason, NEW.document_id, NEW.ledger_entry_id)
     IS DISTINCT FROM (OLD.decided_at, OLD.decided_by, OLD.reject_reason, OLD.document_id, OLD.ledger_entry_id) THEN
    RAISE EXCEPTION 'the answer to a delivery note is given once'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'network_note_guard';
  END IF;
  RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION network_note_guard() FROM PUBLIC;
CREATE TRIGGER network_note_guard BEFORE UPDATE ON network_note
  FOR EACH ROW EXECUTE FUNCTION network_note_guard();

-- A line of a note gains only what the buyer counted when it rejected the note, once.
CREATE FUNCTION network_note_line_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_temp
AS $$
BEGIN
  IF (NEW.shop_id, NEW.note_id, NEW.line_no, NEW.name, NEW.unit, NEW.qty, NEW.unit_price, NEW.line_total, NEW.item_id)
     IS DISTINCT FROM
     (OLD.shop_id, OLD.note_id, OLD.line_no, OLD.name, OLD.unit, OLD.qty, OLD.unit_price, OLD.line_total, OLD.item_id)
     OR OLD.received_qty IS NOT NULL THEN
    RAISE EXCEPTION 'a line of a delivery note does not change'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'network_note_line_guard';
  END IF;
  RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION network_note_line_guard() FROM PUBLIC;
CREATE TRIGGER network_note_line_guard BEFORE UPDATE ON network_note_line
  FOR EACH ROW EXECUTE FUNCTION network_note_line_guard();

-- A payment between the two shops. The side that records it has already written it into its own books;
-- the other side's books take it only when that side confirms.
CREATE TABLE network_payment (
  shop_id            uuid NOT NULL REFERENCES shop(id),
  id                 uuid NOT NULL,
  link_id            uuid NOT NULL,
  role               text NOT NULL CHECK (role IN ('buyer', 'supplier')),
  recorded_by_own    boolean NOT NULL,                                     -- this side recorded it; the other answers
  status             text NOT NULL CHECK (status IN ('awaiting', 'confirmed', 'declined', 'withdrawn', 'lapsed')),
  amount             bigint NOT NULL CHECK (amount > 0),
  currency           text NOT NULL CHECK (currency IN ('UZS', 'USD')),
  note               text CHECK (length(note) <= 200),
  recorded_at        timestamptz NOT NULL,
  recorded_by        uuid REFERENCES membership(id),
  decided_at         timestamptz,
  decided_by         uuid REFERENCES membership(id),
  decline_reason     text CHECK (length(decline_reason) BETWEEN 1 AND 300),
  -- This side's own entry: the buyer's payment on the supplier's account, the supplier's customer payment.
  supplier_entry_id  uuid REFERENCES supplier_entry(id),
  ledger_entry_id    uuid REFERENCES ledger_entry(id),
  PRIMARY KEY (shop_id, id),
  FOREIGN KEY (shop_id, link_id) REFERENCES network_link (shop_id, id),
  CHECK (CASE role WHEN 'buyer' THEN ledger_entry_id IS NULL ELSE supplier_entry_id IS NULL END),
  CHECK ((status = 'declined') = (decline_reason IS NOT NULL))
);
CREATE INDEX network_payment_recent ON network_payment (shop_id, recorded_at DESC, id DESC);
CREATE INDEX network_payment_link ON network_payment (shop_id, link_id, recorded_at DESC, id DESC);
CREATE UNIQUE INDEX network_payment_supplier_entry ON network_payment (supplier_entry_id) WHERE supplier_entry_id IS NOT NULL;
CREATE UNIQUE INDEX network_payment_ledger_entry ON network_payment (ledger_entry_id) WHERE ledger_entry_id IS NOT NULL;

-- What happened to a link, an order, a note or a payment, as this side may know it: insert-only. A step
-- the partner took says so (`by_peer`) and never names the partner's member.
CREATE TABLE network_event (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  shop_id     uuid NOT NULL REFERENCES shop(id),
  link_id     uuid NOT NULL,
  subject     text NOT NULL CHECK (subject IN ('link', 'order', 'note', 'payment')),
  subject_id  uuid NOT NULL,
  kind        text NOT NULL,
  by_peer     boolean NOT NULL,
  member_id   uuid REFERENCES membership(id),
  at          timestamptz NOT NULL,
  detail      jsonb,
  FOREIGN KEY (shop_id, link_id) REFERENCES network_link (shop_id, id),
  CHECK (NOT by_peer OR member_id IS NULL)
);
CREATE INDEX network_event_subject ON network_event (shop_id, subject_id, at, id);

-- A stock receipt answers one delivery note at most.
CREATE UNIQUE INDEX stock_document_origin ON stock_document (origin_ref) WHERE origin_ref IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Row-level security and rights
-- ---------------------------------------------------------------------------

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'network_invite', 'network_link', 'network_order_draft', 'network_order', 'network_order_line', 'network_note',
    'network_note_line', 'network_payment', 'network_event']
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format(
      'CREATE POLICY tenant ON %I USING (shop_id = nullif(current_setting(''qd.shop_id'', true), '''')::uuid) '
      'WITH CHECK (shop_id = nullif(current_setting(''qd.shop_id'', true), '''')::uuid)', t);
  END LOOP;
END $$;

-- The ordinary application reads its own side and writes only what never crosses: an invitation it made
-- (and its withdrawal) and an order it has not sent. Everything two shops share is written by the
-- functions below and by nothing else.
GRANT SELECT, INSERT ON network_invite TO qd_app;
GRANT UPDATE (revoked_at) ON network_invite TO qd_app;
GRANT SELECT, INSERT, DELETE ON network_order_draft TO qd_app;
GRANT UPDATE (note, wanted_date, lines, updated_at) ON network_order_draft TO qd_app;
GRANT SELECT ON network_link, network_order, network_order_line, network_note, network_note_line, network_payment,
  network_event TO qd_app;

-- The worker writes the owner's export of the shop's own side. It reads and nothing else.
GRANT SELECT ON network_link, network_order, network_order_line, network_note, network_note_line, network_payment
  TO qd_worker;

-- ---------------------------------------------------------------------------
-- Helpers of the functions below. Not SECURITY DEFINER themselves and granted to nobody: only a
-- function that already runs with its owner's rights can call them.
-- ---------------------------------------------------------------------------

-- A refusal the application turns into its own error. The message is a code and says nothing else.
CREATE FUNCTION network_refuse(p_code text) RETURNS void
LANGUAGE plpgsql SET search_path = public, pg_temp
AS $$
BEGIN
  RAISE EXCEPTION '%', p_code USING ERRCODE = 'QN000';
END $$;
REVOKE ALL ON FUNCTION network_refuse(text) FROM PUBLIC;

-- The acting shop is the tenant of this transaction, and the network is switched on.
CREATE FUNCTION network_guard(p_shop uuid) RETURNS void
LANGUAGE plpgsql STABLE SET search_path = public, pg_temp
AS $$
BEGIN
  IF p_shop IS NULL OR p_shop IS DISTINCT FROM nullif(current_setting('qd.shop_id', true), '')::uuid THEN
    PERFORM network_refuse('NETWORK_NOT_FOUND');
  END IF;
  IF NOT coalesce((SELECT s.value = 'true'::jsonb FROM platform_setting s WHERE s.key = 'network_on'), false)
     OR NOT coalesce((SELECT s.value = 'true'::jsonb FROM platform_setting s WHERE s.key = 'stock_on'), false) THEN
    PERFORM network_refuse('NETWORK_NOT_FOUND');
  END IF;
END $$;
REVOKE ALL ON FUNCTION network_guard(uuid) FROM PUBLIC;

-- The acting member is an active member of the acting shop.
CREATE FUNCTION network_member(p_shop uuid, p_member uuid) RETURNS void
LANGUAGE plpgsql STABLE SET search_path = public, pg_temp
AS $$
BEGIN
  PERFORM 1 FROM membership m WHERE m.id = p_member AND m.shop_id = p_shop AND m.status = 'active';
  IF NOT FOUND THEN
    PERFORM network_refuse('NETWORK_NOT_FOUND');
  END IF;
END $$;
REVOKE ALL ON FUNCTION network_member(uuid, uuid) FROM PUBLIC;

-- Both copies of a link under their locks, lower shop identifier first; returns the acting shop's copy.
-- Refuses unless the link joins exactly these two shops.
CREATE FUNCTION network_pair(p_shop uuid, p_peer uuid, p_link uuid) RETURNS network_link
LANGUAGE plpgsql SET search_path = public, pg_temp
AS $$
DECLARE
  r network_link%ROWTYPE;
  own network_link%ROWTYPE;
  other network_link%ROWTYPE;
BEGIN
  FOR r IN
    SELECT * FROM network_link l WHERE l.id = p_link AND l.shop_id IN (p_shop, p_peer) ORDER BY l.shop_id FOR UPDATE
  LOOP
    IF r.shop_id = p_shop THEN own := r; ELSE other := r; END IF;
  END LOOP;
  IF own.id IS NULL OR other.id IS NULL OR p_shop = p_peer
     OR own.peer_shop_id <> p_peer OR other.peer_shop_id <> p_shop OR own.role = other.role THEN
    PERFORM network_refuse('NETWORK_NOT_FOUND');
  END IF;
  RETURN own;
END $$;
REVOKE ALL ON FUNCTION network_pair(uuid, uuid, uuid) FROM PUBLIC;

-- One step, written into the history of both sides and into both shops' activity logs. The acting side
-- keeps who did it; the other side is told only that its partner did.
CREATE FUNCTION network_log(
  p_shop uuid, p_peer uuid, p_link uuid, p_subject text, p_subject_id uuid, p_kind text, p_member uuid,
  p_now timestamptz, p_detail jsonb
) RETURNS void
LANGUAGE plpgsql SET search_path = public, pg_temp
AS $$
BEGIN
  INSERT INTO network_event (shop_id, link_id, subject, subject_id, kind, by_peer, member_id, at, detail)
  VALUES (p_shop, p_link, p_subject, p_subject_id, p_kind, false, p_member, p_now, p_detail),
         (p_peer, p_link, p_subject, p_subject_id, p_kind, true, NULL, p_now, p_detail);
  INSERT INTO activity (id, shop_id, at, actor_kind, actor_id, action, subject_type, subject_id, detail)
  VALUES (gen_random_uuid(), p_shop, p_now, 'staff', p_member, 'network.' || p_kind, 'network_' || p_subject,
          p_subject_id, p_detail),
         (gen_random_uuid(), p_peer, p_now, 'system', NULL, 'network.' || p_kind, 'network_' || p_subject,
          p_subject_id, coalesce(p_detail, '{}'::jsonb) || '{"by": "partner"}'::jsonb);
END $$;
REVOKE ALL ON FUNCTION network_log(uuid, uuid, uuid, text, uuid, text, uuid, timestamptz, jsonb) FROM PUBLIC;

-- Whether both shops work in the currency: so'm always; dollars while the platform and both shops do.
CREATE FUNCTION network_currency_ok(p_shop uuid, p_peer uuid, p_currency text) RETURNS boolean
LANGUAGE sql STABLE SET search_path = public, pg_temp
AS $$
  SELECT p_currency = 'UZS' OR (
    p_currency = 'USD'
    AND coalesce((SELECT s.value = 'true'::jsonb FROM platform_setting s WHERE s.key = 'usd_on'), false)
    AND (SELECT count(*) FROM shop s WHERE s.id IN (p_shop, p_peer) AND s.usd_on AND s.status = 'active') = 2);
$$;
REVOKE ALL ON FUNCTION network_currency_ok(uuid, uuid, text) FROM PUBLIC;

-- Everything of a link that still waits is closed when the link ends (or a partner is erased): nothing
-- can be answered across a link that is no more.
CREATE FUNCTION network_close_open(p_shop uuid, p_peer uuid, p_link uuid, p_now timestamptz, p_reason text)
RETURNS void
LANGUAGE plpgsql SET search_path = public, pg_temp
AS $$
BEGIN
  UPDATE network_note SET status = 'void'
   WHERE shop_id IN (p_shop, p_peer) AND link_id = p_link AND status IN ('issued', 'rejected');
  UPDATE network_order SET status = 'cancelled', closed_reason = p_reason, updated_at = p_now
   WHERE shop_id IN (p_shop, p_peer) AND link_id = p_link AND status IN ('sent', 'accepted', 'delivered');
  UPDATE network_payment SET status = 'lapsed', decided_at = p_now
   WHERE shop_id IN (p_shop, p_peer) AND link_id = p_link AND status = 'awaiting';
END $$;
REVOKE ALL ON FUNCTION network_close_open(uuid, uuid, uuid, timestamptz, text) FROM PUBLIC;

-- ---------------------------------------------------------------------------
-- Links
-- ---------------------------------------------------------------------------

-- Take every lock of a link before the caller touches its own books, and say what state it is in.
CREATE FUNCTION network_lock(p_shop uuid, p_peer uuid, p_link uuid) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp
AS $$
BEGIN
  PERFORM network_guard(p_shop);
  RETURN (network_pair(p_shop, p_peer, p_link)).state;
END $$;
REVOKE ALL ON FUNCTION network_lock(uuid, uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION network_lock(uuid, uuid, uuid) TO qd_app;

-- A shop presents a code it was handed. `p_role` is what the presenting shop wants to be; the invitation
-- must be for the opposite. Any reason the code does not work is the same refusal.
CREATE FUNCTION network_invite_redeem(p_shop uuid, p_code_hash bytea, p_role text, p_member uuid, p_now timestamptz)
RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp
AS $$
DECLARE
  invite network_invite%ROWTYPE;
  own_name text;
  their_name text;
  link uuid := gen_random_uuid();
BEGIN
  PERFORM network_guard(p_shop);
  PERFORM network_member(p_shop, p_member);
  IF p_role NOT IN ('buyer', 'supplier') THEN
    PERFORM network_refuse('NETWORK_INVITE_INVALID');
  END IF;
  SELECT * INTO invite FROM network_invite i WHERE i.code_hash = p_code_hash FOR UPDATE;
  IF NOT FOUND OR invite.used_at IS NOT NULL OR invite.revoked_at IS NOT NULL OR invite.expires_at <= p_now
     OR invite.shop_id = p_shop OR invite.as_role = p_role THEN
    PERFORM network_refuse('NETWORK_INVITE_INVALID');
  END IF;
  SELECT s.name INTO their_name FROM shop s WHERE s.id = invite.shop_id AND s.status = 'active';
  SELECT s.name INTO own_name FROM shop s WHERE s.id = p_shop AND s.status = 'active';
  IF their_name IS NULL OR own_name IS NULL THEN
    PERFORM network_refuse('NETWORK_INVITE_INVALID');
  END IF;
  PERFORM 1 FROM network_link l
    WHERE l.shop_id = p_shop AND l.peer_shop_id = invite.shop_id AND l.role = p_role AND l.state IN ('requested', 'active');
  IF FOUND THEN
    PERFORM network_refuse('NETWORK_LINK_EXISTS');
  END IF;
  UPDATE network_invite SET used_at = p_now WHERE id = invite.id;
  INSERT INTO network_link (shop_id, id, peer_shop_id, role, state, invited, peer_name, requested_at, requested_by)
  VALUES (p_shop, link, invite.shop_id, p_role, 'requested', false, their_name, p_now, p_member),
         (invite.shop_id, link, p_shop, invite.as_role, 'requested', true, own_name, p_now, NULL);
  PERFORM network_log(p_shop, invite.shop_id, link, 'link', link, 'link_requested', p_member, p_now, NULL);
  RETURN link;
END $$;
REVOKE ALL ON FUNCTION network_invite_redeem(uuid, bytea, text, uuid, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION network_invite_redeem(uuid, bytea, text, uuid, timestamptz) TO qd_app;

-- The shop that made the invitation accepts or declines the request. Accepting is the moment each side
-- learns the other's contact phone, and never before.
CREATE FUNCTION network_link_decide(
  p_shop uuid, p_peer uuid, p_link uuid, p_accept boolean, p_member uuid, p_now timestamptz
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp
AS $$
DECLARE
  own network_link%ROWTYPE;
BEGIN
  PERFORM network_guard(p_shop);
  PERFORM network_member(p_shop, p_member);
  own := network_pair(p_shop, p_peer, p_link);
  IF NOT own.invited OR own.state <> 'requested' THEN
    PERFORM network_refuse('NETWORK_STATE');
  END IF;
  IF p_accept THEN
    IF (SELECT count(*) FROM shop s WHERE s.id IN (p_shop, p_peer) AND s.status = 'active') <> 2 THEN
      PERFORM network_refuse('NETWORK_PARTNER_UNAVAILABLE');
    END IF;
    UPDATE network_link l
       SET state = 'active', decided_at = p_now,
           decided_by = CASE WHEN l.shop_id = p_shop THEN p_member END,
           peer_phone = (SELECT s.share_phone FROM shop s WHERE s.id = l.peer_shop_id)
     WHERE l.id = p_link AND l.shop_id IN (p_shop, p_peer);
    PERFORM network_log(p_shop, p_peer, p_link, 'link', p_link, 'link_accepted', p_member, p_now, NULL);
  ELSE
    UPDATE network_link l
       SET state = 'declined', decided_at = p_now, decided_by = CASE WHEN l.shop_id = p_shop THEN p_member END
     WHERE l.id = p_link AND l.shop_id IN (p_shop, p_peer);
    PERFORM network_log(p_shop, p_peer, p_link, 'link', p_link, 'link_declined', p_member, p_now, NULL);
  END IF;
END $$;
REVOKE ALL ON FUNCTION network_link_decide(uuid, uuid, uuid, boolean, uuid, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION network_link_decide(uuid, uuid, uuid, boolean, uuid, timestamptz) TO qd_app;

-- Either side ends a link (or takes back a request). Whatever still waited is closed; what was posted
-- and the history stay with each side.
CREATE FUNCTION network_link_end(p_shop uuid, p_peer uuid, p_link uuid, p_member uuid, p_now timestamptz)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp
AS $$
DECLARE
  own network_link%ROWTYPE;
BEGIN
  PERFORM network_guard(p_shop);
  PERFORM network_member(p_shop, p_member);
  own := network_pair(p_shop, p_peer, p_link);
  IF own.state NOT IN ('requested', 'active') THEN
    PERFORM network_refuse('NETWORK_STATE');
  END IF;
  PERFORM network_close_open(p_shop, p_peer, p_link, p_now, 'link ended');
  -- The buyer's supplier row is a platform shop no longer.
  UPDATE supplier s SET linked_shop_id = NULL
    FROM network_link l
   WHERE l.id = p_link AND l.shop_id IN (p_shop, p_peer) AND l.supplier_id = s.id AND s.shop_id = l.shop_id;
  UPDATE network_link l
     SET state = 'ended', ended_at = p_now,
         ended_by = CASE WHEN l.shop_id = p_shop THEN p_member END,
         ended_by_peer = (l.shop_id <> p_shop)
   WHERE l.id = p_link AND l.shop_id IN (p_shop, p_peer);
  PERFORM network_log(p_shop, p_peer, p_link, 'link', p_link, 'link_ended', p_member, p_now, NULL);
END $$;
REVOKE ALL ON FUNCTION network_link_end(uuid, uuid, uuid, uuid, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION network_link_end(uuid, uuid, uuid, uuid, timestamptz) TO qd_app;

-- A shop says which row of its own books the partner is: one of its suppliers (the buyer) or one of
-- its customers (the supplier). Only its own rows are read or written; it is a function because the
-- application may not write `network_link` or `supplier.linked_shop_id` itself.
CREATE FUNCTION network_link_attach(p_shop uuid, p_link uuid, p_counterpart uuid, p_made boolean, p_member uuid)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp
AS $$
DECLARE
  own network_link%ROWTYPE;
BEGIN
  PERFORM network_guard(p_shop);
  PERFORM network_member(p_shop, p_member);
  SELECT * INTO own FROM network_link l WHERE l.shop_id = p_shop AND l.id = p_link FOR UPDATE;
  IF NOT FOUND THEN
    PERFORM network_refuse('NETWORK_NOT_FOUND');
  END IF;
  IF own.state <> 'active' OR own.supplier_id IS NOT NULL OR own.customer_id IS NOT NULL THEN
    PERFORM network_refuse('NETWORK_STATE');
  END IF;
  IF own.role = 'buyer' THEN
    UPDATE supplier s SET linked_shop_id = own.peer_shop_id
     WHERE s.id = p_counterpart AND s.shop_id = p_shop AND s.status = 'active' AND s.linked_shop_id IS NULL;
    IF NOT FOUND THEN
      PERFORM network_refuse('NETWORK_COUNTERPART_INVALID');
    END IF;
    UPDATE network_link l SET supplier_id = p_counterpart, counterpart_made = p_made
     WHERE l.shop_id = p_shop AND l.id = p_link;
  ELSE
    PERFORM 1 FROM customer c WHERE c.id = p_counterpart AND c.shop_id = p_shop AND c.status = 'active';
    IF NOT FOUND THEN
      PERFORM network_refuse('NETWORK_COUNTERPART_INVALID');
    END IF;
    UPDATE network_link l SET customer_id = p_counterpart, counterpart_made = p_made
     WHERE l.shop_id = p_shop AND l.id = p_link;
  END IF;
  INSERT INTO network_event (shop_id, link_id, subject, subject_id, kind, by_peer, member_id, at, detail)
  VALUES (p_shop, p_link, 'link', p_link, 'link_attached', false, p_member, now(), NULL);
END $$;
REVOKE ALL ON FUNCTION network_link_attach(uuid, uuid, uuid, boolean, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION network_link_attach(uuid, uuid, uuid, boolean, uuid) TO qd_app;

-- Whom to tell on the other side: the Telegram chat and language of the partner's active members, with
-- what decides whether each holds the permission the news is for. Nothing else of them is returned, and
-- only to a shop a link joins to that partner.
CREATE FUNCTION network_notice_recipients(p_shop uuid, p_peer uuid, p_link uuid)
RETURNS TABLE (tg_id bigint, lang text, role text, granted text[], denied text[])
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public, pg_temp
AS $$
BEGIN
  PERFORM network_guard(p_shop);
  PERFORM 1 FROM network_link a JOIN network_link b ON b.id = a.id AND b.shop_id = a.peer_shop_id
    WHERE a.id = p_link AND a.shop_id = p_shop AND a.peer_shop_id = p_peer AND b.peer_shop_id = p_shop;
  IF NOT FOUND THEN
    PERFORM network_refuse('NETWORK_NOT_FOUND');
  END IF;
  RETURN QUERY
    SELECT u.tg_id, u.lang, m.role, m.permissions_granted, m.permissions_denied
      FROM membership m JOIN app_user u ON u.id = m.user_id
     WHERE m.shop_id = p_peer AND m.status = 'active' AND u.tg_id IS NOT NULL
     ORDER BY m.created_at, m.id;
END $$;
REVOKE ALL ON FUNCTION network_notice_recipients(uuid, uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION network_notice_recipients(uuid, uuid, uuid) TO qd_app;

-- ---------------------------------------------------------------------------
-- Orders
-- ---------------------------------------------------------------------------

-- The buyer sends an order: both copies are written, with what the buyer wrote and nothing of its
-- catalogue (the buyer's own item of a line stays on the buyer's copy).
CREATE FUNCTION network_order_send(
  p_shop uuid, p_peer uuid, p_link uuid, p_order uuid, p_member uuid, p_note text, p_wanted date, p_lines jsonb,
  p_now timestamptz
) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp
AS $$
DECLARE
  own network_link%ROWTYPE;
  v_number integer;
  v_lines integer;
BEGIN
  PERFORM network_guard(p_shop);
  PERFORM network_member(p_shop, p_member);
  own := network_pair(p_shop, p_peer, p_link);
  IF own.role <> 'buyer' OR own.state <> 'active' THEN
    PERFORM network_refuse('NETWORK_STATE');
  END IF;
  v_lines := CASE WHEN jsonb_typeof(p_lines) = 'array' THEN jsonb_array_length(p_lines) ELSE 0 END;
  IF v_lines NOT BETWEEN 1 AND 100 THEN
    PERFORM network_refuse('NETWORK_INVALID');
  END IF;
  v_number := own.order_seq + 1;
  UPDATE network_link l SET order_seq = v_number WHERE l.id = p_link AND l.shop_id IN (p_shop, p_peer);
  INSERT INTO network_order (shop_id, id, link_id, role, number, status, note, wanted_date, sent_at, sent_by, updated_at)
  VALUES (p_shop, p_order, p_link, 'buyer', v_number, 'sent', p_note, p_wanted, p_now, p_member, p_now),
         (p_peer, p_order, p_link, 'supplier', v_number, 'sent', p_note, p_wanted, p_now, NULL, p_now);
  INSERT INTO network_order_line (shop_id, order_id, line_no, name, unit, qty, item_id)
  SELECT side.shop_id, p_order, x.ord::smallint, x.line ->> 'name', x.line ->> 'unit', (x.line ->> 'qty')::numeric,
         CASE WHEN side.shop_id = p_shop THEN (x.line ->> 'item_id')::uuid END
    FROM jsonb_array_elements(p_lines) WITH ORDINALITY AS x(line, ord)
   CROSS JOIN (VALUES (p_shop), (p_peer)) AS side(shop_id);
  DELETE FROM network_order_draft d WHERE d.id = p_order AND d.shop_id = p_shop;
  PERFORM network_log(p_shop, p_peer, p_link, 'order', p_order, 'order_sent', p_member, p_now,
                      jsonb_build_object('number', v_number));
  RETURN v_number;
END $$;
REVOKE ALL ON FUNCTION network_order_send(uuid, uuid, uuid, uuid, uuid, text, date, jsonb, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION network_order_send(uuid, uuid, uuid, uuid, uuid, text, date, jsonb, timestamptz) TO qd_app;

-- Both copies of an order under their locks (after the link's); returns the acting shop's copy.
CREATE FUNCTION network_order_pair(p_shop uuid, p_peer uuid, p_order uuid) RETURNS network_order
LANGUAGE plpgsql SET search_path = public, pg_temp
AS $$
DECLARE
  seen network_order%ROWTYPE;
  own network_order%ROWTYPE;
  copies integer := 0;
  r network_order%ROWTYPE;
BEGIN
  SELECT * INTO seen FROM network_order o WHERE o.shop_id = p_shop AND o.id = p_order;
  IF NOT FOUND THEN
    PERFORM network_refuse('NETWORK_NOT_FOUND');
  END IF;
  PERFORM network_pair(p_shop, p_peer, seen.link_id);
  FOR r IN
    SELECT * FROM network_order o WHERE o.id = p_order AND o.shop_id IN (p_shop, p_peer) ORDER BY o.shop_id FOR UPDATE
  LOOP
    copies := copies + 1;
    IF r.shop_id = p_shop THEN own := r; END IF;
  END LOOP;
  IF copies <> 2 THEN
    PERFORM network_refuse('NETWORK_NOT_FOUND');
  END IF;
  RETURN own;
END $$;
REVOKE ALL ON FUNCTION network_order_pair(uuid, uuid, uuid) FROM PUBLIC;

-- The supplier accepts an order, naming the currency and, line by line, the quantity it will deliver
-- and its price. The buyer sees the changes beside what it asked for. The supplier's own item of a line
-- stays on the supplier's copy.
CREATE FUNCTION network_order_accept(
  p_shop uuid, p_peer uuid, p_order uuid, p_member uuid, p_currency text, p_lines jsonb, p_now timestamptz
) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp
AS $$
DECLARE
  own network_order%ROWTYPE;
  link network_link%ROWTYPE;
  v_total bigint;
  v_covered integer;
  v_wanted integer;
BEGIN
  PERFORM network_guard(p_shop);
  PERFORM network_member(p_shop, p_member);
  own := network_order_pair(p_shop, p_peer, p_order);
  SELECT * INTO link FROM network_link l WHERE l.shop_id = p_shop AND l.id = own.link_id;
  IF own.role <> 'supplier' OR own.status <> 'sent' OR link.state <> 'active' THEN
    PERFORM network_refuse('NETWORK_STATE');
  END IF;
  IF NOT coalesce(network_currency_ok(p_shop, p_peer, p_currency), false) THEN
    PERFORM network_refuse('NETWORK_CURRENCY');
  END IF;
  IF jsonb_typeof(p_lines) IS DISTINCT FROM 'array' THEN
    PERFORM network_refuse('NETWORK_INVALID');
  END IF;
  SELECT count(*) INTO v_wanted FROM network_order_line n WHERE n.shop_id = p_shop AND n.order_id = p_order;
  SELECT count(DISTINCT x.line_no), sum(round(x.qty * x.unit_price))::bigint INTO v_covered, v_total
    FROM jsonb_to_recordset(p_lines) AS x(line_no smallint, qty numeric, unit_price bigint)
    JOIN network_order_line n ON n.shop_id = p_shop AND n.order_id = p_order AND n.line_no = x.line_no
   WHERE x.qty >= 0 AND x.unit_price >= 0;
  IF v_covered IS DISTINCT FROM v_wanted OR jsonb_array_length(p_lines) <> v_wanted OR coalesce(v_total, 0) <= 0 THEN
    PERFORM network_refuse('NETWORK_INVALID');
  END IF;
  UPDATE network_order_line n
     SET accepted_qty = x.qty, unit_price = x.unit_price,
         item_id = CASE WHEN n.shop_id = p_shop THEN x.item_id ELSE n.item_id END
    FROM jsonb_to_recordset(p_lines) AS x(line_no smallint, qty numeric, unit_price bigint, item_id uuid)
   WHERE n.order_id = p_order AND n.shop_id IN (p_shop, p_peer) AND n.line_no = x.line_no;
  UPDATE network_order o SET status = 'accepted', currency = p_currency, total = v_total, updated_at = p_now
   WHERE o.id = p_order AND o.shop_id IN (p_shop, p_peer);
  PERFORM network_log(p_shop, p_peer, own.link_id, 'order', p_order, 'order_accepted', p_member, p_now,
                      jsonb_build_object('number', own.number, 'currency', p_currency, 'total', v_total));
  RETURN v_total;
END $$;
REVOKE ALL ON FUNCTION network_order_accept(uuid, uuid, uuid, uuid, text, jsonb, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION network_order_accept(uuid, uuid, uuid, uuid, text, jsonb, timestamptz) TO qd_app;

-- The buyer cancels an order, or the supplier declines it, with a reason: while nothing of it has been
-- received. After a delivery that is possible only when the buyer rejected the note, so nothing is posted.
CREATE FUNCTION network_order_close(
  p_shop uuid, p_peer uuid, p_order uuid, p_member uuid, p_reason text, p_now timestamptz
) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp
AS $$
DECLARE
  own network_order%ROWTYPE;
  outcome text;
BEGIN
  PERFORM network_guard(p_shop);
  PERFORM network_member(p_shop, p_member);
  own := network_order_pair(p_shop, p_peer, p_order);
  IF own.status NOT IN ('sent', 'accepted', 'delivered') THEN
    PERFORM network_refuse('NETWORK_STATE');
  END IF;
  IF own.status = 'delivered' THEN
    PERFORM 1 FROM network_note n WHERE n.shop_id = p_shop AND n.order_id = p_order AND n.status = 'issued';
    IF FOUND THEN
      PERFORM network_refuse('NETWORK_STATE');
    END IF;
  END IF;
  outcome := CASE own.role WHEN 'buyer' THEN 'cancelled' ELSE 'declined' END;
  UPDATE network_note n SET status = 'void'
   WHERE n.order_id = p_order AND n.shop_id IN (p_shop, p_peer) AND n.status = 'rejected';
  UPDATE network_order o SET status = outcome, closed_reason = p_reason, updated_at = p_now
   WHERE o.id = p_order AND o.shop_id IN (p_shop, p_peer);
  PERFORM network_log(p_shop, p_peer, own.link_id, 'order', p_order, 'order_' || outcome, p_member, p_now,
                      jsonb_build_object('number', own.number, 'reason', p_reason));
  RETURN outcome;
END $$;
REVOKE ALL ON FUNCTION network_order_close(uuid, uuid, uuid, uuid, text, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION network_order_close(uuid, uuid, uuid, uuid, text, timestamptz) TO qd_app;

-- ---------------------------------------------------------------------------
-- Delivery notes
-- ---------------------------------------------------------------------------

-- The supplier issues a delivery note for an accepted order: the accepted lines as they stand
-- (`p_lines` null), or, as a correction of the note before it (`p_reason` given), the lines it names.
-- The note before it is superseded. Nothing is posted to anybody's books here.
CREATE FUNCTION network_note_issue(
  p_shop uuid, p_peer uuid, p_order uuid, p_note uuid, p_member uuid, p_customer uuid, p_paid bigint, p_reason text,
  p_lines jsonb, p_now timestamptz
) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp
AS $$
DECLARE
  own network_order%ROWTYPE;
  link network_link%ROWTYPE;
  before network_note%ROWTYPE;
  v_total bigint;
  v_number integer;
  v_lines jsonb;
  v_rows integer;
  v_distinct integer;
BEGIN
  PERFORM network_guard(p_shop);
  PERFORM network_member(p_shop, p_member);
  own := network_order_pair(p_shop, p_peer, p_order);
  SELECT * INTO link FROM network_link l WHERE l.shop_id = p_shop AND l.id = own.link_id;
  IF own.role <> 'supplier' OR link.state <> 'active' OR own.status NOT IN ('accepted', 'delivered') THEN
    PERFORM network_refuse('NETWORK_STATE');
  END IF;
  IF link.customer_id IS NULL OR link.customer_id IS DISTINCT FROM p_customer THEN
    PERFORM network_refuse('NETWORK_COUNTERPART_INVALID');
  END IF;
  IF NOT coalesce(network_currency_ok(p_shop, p_peer, own.currency), false) THEN
    PERFORM network_refuse('NETWORK_CURRENCY');
  END IF;
  SELECT * INTO before FROM network_note n
    WHERE n.shop_id = p_shop AND n.order_id = p_order AND n.status IN ('issued', 'received', 'rejected');
  IF (own.status = 'accepted') <> (before.id IS NULL)
     OR before.status = 'received'
     OR (before.id IS NULL) <> (p_reason IS NULL)
     OR (before.id IS NULL AND p_lines IS NOT NULL) THEN
    PERFORM network_refuse('NETWORK_STATE');
  END IF;
  IF before.id IS NOT NULL THEN
    UPDATE network_note n SET status = 'superseded' WHERE n.id = before.id AND n.shop_id IN (p_shop, p_peer);
  END IF;

  v_number := link.note_seq + 1;
  UPDATE network_link l SET note_seq = v_number WHERE l.id = own.link_id AND l.shop_id IN (p_shop, p_peer);
  -- The lines of the note, by the order's line numbers.
  IF p_lines IS NULL THEN
    SELECT jsonb_agg(jsonb_build_object('line_no', n.line_no, 'qty', n.accepted_qty, 'unit_price', n.unit_price))
      INTO v_lines
      FROM network_order_line n WHERE n.shop_id = p_shop AND n.order_id = p_order AND n.accepted_qty > 0;
  ELSE
    IF jsonb_typeof(p_lines) IS DISTINCT FROM 'array' THEN
      PERFORM network_refuse('NETWORK_INVALID');
    END IF;
    SELECT jsonb_agg(jsonb_build_object('line_no', x.line_no, 'qty', x.qty, 'unit_price', x.unit_price)),
           count(*), count(DISTINCT x.line_no)
      INTO v_lines, v_rows, v_distinct
      FROM jsonb_to_recordset(p_lines) AS x(line_no smallint, qty numeric(14,3), unit_price bigint)
      JOIN network_order_line n ON n.shop_id = p_shop AND n.order_id = p_order AND n.line_no = x.line_no
     WHERE x.qty > 0 AND x.unit_price >= 0;
    IF v_rows <> jsonb_array_length(p_lines) OR v_distinct <> v_rows THEN
      PERFORM network_refuse('NETWORK_INVALID');
    END IF;
  END IF;
  SELECT sum(round(w.qty * w.unit_price))::bigint INTO v_total
    FROM jsonb_to_recordset(coalesce(v_lines, '[]'::jsonb)) AS w(line_no smallint, qty numeric, unit_price bigint);
  IF coalesce(v_total, 0) <= 0 OR p_paid IS NULL OR p_paid < 0 OR p_paid > v_total THEN
    PERFORM network_refuse('NETWORK_INVALID');
  END IF;

  INSERT INTO network_note (shop_id, id, link_id, order_id, role, number, status, currency, total, paid,
                            supersedes_id, supersede_reason, issued_at, issued_by, customer_id)
  VALUES (p_shop, p_note, own.link_id, p_order, 'supplier', v_number, 'issued', own.currency, v_total, p_paid,
          before.id, p_reason, p_now, p_member, p_customer),
         (p_peer, p_note, own.link_id, p_order, 'buyer', v_number, 'issued', own.currency, v_total, p_paid,
          before.id, p_reason, p_now, NULL, NULL);
  INSERT INTO network_note_line (shop_id, note_id, line_no, name, unit, qty, unit_price, line_total, item_id)
  SELECT n.shop_id, p_note, row_number() OVER (PARTITION BY n.shop_id ORDER BY n.line_no)::smallint, n.name, n.unit,
         w.qty, w.unit_price, round(w.qty * w.unit_price)::bigint, n.item_id
    FROM jsonb_to_recordset(v_lines) AS w(line_no smallint, qty numeric, unit_price bigint)
    JOIN network_order_line n ON n.order_id = p_order AND n.shop_id IN (p_shop, p_peer) AND n.line_no = w.line_no;
  UPDATE network_order o SET status = 'delivered', updated_at = p_now
   WHERE o.id = p_order AND o.shop_id IN (p_shop, p_peer);
  PERFORM network_log(p_shop, p_peer, own.link_id, 'note', p_note,
                      CASE WHEN before.id IS NULL THEN 'note_issued' ELSE 'note_corrected' END, p_member, p_now,
                      jsonb_build_object('number', v_number, 'currency', own.currency, 'total', v_total, 'paid', p_paid,
                                         'reason', p_reason));
  RETURN v_number;
END $$;
REVOKE ALL ON FUNCTION network_note_issue(uuid, uuid, uuid, uuid, uuid, uuid, bigint, text, jsonb, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION network_note_issue(uuid, uuid, uuid, uuid, uuid, uuid, bigint, text, jsonb, timestamptz) TO qd_app;

-- Both copies of a note under their locks (after the link's); returns the acting shop's copy.
CREATE FUNCTION network_note_pair(p_shop uuid, p_peer uuid, p_note uuid) RETURNS network_note
LANGUAGE plpgsql SET search_path = public, pg_temp
AS $$
DECLARE
  seen network_note%ROWTYPE;
  own network_note%ROWTYPE;
  copies integer := 0;
  r network_note%ROWTYPE;
BEGIN
  SELECT * INTO seen FROM network_note n WHERE n.shop_id = p_shop AND n.id = p_note;
  IF NOT FOUND THEN
    PERFORM network_refuse('NETWORK_NOT_FOUND');
  END IF;
  PERFORM network_pair(p_shop, p_peer, seen.link_id);
  FOR r IN
    SELECT * FROM network_note n WHERE n.id = p_note AND n.shop_id IN (p_shop, p_peer) ORDER BY n.shop_id FOR UPDATE
  LOOP
    copies := copies + 1;
    IF r.shop_id = p_shop THEN own := r; END IF;
  END LOOP;
  IF copies <> 2 THEN
    PERFORM network_refuse('NETWORK_NOT_FOUND');
  END IF;
  RETURN own;
END $$;
REVOKE ALL ON FUNCTION network_note_pair(uuid, uuid, uuid) FROM PUBLIC;

-- The buyer rejects a note with a reason, and may say what did arrive, line by line. Nothing is posted;
-- the supplier sees it and may issue a corrected note.
CREATE FUNCTION network_note_reject(
  p_shop uuid, p_peer uuid, p_note uuid, p_member uuid, p_reason text, p_lines jsonb, p_now timestamptz
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp
AS $$
DECLARE
  own network_note%ROWTYPE;
  link network_link%ROWTYPE;
BEGIN
  PERFORM network_guard(p_shop);
  PERFORM network_member(p_shop, p_member);
  own := network_note_pair(p_shop, p_peer, p_note);
  SELECT * INTO link FROM network_link l WHERE l.shop_id = p_shop AND l.id = own.link_id;
  IF own.role <> 'buyer' OR own.status <> 'issued' OR link.state <> 'active' OR p_reason IS NULL THEN
    PERFORM network_refuse('NETWORK_STATE');
  END IF;
  IF p_lines IS NOT NULL THEN
    UPDATE network_note_line n SET received_qty = x.received_qty
      FROM jsonb_to_recordset(p_lines) AS x(line_no smallint, received_qty numeric)
     WHERE n.note_id = p_note AND n.shop_id IN (p_shop, p_peer) AND n.line_no = x.line_no AND x.received_qty >= 0;
  END IF;
  UPDATE network_note n
     SET status = 'rejected', reject_reason = p_reason, decided_at = p_now,
         decided_by = CASE WHEN n.shop_id = p_shop THEN p_member END
   WHERE n.id = p_note AND n.shop_id IN (p_shop, p_peer);
  PERFORM network_log(p_shop, p_peer, own.link_id, 'note', p_note, 'note_rejected', p_member, p_now,
                      jsonb_build_object('number', own.number, 'reason', p_reason));
END $$;
REVOKE ALL ON FUNCTION network_note_reject(uuid, uuid, uuid, uuid, text, jsonb, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION network_note_reject(uuid, uuid, uuid, uuid, text, jsonb, timestamptz) TO qd_app;

-- THE ONE PLACE A TRANSACTION MAY NAME A SECOND TENANT. The buyer is confirming a delivery note, and
-- the supplier's side of that sale has to be written by the application's own ledger and stock code,
-- which works under the tenant setting. This moves the setting to the supplier, and only if: the caller
-- is the tenant `p_home`; a link joins exactly these two shops and is active; `p_home` is its buyer;
-- and both copies of the note are `issued` (they are locked here, after the link, so nothing else can
-- answer the note meanwhile). `network_leave_peer` moves it back. What the application then does as the
-- supplier is confined by row-level security to the supplier's rows, and `network_receipt_finish`
-- checks the outcome.
CREATE FUNCTION network_enter_peer(p_home uuid, p_peer uuid, p_note uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp
AS $$
DECLARE
  own network_note%ROWTYPE;
  link network_link%ROWTYPE;
BEGIN
  PERFORM network_guard(p_home);
  own := network_note_pair(p_home, p_peer, p_note);
  SELECT * INTO link FROM network_link l WHERE l.shop_id = p_home AND l.id = own.link_id;
  IF own.role <> 'buyer' OR link.state <> 'active' THEN
    PERFORM network_refuse('NETWORK_STATE');
  END IF;
  PERFORM 1 FROM network_note n WHERE n.id = p_note AND n.shop_id IN (p_home, p_peer) AND n.status = 'issued'
    HAVING count(*) = 2;
  IF NOT FOUND THEN
    PERFORM network_refuse('NETWORK_STATE');
  END IF;
  PERFORM 1 FROM shop s WHERE s.id = p_peer AND s.status = 'active';
  IF NOT FOUND THEN
    PERFORM network_refuse('NETWORK_PARTNER_UNAVAILABLE');
  END IF;
  PERFORM set_config('qd.network_receipt', p_home::text || ' ' || p_peer::text || ' ' || p_note::text, true);
  PERFORM set_config('qd.shop_id', p_peer::text, true);
END $$;
REVOKE ALL ON FUNCTION network_enter_peer(uuid, uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION network_enter_peer(uuid, uuid, uuid) TO qd_app;

-- Back to the buyer: only from the supplier a receipt was entered for, in this transaction.
CREATE FUNCTION network_leave_peer(p_home uuid, p_peer uuid, p_note uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp
AS $$
BEGIN
  IF p_peer IS DISTINCT FROM nullif(current_setting('qd.shop_id', true), '')::uuid
     OR current_setting('qd.network_receipt', true) IS DISTINCT FROM
        p_home::text || ' ' || p_peer::text || ' ' || p_note::text THEN
    PERFORM network_refuse('NETWORK_NOT_FOUND');
  END IF;
  PERFORM 1 FROM network_note n JOIN network_link l ON l.shop_id = n.shop_id AND l.id = n.link_id
    WHERE n.shop_id = p_home AND n.id = p_note AND n.role = 'buyer' AND n.status = 'issued' AND l.peer_shop_id = p_peer;
  IF NOT FOUND THEN
    PERFORM network_refuse('NETWORK_NOT_FOUND');
  END IF;
  PERFORM set_config('qd.network_receipt', '', true);
  PERFORM set_config('qd.shop_id', p_home::text, true);
END $$;
REVOKE ALL ON FUNCTION network_leave_peer(uuid, uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION network_leave_peer(uuid, uuid, uuid) TO qd_app;

-- The buyer's confirmation takes effect: the note is received on both sides, in the transaction that
-- posted both books. Refuses unless both postings are there and say what the note says: the buyer's
-- posted receipt (this note's, from this supplier, the note's currency, total and what was paid) and the
-- supplier's credit sale to the buyer's account for the note's total, written in the name of the member
-- who issued the note, with the payment of what was paid on delivery.
CREATE FUNCTION network_receipt_finish(
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

-- ---------------------------------------------------------------------------
-- Payments
-- ---------------------------------------------------------------------------

-- Whether `p_entry` is the acting shop's own, standing entry for a payment of this link: the buyer's
-- payment on the linked supplier's account, or the supplier's payment entry of the linked customer.
CREATE FUNCTION network_own_payment_entry(
  p_shop uuid, p_link network_link, p_entry uuid, p_amount bigint, p_currency text, p_member uuid
) RETURNS boolean
LANGUAGE sql STABLE SET search_path = public, pg_temp
AS $$
  SELECT CASE p_link.role
    WHEN 'buyer' THEN EXISTS (
      SELECT 1 FROM supplier_entry e
       WHERE e.id = p_entry AND e.shop_id = p_shop AND e.kind = 'payment' AND e.supplier_id = p_link.supplier_id
         AND e.amount = p_amount AND e.currency = p_currency AND e.author_id = p_member AND e.document_id IS NULL
         AND NOT EXISTS (SELECT 1 FROM supplier_entry r WHERE r.reverses_id = e.id))
    ELSE EXISTS (
      SELECT 1 FROM ledger_entry e
       WHERE e.id = p_entry AND e.shop_id = p_shop AND e.kind = 'payment' AND e.customer_id = p_link.customer_id
         AND e.amount = p_amount AND e.currency = p_currency AND e.author_id = p_member
         AND NOT EXISTS (SELECT 1 FROM ledger_entry r WHERE r.reverses_id = e.id))
  END;
$$;
REVOKE ALL ON FUNCTION network_own_payment_entry(uuid, network_link, uuid, bigint, text, uuid) FROM PUBLIC;

-- One side records a payment it has already written into its own books (`p_entry`, checked here). The
-- other side sees it as awaiting its confirmation, and its books do not move.
CREATE FUNCTION network_payment_record(
  p_shop uuid, p_peer uuid, p_link uuid, p_payment uuid, p_member uuid, p_amount bigint, p_currency text, p_note text,
  p_entry uuid, p_now timestamptz
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp
AS $$
DECLARE
  own network_link%ROWTYPE;
BEGIN
  PERFORM network_guard(p_shop);
  PERFORM network_member(p_shop, p_member);
  own := network_pair(p_shop, p_peer, p_link);
  IF own.state <> 'active' THEN
    PERFORM network_refuse('NETWORK_STATE');
  END IF;
  IF NOT coalesce(network_currency_ok(p_shop, p_peer, p_currency), false) THEN
    PERFORM network_refuse('NETWORK_CURRENCY');
  END IF;
  IF NOT coalesce(network_own_payment_entry(p_shop, own, p_entry, p_amount, p_currency, p_member), false) THEN
    PERFORM network_refuse('NETWORK_BOOKS_MISMATCH');
  END IF;
  INSERT INTO network_payment (shop_id, id, link_id, role, recorded_by_own, status, amount, currency, note,
                               recorded_at, recorded_by, supplier_entry_id, ledger_entry_id)
  VALUES (p_shop, p_payment, p_link, own.role, true, 'awaiting', p_amount, p_currency, p_note, p_now, p_member,
          CASE own.role WHEN 'buyer' THEN p_entry END, CASE own.role WHEN 'supplier' THEN p_entry END),
         (p_peer, p_payment, p_link, CASE own.role WHEN 'buyer' THEN 'supplier' ELSE 'buyer' END, false, 'awaiting',
          p_amount, p_currency, p_note, p_now, NULL, NULL, NULL);
  PERFORM network_log(p_shop, p_peer, p_link, 'payment', p_payment, 'payment_recorded', p_member, p_now,
                      jsonb_build_object('amount', p_amount, 'currency', p_currency));
END $$;
REVOKE ALL ON FUNCTION network_payment_record(uuid, uuid, uuid, uuid, uuid, bigint, text, text, uuid, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION network_payment_record(uuid, uuid, uuid, uuid, uuid, bigint, text, text, uuid, timestamptz) TO qd_app;

-- Both copies of a payment under their locks (after the link's); returns the acting shop's copy.
CREATE FUNCTION network_payment_pair(p_shop uuid, p_peer uuid, p_payment uuid) RETURNS network_payment
LANGUAGE plpgsql SET search_path = public, pg_temp
AS $$
DECLARE
  seen network_payment%ROWTYPE;
  own network_payment%ROWTYPE;
  copies integer := 0;
  r network_payment%ROWTYPE;
BEGIN
  SELECT * INTO seen FROM network_payment p WHERE p.shop_id = p_shop AND p.id = p_payment;
  IF NOT FOUND THEN
    PERFORM network_refuse('NETWORK_NOT_FOUND');
  END IF;
  PERFORM network_pair(p_shop, p_peer, seen.link_id);
  FOR r IN
    SELECT * FROM network_payment p WHERE p.id = p_payment AND p.shop_id IN (p_shop, p_peer) ORDER BY p.shop_id FOR UPDATE
  LOOP
    copies := copies + 1;
    IF r.shop_id = p_shop THEN own := r; END IF;
  END LOOP;
  IF copies <> 2 THEN
    PERFORM network_refuse('NETWORK_NOT_FOUND');
  END IF;
  RETURN own;
END $$;
REVOKE ALL ON FUNCTION network_payment_pair(uuid, uuid, uuid) FROM PUBLIC;

-- The other side answers. Confirming needs its own entry, already written into its own books in this
-- transaction (`p_entry`, checked here); declining needs a reason and writes nothing anywhere.
CREATE FUNCTION network_payment_decide(
  p_shop uuid, p_peer uuid, p_payment uuid, p_member uuid, p_confirm boolean, p_reason text, p_entry uuid,
  p_now timestamptz
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp
AS $$
DECLARE
  own network_payment%ROWTYPE;
  link network_link%ROWTYPE;
BEGIN
  PERFORM network_guard(p_shop);
  PERFORM network_member(p_shop, p_member);
  own := network_payment_pair(p_shop, p_peer, p_payment);
  SELECT * INTO link FROM network_link l WHERE l.shop_id = p_shop AND l.id = own.link_id;
  IF own.recorded_by_own OR own.status <> 'awaiting' OR link.state <> 'active' THEN
    PERFORM network_refuse('NETWORK_STATE');
  END IF;
  IF p_confirm THEN
    IF NOT coalesce(network_own_payment_entry(p_shop, link, p_entry, own.amount, own.currency, p_member), false) THEN
      PERFORM network_refuse('NETWORK_BOOKS_MISMATCH');
    END IF;
    UPDATE network_payment p
       SET status = 'confirmed', decided_at = p_now,
           decided_by = CASE WHEN p.shop_id = p_shop THEN p_member END,
           supplier_entry_id = CASE WHEN p.shop_id = p_shop AND p.role = 'buyer' THEN p_entry ELSE p.supplier_entry_id END,
           ledger_entry_id = CASE WHEN p.shop_id = p_shop AND p.role = 'supplier' THEN p_entry ELSE p.ledger_entry_id END
     WHERE p.id = p_payment AND p.shop_id IN (p_shop, p_peer);
    PERFORM network_log(p_shop, p_peer, own.link_id, 'payment', p_payment, 'payment_confirmed', p_member, p_now,
                        jsonb_build_object('amount', own.amount, 'currency', own.currency));
  ELSE
    IF p_reason IS NULL THEN
      PERFORM network_refuse('NETWORK_INVALID');
    END IF;
    UPDATE network_payment p
       SET status = 'declined', decline_reason = p_reason, decided_at = p_now,
           decided_by = CASE WHEN p.shop_id = p_shop THEN p_member END
     WHERE p.id = p_payment AND p.shop_id IN (p_shop, p_peer);
    PERFORM network_log(p_shop, p_peer, own.link_id, 'payment', p_payment, 'payment_declined', p_member, p_now,
                        jsonb_build_object('amount', own.amount, 'currency', own.currency, 'reason', p_reason));
  END IF;
END $$;
REVOKE ALL ON FUNCTION network_payment_decide(uuid, uuid, uuid, uuid, boolean, text, uuid, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION network_payment_decide(uuid, uuid, uuid, uuid, boolean, text, uuid, timestamptz) TO qd_app;

-- The side that recorded a payment takes it back while it still waits: only after it has cancelled its
-- own entry in its own books (checked here), so the two never disagree about a withdrawn payment.
CREATE FUNCTION network_payment_withdraw(p_shop uuid, p_peer uuid, p_payment uuid, p_member uuid, p_now timestamptz)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp
AS $$
DECLARE
  own network_payment%ROWTYPE;
BEGIN
  PERFORM network_guard(p_shop);
  PERFORM network_member(p_shop, p_member);
  own := network_payment_pair(p_shop, p_peer, p_payment);
  IF NOT own.recorded_by_own OR own.status <> 'awaiting' THEN
    PERFORM network_refuse('NETWORK_STATE');
  END IF;
  IF NOT (EXISTS (SELECT 1 FROM supplier_entry r WHERE r.reverses_id = own.supplier_entry_id AND r.shop_id = p_shop)
          OR EXISTS (SELECT 1 FROM ledger_entry r WHERE r.reverses_id = own.ledger_entry_id AND r.shop_id = p_shop)) THEN
    PERFORM network_refuse('NETWORK_BOOKS_MISMATCH');
  END IF;
  UPDATE network_payment p
     SET status = 'withdrawn', decided_at = p_now, decided_by = CASE WHEN p.shop_id = p_shop THEN p_member END
   WHERE p.id = p_payment AND p.shop_id IN (p_shop, p_peer);
  PERFORM network_log(p_shop, p_peer, own.link_id, 'payment', p_payment, 'payment_withdrawn', p_member, p_now,
                      jsonb_build_object('amount', own.amount, 'currency', own.currency));
END $$;
REVOKE ALL ON FUNCTION network_payment_withdraw(uuid, uuid, uuid, uuid, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION network_payment_withdraw(uuid, uuid, uuid, uuid, timestamptz) TO qd_app;

-- ---------------------------------------------------------------------------
-- Erasing a shop
-- ---------------------------------------------------------------------------

-- The function of migration 0043 with the network added. The erased shop's side of every link, order,
-- note and payment is deleted with the rest of it. The partner keeps its own side and its own posted
-- books; its link ends, what still waited is closed, and the partner is shown as removed: its name and
-- phone are cleared from the link, and the phone from the supplier or customer row the network made
-- for it (a row the partner's shop chose itself is its own and is left as it is).
CREATE OR REPLACE FUNCTION erase_shop(p_shop_id uuid) RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  people uuid[];
  person uuid;
  partner record;
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

  -- Every copy of every link of this shop, its own and its partners', under lock in the order every step
  -- of a link takes them (the lower shop first), so an erasure and a step never wait for each other.
  PERFORM 1 FROM network_link l
    WHERE l.shop_id = p_shop_id OR l.peer_shop_id = p_shop_id ORDER BY l.id, l.shop_id FOR UPDATE;
  -- The partners' side of the network.
  FOR partner IN
    SELECT l.shop_id, l.id, l.state, l.supplier_id, l.customer_id, l.counterpart_made
      FROM network_link l WHERE l.peer_shop_id = p_shop_id ORDER BY l.shop_id, l.id FOR UPDATE
  LOOP
    UPDATE network_note SET status = 'void'
     WHERE shop_id = partner.shop_id AND link_id = partner.id AND status IN ('issued', 'rejected');
    UPDATE network_order SET status = 'cancelled', closed_reason = 'partner removed', updated_at = now()
     WHERE shop_id = partner.shop_id AND link_id = partner.id AND status IN ('sent', 'accepted', 'delivered');
    UPDATE network_payment SET status = 'lapsed', decided_at = now()
     WHERE shop_id = partner.shop_id AND link_id = partner.id AND status = 'awaiting';
    UPDATE supplier SET linked_shop_id = NULL, phone = CASE WHEN partner.counterpart_made THEN NULL ELSE phone END
     WHERE shop_id = partner.shop_id AND id = partner.supplier_id;
    IF partner.counterpart_made THEN
      UPDATE customer SET phone = NULL WHERE shop_id = partner.shop_id AND id = partner.customer_id;
    END IF;
    UPDATE network_link
       SET state = CASE WHEN state IN ('requested', 'active') THEN 'ended' ELSE state END,
           ended_at = coalesce(ended_at, now()),
           ended_by_peer = CASE WHEN state IN ('requested', 'active') THEN true ELSE ended_by_peer END,
           peer_removed = true, peer_name = NULL, peer_phone = NULL
     WHERE shop_id = partner.shop_id AND id = partner.id;
    INSERT INTO network_event (shop_id, link_id, subject, subject_id, kind, by_peer, member_id, at, detail)
    VALUES (partner.shop_id, partner.id, 'link', partner.id, 'partner_removed', true, NULL, now(), NULL);
  END LOOP;
  UPDATE supplier SET linked_shop_id = NULL WHERE linked_shop_id = p_shop_id;

  -- Children before parents. Every table that carries a shop identifier is listed here;
  -- tests/db/test_shop_erasure.py fails when one is added and not listed.
  DELETE FROM network_event WHERE shop_id = p_shop_id;
  DELETE FROM network_payment WHERE shop_id = p_shop_id;
  DELETE FROM network_note_line WHERE shop_id = p_shop_id;
  DELETE FROM network_note WHERE shop_id = p_shop_id;
  DELETE FROM network_order_line WHERE shop_id = p_shop_id;
  DELETE FROM network_order WHERE shop_id = p_shop_id;
  DELETE FROM network_order_draft WHERE shop_id = p_shop_id;
  DELETE FROM network_link WHERE shop_id = p_shop_id;
  DELETE FROM network_invite WHERE shop_id = p_shop_id;
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

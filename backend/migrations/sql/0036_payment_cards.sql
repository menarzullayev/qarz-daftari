-- Several cards to pay the subscription to (the founder's decision of 2026-10-09).
--
-- The platform had one receiving card, the setting `card_number`. The administrator now keeps a list of
-- up to ten, the setting `payment_cards`: an array of {"number": sixteen digits, "label": a short name
-- such as the card's kind and bank}, in the order they are offered. The first is the primary one; the
-- payer chooses whichever suits them. The list is checked by the application
-- (qarz.domain.platform_settings) and written, like every setting, only by admin_set_platform_setting;
-- no right on platform_setting changes here.

-- A card number that was stored becomes a list of that one card. It had no label, so it is given a
-- plain one the administrator can change. A value that is no card number (null, or something the
-- application would not have applied) becomes no list at all. The old row goes either way.
INSERT INTO platform_setting (key, value, updated_by, updated_at)
SELECT 'payment_cards',
       jsonb_build_array(jsonb_build_object('number', replace(p.value #>> '{}', ' ', ''), 'label', 'Karta')),
       p.updated_by, p.updated_at
  FROM platform_setting p
 WHERE p.key = 'card_number'
   AND jsonb_typeof(p.value) = 'string'
   AND replace(p.value #>> '{}', ' ', '') ~ '^[0-9]{16}$'
ON CONFLICT (key) DO NOTHING;
DELETE FROM platform_setting WHERE key = 'card_number';

-- Which card the payer says they paid to, so that the reviewer knows which account's statement to look
-- at: the card's label and the last four digits of its number as they were when it was chosen, never the
-- whole number. Null for receipts sent before this, and when the payer did not say.
ALTER TABLE subscription_receipt
  ADD COLUMN paid_to_card text CHECK (length(paid_to_card) BETWEEN 1 AND 60);

-- The administrator's two readings of receipts (0024, restated in 0030) also say which card was paid
-- to. A function's result columns cannot be changed in place, so both are dropped and created again;
-- nothing else in them is changed. Since 0031 they are the administrator's side's alone.
DROP FUNCTION admin_receipts(uuid, text, timestamptz, uuid, integer);
DROP FUNCTION admin_receipt(uuid, uuid, boolean);

CREATE FUNCTION admin_receipts(
  p_admin uuid, p_status text, p_after_created timestamptz, p_after_id uuid, p_limit integer
)
RETURNS TABLE (
  receipt_id uuid, shop_id uuid, shop_name text, stated_amount bigint, stated_months smallint, status text,
  months smallint, reject_reason text, created_at timestamptz, decided_at timestamptz, decided_by uuid,
  decided_by_tg bigint, paid_to_card text, has_file boolean, copies integer
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  SELECT r.id, r.shop_id, s.name, r.stated_amount, r.stated_months, r.status, r.months, r.reject_reason,
         r.created_at, r.decided_at, r.decided_by, r.decided_by_tg, r.paid_to_card, r.file_id IS NOT NULL,
         (SELECT count(*)::integer
            FROM stored_file mine
            JOIN stored_file other
              ON other.sha256 = mine.sha256 AND other.purpose = 'subscription_receipt' AND other.id <> mine.id
            JOIN subscription_receipt o ON o.file_id = other.id
           WHERE mine.id = r.file_id)
    FROM subscription_receipt r
    JOIN shop s ON s.id = r.shop_id
   WHERE r.status = p_status
     AND s.status <> 'erased'
     AND EXISTS (SELECT 1 FROM admin_account a WHERE a.user_id = p_admin AND a.status = 'active')
     AND (p_after_created IS NULL OR (r.created_at, r.id) > (p_after_created, p_after_id))
   ORDER BY r.created_at, r.id
   LIMIT least(greatest(p_limit, 1), 101);
$$;

CREATE FUNCTION admin_receipt(p_admin uuid, p_receipt uuid, p_lock boolean)
RETURNS TABLE (
  receipt_id uuid, shop_id uuid, shop_name text, stated_amount bigint, stated_months smallint, status text,
  months smallint, reject_reason text, created_at timestamptz, decided_at timestamptz, decided_by uuid,
  decided_by_tg bigint, paid_to_card text,
  file_id uuid, object_key text, sha256 bytea, size_bytes integer, mime text, delete_after timestamptz
)
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM admin_account a WHERE a.user_id = p_admin AND a.status = 'active') THEN
    RETURN;
  END IF;
  IF p_lock THEN
    PERFORM 1 FROM subscription_receipt r WHERE r.id = p_receipt FOR UPDATE;
  END IF;
  RETURN QUERY
    SELECT r.id, r.shop_id, s.name, r.stated_amount, r.stated_months, r.status, r.months, r.reject_reason,
           r.created_at, r.decided_at, r.decided_by, r.decided_by_tg, r.paid_to_card,
           f.id, f.object_key, f.sha256, f.size_bytes, f.mime, f.delete_after
      FROM subscription_receipt r
      JOIN shop s ON s.id = r.shop_id
      LEFT JOIN stored_file f ON f.id = r.file_id
     WHERE r.id = p_receipt AND s.status <> 'erased';
END $$;

REVOKE ALL ON FUNCTION admin_receipts(uuid, text, timestamptz, uuid, integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION admin_receipt(uuid, uuid, boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION admin_receipts(uuid, text, timestamptz, uuid, integer) TO qd_admin;
GRANT EXECUTE ON FUNCTION admin_receipt(uuid, uuid, boolean) TO qd_admin;

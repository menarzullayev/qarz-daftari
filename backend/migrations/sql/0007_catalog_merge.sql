-- Merging a learned catalog item into an existing one (story S6.1; REQ-040, domain rule BR-6).

-- A learned item is often another spelling of a good the catalog already has. The approved schema can
-- hide such an item but cannot remember which item it stands for, so the same spelling typed again would
-- still point at the hidden duplicate. This column makes the hidden item an alias of the item it was
-- merged into. Goods lines are untouched: they are insert-only and keep their own name and price (INV-17).
ALTER TABLE catalog_item ADD COLUMN merged_into uuid;

-- The target must be an item of the same shop. Row-level security does not apply to foreign-key checks,
-- so the shop is part of the key.
ALTER TABLE catalog_item ADD CONSTRAINT catalog_item_shop_id_id_key UNIQUE (shop_id, id);
ALTER TABLE catalog_item ADD CONSTRAINT catalog_item_merged_into_fkey
  FOREIGN KEY (shop_id, merged_into) REFERENCES catalog_item (shop_id, id);

-- An alias is hidden, reviewed, and never points at itself.
ALTER TABLE catalog_item ADD CONSTRAINT catalog_item_alias_is_hidden
  CHECK (merged_into IS NULL OR (status = 'hidden' AND NOT learned AND merged_into <> id));

-- No new grant or policy: the table-level grants to qd_app and the tenant policy of 0001 cover the column.

-- Separate permissions per member of staff (expansion module G; docs/08-technical-spec/OUTPUT.md,
-- "Access model").
--
-- A role stays what it was: a preset that gives a member a default set of permissions. The owner may now
-- change that set for one member, permission by permission. What is stored is only the difference from
-- the role: the keys granted beyond it and the keys denied despite it.
--
-- Where it is stored: two arrays on the membership row itself, not a table of their own.
--   * Every request already reads the caller's membership row by (shop_id, user_id), a unique index. With
--     the changes on that row, authorization stays one indexed read of one row: no join, no second index.
--   * `membership` is a tenant table with forced row-level security already, so the changes are scoped to
--     the shop by the same policy as the role they modify, and no new right is granted to any role.
--   * A member's changes are replaced as a whole (the matrix is saved at once), which one UPDATE of one
--     row does atomically. A shop has a handful of members, so "who holds X" is a scan of those rows.
--
-- What the database holds to, whatever the application does:
--   * the owner has no changes: nothing stored can reduce the owner's rights;
--   * a key is never granted and denied at once, and every key has the shape of a catalogue key;
--   * the changes belong to the role they were made for. They are cleared when the member's role changes
--     (also by an ownership transfer or an administrator's reassignment), when the member is removed, and
--     when a removed member joins again: a person invited back does not find old grants waiting.
--
-- The catalogue of keys is the domain layer's (qarz.domain.permissions). A stored key that the catalogue
-- does not know, or one that may not be changed, is ignored there; the database only keeps the shape.
ALTER TABLE membership
  ADD COLUMN permissions_granted text[] NOT NULL DEFAULT '{}',
  ADD COLUMN permissions_denied  text[] NOT NULL DEFAULT '{}';

ALTER TABLE membership
  ADD CONSTRAINT membership_permissions_owner CHECK (
    role <> 'owner' OR (permissions_granted = '{}' AND permissions_denied = '{}')
  ),
  ADD CONSTRAINT membership_permissions_disjoint CHECK (NOT (permissions_granted && permissions_denied)),
  ADD CONSTRAINT membership_permissions_shape CHECK (
    cardinality(permissions_granted) <= 64
    AND cardinality(permissions_denied) <= 64
    AND array_position(permissions_granted || permissions_denied, NULL) IS NULL
    AND array_to_string(permissions_granted || permissions_denied, ',')
        ~ '^([a-z][a-z_]*(\.[a-z][a-z_]*)+(,[a-z][a-z_]*(\.[a-z][a-z_]*)+)*)?$'
    -- One key per element: an element that itself holds a comma would read as two keys above.
    AND cardinality(permissions_granted || permissions_denied)
        = cardinality(string_to_array(array_to_string(permissions_granted || permissions_denied, ','), ','))
  );

-- Runs with the rights of whoever updates the row and touches nothing but the row being written, so it
-- needs no right of its own and nobody is granted the right to call it.
CREATE FUNCTION membership_permissions_follow_role() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
  IF NEW.role IS DISTINCT FROM OLD.role OR NEW.status = 'removed' OR OLD.status = 'removed' THEN
    NEW.permissions_granted := '{}';
    NEW.permissions_denied := '{}';
  END IF;
  RETURN NEW;
END $$;

REVOKE ALL ON FUNCTION membership_permissions_follow_role() FROM PUBLIC;

CREATE TRIGGER membership_permissions_follow_role
  BEFORE UPDATE ON membership
  FOR EACH ROW EXECUTE FUNCTION membership_permissions_follow_role();

-- What a change of permissions was: the member's changes before and after it. The activity log is
-- insert-only; the column is empty for every other action.
ALTER TABLE activity ADD COLUMN detail jsonb;

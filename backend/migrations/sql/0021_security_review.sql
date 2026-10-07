-- Fixes from the security review (docs/10-operations/security-review.md, findings 2 and 3).

-- Finding 3. A function that runs with its owner's rights and names only `public` in its search path
-- still looks in the session's temporary schema first, so a temporary table made by the application role
-- could stand in for a real one. Naming pg_temp last closes that for every such function there is.
DO $$
DECLARE
  fn record;
  path text;
BEGIN
  FOR fn IN
    SELECT p.oid::regprocedure AS signature,
           (SELECT substr(setting, length('search_path=') + 1)
              FROM unnest(coalesce(p.proconfig, '{}')) AS setting
             WHERE setting LIKE 'search_path=%') AS search_path
      FROM pg_proc p
      JOIN pg_namespace n ON n.oid = p.pronamespace
     WHERE p.prosecdef AND n.nspname IN ('public', 'measure')
  LOOP
    path := coalesce(fn.search_path, 'public');
    IF path !~ '(^|,)\s*pg_temp\s*$' THEN
      EXECUTE format('ALTER FUNCTION %s SET search_path = %s, pg_temp', fn.signature, path);
    END IF;
  END LOOP;
END $$;

-- Finding 2. `erase_shop` erases a shop whose deletion is due, and the application role could itself
-- write that it was due. The waiting period (BR-25) is now held by the database: the application role may
-- ask for deletion only with a due time at least 30 days ahead by the database's clock, may not bring a
-- due time forward, and may not mark a shop erased. Erasure itself runs with its owner's rights and is
-- not affected; neither is the migration owner, who is not the application.
CREATE FUNCTION shop_deletion_guard() RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF current_user <> 'qd_app' THEN
    RETURN NEW;
  END IF;
  IF NEW.status = 'erased' AND OLD.status <> 'erased' THEN
    RAISE EXCEPTION 'a shop is erased only by erase_shop' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF OLD.status = 'erased' AND NEW.status <> 'erased' THEN
    RAISE EXCEPTION 'an erased shop stays erased' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF NEW.status = 'deletion_pending'
     AND (OLD.status <> 'deletion_pending' OR NEW.deletion_due IS DISTINCT FROM OLD.deletion_due)
     -- Five minutes of allowance for the application server's clock running behind the database's.
     AND (NEW.deletion_due IS NULL OR NEW.deletion_due < now() + interval '30 days' - interval '5 minutes')
  THEN
    RAISE EXCEPTION 'a shop waits 30 days before it is erased' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER shop_deletion_guard
  BEFORE UPDATE OF status, deletion_due ON shop
  FOR EACH ROW EXECUTE FUNCTION shop_deletion_guard();

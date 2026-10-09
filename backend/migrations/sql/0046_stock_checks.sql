-- The operations watch learns whether the stock's two kept figures still agree with their ledgers.
--
-- `stock_level` and `supplier_balance` are written only by triggers (migration 0043), and the two
-- comparisons `stock_level_mismatches` and `supplier_balance_mismatches` list any difference. They read
-- every shop and return identifiers, quantities and amounts, so they stay closed to every role. As with
-- the open debts (migration 0035, `open_debt_mismatch_count`), the worker is given a count alone, for a
-- daily check: a number, nothing of a shop, an item or a supplier.
--
-- Nothing else changes: no table, no column, no policy, no right on a table.

CREATE FUNCTION stock_level_mismatch_count()
RETURNS bigint
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  SELECT count(*) FROM stock_level_mismatches(NULL);
$$;
REVOKE ALL ON FUNCTION stock_level_mismatch_count() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION stock_level_mismatch_count() TO qd_worker;

CREATE FUNCTION supplier_balance_mismatch_count()
RETURNS bigint
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  SELECT count(*) FROM supplier_balance_mismatches(NULL);
$$;
REVOKE ALL ON FUNCTION supplier_balance_mismatch_count() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION supplier_balance_mismatch_count() TO qd_worker;

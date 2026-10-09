-- The operations watch learns whether the stock's two kept figures still agree with their ledgers.
--
-- `stock_level` and `supplier_balance` are written only by triggers (migration 0043), and the two
-- comparisons `stock_level_mismatches` and `supplier_balance_mismatches` list any difference. They read
-- every shop and return identifiers, quantities and amounts, so they stay closed to every role. As with
-- the open debts (migration 0035, `open_debt_mismatch_count`), the worker is given a count alone, for a
-- daily check: a number, nothing of a shop, an item or a supplier.
--
-- The administrators' side is given a count in the same way (below): how many active customers each of
-- some shops has, which is what the free plan counts.
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

-- How many customers of each of these shops have the status active: what the free plan counts (BR-33),
-- for the administrators' list of shops, which shows a shop the plan holds as free and how much of the
-- plan it uses. Numbers and nothing else of a shop's customers; a shop that has none, or that does not
-- exist, has no row. One statement for a page of shops, so that the administrators' side never sets a
-- tenant of its own: the tenant is set in one place of the application, where a shop's transaction opens.
CREATE FUNCTION admin_active_customer_counts(p_shops uuid[])
RETURNS TABLE (shop_id uuid, customers bigint)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  SELECT c.shop_id, count(*) FROM customer c
   WHERE c.shop_id = ANY(p_shops) AND c.status = 'active'
   GROUP BY c.shop_id;
$$;
REVOKE ALL ON FUNCTION admin_active_customer_counts(uuid[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION admin_active_customer_counts(uuid[]) TO qd_admin;

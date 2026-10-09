-- Every entry and every promise refreshes the open debts of its customer (migration 0026), and the refresh
-- begins by deleting that customer's rows: WHERE customer_id = ANY (...). The only index led with shop_id,
-- so each delete read the whole table. Measured on the generated data of the load test (738 534 rows):
-- 30 ms a refresh, which made recording an entry five to seven times slower than before 0026 and left
-- four server processes unable to keep up with 100 requests a second. With this index: 1 ms.

CREATE INDEX open_debt_customer ON open_debt (customer_id);

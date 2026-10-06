\set ON_ERROR_STOP off
\set A '''00000000-0000-7000-8000-00000000000a'''
\set B '''00000000-0000-7000-8000-00000000000b'''

-- seed as superuser
INSERT INTO app_user(id,tg_id) VALUES ('00000000-0000-7000-8000-000000000001',1),('00000000-0000-7000-8000-000000000002',2);
INSERT INTO shop(id,name) VALUES (:A,'Shop A'),(:B,'Shop B');
INSERT INTO membership(id,shop_id,user_id,role) VALUES
 ('00000000-0000-7000-8000-0000000000a1',:A,'00000000-0000-7000-8000-000000000001','owner'),
 ('00000000-0000-7000-8000-0000000000b1',:B,'00000000-0000-7000-8000-000000000002','owner');
INSERT INTO customer(id,shop_id,display_name,name_norm) VALUES
 ('00000000-0000-7000-8000-0000000000a2',:A,'Ali','ali'),
 ('00000000-0000-7000-8000-0000000000b2',:B,'Vali','vali');
INSERT INTO ledger_entry(id,shop_id,customer_id,seq,kind,amount,author_id) VALUES
 ('00000000-0000-7000-8000-0000000000b3',:B,'00000000-0000-7000-8000-0000000000b2',1,'credit',99000,'00000000-0000-7000-8000-0000000000b1');

\echo == POS1 app role in shop A records an entry with goods lines that sum to the total (expect COMMIT)
SET ROLE qd_app;
BEGIN;
SET LOCAL qd.shop_id = '00000000-0000-7000-8000-00000000000a';
INSERT INTO ledger_entry(id,shop_id,customer_id,seq,kind,amount,author_id) VALUES
 ('00000000-0000-7000-8000-0000000000a3',:A,'00000000-0000-7000-8000-0000000000a2',1,'credit',45000,'00000000-0000-7000-8000-0000000000a1');
INSERT INTO goods_line(id,shop_id,entry_id,line_no,name,qty,unit,unit_price,line_total) VALUES
 ('00000000-0000-7000-8000-0000000000a4',:A,'00000000-0000-7000-8000-0000000000a3',1,'Non',5,'dona',4000,20000),
 ('00000000-0000-7000-8000-0000000000a5',:A,'00000000-0000-7000-8000-0000000000a3',2,'Yog',1,'litr',25000,25000);
COMMIT;

\echo == NEG1 second batch of lines for the same entry in a later transaction (expect: already recorded)
BEGIN;
SET LOCAL qd.shop_id = '00000000-0000-7000-8000-00000000000a';
INSERT INTO goods_line(id,shop_id,entry_id,line_no,name,qty,unit,unit_price,line_total) VALUES
 ('00000000-0000-7000-8000-0000000000a6',:A,'00000000-0000-7000-8000-0000000000a3',3,'Choy',1,'dona',1000,1000);
COMMIT;

\echo == NEG2 lines that do not sum to the entry total (expect: sum error at COMMIT)
BEGIN;
SET LOCAL qd.shop_id = '00000000-0000-7000-8000-00000000000a';
INSERT INTO ledger_entry(id,shop_id,customer_id,seq,kind,amount,author_id) VALUES
 ('00000000-0000-7000-8000-0000000000a7',:A,'00000000-0000-7000-8000-0000000000a2',2,'credit',10000,'00000000-0000-7000-8000-0000000000a1');
INSERT INTO goods_line(id,shop_id,entry_id,line_no,name,qty,unit,unit_price,line_total) VALUES
 ('00000000-0000-7000-8000-0000000000a8',:A,'00000000-0000-7000-8000-0000000000a7',1,'Non',1,'dona',4000,4000);
COMMIT;

\echo == NEG3 app role updates a ledger entry (expect: permission denied)
BEGIN;
SET LOCAL qd.shop_id = '00000000-0000-7000-8000-00000000000a';
UPDATE ledger_entry SET amount = 1;
ROLLBACK;

\echo == NEG4 app role deletes a goods line (expect: permission denied)
BEGIN;
SET LOCAL qd.shop_id = '00000000-0000-7000-8000-00000000000a';
DELETE FROM goods_line;
ROLLBACK;

\echo == NEG5 shop A reads shop B (expect: 0 customers, 0 entries, own counts 1 and 1)
BEGIN;
SET LOCAL qd.shop_id = '00000000-0000-7000-8000-00000000000a';
SELECT (SELECT count(*) FROM customer WHERE shop_id = :B) AS b_customers,
       (SELECT count(*) FROM ledger_entry WHERE customer_id = '00000000-0000-7000-8000-0000000000b2') AS b_entries,
       (SELECT count(*) FROM customer) AS visible_customers,
       (SELECT count(*) FROM ledger_entry) AS visible_entries,
       (SELECT count(*) FROM shop) AS visible_shops;
ROLLBACK;

\echo == NEG6 shop A inserts a row tagged as shop B (expect: row-level security violation)
BEGIN;
SET LOCAL qd.shop_id = '00000000-0000-7000-8000-00000000000a';
INSERT INTO customer(id,shop_id,display_name,name_norm) VALUES ('00000000-0000-7000-8000-0000000000b9',:B,'X','x');
ROLLBACK;

\echo == NEG7 no tenant set (expect: 0 rows visible)
BEGIN;
SELECT count(*) AS visible_without_tenant FROM customer;
ROLLBACK;

\echo == NEG8 second active owner in a shop (expect: unique violation)
RESET ROLE;
INSERT INTO membership(id,shop_id,user_id,role) VALUES ('00000000-0000-7000-8000-0000000000a9',:A,'00000000-0000-7000-8000-000000000002','owner');

\echo == NEG9 second dispute on the same entry (expect: unique violation on the second)
INSERT INTO dispute(id,shop_id,entry_id,reason) VALUES ('00000000-0000-7000-8000-0000000000d1',:A,'00000000-0000-7000-8000-0000000000a3','wrong amount');
INSERT INTO dispute(id,shop_id,entry_id,reason) VALUES ('00000000-0000-7000-8000-0000000000d2',:A,'00000000-0000-7000-8000-0000000000a3','again');

\echo == NEG10 support access longer than 24 hours (expect: check violation)
INSERT INTO support_access(id,shop_id,admin_id,reason,ends_at) VALUES ('00000000-0000-7000-8000-0000000000e1',:A,'00000000-0000-7000-8000-000000000001','test', now() + interval '3 days');

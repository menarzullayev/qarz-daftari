"""Erasing a shop, called directly as the application role (migration 0017; REQ-048, BR-25).

The function deletes from tables the application role may never delete from, so what it will and will not
do is checked here without the application in between.
"""

import uuid
from typing import Any

import psycopg
import pytest

from ..conftest import AppSession, Shop, add_entry, add_line

pytestmark = pytest.mark.db


def tables_with_a_shop(owner: psycopg.Connection) -> set[str]:
    rows = owner.execute(
        "SELECT table_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND column_name = 'shop_id' AND table_name <> 'shop'"
    ).fetchall()
    return {str(row[0]) for row in rows}


def rows_of(owner: psycopg.Connection, shop_id: uuid.UUID) -> dict[str, int]:
    counts = {}
    for table in sorted(tables_with_a_shop(owner)):
        row = owner.execute(f"SELECT count(*) FROM {table} WHERE shop_id = %s", (shop_id,)).fetchone()
        assert row is not None
        counts[table] = int(row[0])
    return counts


def fill(owner: psycopg.Connection, shop: Shop) -> uuid.UUID:
    """Put rows of the shop into as many tables as the ledger's own rules allow. Returns a linked user."""
    entry = add_entry(owner, shop, seq=1, amount=30000)
    add_line(owner, shop, entry, 1, 30000)
    payment = add_entry(owner, shop, seq=2, amount=10000, kind="payment")
    add_entry(owner, shop, seq=3, amount=10000, kind="reversal", reverses=payment)
    linked = uuid.uuid4()
    owner.execute("INSERT INTO app_user (id, tg_id) VALUES (%s, %s)", (linked, uuid.uuid4().int % 10**15))
    statements: list[tuple[str, tuple[Any, ...]]] = [
        (
            "INSERT INTO promise (id, shop_id, entry_id, promised_date, actor) "
            "VALUES (gen_random_uuid(), %s, %s, current_date, 'staff')",
            (shop.shop_id, entry),
        ),
        (
            "INSERT INTO dispute (id, shop_id, entry_id, reason) VALUES (gen_random_uuid(), %s, %s, 'Men olmaganman')",
            (shop.shop_id, entry),
        ),
        (
            "INSERT INTO customer_link (id, shop_id, customer_id, user_id, status, consent_text_v, consent_at) "
            "VALUES (gen_random_uuid(), %s, %s, %s, 'active', 2, now())",
            (shop.shop_id, shop.customer_id, linked),
        ),
        (
            "INSERT INTO customer_share (id, shop_id, customer_id, token_hash, created_by, expires_at) "
            "VALUES (gen_random_uuid(), %s, %s, sha256(gen_random_uuid()::text::bytea), %s, "
            "now() + interval '90 days')",
            (shop.shop_id, shop.customer_id, shop.member_id),
        ),
        (
            "INSERT INTO reminder (id, shop_id, customer_id, kind, channel, amount, sent_on) "
            "VALUES (gen_random_uuid(), %s, %s, 'auto', 'telegram', 100, current_date)",
            (shop.shop_id, shop.customer_id),
        ),
        (
            "INSERT INTO export_job (id, shop_id, requested_by) VALUES (gen_random_uuid(), %s, %s)",
            (shop.shop_id, shop.member_id),
        ),
        (
            "INSERT INTO removal_request (id, shop_id, customer_id, status) "
            "VALUES (gen_random_uuid(), %s, %s, 'waiting')",
            (shop.shop_id, shop.customer_id),
        ),
        (
            "INSERT INTO catalog_item (id, shop_id, name, name_norm, price) "
            "VALUES (gen_random_uuid(), %s, 'Un', 'un', 9000)",
            (shop.shop_id,),
        ),
        (
            "INSERT INTO invitation (token_hash, shop_id, kind) VALUES (%s, %s, 'counter')",
            (uuid.uuid4().bytes, shop.shop_id),
        ),
        (
            "INSERT INTO subscription (shop_id, state, trial_ends) VALUES (%s, 'trial', current_date + 5)",
            (shop.shop_id,),
        ),
        (
            "INSERT INTO online_payment (id, shop_id, months, amount, state, provider, provider_txn) "
            "VALUES (gen_random_uuid(), %s, 1, 100000, 'pending', 'payme', %s)",
            (shop.shop_id, f"txn-{uuid.uuid4().hex}"),
        ),
        (
            "INSERT INTO activity (id, shop_id, actor_kind, action, subject_type) "
            "VALUES (gen_random_uuid(), %s, 'system', 'test', 'shop')",
            (shop.shop_id,),
        ),
        (
            "INSERT INTO request_key (shop_id, key, response) VALUES (%s, %s, '{}'::jsonb)",
            (shop.shop_id, f"key-{uuid.uuid4().hex}"),
        ),
        (
            "INSERT INTO outbox_message (id, channel, recipient, shop_id, payload, dedupe_key) "
            "VALUES (gen_random_uuid(), 'telegram', '1', %s, '{\"text\": \"Ali, 30 000\"}'::jsonb, %s)",
            (shop.shop_id, f"test-{uuid.uuid4().hex}"),
        ),
        (
            "INSERT INTO payment_notice (id, shop_id, customer_id, amount) VALUES (gen_random_uuid(), %s, %s, 500)",
            (shop.shop_id, shop.customer_id),
        ),
        (
            "INSERT INTO date_change_request (id, shop_id, entry_id, requested_date) "
            "VALUES (gen_random_uuid(), %s, %s, current_date + 9)",
            (shop.shop_id, entry),
        ),
    ]
    for sql, values in statements:
        owner.execute(sql, values)
    fill_stock(owner, shop, entry)
    owner.execute("UPDATE app_user SET active_shop = %s WHERE id = %s", (shop.shop_id, shop.user_id))
    return linked


def fill_stock(owner: psycopg.Connection, shop: Shop, entry: uuid.UUID) -> None:
    """A row of the shop in each table of the stock and of the suppliers (migration 0043): a receipt of
    one item from a supplier, on credit, and a sale of part of it."""
    item, supplier, document = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    owner.execute(
        "INSERT INTO catalog_item (id, shop_id, name, name_norm, price, tracked) "
        "VALUES (%s, %s, 'Shakar', 'shakar', 12000, true)",
        (item, shop.shop_id),
    )
    owner.execute(
        "INSERT INTO catalog_barcode (shop_id, code, item_id) VALUES (%s, '4780000000014', %s)", (shop.shop_id, item)
    )
    owner.execute(
        "INSERT INTO supplier (id, shop_id, name, name_norm) VALUES (%s, %s, 'Ulgurji', 'ulgurji')",
        (supplier, shop.shop_id),
    )
    owner.execute(
        "INSERT INTO stock_document (id, shop_id, kind, number, doc_date, supplier_id, total, draft, created_by) "
        "VALUES (%s, %s, 'receipt', 1, current_date, %s, 100000, '{}'::jsonb, %s)",
        (document, shop.shop_id, supplier, shop.member_id),
    )
    owner.execute(
        "INSERT INTO stock_document_line (shop_id, document_id, line_no, item_id, qty, unit_cost, line_total) "
        "VALUES (%s, %s, 1, %s, 10, 10000, 100000)",
        (shop.shop_id, document, item),
    )
    owner.execute(
        "INSERT INTO stock_movement (id, shop_id, item_id, item_seq, kind, qty, unit_cost, cost_total, value_delta, "
        "  currency, on_hand_after, value_after, document_id, line_no, author_id) "
        "VALUES (gen_random_uuid(), %s, %s, 1, 'receipt', 10, 10000, 100000, 100000, 'UZS', 10, 100000, %s, 1, %s)",
        (shop.shop_id, item, document, shop.member_id),
    )
    owner.execute(
        "INSERT INTO stock_movement (id, shop_id, item_id, item_seq, kind, qty, cost_total, sale_total, value_delta, "
        "  currency, on_hand_after, value_after, ledger_entry_id, author_id) "
        "VALUES (gen_random_uuid(), %s, %s, 2, 'sale', -2, 20000, 24000, -20000, 'UZS', 8, 80000, %s, %s)",
        (shop.shop_id, item, entry, shop.member_id),
    )
    owner.execute(
        "UPDATE stock_document SET status = 'posted', draft = NULL, posted_by = %s, posted_at = now() WHERE id = %s",
        (shop.member_id, document),
    )
    owner.execute(
        "INSERT INTO supplier_entry (id, shop_id, supplier_id, seq, kind, amount, document_id, author_id) "
        "VALUES (gen_random_uuid(), %s, %s, 1, 'purchase', 100000, %s, %s)",
        (shop.shop_id, supplier, document, shop.member_id),
    )


def make_due(owner: psycopg.Connection, shop: Shop, interval: str = "-1 minute") -> None:
    owner.execute(
        f"UPDATE shop SET status = 'deletion_pending', deletion_due = now() + interval '{interval}' WHERE id = %s",
        (shop.shop_id,),
    )


def erase(as_worker: AppSession, shop_id: uuid.UUID) -> bool:
    with as_worker(None) as app:
        row = app.execute("SELECT erase_shop(%s)", (shop_id,)).fetchone()
    assert row is not None
    return bool(row[0])


@pytest.mark.parametrize("function", ["erase_shop(uuid)", "shops_to_erase()"])
def test_the_erasure_functions_are_for_the_worker_role_only(owner: psycopg.Connection, function: str) -> None:
    row = owner.execute(
        "SELECT has_function_privilege('qd_worker', %s, 'EXECUTE'), has_function_privilege('public', %s, 'EXECUTE'), "
        "has_function_privilege('qd_app', %s, 'EXECUTE'), has_function_privilege('qd_admin', %s, 'EXECUTE')",
        (function, function, function, function),
    ).fetchone()
    # Since migration 0031 erasure is the worker's: the ordinary application asks for a deletion and can
    # never carry one out.
    assert row == (True, False, False, False)


def test_every_table_that_names_a_shop_is_emptied_by_the_function(owner: psycopg.Connection) -> None:
    """A table added later with a shop identifier must be added to erase_shop, or this fails."""
    source = owner.execute("SELECT pg_get_functiondef('erase_shop(uuid)'::regprocedure)").fetchone()
    assert source is not None
    missing = {table for table in tables_with_a_shop(owner) if f"DELETE FROM {table} WHERE shop_id" not in source[0]}
    assert missing == set()
    assert len(tables_with_a_shop(owner)) >= 22


def test_a_shop_that_was_not_asked_to_be_deleted_cannot_be_erased(
    owner: psycopg.Connection, as_worker: AppSession, shop_a: Shop
) -> None:
    fill(owner, shop_a)
    before = rows_of(owner, shop_a.shop_id)
    assert erase(as_worker, shop_a.shop_id) is False
    assert erase(as_worker, uuid.uuid4()) is False
    assert rows_of(owner, shop_a.shop_id) == before
    assert owner.execute("SELECT status, name FROM shop WHERE id = %s", (shop_a.shop_id,)).fetchone() == (
        "active",
        "Shop A",
    )


@pytest.mark.parametrize("interval", ["1 minute", "29 days"])
def test_a_shop_inside_its_waiting_period_cannot_be_erased(
    owner: psycopg.Connection, as_worker: AppSession, shop_a: Shop, interval: str
) -> None:
    fill(owner, shop_a)
    make_due(owner, shop_a, interval)
    before = rows_of(owner, shop_a.shop_id)
    assert erase(as_worker, shop_a.shop_id) is False
    assert rows_of(owner, shop_a.shop_id) == before
    with as_worker(None) as app:
        listed = app.execute("SELECT shop_id FROM shops_to_erase()").fetchall()
    assert shop_a.shop_id not in {row[0] for row in listed}


def test_erasure_removes_everything_of_that_shop_and_nothing_of_another(
    owner: psycopg.Connection, as_worker: AppSession, shop_a: Shop, shop_b: Shop
) -> None:
    linked_a = fill(owner, shop_a)
    linked_b = fill(owner, shop_b)
    # The owner of shop A also works in shop B: that person must not be forgotten.
    owner.execute(
        "INSERT INTO membership (id, shop_id, user_id, role) VALUES (gen_random_uuid(), %s, %s, 'seller')",
        (shop_b.shop_id, shop_a.user_id),
    )
    before_b = rows_of(owner, shop_b.shop_id)
    assert all(count > 0 for table, count in rows_of(owner, shop_a.shop_id).items() if table not in UNFILLED)

    make_due(owner, shop_a)
    with as_worker(None) as app:
        listed = app.execute("SELECT shop_id, shop_name, owner_tg IS NOT NULL FROM shops_to_erase()").fetchall()
    assert (shop_a.shop_id, "Shop A", True) in listed
    assert erase(as_worker, shop_a.shop_id) is True

    assert rows_of(owner, shop_a.shop_id) == dict.fromkeys(rows_of(owner, shop_a.shop_id), 0)
    assert owner.execute(
        "SELECT status, name, deletion_due, reminders_on FROM shop WHERE id = %s", (shop_a.shop_id,)
    ).fetchone() == ("erased", "erased", None, False)
    assert rows_of(owner, shop_b.shop_id) == before_b, "another shop keeps every row"

    # The customer of A was nothing else: forgotten. The owner of A still works in B: kept, without A as active shop.
    assert owner.execute("SELECT tg_id FROM app_user WHERE id = %s", (linked_a,)).fetchone() == (None,)
    kept = owner.execute(
        "SELECT tg_id IS NOT NULL, active_shop FROM app_user WHERE id = %s", (shop_a.user_id,)
    ).fetchone()
    assert kept == (True, None)
    assert owner.execute("SELECT tg_id IS NOT NULL FROM app_user WHERE id = %s", (linked_b,)).fetchone() == (True,)

    # Done once: a second call finds nothing to erase.
    assert erase(as_worker, shop_a.shop_id) is False


def test_the_application_role_still_cannot_delete_from_the_ledger_itself(
    owner: psycopg.Connection, as_app: AppSession, as_worker: AppSession, shop_a: Shop
) -> None:
    fill(owner, shop_a)
    for table in ("ledger_entry", "goods_line", "promise", "activity"):
        for role in (as_app, as_worker):  # nor can the role that erases shops
            with pytest.raises(psycopg.errors.InsufficientPrivilege), role(shop_a.shop_id) as app:
                app.execute(f"DELETE FROM {table}")


# Tables `fill` leaves empty: they need files, administrators or imports that do not exist in this test.
UNFILLED = {"import_batch", "stored_file", "subscription_receipt", "support_access", "ownership_transfer"}

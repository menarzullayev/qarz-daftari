"""The approved schema's rules, exercised against a real PostgreSQL built by the migrations.

Ported from docs/08-technical-spec/tests/schema_checks.sql, plus the goods-line time limit, which the
specification listed as not yet verified. Each rule has a case that must be accepted and one that must
be rejected.
"""

import re
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg import errors

from ..conftest import AppSession, Shop, add_entry, add_line

pytestmark = pytest.mark.db

REPO = Path(__file__).resolve().parents[3]
TASHKENT_MIDNIGHT = "date_trunc('day', now() AT TIME ZONE 'Asia/Tashkent') AT TIME ZONE 'Asia/Tashkent'"


# --- ledger immutability (ADR-005, REQ-011, REQ-N07) -------------------------------------------------


def test_app_role_can_insert_entries(as_app: AppSession, shop_a: Shop, owner: psycopg.Connection) -> None:
    with as_app(shop_a.shop_id) as conn:
        entry = add_entry(conn, shop_a, seq=1, amount=45_000)
    row = owner.execute("SELECT amount FROM ledger_entry WHERE id = %s", (entry,)).fetchone()
    assert row == (45_000,)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE ledger_entry SET amount = 1",
        "DELETE FROM ledger_entry",
        "TRUNCATE ledger_entry",
        "UPDATE goods_line SET line_total = 1",
        "DELETE FROM goods_line",
        "UPDATE promise SET promised_date = current_date",
        "DELETE FROM promise",
        "UPDATE activity SET action = 'x'",
        "DELETE FROM activity",
    ],
)
def test_app_role_cannot_change_insert_only_tables(as_app: AppSession, shop_a: Shop, statement: str) -> None:
    with pytest.raises(errors.InsufficientPrivilege), as_app(shop_a.shop_id) as conn:
        conn.execute(statement)  # type: ignore[arg-type]


def test_entry_amount_must_be_positive(as_app: AppSession, shop_a: Shop) -> None:
    with pytest.raises(errors.CheckViolation), as_app(shop_a.shop_id) as conn:
        add_entry(conn, shop_a, seq=1, amount=0)


def test_entry_is_reversed_at_most_once(as_app: AppSession, shop_a: Shop) -> None:
    with as_app(shop_a.shop_id) as conn:
        original = add_entry(conn, shop_a, seq=1, amount=10_000)
        add_entry(conn, shop_a, seq=2, amount=10_000, kind="reversal", reverses=original)
    with pytest.raises(errors.UniqueViolation), as_app(shop_a.shop_id) as conn:
        add_entry(conn, shop_a, seq=3, amount=10_000, kind="reversal", reverses=original)


def test_reversal_must_reference_an_entry(as_app: AppSession, shop_a: Shop) -> None:
    with pytest.raises(errors.CheckViolation), as_app(shop_a.shop_id) as conn:
        add_entry(conn, shop_a, seq=1, amount=10_000, kind="reversal", reverses=None)


# --- goods lines (REQ-037, REQ-038, INV-7, INV-8) ----------------------------------------------------


def test_lines_that_sum_to_the_total_are_accepted(as_app: AppSession, shop_a: Shop, owner: psycopg.Connection) -> None:
    with as_app(shop_a.shop_id) as conn:
        entry = add_entry(conn, shop_a, seq=1, amount=45_000)
        add_line(conn, shop_a, entry, 1, 20_000)
        add_line(conn, shop_a, entry, 2, 25_000)
    count = owner.execute("SELECT count(*) FROM goods_line WHERE entry_id = %s", (entry,)).fetchone()
    assert count == (2,)


def test_lines_that_do_not_sum_to_the_total_are_rejected_at_commit(
    as_app: AppSession, shop_a: Shop, owner: psycopg.Connection
) -> None:
    entry = uuid.uuid4()
    mismatch = pytest.raises(errors.RaiseException, match="sum to 4000 but the entry total is 10000")
    with mismatch, as_app(shop_a.shop_id) as conn:
        entry = add_entry(conn, shop_a, seq=1, amount=10_000)
        add_line(conn, shop_a, entry, 1, 4_000)
    # the whole transaction, including the entry, was rolled back
    assert owner.execute("SELECT count(*) FROM ledger_entry WHERE id = %s", (entry,)).fetchone() == (0,)


def test_second_batch_of_lines_is_rejected(as_app: AppSession, shop_a: Shop) -> None:
    with as_app(shop_a.shop_id) as conn:
        entry = add_entry(conn, shop_a, seq=1, amount=20_000)
        add_line(conn, shop_a, entry, 1, 20_000)
    with pytest.raises(errors.RaiseException, match="already recorded"), as_app(shop_a.shop_id) as conn:
        add_line(conn, shop_a, entry, 2, 1_000)


def test_lines_are_only_for_credit_entries(as_app: AppSession, shop_a: Shop) -> None:
    with as_app(shop_a.shop_id) as conn:
        add_entry(conn, shop_a, seq=1, amount=20_000)
        payment = add_entry(conn, shop_a, seq=2, amount=5_000, kind="payment")
    with pytest.raises(errors.RaiseException, match="only on credit entries"), as_app(shop_a.shop_id) as conn:
        add_line(conn, shop_a, payment, 1, 5_000)


def test_lines_can_be_added_until_the_end_of_the_next_day(
    as_app: AppSession, shop_a: Shop, owner: psycopg.Connection
) -> None:
    # Sold in the last second of yesterday (Tashkent): today is "the day after the sale", still allowed.
    entry = add_entry(owner, shop_a, seq=1, amount=8_000, created_at_sql=f"{TASHKENT_MIDNIGHT} - interval '1 second'")
    with as_app(shop_a.shop_id) as conn:
        add_line(conn, shop_a, entry, 1, 8_000)
    assert owner.execute("SELECT count(*) FROM goods_line WHERE entry_id = %s", (entry,)).fetchone() == (1,)


def test_lines_are_rejected_after_the_end_of_the_next_day(
    as_app: AppSession, shop_a: Shop, owner: psycopg.Connection
) -> None:
    # Sold in the last second of the day before yesterday: the window closed at today's midnight.
    entry = add_entry(
        owner, shop_a, seq=1, amount=8_000, created_at_sql=f"{TASHKENT_MIDNIGHT} - interval '1 day 1 second'"
    )
    with (
        pytest.raises(errors.RaiseException, match="time for adding goods lines"),
        as_app(shop_a.shop_id) as conn,
    ):
        add_line(conn, shop_a, entry, 1, 8_000)


# --- tenant isolation (ADR-016, REQ-020, REQ-N12) ----------------------------------------------------

TENANT_TABLES = [
    "membership", "invitation", "catalog_item", "customer", "import_batch", "ledger_entry", "goods_line",
    "promise", "customer_link", "dispute", "payment_notice", "date_change_request", "reminder",
    "removal_request", "stored_file", "subscription", "subscription_receipt", "support_access", "activity",
    "request_key",
]  # fmt: skip


def test_a_shop_sees_only_its_own_rows(
    as_app: AppSession, shop_a: Shop, shop_b: Shop, owner: psycopg.Connection
) -> None:
    add_entry(owner, shop_a, seq=1, amount=1_000)
    add_entry(owner, shop_b, seq=1, amount=2_000)
    with as_app(shop_a.shop_id) as conn:
        assert conn.execute("SELECT id FROM shop").fetchall() == [(shop_a.shop_id,)]
        assert conn.execute("SELECT id FROM customer").fetchall() == [(shop_a.customer_id,)]
        assert conn.execute("SELECT amount FROM ledger_entry").fetchall() == [(1_000,)]
        # asking for the other shop by identifier returns nothing, not an error
        assert conn.execute("SELECT 1 FROM customer WHERE id = %s", (shop_b.customer_id,)).fetchall() == []
        assert conn.execute("SELECT 1 FROM ledger_entry WHERE shop_id = %s", (shop_b.shop_id,)).fetchall() == []


def test_a_shop_cannot_write_rows_tagged_as_another_shop(as_app: AppSession, shop_a: Shop, shop_b: Shop) -> None:
    with (
        pytest.raises(errors.InsufficientPrivilege, match="row-level security"),
        as_app(shop_a.shop_id) as conn,
    ):
        conn.execute(
            "INSERT INTO customer (id, shop_id, display_name, name_norm) VALUES (%s, %s, 'X', 'x')",
            (uuid.uuid4(), shop_b.shop_id),
        )


def test_a_shop_cannot_modify_another_shops_rows(
    as_app: AppSession, shop_a: Shop, shop_b: Shop, owner: psycopg.Connection
) -> None:
    with as_app(shop_a.shop_id) as conn:
        changed = conn.execute("UPDATE customer SET display_name = 'hacked' WHERE id = %s", (shop_b.customer_id,))
        assert changed.rowcount == 0
        deleted = conn.execute("DELETE FROM customer WHERE id = %s", (shop_b.customer_id,))
        assert deleted.rowcount == 0
    name = owner.execute("SELECT display_name FROM customer WHERE id = %s", (shop_b.customer_id,)).fetchone()
    assert name == ("Customer B",)


def test_without_a_tenant_nothing_is_visible(as_app: AppSession, shop_a: Shop) -> None:
    with as_app(None) as conn:
        for table in ["shop", *TENANT_TABLES]:
            assert conn.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,), table


def test_every_table_with_a_shop_column_is_protected(owner: psycopg.Connection) -> None:
    """A new tenant table added without row-level security must fail this test."""
    rows = owner.execute(
        """
        SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
               EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid) AS has_policy
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
        JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'shop_id' AND NOT a.attisdropped
        WHERE c.relkind = 'r'
        """
    ).fetchall()
    protected = {name for name, enabled, forced, has_policy in rows if enabled and forced and has_policy}
    with_shop_column = {name for name, *_ in rows}
    # outbox_message carries shop_id only for quota and tracing and is deliberately not tenant-scoped
    assert with_shop_column - protected == {"outbox_message"}
    assert protected == set(TENANT_TABLES)


def test_app_role_cannot_bypass_row_level_security(owner: psycopg.Connection) -> None:
    row = owner.execute("SELECT rolbypassrls, rolsuper FROM pg_roles WHERE rolname = 'qd_app'").fetchone()
    assert row == (False, False)


# --- other invariants ---------------------------------------------------------------------------------


def test_one_active_owner_per_shop(owner: psycopg.Connection, shop_a: Shop, shop_b: Shop) -> None:
    with pytest.raises(errors.UniqueViolation):
        owner.execute(
            "INSERT INTO membership (id, shop_id, user_id, role) VALUES (%s, %s, %s, 'owner')",
            (uuid.uuid4(), shop_a.shop_id, shop_b.user_id),
        )
    # a manager in the same shop is fine
    owner.execute(
        "INSERT INTO membership (id, shop_id, user_id, role) VALUES (%s, %s, %s, 'manager')",
        (uuid.uuid4(), shop_a.shop_id, shop_b.user_id),
    )


def test_one_dispute_per_entry(owner: psycopg.Connection, shop_a: Shop) -> None:
    entry = add_entry(owner, shop_a, seq=1, amount=5_000)
    insert = "INSERT INTO dispute (id, shop_id, entry_id, reason) VALUES (%s, %s, %s, %s)"
    owner.execute(insert, (uuid.uuid4(), shop_a.shop_id, entry, "wrong amount"))
    with pytest.raises(errors.UniqueViolation):
        owner.execute(insert, (uuid.uuid4(), shop_a.shop_id, entry, "again"))


def test_one_open_date_request_per_entry(owner: psycopg.Connection, shop_a: Shop) -> None:
    entry = add_entry(owner, shop_a, seq=1, amount=5_000)
    insert = (
        "INSERT INTO date_change_request (id, shop_id, entry_id, requested_date, status) "
        "VALUES (%s, %s, %s, current_date + 7, %s)"
    )
    owner.execute(insert, (uuid.uuid4(), shop_a.shop_id, entry, "declined"))
    owner.execute(insert, (uuid.uuid4(), shop_a.shop_id, entry, "open"))
    with pytest.raises(errors.UniqueViolation):
        owner.execute(insert, (uuid.uuid4(), shop_a.shop_id, entry, "open"))


def test_support_access_lasts_at_most_24_hours(owner: psycopg.Connection, shop_a: Shop) -> None:
    insert = (
        "INSERT INTO support_access (id, shop_id, admin_id, reason, ends_at) "
        "VALUES (%s, %s, %s, 'test', now() + %s::interval)"
    )
    owner.execute(insert, (uuid.uuid4(), shop_a.shop_id, shop_a.user_id, "24 hours"))
    with pytest.raises(errors.CheckViolation):
        owner.execute(insert, (uuid.uuid4(), shop_a.shop_id, shop_a.user_id, "24 hours 1 second"))


def test_an_active_link_requires_consent(owner: psycopg.Connection, shop_a: Shop) -> None:
    insert = (
        "INSERT INTO customer_link (id, shop_id, customer_id, user_id, status, consent_text_v, consent_at) "
        "VALUES (%s, %s, %s, %s, 'active', %s, %s)"
    )
    with pytest.raises(errors.CheckViolation):
        owner.execute(insert, (uuid.uuid4(), shop_a.shop_id, shop_a.customer_id, shop_a.user_id, None, None))
    owner.execute(insert, (uuid.uuid4(), shop_a.shop_id, shop_a.customer_id, shop_a.user_id, 2, "2026-10-06T10:00:00Z"))


def test_measurement_schema_holds_no_identifying_columns(owner: psycopg.Connection) -> None:
    """ADR-010 and NFR-008: nothing in the measure schema may name a person or be a tenant key."""
    columns = {
        row[0]
        for row in owner.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = 'measure'"
        ).fetchall()
    }
    assert columns, "measure schema is missing"
    forbidden = re.compile(r"(name|phone|tg_id|user_id|customer_id|shop_id|member)")
    assert [c for c in columns if forbidden.search(c)] == []


# --- the migration matches the approved specification --------------------------------------------------


def test_migration_sql_matches_the_approved_schema() -> None:
    approved = (REPO / "docs/08-technical-spec/schema.sql").read_text(encoding="utf-8")
    migration = (REPO / "backend/migrations/sql/0001_initial.sql").read_text(encoding="utf-8")
    role_in_spec = "CREATE ROLE qd_app LOGIN PASSWORD 'set-at-deploy' NOBYPASSRLS;"
    assert role_in_spec in approved
    start = migration.index("DO $$ BEGIN\n  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'qd_app')")
    end = migration.index("END $$;", start) + len("END $$;")
    # The only permitted difference: the migration creates the role without a login or password,
    # which are set at deploy.
    assert "NOLOGIN NOBYPASSRLS" in migration[start:end]
    assert migration[:start] + role_in_spec + migration[end:] == approved

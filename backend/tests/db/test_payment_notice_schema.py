"""What migration 0012 leaves in the database, and what the database itself enforces for notices and files."""

import uuid
from pathlib import Path

import psycopg
import pytest

from ..conftest import AppSession, Shop

pytestmark = pytest.mark.db

MIGRATION = Path(__file__).resolve().parents[2] / "migrations" / "sql" / "0012_payment_notices.sql"
KINDS_BEFORE = {"shop_name", "entry", "promise_date", "consent", "dispute", "decline"}
KINDS_ADDED = {"notice", "notice_decline"}


def _allowed_kinds(conn: psycopg.Connection) -> set[str]:
    row = conn.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'chat_pending_kind_check'"
    ).fetchone()
    assert row is not None
    definition = str(row[0])
    return set(definition.split("'")[1::2])


def _pending(conn: psycopg.Connection, user_id: uuid.UUID, kind: str) -> None:
    conn.execute(
        "INSERT INTO chat_pending (id, user_id, kind, payload, expires_at) "
        "VALUES (%s, %s, %s, '{}', now() + interval '10 minutes')",
        (uuid.uuid4(), user_id, kind),
    )


def test_the_new_kinds_of_chat_question_are_allowed_and_every_earlier_kind_still_is(
    owner: psycopg.Connection, shop_a: Shop
) -> None:
    assert _allowed_kinds(owner) >= KINDS_BEFORE | KINDS_ADDED
    for kind in sorted(KINDS_BEFORE | KINDS_ADDED):
        _pending(owner, shop_a.user_id, kind)
    for kind in ("nonsense", "NOTICE", "", "notice "):
        with pytest.raises(psycopg.errors.CheckViolation):
            _pending(owner, shop_a.user_id, kind)


def test_the_migration_keeps_a_kind_that_another_migration_added_before_it(owner: psycopg.Connection) -> None:
    """It extends the list it finds, so it does not matter which migration ran first."""
    block = MIGRATION.read_text(encoding="utf-8")
    block = block[block.index("DO $$") : block.index("END $$;") + len("END $$;")]
    with owner.transaction():
        # Rows of kinds the stand-in list below leaves out would not pass it; all of this is rolled back.
        owner.execute("DELETE FROM chat_pending")
        owner.execute("ALTER TABLE chat_pending DROP CONSTRAINT chat_pending_kind_check")
        owner.execute(
            "ALTER TABLE chat_pending ADD CONSTRAINT chat_pending_kind_check "
            "CHECK (kind IN ('shop_name', 'entry', 'added_by_another_migration'))"
        )
        assert _allowed_kinds(owner) == {"shop_name", "entry", "added_by_another_migration"}
        owner.execute(block)
        assert _allowed_kinds(owner) == {"shop_name", "entry", "added_by_another_migration", *KINDS_ADDED}
        owner.execute(block)  # running it twice changes nothing more
        assert _allowed_kinds(owner) == {"shop_name", "entry", "added_by_another_migration", *KINDS_ADDED}
        raise psycopg.Rollback()
    assert "added_by_another_migration" not in _allowed_kinds(owner)


def _file(conn: psycopg.Connection, shop: Shop, key: str) -> uuid.UUID:
    file_id = uuid.uuid4()
    conn.execute(
        "INSERT INTO stored_file (id, shop_id, purpose, object_key, sha256, size_bytes, mime) "
        "VALUES (%s, %s, 'payment_notice', %s, %s, 3, 'image/png')",
        (file_id, shop.shop_id, key, b"\x00" * 32),
    )
    return file_id


def _notice(conn: psycopg.Connection, shop: Shop, file_id: uuid.UUID | None) -> uuid.UUID:
    notice_id = uuid.uuid4()
    conn.execute(
        "INSERT INTO payment_notice (id, shop_id, customer_id, amount, file_id) VALUES (%s, %s, %s, 1000, %s)",
        (notice_id, shop.shop_id, shop.customer_id, file_id),
    )
    return notice_id


def test_a_receipt_belongs_to_one_notice(owner: psycopg.Connection, shop_a: Shop) -> None:
    file_id = _file(owner, shop_a, "aa/one")
    _notice(owner, shop_a, file_id)
    _notice(owner, shop_a, None)
    _notice(owner, shop_a, None)  # any number of notices without a receipt
    with pytest.raises(psycopg.errors.UniqueViolation):
        _notice(owner, shop_a, file_id)


def test_files_and_notices_of_one_shop_are_invisible_and_untouchable_from_another(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop, shop_b: Shop
) -> None:
    file_id = _file(owner, shop_a, "aa/two")
    notice_id = _notice(owner, shop_a, file_id)

    with as_app(shop_a.shop_id) as conn:
        assert conn.execute("SELECT count(*) FROM stored_file WHERE id = %s", (file_id,)).fetchone() == (1,)
        assert conn.execute("SELECT count(*) FROM payment_notice WHERE id = %s", (notice_id,)).fetchone() == (1,)
    for other in (shop_b.shop_id, None):
        with as_app(other) as conn:
            assert conn.execute("SELECT count(*) FROM stored_file").fetchone() == (0,)
            assert conn.execute("SELECT count(*) FROM payment_notice").fetchone() == (0,)
            assert conn.execute("DELETE FROM stored_file WHERE id = %s", (file_id,)).rowcount == 0
            changed = conn.execute("UPDATE stored_file SET delete_after = now() WHERE id = %s", (file_id,)).rowcount
            assert changed == 0
            closed = conn.execute("UPDATE payment_notice SET status = 'declined' WHERE id = %s", (notice_id,)).rowcount
            assert closed == 0
    # A row cannot be written into another shop either.
    with pytest.raises(psycopg.errors.InsufficientPrivilege), as_app(shop_b.shop_id) as conn:
        _file(conn, shop_a, "aa/three")
    assert owner.execute("SELECT delete_after FROM stored_file WHERE id = %s", (file_id,)).fetchone() == (None,)
    assert owner.execute("SELECT status FROM payment_notice WHERE id = %s", (notice_id,)).fetchone() == ("sent",)


def test_an_accepted_notice_always_names_its_payment_and_no_other_does(owner: psycopg.Connection, shop_a: Shop) -> None:
    notice_id = _notice(owner, shop_a, None)
    with pytest.raises(psycopg.errors.CheckViolation):
        owner.execute("UPDATE payment_notice SET status = 'accepted' WHERE id = %s", (notice_id,))
    for amount in (0, -5):
        with pytest.raises(psycopg.errors.CheckViolation):
            owner.execute("UPDATE payment_notice SET amount = %s WHERE id = %s", (amount, notice_id))

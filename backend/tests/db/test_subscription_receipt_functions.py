"""Subscription receipts in the database, used directly as the application role (migration 0024).

A shop sends receipts and reads its own; it can never decide one, and it learns nothing of another
shop's. The administrator's functions run with their owner's rights and do nothing for anyone who is not
an active administrator.
"""

import hashlib
import uuid
from typing import Any

import psycopg
import pytest
from psycopg import errors

from ..conftest import AppSession, Shop

pytestmark = pytest.mark.db

FUNCTIONS = [
    "subscription_receipt_copies(uuid)",
    "admin_receipts(uuid, text, timestamptz, uuid, integer)",
    "admin_receipt(uuid, uuid, boolean)",
    "admin_receipt_copies(uuid, uuid)",
    "admin_decide_receipt(uuid, uuid, text, smallint, text, timestamptz)",
    "admin_shop_activity(uuid, uuid, text, uuid)",
    "oldest_waiting_receipt()",
]


def _admin(owner: psycopg.Connection, status: str = "active") -> uuid.UUID:
    user_id = uuid.uuid4()
    owner.execute("INSERT INTO app_user (id, tg_id) VALUES (%s, %s)", (user_id, uuid.uuid4().int % 10**15))
    owner.execute(
        "INSERT INTO admin_account (user_id, totp_secret, status) VALUES (%s, %s, %s)", (user_id, b"sealed", status)
    )
    return user_id


def _receipt(
    owner: psycopg.Connection, shop: Shop, content: bytes, *, purpose: str = "subscription_receipt"
) -> uuid.UUID:
    """A receipt with a file of the given content, written as the migration owner."""
    file_id, receipt_id = uuid.uuid4(), uuid.uuid4()
    owner.execute(
        "INSERT INTO stored_file (id, shop_id, purpose, object_key, sha256, size_bytes, mime) "
        "VALUES (%s, %s, %s, %s, %s, %s, 'image/jpeg')",
        (file_id, shop.shop_id, purpose, f"aa/{uuid.uuid4().hex * 2}", hashlib.sha256(content).digest(), len(content)),
    )
    if purpose == "subscription_receipt":
        owner.execute(
            "INSERT INTO subscription_receipt (id, shop_id, file_id, stated_amount, stated_months) "
            "VALUES (%s, %s, %s, 100000, 1)",
            (receipt_id, shop.shop_id, file_id),
        )
    return receipt_id


def _file_of(owner: psycopg.Connection, receipt: uuid.UUID) -> Any:
    row = owner.execute("SELECT file_id FROM subscription_receipt WHERE id = %s", (receipt,)).fetchone()
    assert row is not None
    return row[0]


def test_a_shop_adds_and_reads_its_receipts_and_can_never_change_or_remove_one(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop, shop_b: Shop
) -> None:
    mine, theirs = _receipt(owner, shop_a, uuid.uuid4().bytes), _receipt(owner, shop_b, uuid.uuid4().bytes)
    with as_app(shop_a.shop_id) as conn:
        assert conn.execute("SELECT id FROM subscription_receipt").fetchall() == [(mine,)]
        conn.execute(
            "INSERT INTO subscription_receipt (id, shop_id, stated_amount, stated_months) VALUES (%s, %s, 5000, 2)",
            (uuid.uuid4(), shop_a.shop_id),
        )
    for statement in (
        "UPDATE subscription_receipt SET status = 'approved', months = 12 WHERE id = %s",
        "UPDATE subscription_receipt SET stated_amount = 1 WHERE id = %s",
        "DELETE FROM subscription_receipt WHERE id = %s",
    ):
        for target in (mine, theirs):
            with pytest.raises(errors.InsufficientPrivilege), as_app(shop_a.shop_id) as conn:
                conn.execute(statement, (target,))
    with pytest.raises(errors.InsufficientPrivilege), as_app(shop_a.shop_id) as conn:
        conn.execute("TRUNCATE subscription_receipt")
    # Nor can it write a receipt into another shop.
    with pytest.raises(errors.InsufficientPrivilege), as_app(shop_a.shop_id) as conn:
        conn.execute(
            "INSERT INTO subscription_receipt (id, shop_id, stated_amount) VALUES (%s, %s, 5000)",
            (uuid.uuid4(), shop_b.shop_id),
        )
    assert owner.execute("SELECT status FROM subscription_receipt WHERE id IN (%s, %s)", (mine, theirs)).fetchall() == [
        ("submitted",),
        ("submitted",),
    ]


@pytest.mark.parametrize("months", [0, 37, -1])
def test_stated_months_are_between_one_and_thirty_six(owner: psycopg.Connection, shop_a: Shop, months: int) -> None:
    with pytest.raises(errors.CheckViolation):
        owner.execute(
            "INSERT INTO subscription_receipt (id, shop_id, stated_amount, stated_months) VALUES (%s, %s, 5000, %s)",
            (uuid.uuid4(), shop_a.shop_id, months),
        )


def test_a_file_belongs_to_one_receipt_and_deleting_it_leaves_the_receipt(
    owner: psycopg.Connection, shop_a: Shop
) -> None:
    receipt = _receipt(owner, shop_a, uuid.uuid4().bytes)
    file_id = _file_of(owner, receipt)
    with pytest.raises(errors.UniqueViolation):
        owner.execute(
            "INSERT INTO subscription_receipt (id, shop_id, file_id, stated_amount) VALUES (%s, %s, %s, 5000)",
            (uuid.uuid4(), shop_a.shop_id, file_id),
        )
    owner.execute("DELETE FROM stored_file WHERE id = %s", (file_id,))
    assert owner.execute("SELECT file_id, status FROM subscription_receipt WHERE id = %s", (receipt,)).fetchone() == (
        None,
        "submitted",
    )


def test_a_shop_learns_how_many_copies_of_its_own_receipt_exist_and_nothing_else(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop, shop_b: Shop
) -> None:
    content, other = uuid.uuid4().bytes, uuid.uuid4().bytes
    mine = _file_of(owner, _receipt(owner, shop_a, content))
    theirs = _file_of(owner, _receipt(owner, shop_b, content))
    _receipt(owner, shop_b, content)
    lonely = _file_of(owner, _receipt(owner, shop_a, other))
    # The same content kept for another purpose is not a subscription receipt and is not counted.
    _receipt(owner, shop_b, content, purpose="payment_notice")

    def copies(shop: Shop | None, file_id: Any) -> Any:
        with as_app(None if shop is None else shop.shop_id) as conn:
            row = conn.execute("SELECT subscription_receipt_copies(%s)", (file_id,)).fetchone()
        assert row is not None
        return row[0]

    assert copies(shop_a, mine) == 2
    assert copies(shop_b, theirs) == 2
    assert copies(shop_a, lonely) == 0
    # Asked about a file that is not its own, a shop is told nothing; so is a caller with no shop.
    assert copies(shop_a, theirs) == 0
    assert copies(None, mine) == 0
    assert copies(shop_a, uuid.uuid4()) == 0


def test_the_administrators_functions_do_nothing_for_anyone_else(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop
) -> None:
    receipt = _receipt(owner, shop_a, uuid.uuid4().bytes)
    admin, disabled, nobody = _admin(owner), _admin(owner, "disabled"), uuid.uuid4()
    for caller in (disabled, nobody, shop_a.user_id):
        for tenant in (None, shop_a.shop_id):
            with as_app(tenant) as conn:
                listed = conn.execute(
                    "SELECT receipt_id FROM admin_receipts(%s, 'submitted', NULL, NULL, 100)", (caller,)
                ).fetchall()
                assert listed == []
                assert conn.execute("SELECT * FROM admin_receipt(%s, %s, false)", (caller, receipt)).fetchall() == []
                assert conn.execute("SELECT * FROM admin_receipt(%s, %s, true)", (caller, receipt)).fetchall() == []
                assert conn.execute("SELECT * FROM admin_receipt_copies(%s, %s)", (caller, receipt)).fetchall() == []
                assert conn.execute(
                    "SELECT admin_decide_receipt(%s, %s, 'approved', 1::smallint, NULL, now())", (caller, receipt)
                ).fetchone() == (False,)
                assert conn.execute(
                    "SELECT admin_shop_activity(%s, %s, 'subscription.receipt_approved', %s)",
                    (caller, shop_a.shop_id, receipt),
                ).fetchone() == (False,)
    assert owner.execute(
        "SELECT status, decided_by FROM subscription_receipt WHERE id = %s", (receipt,)
    ).fetchone() == (
        "submitted",
        None,
    )
    assert owner.execute(
        "SELECT count(*) FROM activity WHERE shop_id = %s AND actor_kind = 'admin'", (shop_a.shop_id,)
    ).fetchone() == (0,)

    with as_app(None) as conn:
        seen = conn.execute(
            "SELECT receipt_id, shop_name, has_file FROM admin_receipts(%s, 'submitted', NULL, NULL, 100)", (admin,)
        ).fetchall()
        assert (receipt, "Shop A", True) in seen
        assert conn.execute(
            "SELECT admin_decide_receipt(%s, %s, 'rejected', NULL, 'Soxta chek', now())", (admin, receipt)
        ).fetchone() == (True,)
        # Decided: neither a second decision nor a different one changes it.
        for status, months in (("approved", 3), ("rejected", None)):
            assert conn.execute(
                "SELECT admin_decide_receipt(%s, %s, %s, %s::smallint, 'yana', now())", (admin, receipt, status, months)
            ).fetchone() == (False,)
    assert owner.execute(
        "SELECT status, months, reject_reason, decided_by FROM subscription_receipt WHERE id = %s", (receipt,)
    ).fetchone() == ("rejected", None, "Soxta chek", admin)


def test_a_decision_is_approved_or_rejected_and_an_approval_carries_months(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop
) -> None:
    receipt, admin = _receipt(owner, shop_a, uuid.uuid4().bytes), _admin(owner)
    with pytest.raises(errors.RaiseException), as_app(None) as conn:
        conn.execute("SELECT admin_decide_receipt(%s, %s, 'submitted', NULL, NULL, now())", (admin, receipt))
    with pytest.raises(errors.CheckViolation), as_app(None) as conn:
        conn.execute("SELECT admin_decide_receipt(%s, %s, 'approved', NULL, NULL, now())", (admin, receipt))
    with as_app(None) as conn:
        assert conn.execute(
            "SELECT admin_decide_receipt(%s, %s, 'approved', 2::smallint, 'ignored for an approval', now())",
            (admin, receipt),
        ).fetchone() == (True,)
    assert owner.execute(
        "SELECT status, months, reject_reason FROM subscription_receipt WHERE id = %s", (receipt,)
    ).fetchone() == ("approved", 2, None)


def test_the_oldest_waiting_receipt_is_a_time_and_nothing_more(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop
) -> None:
    owner.execute(
        "UPDATE subscription_receipt SET status = 'rejected', reject_reason = 'test' WHERE status = 'submitted'"
    )
    with as_app(None) as conn:
        assert conn.execute("SELECT oldest_waiting_receipt()").fetchone() == (None,)
    old, new = _receipt(owner, shop_a, uuid.uuid4().bytes), _receipt(owner, shop_a, uuid.uuid4().bytes)
    owner.execute("UPDATE subscription_receipt SET created_at = now() - interval '3 days' WHERE id = %s", (old,))
    expected = owner.execute("SELECT created_at FROM subscription_receipt WHERE id = %s", (old,)).fetchone()
    with as_app(None) as conn:
        assert conn.execute("SELECT oldest_waiting_receipt()").fetchone() == expected
    # Once it is decided the next one is the oldest.
    owner.execute("UPDATE subscription_receipt SET status = 'rejected', reject_reason = 'x' WHERE id = %s", (old,))
    newer = owner.execute("SELECT created_at FROM subscription_receipt WHERE id = %s", (new,)).fetchone()
    with as_app(shop_a.shop_id) as conn:
        assert conn.execute("SELECT oldest_waiting_receipt()").fetchone() == newer
    owner.execute("UPDATE subscription_receipt SET status = 'rejected', reject_reason = 'x' WHERE id = %s", (new,))


@pytest.mark.parametrize("signature", FUNCTIONS)
def test_each_function_runs_as_its_owner_with_a_pinned_search_path_and_is_closed_to_public(
    owner: psycopg.Connection, signature: str
) -> None:
    row = owner.execute(
        "SELECT prosecdef, proconfig, has_function_privilege('qd_app', oid, 'EXECUTE'), "
        "has_function_privilege('public', oid, 'EXECUTE') FROM pg_proc WHERE oid = %s::regprocedure",
        (signature,),
    ).fetchone()
    assert row == (True, ["search_path=public, pg_temp"], True, False)

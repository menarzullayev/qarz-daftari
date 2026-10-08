"""Subscription receipts in the database, used directly as the application role (migration 0024).

A shop sends receipts and reads its own; it can never decide one, and it learns nothing of another
shop's. The administrator's functions run with their owner's rights and do nothing for anyone who is not
an active administrator.
"""

import hashlib
import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from psycopg import errors

from ..conftest import AppSession, Shop

pytestmark = pytest.mark.db

# One of the three application roles may call it. Which one is listed, function by function, in
# tests/db/test_database_roles.py.
_SOME_ROLE = (
    "(has_function_privilege('qd_app', oid, 'EXECUTE') OR has_function_privilege('qd_admin', oid, 'EXECUTE') "
    "OR has_function_privilege('qd_worker', oid, 'EXECUTE'))"
)

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
    owner: psycopg.Connection, as_admin: AppSession, shop_a: Shop
) -> None:
    content = uuid.uuid4().bytes
    receipt, copy = _receipt(owner, shop_a, content), _receipt(owner, shop_a, content)
    admin, disabled, nobody = _admin(owner), _admin(owner, "disabled"), uuid.uuid4()
    for caller in (disabled, nobody, shop_a.user_id):
        for tenant in (None, shop_a.shop_id):
            with as_admin(tenant) as conn:
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

    with as_admin(None) as conn:
        copies = conn.execute("SELECT receipt_id FROM admin_receipt_copies(%s, %s)", (admin, receipt)).fetchall()
        assert copies == [(copy,)]
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
    owner: psycopg.Connection, as_admin: AppSession, shop_a: Shop
) -> None:
    receipt, admin = _receipt(owner, shop_a, uuid.uuid4().bytes), _admin(owner)
    with pytest.raises(errors.RaiseException), as_admin(None) as conn:
        conn.execute("SELECT admin_decide_receipt(%s, %s, 'submitted', NULL, NULL, now())", (admin, receipt))
    with pytest.raises(errors.CheckViolation), as_admin(None) as conn:
        conn.execute("SELECT admin_decide_receipt(%s, %s, 'approved', NULL, NULL, now())", (admin, receipt))
    with as_admin(None) as conn:
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
        f"SELECT prosecdef, proconfig, {_SOME_ROLE}, "
        "has_function_privilege('public', oid, 'EXECUTE') FROM pg_proc WHERE oid = %s::regprocedure",
        (signature,),
    ).fetchone()
    assert row == (True, ["search_path=public, pg_temp"], True, False)


# --- decisions from the review group (migration 0030; DEC-064) ----------------------------------------------

GROUP_FUNCTIONS = [
    "review_group_receipt(bigint, uuid, boolean)",
    "review_group_decide_receipt(bigint, bigint, uuid, text, smallint, text, text, date, text, jsonb, timestamptz)",
]
_GROUP_DECIDE = (
    "SELECT review_group_decide_receipt("
    "%s, %s, %s, %s, %s::smallint, %s, %s, %s::date, NULL, jsonb_build_object('via', 'group'), now())"
)
DECIDER = 5_550_001


@pytest.fixture
def review_group(owner: psycopg.Connection) -> Iterator[int]:
    """A configured review group; the setting is global, so it is taken away again."""
    group = -1_000_000_000_000 - uuid.uuid4().int % 10**9
    owner.execute(
        "INSERT INTO platform_setting (key, value, updated_by) VALUES ('review_group', to_jsonb(%s::bigint), 'test') "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_by = EXCLUDED.updated_by",
        (group,),
    )
    yield group
    owner.execute("DELETE FROM platform_setting WHERE key = 'review_group'")


def _with_subscription(owner: psycopg.Connection, shop: Shop) -> None:
    owner.execute(
        "INSERT INTO subscription (shop_id, state) VALUES (%s, 'limited') "
        "ON CONFLICT (shop_id) DO UPDATE SET state = 'limited', paid_through = NULL, prior_state = NULL",
        (shop.shop_id,),
    )


def _decision(owner: psycopg.Connection, receipt: uuid.UUID) -> Any:
    return owner.execute(
        "SELECT status, months, reject_reason, decided_by, decided_by_tg FROM subscription_receipt WHERE id = %s",
        (receipt,),
    ).fetchone()


def _audit_of(owner: psycopg.Connection, receipt: uuid.UUID) -> list[tuple[Any, ...]]:
    return owner.execute(
        "SELECT action, admin_id, actor_tg, target_type, target_shop, reason, detail FROM admin_audit "
        "WHERE target_id = %s ORDER BY at, id",
        (str(receipt),),
    ).fetchall()


def test_a_review_group_decision_is_written_with_the_telegram_identifier_and_no_administrator(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop, review_group: int
) -> None:
    _with_subscription(owner, shop_a)
    approved, rejected = _receipt(owner, shop_a, uuid.uuid4().bytes), _receipt(owner, shop_a, uuid.uuid4().bytes)
    with as_app(None) as conn:
        seen = conn.execute(
            "SELECT receipt_id, shop_id, shop_name, stated_amount, stated_months, status, state, paid_through "
            "FROM review_group_receipt(%s, %s, true)",
            (review_group, approved),
        ).fetchall()
        assert seen == [(approved, shop_a.shop_id, "Shop A", 100000, 1, "submitted", "limited", None)]
        assert conn.execute(
            _GROUP_DECIDE, (review_group, DECIDER, approved, "approved", 1, None, "active", "2027-01-31")
        ).fetchone() == (True,)
        assert conn.execute(
            _GROUP_DECIDE, (review_group, DECIDER, rejected, "rejected", None, "Soxta chek", None, None)
        ).fetchone() == (True,)
        # Decided: neither a second decision nor a different one changes it.
        for status, months in (("approved", 3), ("rejected", None)):
            assert conn.execute(
                _GROUP_DECIDE, (review_group, DECIDER + 1, approved, status, months, "yana", "active", "2030-01-01")
            ).fetchone() == (False,)
    assert _decision(owner, approved) == ("approved", 1, None, None, DECIDER)
    assert _decision(owner, rejected) == ("rejected", None, "Soxta chek", None, DECIDER)
    state = owner.execute(
        "SELECT state, paid_through::text, prior_state FROM subscription WHERE shop_id = %s", (shop_a.shop_id,)
    ).fetchone()
    assert state == ("active", "2027-01-31", None), "only the approval touched the subscription, and once"
    assert _audit_of(owner, approved) == [
        (
            "subscription.receipt_approved",
            None,
            DECIDER,
            "receipt",
            shop_a.shop_id,
            None,
            {"via": "group", "group": review_group, "stated_amount": 100000, "stated_months": 1},
        )
    ]
    assert [row[:3] + row[5:6] for row in _audit_of(owner, rejected)] == [
        ("subscription.receipt_rejected", None, DECIDER, "Soxta chek")
    ]
    lines = owner.execute(
        "SELECT actor_kind, actor_id, action, subject_id FROM activity WHERE shop_id = %s AND subject_id = ANY(%s) "
        "ORDER BY action",
        (shop_a.shop_id, [approved, rejected]),
    ).fetchall()
    assert lines == [
        ("admin", None, "subscription.receipt_approved", approved),
        ("admin", None, "subscription.receipt_rejected", rejected),
    ]


@pytest.mark.parametrize("wrong", ["another group", "no group given", "no decider", "decider zero", "decider negative"])
def test_the_review_group_functions_do_nothing_for_another_group_or_without_a_decider(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop, review_group: int, wrong: str
) -> None:
    _with_subscription(owner, shop_a)
    receipt = _receipt(owner, shop_a, uuid.uuid4().bytes)
    group: int | None = {"another group": review_group - 1, "no group given": None}.get(wrong, review_group)
    decider: int | None = {"no decider": None, "decider zero": 0, "decider negative": -DECIDER}.get(wrong, DECIDER)
    with as_app(None) as conn:
        if group != review_group:
            for lock in (False, True):
                read = conn.execute("SELECT * FROM review_group_receipt(%s, %s, %s)", (group, receipt, lock))
                assert read.fetchall() == []
        for status, months in (("approved", 1), ("rejected", None)):
            assert conn.execute(
                _GROUP_DECIDE, (group, decider, receipt, status, months, "sabab", "active", "2030-01-01")
            ).fetchone() == (False,)
    assert _decision(owner, receipt) == ("submitted", None, None, None, None)
    assert _audit_of(owner, receipt) == []
    assert owner.execute(
        "SELECT state, paid_through FROM subscription WHERE shop_id = %s", (shop_a.shop_id,)
    ).fetchone() == ("limited", None)


def test_without_a_configured_review_group_nothing_can_be_decided_from_a_group(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop, review_group: int
) -> None:
    _with_subscription(owner, shop_a)
    receipt = _receipt(owner, shop_a, uuid.uuid4().bytes)
    for stored in (None, f'"{review_group}"', "true", "null"):
        if stored is None:
            owner.execute("DELETE FROM platform_setting WHERE key = 'review_group'")
        else:
            owner.execute(
                "INSERT INTO platform_setting (key, value, updated_by) VALUES ('review_group', %s::jsonb, 'test') "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                (stored,),
            )
        with as_app(None) as conn:
            read = conn.execute("SELECT * FROM review_group_receipt(%s, %s, false)", (review_group, receipt))
            assert read.fetchall() == [], stored
            assert conn.execute(
                _GROUP_DECIDE, (review_group, DECIDER, receipt, "approved", 1, None, "active", "2030-01-01")
            ).fetchone() == (False,), stored
    assert _decision(owner, receipt) == ("submitted", None, None, None, None)


def test_a_receipt_of_an_erased_shop_is_not_decided_from_the_group(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop, review_group: int
) -> None:
    _with_subscription(owner, shop_a)
    receipt = _receipt(owner, shop_a, uuid.uuid4().bytes)
    owner.execute("UPDATE shop SET status = 'erased' WHERE id = %s", (shop_a.shop_id,))
    try:
        with as_app(None) as conn:
            read = conn.execute("SELECT * FROM review_group_receipt(%s, %s, true)", (review_group, receipt))
            assert read.fetchall() == []
            assert conn.execute(
                _GROUP_DECIDE, (review_group, DECIDER, receipt, "approved", 1, None, "active", "2030-01-01")
            ).fetchone() == (False,)
        assert _decision(owner, receipt) == ("submitted", None, None, None, None)
    finally:
        owner.execute("UPDATE shop SET status = 'active' WHERE id = %s", (shop_a.shop_id,))


def test_a_group_decision_is_approved_or_rejected_and_an_approval_carries_months(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop, review_group: int
) -> None:
    _with_subscription(owner, shop_a)
    receipt = _receipt(owner, shop_a, uuid.uuid4().bytes)
    with pytest.raises(errors.RaiseException), as_app(None) as conn:
        conn.execute(_GROUP_DECIDE, (review_group, DECIDER, receipt, "submitted", None, None, None, None))
    with pytest.raises(errors.CheckViolation), as_app(None) as conn:
        conn.execute(_GROUP_DECIDE, (review_group, DECIDER, receipt, "approved", None, None, "active", "2030-01-01"))
    assert _decision(owner, receipt) == ("submitted", None, None, None, None)
    assert _audit_of(owner, receipt) == [], "a decision that failed left no audit row behind"


def test_a_receipt_has_one_decider_and_an_audit_row_exactly_one_actor(owner: psycopg.Connection, shop_a: Shop) -> None:
    receipt, admin = _receipt(owner, shop_a, uuid.uuid4().bytes), _admin(owner)
    for by_admin, by_tg in ((admin, DECIDER), (None, 0), (None, -1)):
        with pytest.raises(errors.CheckViolation), owner.transaction():
            owner.execute(
                "UPDATE subscription_receipt SET decided_by = %s, decided_by_tg = %s WHERE id = %s",
                (by_admin, by_tg, receipt),
            )
    audit = (
        "INSERT INTO admin_audit (id, admin_id, actor_tg, action, target_type) "
        "VALUES (gen_random_uuid(), %s, %s, 'test.checked', 'receipt')"
    )
    for by_admin, by_tg in ((None, None), (admin, DECIDER), (None, 0), (None, -DECIDER)):
        with pytest.raises(errors.CheckViolation), owner.transaction():
            owner.execute(audit, (by_admin, by_tg))
    with owner.transaction(force_rollback=True):
        owner.execute(audit, (admin, None))
        owner.execute(audit, (None, DECIDER))


def test_a_receipt_of_a_shop_without_a_subscription_row_is_read_and_can_be_rejected_but_not_approved(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop, review_group: int
) -> None:
    owner.execute("DELETE FROM subscription WHERE shop_id = %s", (shop_a.shop_id,))
    first, second = _receipt(owner, shop_a, uuid.uuid4().bytes), _receipt(owner, shop_a, uuid.uuid4().bytes)
    with as_app(None) as conn:
        seen = conn.execute(
            "SELECT status, state, paid_through FROM review_group_receipt(%s, %s, true)", (review_group, first)
        ).fetchall()
        assert seen == [("submitted", None, None)]
        assert conn.execute(
            _GROUP_DECIDE, (review_group, DECIDER, first, "rejected", None, "Soxta chek", None, None)
        ).fetchone() == (True,)
    with pytest.raises(errors.RaiseException), as_app(None) as conn:
        conn.execute(_GROUP_DECIDE, (review_group, DECIDER, second, "approved", 1, None, "active", "2030-01-01"))
    assert _decision(owner, first)[0] == "rejected"
    assert _decision(owner, second) == ("submitted", None, None, None, None), "the failed approval left nothing"
    assert _audit_of(owner, second) == []


@pytest.mark.parametrize("signature", GROUP_FUNCTIONS)
def test_the_review_group_functions_run_as_their_owner_and_are_closed_to_public(
    owner: psycopg.Connection, signature: str
) -> None:
    row = owner.execute(
        f"SELECT prosecdef, proconfig, {_SOME_ROLE}, "
        "has_function_privilege('public', oid, 'EXECUTE') FROM pg_proc WHERE oid = %s::regprocedure",
        (signature,),
    ).fetchone()
    assert row == (True, ["search_path=public, pg_temp"], True, False)

"""The customer-side database functions, called directly as the application role (migration 0008).

They run with their owner's rights and cross the tenant boundary on purpose, so each must act only on
the user it is given. The chat checks the same things first; these tests remove the chat from the picture.
"""

import hashlib
import uuid
from typing import Any

import psycopg
import pytest

from ..conftest import AppSession, Shop

pytestmark = pytest.mark.db

FUNCTIONS = [
    "customer_token_info(bytea)",
    "link_customer(bytea, uuid, smallint, text)",
    "my_accounts(uuid)",
    "end_my_link(uuid, uuid)",
    "mark_recipient_reachable(uuid)",
]


def _user(owner: psycopg.Connection) -> uuid.UUID:
    user_id = uuid.uuid4()
    owner.execute("INSERT INTO app_user (id, tg_id) VALUES (%s, %s)", (user_id, uuid.uuid4().int % 10**15))
    return user_id


def _link(owner: psycopg.Connection, shop: Shop, user: uuid.UUID, status: str = "active") -> uuid.UUID:
    link_id = uuid.uuid4()
    owner.execute(
        "INSERT INTO customer_link (id, shop_id, customer_id, user_id, status, consent_text_v, consent_at) "
        "VALUES (%s, %s, %s, %s, %s, 2, now())",
        (link_id, shop.shop_id, shop.customer_id, user, status),
    )
    return link_id


def _code(owner: psycopg.Connection, shop: Shop, kind: str, **columns: Any) -> bytes:
    digest = hashlib.sha256(uuid.uuid4().bytes).digest()
    customer = shop.customer_id if kind == "customer" else None
    role = "seller" if kind == "staff" else None
    owner.execute(
        "INSERT INTO invitation (token_hash, shop_id, kind, customer_id, role, status, expires_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (digest, shop.shop_id, kind, customer, role, columns.get("status", "issued"), columns.get("expires_at")),
    )
    return digest


def _status(owner: psycopg.Connection, link_id: uuid.UUID) -> Any:
    row = owner.execute("SELECT status FROM customer_link WHERE id = %s", (link_id,)).fetchone()
    assert row is not None
    return row[0]


@pytest.mark.parametrize("function", FUNCTIONS)
def test_only_the_application_role_may_call_them(owner: psycopg.Connection, function: str) -> None:
    row = owner.execute(
        "SELECT has_function_privilege('qd_app', %s, 'EXECUTE'), has_function_privilege('public', %s, 'EXECUTE'), "
        "(SELECT prosecdef FROM pg_proc WHERE oid = %s::regprocedure)",
        (function, function, function),
    ).fetchone()
    assert row == (True, False, True)


def test_the_application_cannot_read_links_without_a_tenant_but_a_person_gets_their_own_accounts(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop, shop_b: Shop
) -> None:
    me, other = _user(owner), _user(owner)
    _link(owner, shop_a, me)
    _link(owner, shop_b, other)
    owner.execute(
        "INSERT INTO ledger_entry (id, shop_id, customer_id, seq, kind, amount, author_id) "
        "VALUES (gen_random_uuid(), %s, %s, 1, 'credit', 40000, %s), (gen_random_uuid(), %s, %s, "
        "1, 'credit', 90000, %s)",
        (shop_a.shop_id, shop_a.customer_id, shop_a.member_id, shop_b.shop_id, shop_b.customer_id, shop_b.member_id),
    )
    with as_app(None) as app:
        assert app.execute("SELECT count(*) FROM customer_link").fetchone() == (0,)
        mine = app.execute("SELECT shop_id, customer_id, balance FROM my_accounts(%s)", (me,)).fetchall()
        theirs = app.execute("SELECT shop_id, balance FROM my_accounts(%s)", (other,)).fetchall()
        nobody = app.execute("SELECT count(*) FROM my_accounts(%s)", (uuid.uuid4(),)).fetchone()
    assert mine == [(shop_a.shop_id, shop_a.customer_id, 40000)]
    assert theirs == [(shop_b.shop_id, 90000)]
    assert nobody == (0,)


@pytest.mark.parametrize("status", ["waiting", "ended"])
def test_a_link_that_is_not_live_is_not_an_account(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop, status: str
) -> None:
    me = _user(owner)
    link_id = _link(owner, shop_a, me, "active")
    owner.execute("UPDATE customer_link SET status = %s WHERE id = %s", (status, link_id))
    with as_app(None) as app:
        assert app.execute("SELECT count(*) FROM my_accounts(%s)", (me,)).fetchone() == (0,)


def test_a_person_can_end_only_their_own_link_in_the_shop_they_name(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop, shop_b: Shop
) -> None:
    me, other = _user(owner), _user(owner)
    mine, theirs = _link(owner, shop_a, me), _link(owner, shop_b, other)
    with as_app(None) as app:
        assert app.execute("SELECT end_my_link(%s, %s)", (other, shop_a.shop_id)).fetchone() == (0,)
        assert app.execute("SELECT end_my_link(%s, %s)", (me, shop_b.shop_id)).fetchone() == (0,)
        assert app.execute("SELECT end_my_link(%s, %s)", (uuid.uuid4(), shop_a.shop_id)).fetchone() == (0,)
    assert (_status(owner, mine), _status(owner, theirs)) == ("active", "active")

    with as_app(None) as app:
        assert app.execute("SELECT end_my_link(%s, %s)", (me, shop_a.shop_id)).fetchone() == (1,)
        assert app.execute("SELECT end_my_link(%s, %s)", (me, shop_a.shop_id)).fetchone() == (0,)
    assert (_status(owner, mine), _status(owner, theirs)) == ("ended", "active")


def test_becoming_reachable_touches_only_that_persons_links(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop, shop_b: Shop
) -> None:
    me, other = _user(owner), _user(owner)
    mine, theirs = _link(owner, shop_a, me, "unreachable"), _link(owner, shop_b, other, "unreachable")
    with as_app(None) as app:
        assert app.execute("SELECT mark_recipient_reachable(%s)", (me,)).fetchone() == (1,)
    assert (_status(owner, mine), _status(owner, theirs)) == ("active", "unreachable")


@pytest.mark.parametrize(
    ("kind", "columns"),
    [
        ("staff", {}),
        ("customer", {"status": "used"}),
        ("customer", {"status": "cancelled"}),
        ("counter", {"status": "cancelled"}),
        ("customer", {"expires_at": "2020-01-01T00:00:00Z"}),
    ],
    ids=["staff invitation", "used", "cancelled", "replaced counter code", "expired"],
)
def test_a_code_that_is_not_a_usable_customer_code_links_nobody(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop, kind: str, columns: dict[str, Any]
) -> None:
    me = _user(owner)
    digest = _code(owner, shop_a, kind, **columns)
    with as_app(None) as app:
        assert app.execute("SELECT count(*) FROM customer_token_info(%s)", (digest,)).fetchone() == (0,)
        outcome = app.execute("SELECT outcome FROM link_customer(%s, %s, 2::smallint, 'X')", (digest, me)).fetchone()
    assert outcome == ("invalid",)
    assert owner.execute("SELECT count(*) FROM customer_link WHERE user_id = %s", (me,)).fetchone() == (0,)


def test_a_personal_code_links_one_person_to_exactly_that_record(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop, shop_b: Shop
) -> None:
    me, late = _user(owner), _user(owner)
    digest = _code(owner, shop_a, "customer")
    with as_app(None) as app:
        info = app.execute("SELECT kind, shop_id, shop_name FROM customer_token_info(%s)", (digest,)).fetchone()
        first = app.execute(
            "SELECT outcome, shop_id, customer_id FROM link_customer(%s, %s, 2::smallint, NULL)", (digest, me)
        ).fetchone()
        second = app.execute("SELECT outcome FROM link_customer(%s, %s, 2::smallint, NULL)", (digest, late)).fetchone()
    assert info == ("customer", shop_a.shop_id, "Shop A")
    assert first == ("linked", shop_a.shop_id, shop_a.customer_id)
    assert second == ("invalid",), "a personal code is used once"
    rows = owner.execute(
        "SELECT user_id, shop_id, customer_id, status, consent_text_v, consent_at IS NOT NULL FROM customer_link "
        "WHERE shop_id IN (%s, %s)",
        (shop_a.shop_id, shop_b.shop_id),
    ).fetchall()
    assert rows == [(me, shop_a.shop_id, shop_a.customer_id, "active", 2, True)]


def test_a_record_that_is_linked_is_not_linked_again(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop
) -> None:
    holder, late = _user(owner), _user(owner)
    _link(owner, shop_a, holder)
    digest = _code(owner, shop_a, "customer")
    with as_app(None) as app:
        assert app.execute(
            "SELECT outcome FROM link_customer(%s, %s, 2::smallint, NULL)", (digest, late)
        ).fetchone() == ("taken",)
        # The holder opening a code of the same shop is told they are already there.
        assert app.execute(
            "SELECT outcome FROM link_customer(%s, %s, 2::smallint, NULL)", (digest, holder)
        ).fetchone() == ("already",)
    assert owner.execute("SELECT count(*) FROM customer_link WHERE shop_id = %s", (shop_a.shop_id,)).fetchone() == (1,)


def test_the_database_refuses_a_waiting_entry_without_consent_and_a_name_outside_waiting(
    owner: psycopg.Connection, shop_a: Shop
) -> None:
    user = _user(owner)
    with pytest.raises(psycopg.errors.CheckViolation):
        owner.execute(
            "INSERT INTO customer_link (id, shop_id, user_id, status) VALUES (gen_random_uuid(), %s, %s, 'waiting')",
            (shop_a.shop_id, user),
        )
    with pytest.raises(psycopg.errors.CheckViolation):
        owner.execute(
            "INSERT INTO customer_link (id, shop_id, customer_id, user_id, status, consent_text_v, "
            "consent_at, waiting_name) "
            "VALUES (gen_random_uuid(), %s, %s, %s, 'active', 2, now(), 'Karim')",
            (shop_a.shop_id, shop_a.customer_id, user),
        )


@pytest.mark.parametrize("function", ["my_link(uuid, uuid)", "forget_user_if_unused(uuid)"])
def test_the_customer_page_functions_are_for_the_application_role_only(
    owner: psycopg.Connection, function: str
) -> None:
    row = owner.execute(
        "SELECT has_function_privilege('qd_app', %s, 'EXECUTE'), has_function_privilege('public', %s, 'EXECUTE')",
        (function, function),
    ).fetchone()
    assert row == (True, False)


def test_a_link_resolves_only_for_its_own_user_and_only_while_live(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop
) -> None:
    me, other = _user(owner), _user(owner)
    link_id = _link(owner, shop_a, me)
    with as_app(None) as app:
        assert app.execute("SELECT shop_id, customer_id FROM my_link(%s, %s)", (me, link_id)).fetchall() == [
            (shop_a.shop_id, shop_a.customer_id)
        ]
        assert app.execute("SELECT count(*) FROM my_link(%s, %s)", (other, link_id)).fetchone() == (0,)
        assert app.execute("SELECT count(*) FROM my_link(%s, %s)", (me, uuid.uuid4())).fetchone() == (0,)
    owner.execute("UPDATE customer_link SET status = 'ended', ended_at = now() WHERE id = %s", (link_id,))
    with as_app(None) as app:
        assert app.execute("SELECT count(*) FROM my_link(%s, %s)", (me, link_id)).fetchone() == (0,)


def test_only_a_person_nothing_refers_to_is_forgotten(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop, shop_b: Shop
) -> None:
    unused, linked, admin = _user(owner), _user(owner), _user(owner)
    _link(owner, shop_b, linked, "ended")  # even an ended link still names the person
    owner.execute("INSERT INTO admin_account (user_id, totp_secret) VALUES (%s, %s)", (admin, b"test-only"))
    owner.execute(
        "INSERT INTO user_session (id, token_hash, user_id, kind, expires_at) "
        "VALUES (gen_random_uuid(), %s, %s, 'webapp', now() + interval '1 hour')",
        (uuid.uuid4().bytes, unused),
    )
    with as_app(None) as app:
        results = [
            app.execute("SELECT forget_user_if_unused(%s)", (user,)).fetchone()
            for user in (shop_a.user_id, linked, admin, unused, unused, uuid.uuid4())
        ]
    assert results == [(False,), (False,), (False,), (True,), (False,), (False,)]
    kept = owner.execute(
        "SELECT count(*) FROM app_user WHERE id IN (%s, %s, %s) AND tg_id IS NOT NULL", (shop_a.user_id, linked, admin)
    ).fetchone()
    assert kept == (3,)
    assert owner.execute("SELECT tg_id FROM app_user WHERE id = %s", (unused,)).fetchone() == (None,)
    assert owner.execute(
        "SELECT revoked_at IS NOT NULL FROM user_session WHERE user_id = %s", (unused,)
    ).fetchall() == [(True,)]


def test_the_reminder_schedule_function_returns_only_shops_that_should_be_served(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop, shop_b: Shop
) -> None:
    row = owner.execute(
        "SELECT has_function_privilege('qd_app', 'shops_due_for_reminders(smallint)', 'EXECUTE'), "
        "has_function_privilege('public', 'shops_due_for_reminders(smallint)', 'EXECUTE')"
    ).fetchone()
    assert row == (True, False)

    for shop in (shop_a, shop_b):
        owner.execute(
            "INSERT INTO subscription (shop_id, state, trial_ends) VALUES (%s, 'trial', current_date + 30)",
            (shop.shop_id,),
        )
    owner.execute("UPDATE shop SET reminders_on = true, reminder_hour = 17 WHERE id = %s", (shop_a.shop_id,))
    owner.execute("UPDATE shop SET reminder_hour = 17 WHERE id = %s", (shop_b.shop_id,))  # same hour, but off

    def due(hour: int) -> set[uuid.UUID]:
        with as_app(None) as app:
            rows = app.execute("SELECT shop_id FROM shops_due_for_reminders(%s::smallint)", (hour,)).fetchall()
        return {row[0] for row in rows} & {shop_a.shop_id, shop_b.shop_id}

    assert due(17) == {shop_a.shop_id}, "shop B has reminders off"
    assert due(16) == set() and due(18) == set()
    owner.execute("UPDATE subscription SET state = 'suspended' WHERE shop_id = %s", (shop_a.shop_id,))
    assert due(17) == set()
    owner.execute("UPDATE subscription SET state = 'limited' WHERE shop_id = %s", (shop_a.shop_id,))
    assert due(17) == {shop_a.shop_id}
    owner.execute("UPDATE shop SET status = 'deletion_pending' WHERE id = %s", (shop_a.shop_id,))
    assert due(17) == set()

"""The administrator's database objects, used directly as the application role (migration 0016).

The functions run with their owner's rights and cross the tenant boundary on purpose, so each must do
nothing for someone who is not an active administrator, and none may return a customer, an entry or an
amount owed (REQ-059). The audit and the admin sessions carry grants the application cannot exceed.
"""

import uuid
from datetime import date, datetime, timedelta
from itertools import product
from typing import Any
from zoneinfo import ZoneInfo

import psycopg
import pytest
from psycopg import errors

from qarz.domain.subscription import effective_state

from ..conftest import AppSession, Shop

pytestmark = pytest.mark.db

TODAY = datetime.now(ZoneInfo("Asia/Tashkent")).date()

FUNCTIONS = [
    "admin_shop_search(uuid, date, text, text, uuid, timestamptz, uuid, integer)",
    "admin_shop_receipts(uuid, uuid)",
    "admin_lock_subscription(uuid, uuid)",
    "admin_store_subscription(uuid, uuid, text, date, date, text, timestamptz)",
]
SEARCH = "SELECT * FROM admin_shop_search(%s, %s, %s, %s, %s, %s, %s, %s)"
SEARCH_COLUMNS = [
    "shop_id",
    "name",
    "lang",
    "status",
    "created_at",
    "deletion_due",
    "state",
    "effective_state",
    "trial_ends",
    "paid_through",
    "prior_state",
    "owner_tg",
    "staff_count",
    "customer_count",
]


def _user(owner: psycopg.Connection) -> uuid.UUID:
    user_id = uuid.uuid4()
    owner.execute("INSERT INTO app_user (id, tg_id) VALUES (%s, %s)", (user_id, uuid.uuid4().int % 10**15))
    return user_id


def _admin(owner: psycopg.Connection, status: str = "active") -> uuid.UUID:
    user_id = _user(owner)
    owner.execute(
        "INSERT INTO admin_account (user_id, totp_secret, status) VALUES (%s, %s, %s)", (user_id, b"sealed", status)
    )
    return user_id


def _tag() -> str:
    """A name part no other test's shop carries: the test database is shared by the whole session."""
    return uuid.uuid4().hex[:12]


def _named(owner: psycopg.Connection, shop: Shop, name: str) -> None:
    owner.execute("UPDATE shop SET name = %s WHERE id = %s", (name, shop.shop_id))


def _subscribe(
    owner: psycopg.Connection,
    shop: Shop,
    state: str,
    *,
    trial_ends: date | None = None,
    paid_through: date | None = None,
    prior_state: str | None = None,
) -> None:
    owner.execute(
        "INSERT INTO subscription (shop_id, state, trial_ends, paid_through, prior_state) VALUES (%s, %s, %s, %s, %s)",
        (shop.shop_id, state, trial_ends, paid_through, prior_state),
    )


def _search(conn: psycopg.Connection, admin: uuid.UUID, **filters: Any) -> list[dict[str, Any]]:
    cursor = conn.execute(
        SEARCH,
        (
            admin,
            filters.get("today", TODAY),
            filters.get("query"),
            filters.get("state"),
            filters.get("shop"),
            filters.get("after_created"),
            filters.get("after_id"),
            filters.get("limit", 100),
        ),
    )
    assert cursor.description is not None
    names = [column.name for column in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def _stored(owner: psycopg.Connection, shop: Shop) -> Any:
    return owner.execute(
        "SELECT state, trial_ends, paid_through, prior_state FROM subscription WHERE shop_id = %s", (shop.shop_id,)
    ).fetchone()


# --- who may call them ----------------------------------------------------------------------------------


@pytest.mark.parametrize("function", FUNCTIONS)
def test_only_the_application_role_may_call_them(owner: psycopg.Connection, function: str) -> None:
    row = owner.execute(
        "SELECT has_function_privilege('qd_app', %s, 'EXECUTE'), has_function_privilege('public', %s, 'EXECUTE'), "
        "(SELECT prosecdef FROM pg_proc WHERE oid = %s::regprocedure)",
        (function, function, function),
    ).fetchone()
    assert row == (True, False, True)


def test_the_migration_adds_no_other_admin_function(owner: psycopg.Connection) -> None:
    rows = owner.execute(
        "SELECT p.oid::regprocedure::text FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
        "WHERE n.nspname = 'public' AND p.proname LIKE 'admin\\_%'"
    ).fetchall()
    normalized = {name.replace("timestamp with time zone", "timestamptz") for (name,) in rows}
    assert normalized == {function.replace(", ", ",") for function in FUNCTIONS}


# --- search ---------------------------------------------------------------------------------------------


def test_search_shows_a_shop_without_anything_of_its_customers(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop, shop_b: Shop
) -> None:
    admin, tag = _admin(owner), _tag()
    _named(owner, shop_a, f"Dokon {tag} bir")
    _named(owner, shop_b, f"Dokon {tag} ikki")
    _subscribe(owner, shop_a, "trial", trial_ends=TODAY + timedelta(days=3))
    # Shop A: its owner, one more active member, one suspended; its customer plus one anonymized.
    extra, held = _user(owner), _user(owner)
    owner.execute(
        "INSERT INTO membership (id, shop_id, user_id, role, status) VALUES "
        "(gen_random_uuid(), %s, %s, 'seller', 'active'), (gen_random_uuid(), %s, %s, 'seller', 'suspended')",
        (shop_a.shop_id, extra, shop_a.shop_id, held),
    )
    owner.execute(
        "INSERT INTO customer (id, shop_id, display_name, name_norm, status) "
        "VALUES (gen_random_uuid(), %s, 'Mijoz 2', 'mijoz 2', 'archived'), "
        "(gen_random_uuid(), %s, 'O''chirilgan', 'ochirilgan', 'anonymized')",
        (shop_a.shop_id, shop_a.shop_id),
    )
    owner.execute(
        "INSERT INTO ledger_entry (id, shop_id, customer_id, seq, kind, amount, author_id) "
        "VALUES (gen_random_uuid(), %s, %s, 1, 'credit', 777000, %s)",
        (shop_a.shop_id, shop_a.customer_id, shop_a.member_id),
    )
    owner_tg = owner.execute("SELECT tg_id FROM app_user WHERE id = %s", (shop_a.user_id,)).fetchone()
    assert owner_tg is not None

    with as_app(None) as conn:
        assert conn.execute("SELECT count(*) FROM shop").fetchone() == (0,), "the role itself sees no shop"
        rows = _search(conn, admin, query=tag)

    assert {row["shop_id"] for row in rows} == {shop_a.shop_id, shop_b.shop_id}
    assert len(rows) == 2
    found = next(row for row in rows if row["shop_id"] == shop_a.shop_id)
    assert list(found) == SEARCH_COLUMNS, "no column beyond the ones an administrator may see"
    assert found["name"] == f"Dokon {tag} bir"
    assert (found["state"], found["effective_state"], found["trial_ends"]) == ("trial", "trial", TODAY + timedelta(3))
    assert found["owner_tg"] == owner_tg[0]
    assert found["staff_count"] == 2, "active members only"
    assert found["customer_count"] == 2, "customers that still are somebody: not the anonymized one"
    everything = " ".join(str(value) for row in rows for value in row.values())
    assert "Customer A" not in everything
    assert "Mijoz" not in everything
    assert "777000" not in everything

    without_subscription = next(row for row in rows if row["shop_id"] == shop_b.shop_id)
    assert (without_subscription["state"], without_subscription["effective_state"]) == (None, "limited")


@pytest.mark.parametrize("who", ["a stranger", "a disabled administrator", "nobody"])
def test_search_returns_nothing_to_someone_who_is_not_an_active_administrator(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop, who: str
) -> None:
    tag = _tag()
    _named(owner, shop_a, f"Dokon {tag}")
    caller = {"a stranger": _user(owner), "a disabled administrator": _admin(owner, "disabled"), "nobody": None}[who]
    with as_app(None) as conn:
        assert _search(conn, caller, query=tag) == []  # type: ignore[arg-type]
        assert _search(conn, caller, shop=shop_a.shop_id) == []  # type: ignore[arg-type]
    with as_app(None) as conn:
        assert len(_search(conn, _admin(owner), query=tag)) == 1, "an administrator does find it"


def test_search_matches_a_part_of_the_name_and_treats_wildcards_as_text(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop, shop_b: Shop
) -> None:
    admin, tag = _admin(owner), _tag()
    _named(owner, shop_a, f"{tag} 100% Halol_Market")
    _named(owner, shop_b, f"{tag} 100 foiz HalolXMarket")
    with as_app(None) as conn:
        assert len(_search(conn, admin, query=tag.upper())) == 2, "letter case does not matter"
        assert [row["shop_id"] for row in _search(conn, admin, query=f"{tag} 100%")] == [shop_a.shop_id]
        assert [row["shop_id"] for row in _search(conn, admin, query="Halol_Market")] == [shop_a.shop_id]
        assert _search(conn, admin, query=f"{tag}\\") == []
        assert _search(conn, admin, query=f"{tag} yo'q") == []


@pytest.mark.parametrize(
    ("state", "trial_delta", "paid_delta"),
    [
        (state, trial, paid)
        for state, trial, paid in product(
            ["trial", "active", "limited", "suspended"], [None, -1, 0, 1], [None, -1, 0, 1]
        )
    ],
)
def test_the_state_filter_agrees_with_the_domain_rule(
    owner: psycopg.Connection,
    as_app: AppSession,
    shop_a: Shop,
    state: str,
    trial_delta: int | None,
    paid_delta: int | None,
) -> None:
    admin = _admin(owner)
    trial_ends = None if trial_delta is None else TODAY + timedelta(days=trial_delta)
    paid_through = None if paid_delta is None else TODAY + timedelta(days=paid_delta)
    _subscribe(owner, shop_a, state, trial_ends=trial_ends, paid_through=paid_through)
    expected = effective_state(state, trial_ends, paid_through, TODAY)
    with as_app(None) as conn:
        assert _search(conn, admin, shop=shop_a.shop_id)[0]["effective_state"] == expected
        for asked in ("trial", "active", "limited", "suspended"):
            found = _search(conn, admin, shop=shop_a.shop_id, state=asked)
            assert bool(found) is (asked == expected), (asked, expected)


def test_search_pages_newest_first_without_gaps_or_repeats(owner: psycopg.Connection, as_app: AppSession) -> None:
    admin, tag = _admin(owner), _tag()
    ids = [uuid.uuid4() for _ in range(5)]
    base = datetime.now(ZoneInfo("Asia/Tashkent"))
    for position, shop_id in enumerate(ids):
        # Three shops share a creation time and pages hold two, so the identifier has to break the tie.
        owner.execute(
            "INSERT INTO shop (id, name, created_at) VALUES (%s, %s, %s)",
            (shop_id, f"Sahifa {tag} {position}", base - timedelta(minutes=position // 3)),
        )
    with as_app(None) as conn:
        everything = _search(conn, admin, query=tag)
        assert [(row["created_at"], row["shop_id"]) for row in everything] == sorted(
            ((row["created_at"], row["shop_id"]) for row in everything), reverse=True
        )
        seen: list[uuid.UUID] = []
        after: tuple[Any, Any] = (None, None)
        while True:
            page = _search(conn, admin, query=tag, after_created=after[0], after_id=after[1], limit=2)
            if not page:
                break
            assert len(seen) < len(everything), "the pages must come to an end"
            assert len(page) <= 2
            seen += [row["shop_id"] for row in page]
            after = (page[-1]["created_at"], page[-1]["shop_id"])
    assert seen == [row["shop_id"] for row in everything]
    assert sorted(seen) == sorted(ids)


def test_search_never_returns_more_than_a_page_and_one(owner: psycopg.Connection, as_app: AppSession) -> None:
    admin, tag = _admin(owner), _tag()
    owner.execute(
        "INSERT INTO shop (id, name) SELECT gen_random_uuid(), %s || n FROM generate_series(1, 105) n",
        (f"Kop {tag} ",),
    )
    with as_app(None) as conn:
        assert len(_search(conn, admin, query=tag, limit=100000)) == 101
        assert len(_search(conn, admin, query=tag, limit=0)) == 1
        assert len(_search(conn, admin, query=tag, limit=-5)) == 1


# --- receipts -------------------------------------------------------------------------------------------


def test_receipts_are_one_shops_payment_history_for_an_administrator_only(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop, shop_b: Shop
) -> None:
    admin = _admin(owner)
    mine, other = uuid.uuid4(), uuid.uuid4()
    owner.execute(
        "INSERT INTO subscription_receipt (id, shop_id, stated_amount, status, months, decided_at) VALUES "
        "(%s, %s, 100000, 'approved', 1, now()), (%s, %s, 200000, 'submitted', NULL, NULL)",
        (mine, shop_a.shop_id, other, shop_b.shop_id),
    )
    call = "SELECT receipt_id, stated_amount, status, months FROM admin_shop_receipts(%s, %s)"
    with as_app(None) as conn:
        assert conn.execute("SELECT count(*) FROM subscription_receipt").fetchone() == (0,)
        assert conn.execute(call, (admin, shop_a.shop_id)).fetchall() == [(mine, 100000, "approved", 1)]
        assert conn.execute(call, (admin, uuid.uuid4())).fetchall() == []
        for caller in (_user(owner), _admin(owner, "disabled"), shop_a.user_id):
            assert conn.execute(call, (caller, shop_a.shop_id)).fetchall() == []


# --- reading and storing a subscription -----------------------------------------------------------------

LOCK = "SELECT * FROM admin_lock_subscription(%s, %s)"
STORE = "SELECT admin_store_subscription(%s, %s, %s, %s, %s, %s, now())"


def test_lock_returns_the_subscription_and_where_to_reach_the_owner(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop
) -> None:
    admin = _admin(owner)
    _subscribe(owner, shop_a, "suspended", trial_ends=TODAY, prior_state="trial")
    owner.execute("UPDATE app_user SET lang = 'ru' WHERE id = %s", (shop_a.user_id,))
    owner_tg = owner.execute("SELECT tg_id FROM app_user WHERE id = %s", (shop_a.user_id,)).fetchone()
    assert owner_tg is not None
    with as_app(None) as conn:
        assert conn.execute(LOCK, (admin, shop_a.shop_id)).fetchall() == [
            ("suspended", TODAY, None, "trial", "Shop A", owner_tg[0], "ru")
        ]


def test_lock_holds_the_row_until_the_transaction_ends(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop, database_url: str
) -> None:
    admin = _admin(owner)
    _subscribe(owner, shop_a, "trial", trial_ends=TODAY)
    grab = "SELECT 1 FROM subscription WHERE shop_id = %s FOR UPDATE NOWAIT"
    with psycopg.connect(database_url) as other:
        with as_app(None) as conn:
            assert len(conn.execute(LOCK, (admin, shop_a.shop_id)).fetchall()) == 1
            with pytest.raises(errors.LockNotAvailable):
                other.execute(grab, (shop_a.shop_id,))
            other.rollback()
        assert other.execute(grab, (shop_a.shop_id,)).fetchall() == [(1,)], "free again after the commit"


def test_lock_returns_nothing_without_an_active_administrator_or_a_living_shop(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop, shop_b: Shop
) -> None:
    admin = _admin(owner)
    _subscribe(owner, shop_a, "trial", trial_ends=TODAY)
    _subscribe(owner, shop_b, "trial", trial_ends=TODAY)
    owner.execute("UPDATE shop SET status = 'erased' WHERE id = %s", (shop_b.shop_id,))
    with as_app(None) as conn:
        for caller in (_user(owner), _admin(owner, "disabled"), shop_a.user_id):
            assert conn.execute(LOCK, (caller, shop_a.shop_id)).fetchall() == []
        assert conn.execute(LOCK, (admin, shop_b.shop_id)).fetchall() == [], "an erased shop"
        assert conn.execute(LOCK, (admin, uuid.uuid4())).fetchall() == [], "an unknown shop"
        assert len(conn.execute(LOCK, (admin, shop_a.shop_id)).fetchall()) == 1


def test_store_writes_exactly_the_given_shops_subscription(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop, shop_b: Shop
) -> None:
    admin = _admin(owner)
    _subscribe(owner, shop_a, "trial", trial_ends=TODAY)
    _subscribe(owner, shop_b, "trial", trial_ends=TODAY)
    until = TODAY + timedelta(days=30)
    with as_app(None) as conn:
        assert conn.execute("SELECT count(*) FROM subscription").fetchone() == (0,), "the role itself sees none"
        assert conn.execute(STORE, (admin, shop_a.shop_id, "active", TODAY, until, None)).fetchone() == (True,)
    assert _stored(owner, shop_a) == ("active", TODAY, until, None)
    assert _stored(owner, shop_b) == ("trial", TODAY, None, None), "the other shop is untouched"


def test_store_does_nothing_without_an_active_administrator_or_a_living_shop(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop, shop_b: Shop
) -> None:
    admin = _admin(owner)
    _subscribe(owner, shop_a, "trial", trial_ends=TODAY)
    _subscribe(owner, shop_b, "trial", trial_ends=TODAY)
    owner.execute("UPDATE shop SET status = 'erased' WHERE id = %s", (shop_b.shop_id,))
    with as_app(None) as conn:
        for caller in (_user(owner), _admin(owner, "disabled"), shop_a.user_id):
            assert conn.execute(STORE, (caller, shop_a.shop_id, "suspended", None, None, "trial")).fetchone() == (
                False,
            )
        assert conn.execute(STORE, (admin, shop_b.shop_id, "suspended", None, None, "trial")).fetchone() == (False,)
        assert conn.execute(STORE, (admin, uuid.uuid4(), "suspended", None, None, "trial")).fetchone() == (False,)
    assert _stored(owner, shop_a) == ("trial", TODAY, None, None)
    assert _stored(owner, shop_b) == ("trial", TODAY, None, None)


def test_store_cannot_write_a_state_the_schema_does_not_know(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop
) -> None:
    admin = _admin(owner)
    _subscribe(owner, shop_a, "trial", trial_ends=TODAY)
    with pytest.raises(errors.CheckViolation), as_app(None) as conn:
        conn.execute(STORE, (admin, shop_a.shop_id, "free-forever", None, None, None))
    assert _stored(owner, shop_a) == ("trial", TODAY, None, None)


# --- the audit and the other new tables -----------------------------------------------------------------

AUDIT_INSERT = (
    "INSERT INTO admin_audit (id, admin_id, action, target_type, target_id, reason) "
    "VALUES (%s, %s, 'shop.viewed', 'shop', 'x', 'because')"
)


def test_the_application_can_add_to_the_audit_and_read_it(owner: psycopg.Connection, as_app: AppSession) -> None:
    admin, audit_id = _admin(owner), uuid.uuid4()
    with as_app(None) as conn:
        conn.execute(AUDIT_INSERT, (audit_id, admin))
        assert conn.execute("SELECT admin_id, reason FROM admin_audit WHERE id = %s", (audit_id,)).fetchone() == (
            admin,
            "because",
        )


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE admin_audit SET reason = 'rewritten' WHERE id = %(id)s",
        "UPDATE admin_audit SET admin_id = admin_id WHERE id = %(id)s",
        "DELETE FROM admin_audit WHERE id = %(id)s",
        "DELETE FROM admin_audit",
        "TRUNCATE admin_audit",
    ],
)
def test_the_application_can_never_change_or_remove_an_audit_row(
    owner: psycopg.Connection, as_app: AppSession, statement: str
) -> None:
    admin, audit_id = _admin(owner), uuid.uuid4()
    owner.execute(AUDIT_INSERT, (audit_id, admin))
    with pytest.raises(errors.InsufficientPrivilege), as_app(None) as conn:
        conn.execute(statement, {"id": audit_id})
    assert owner.execute("SELECT reason FROM admin_audit WHERE id = %s", (audit_id,)).fetchone() == ("because",)


def test_the_grants_on_the_new_tables_are_exactly_these(owner: psycopg.Connection) -> None:
    rows = owner.execute(
        "SELECT table_name, privilege_type FROM information_schema.role_table_grants "
        "WHERE grantee = 'qd_app' AND table_name IN ('admin_audit', 'admin_session', 'admin_request_key')"
    ).fetchall()
    granted: dict[str, set[str]] = {}
    for table, privilege in rows:
        granted.setdefault(table, set()).add(privilege)
    assert granted == {
        "admin_audit": {"SELECT", "INSERT"},
        "admin_session": {"SELECT", "INSERT", "UPDATE"},
        "admin_request_key": {"SELECT", "INSERT", "DELETE"},
    }


def test_the_audit_refuses_a_target_it_does_not_know(owner: psycopg.Connection) -> None:
    admin = _admin(owner)
    with pytest.raises(errors.CheckViolation):
        owner.execute(
            "INSERT INTO admin_audit (id, admin_id, action, target_type) "
            "VALUES (gen_random_uuid(), %s, 'x', 'customer')",
            (admin,),
        )


def test_an_admin_session_lasts_at_most_eight_hours(owner: psycopg.Connection) -> None:
    admin = _admin(owner)
    insert = (
        "INSERT INTO admin_session (id, token_hash, user_id, created_at, expires_at) "
        "VALUES (gen_random_uuid(), %s, %s, now(), now() + %s::interval)"
    )
    owner.execute(insert, (uuid.uuid4().bytes, admin, "8 hours"))
    with pytest.raises(errors.CheckViolation):
        owner.execute(insert, (uuid.uuid4().bytes, admin, "8 hours 1 second"))


def test_an_admin_session_belongs_to_an_administrator_account(owner: psycopg.Connection) -> None:
    with pytest.raises(errors.ForeignKeyViolation):
        owner.execute(
            "INSERT INTO admin_session (id, token_hash, user_id, created_at, expires_at) "
            "VALUES (gen_random_uuid(), %s, %s, now(), now() + interval '1 hour')",
            (uuid.uuid4().bytes, _user(owner)),
        )

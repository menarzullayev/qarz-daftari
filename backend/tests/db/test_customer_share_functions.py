"""A customer's read-only link in the database (migration 0040), without the application in between.

The lookup answers for a live link and for nothing else; the table is one shop's at a time; and the
roles can do to it only what their code does.
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

LOOKUP = "SELECT share_id, shop_id, customer_id, token_hash FROM customer_share_lookup(%s, now())"


def digest(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def fresh() -> str:
    """A token nobody used before: the database lives for the whole session and tokens are unique in it."""
    return uuid.uuid4().hex


@pytest.fixture
def switch_on(owner: psycopg.Connection) -> Iterator[None]:
    """The platform switch, on for one test: the table is global and the database lives for the session."""
    owner.execute(
        "INSERT INTO platform_setting (key, value, updated_by) VALUES ('customer_links_on', 'true', 'db-test') "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
    )
    yield
    owner.execute("DELETE FROM platform_setting WHERE key = 'customer_links_on'")


def add_share(conn: psycopg.Connection, shop: Shop, token: str, *, expires: str = "now() + interval '90 days'") -> Any:
    share_id = uuid.uuid4()
    conn.execute(
        "INSERT INTO customer_share (id, shop_id, customer_id, token_hash, created_by, expires_at) "
        f"VALUES (%s, %s, %s, %s, %s, {expires})",
        (share_id, shop.shop_id, shop.customer_id, digest(token), shop.member_id),
    )
    return share_id


def lookup(as_app: AppSession, token: str) -> list[tuple[Any, ...]]:
    # No shop is set: whoever opens a link belongs to none.
    with as_app(None) as conn:
        return conn.execute(LOOKUP, (digest(token),)).fetchall()


# --- the lookup ------------------------------------------------------------------------------------------


def test_a_live_link_is_found_outside_any_shop(
    as_app: AppSession, owner: psycopg.Connection, shop_a: Shop, switch_on: None
) -> None:
    token = fresh()
    share = add_share(owner, shop_a, token)
    assert lookup(as_app, token) == [(share, shop_a.shop_id, shop_a.customer_id, digest(token))]
    assert lookup(as_app, fresh()) == []


@pytest.mark.parametrize(
    "spoil",
    [
        "UPDATE customer_share SET revoked_at = now() WHERE shop_id = %(shop)s",
        "UPDATE customer SET status = 'anonymized' WHERE id = %(customer)s",
        "UPDATE shop SET status = 'deletion_pending', deletion_due = now() + interval '30 days' WHERE id = %(shop)s",
        "UPDATE shop SET status = 'erased' WHERE id = %(shop)s",
        "DELETE FROM platform_setting WHERE key = 'customer_links_on'",
        "UPDATE platform_setting SET value = 'false' WHERE key = 'customer_links_on'",
        "UPDATE platform_setting SET value = '\"true\"' WHERE key = 'customer_links_on'",
        "UPDATE platform_setting SET value = '1' WHERE key = 'customer_links_on'",
    ],
)
def test_anything_but_a_live_link_of_a_live_shop_with_the_switch_on_is_not_found(
    as_app: AppSession, owner: psycopg.Connection, shop_a: Shop, switch_on: None, spoil: str
) -> None:
    token = fresh()
    add_share(owner, shop_a, token)
    assert len(lookup(as_app, token)) == 1
    owner.execute(spoil, {"customer": shop_a.customer_id, "shop": shop_a.shop_id})  # type: ignore[arg-type]
    assert lookup(as_app, token) == []


def test_an_expired_link_is_not_found_from_the_moment_it_expires(
    as_app: AppSession, owner: psycopg.Connection, shop_a: Shop, switch_on: None
) -> None:
    token = fresh()
    share = add_share(owner, shop_a, token)
    row = owner.execute("SELECT expires_at FROM customer_share WHERE id = %s", (share,)).fetchone()
    assert row is not None
    with as_app(None) as conn:
        at = "SELECT count(*) FROM customer_share_lookup(%s, %s::timestamptz + %s::interval)"
        assert conn.execute(at, (digest(token), row[0], "-1 second")).fetchone() == (1,)
        assert conn.execute(at, (digest(token), row[0], "0 seconds")).fetchone() == (0,)


def test_the_lookup_runs_with_a_pinned_search_path_and_its_owners_rights(owner: psycopg.Connection) -> None:
    row = owner.execute(
        "SELECT prosecdef, proconfig FROM pg_proc WHERE oid = 'customer_share_lookup(bytea,timestamptz)'::regprocedure"
    ).fetchone()
    assert row == (True, ["search_path=public, pg_temp"])


# --- one shop at a time ------------------------------------------------------------------------------------


def test_the_table_is_one_shops_at_a_time_and_nobodys_without_a_shop(
    as_app: AppSession, owner: psycopg.Connection, shop_a: Shop, shop_b: Shop
) -> None:
    mine = add_share(owner, shop_a, fresh())
    add_share(owner, shop_b, fresh())
    with as_app(shop_a.shop_id) as conn:
        assert conn.execute("SELECT id FROM customer_share").fetchall() == [(mine,)]
    with as_app(None) as conn:
        assert conn.execute("SELECT count(*) FROM customer_share").fetchone() == (0,)
    forced = owner.execute(
        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = 'customer_share'"
    ).fetchone()
    assert forced == (True, True)


def test_a_link_cannot_be_written_into_another_shop(as_app: AppSession, shop_a: Shop, shop_b: Shop) -> None:
    with as_app(shop_a.shop_id) as conn:
        add_share(conn, shop_a, fresh())
    with pytest.raises(errors.InsufficientPrivilege, match="row-level security"), as_app(shop_a.shop_id) as conn:
        add_share(conn, shop_b, fresh())


def test_ending_links_in_one_shop_cannot_reach_another(
    as_app: AppSession, owner: psycopg.Connection, shop_a: Shop, shop_b: Shop
) -> None:
    add_share(owner, shop_b, fresh())
    with as_app(shop_a.shop_id) as conn:
        ended = conn.execute("UPDATE customer_share SET revoked_at = now()")
        assert ended.rowcount == 0
    alive = "SELECT count(*) FROM customer_share WHERE shop_id = %s AND revoked_at IS NULL"
    assert owner.execute(alive, (shop_b.shop_id,)).fetchone() == (1,)


# --- the rules of the table ----------------------------------------------------------------------------------


def test_a_customer_has_one_live_link_and_any_number_of_ended_ones(owner: psycopg.Connection, shop_a: Shop) -> None:
    end = "UPDATE customer_share SET revoked_at = now() WHERE customer_id = %s AND revoked_at IS NULL"
    add_share(owner, shop_a, fresh())
    with pytest.raises(errors.UniqueViolation, match="one_live_share_per_customer"):
        add_share(owner, shop_a, fresh())
    owner.execute(end, (shop_a.customer_id,))
    add_share(owner, shop_a, fresh())
    owner.execute(end, (shop_a.customer_id,))
    add_share(owner, shop_a, fresh())
    assert owner.execute(
        "SELECT count(*) FROM customer_share WHERE customer_id = %s", (shop_a.customer_id,)
    ).fetchone() == (3,)


def test_two_links_cannot_have_the_same_token(owner: psycopg.Connection, shop_a: Shop, shop_b: Shop) -> None:
    token = fresh()
    add_share(owner, shop_a, token)
    with pytest.raises(errors.UniqueViolation, match="customer_share_token_hash_key"):
        add_share(owner, shop_b, token)


@pytest.mark.parametrize("stored", [b"", b"short", b"x" * 31, b"x" * 33])
def test_what_is_stored_for_a_token_is_a_sha256(owner: psycopg.Connection, shop_a: Shop, stored: bytes) -> None:
    with pytest.raises(errors.CheckViolation):
        owner.execute(
            "INSERT INTO customer_share (id, shop_id, customer_id, token_hash, created_by, expires_at) "
            "VALUES (gen_random_uuid(), %s, %s, %s, %s, now() + interval '1 day')",
            (shop_a.shop_id, shop_a.customer_id, stored, shop_a.member_id),
        )


def test_a_link_cannot_be_made_already_expired(owner: psycopg.Connection, shop_a: Shop) -> None:
    with pytest.raises(errors.CheckViolation):
        add_share(owner, shop_a, fresh(), expires="now() - interval '1 second'")


@pytest.mark.parametrize("phone", ["+998901234567", "+12025550123", None])
def test_the_shops_phone_is_an_international_number_or_nothing(
    owner: psycopg.Connection, shop_a: Shop, phone: str | None
) -> None:
    owner.execute("UPDATE shop SET share_phone = %s WHERE id = %s", (phone, shop_a.shop_id))


@pytest.mark.parametrize("phone", ["901234567", "+99890 123 45 67", "+1234567", "call me", ""])
def test_anything_else_is_refused_as_the_shops_phone(owner: psycopg.Connection, shop_a: Shop, phone: str) -> None:
    with pytest.raises(errors.CheckViolation):
        owner.execute("UPDATE shop SET share_phone = %s WHERE id = %s", (phone, shop_a.shop_id))


# --- what each role does with it -------------------------------------------------------------------------------


def test_the_application_ends_a_link_but_cannot_rewrite_or_delete_one(
    as_app: AppSession, owner: psycopg.Connection, shop_a: Shop
) -> None:
    add_share(owner, shop_a, fresh())
    with as_app(shop_a.shop_id) as conn:
        conn.execute("UPDATE customer_share SET last_opened_at = now(), opened_on = current_date")
        conn.execute("UPDATE customer_share SET revoked_at = now()")
    for statement in (
        "UPDATE customer_share SET token_hash = sha256('other')",
        "UPDATE customer_share SET expires_at = now() + interval '10 years'",
        "UPDATE customer_share SET customer_id = gen_random_uuid()",
        "UPDATE customer_share SET shop_id = gen_random_uuid()",
        "DELETE FROM customer_share",
        "TRUNCATE customer_share",
    ):
        with pytest.raises(errors.InsufficientPrivilege, match="permission denied"), as_app(shop_a.shop_id) as conn:
            conn.execute(statement)  # type: ignore[arg-type]


def test_the_worker_ends_a_customers_links_and_reads_no_token(
    as_worker: AppSession, owner: psycopg.Connection, shop_a: Shop
) -> None:
    """What the worker runs when it completes the removal of a customer's data."""
    add_share(owner, shop_a, fresh())
    with as_worker(shop_a.shop_id) as conn:
        ended = conn.execute(
            "UPDATE customer_share SET revoked_at = now() WHERE customer_id = %s AND revoked_at IS NULL",
            (shop_a.customer_id,),
        )
        assert ended.rowcount == 1
    for statement in ("SELECT token_hash FROM customer_share", "SELECT * FROM customer_share"):
        with pytest.raises(errors.InsufficientPrivilege), as_worker(shop_a.shop_id) as conn:
            conn.execute(statement)  # type: ignore[arg-type]


@pytest.mark.parametrize("role", ["qd_admin", "qd_worker"])
def test_only_the_ordinary_application_may_ask_what_a_link_opens(
    request: pytest.FixtureRequest, role: str, switch_on: None
) -> None:
    session: AppSession = request.getfixturevalue("as_admin" if role == "qd_admin" else "as_worker")
    with pytest.raises(errors.InsufficientPrivilege), session(None) as conn:
        conn.execute(LOOKUP, (digest(fresh()),))

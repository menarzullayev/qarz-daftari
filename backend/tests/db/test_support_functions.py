"""Support access in the database, used directly as the roles that use it (migration 0025; REQ-059).

The administrators' functions are called as `qd_admin`; what the owner does with the table is done as
`qd_app`; erasure is the worker's (migration 0031).

The functions cross the tenant boundary for an administrator, so each must do nothing for anyone who is
not an active administrator, and `admin_open_shop` must say yes only for that administrator's own
current access to that shop. The table's history must not be something the application can rewrite.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
from psycopg import errors

from ..conftest import AppSession, Shop, refused

pytestmark = pytest.mark.db

NOW = datetime.now(UTC).replace(microsecond=0)
HOUR = timedelta(hours=1)
OPEN_SHOP = "SELECT access_id, ends_at FROM admin_open_shop(%s, %s, %s)"
OPEN = "SELECT outcome, access_id, shop_name, owner_tg, owner_lang FROM admin_support_open(%s, %s, %s, %s, %s, %s)"
CLOSE = "SELECT access_id, shop_name, owner_tg, owner_lang FROM admin_support_close(%s, %s, %s)"
LIST = "SELECT access_id, shop_id, admin_id, closed_by FROM admin_support_list(%s, %s, %s, %s, %s, %s, %s)"


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


def _access(
    owner: psycopg.Connection,
    shop: Shop,
    admin: uuid.UUID,
    *,
    starts: datetime = NOW - HOUR,
    ends: datetime = NOW + HOUR,
    closed: datetime | None = None,
) -> uuid.UUID:
    access_id = uuid.uuid4()
    owner.execute(
        "INSERT INTO support_access (id, shop_id, admin_id, reason, starts_at, ends_at, closed_at, closed_by) "
        "VALUES (%s, %s, %s, 'Yordam uchun', %s, %s, %s, %s)",
        (access_id, shop.shop_id, admin, starts, ends, closed, None if closed is None else "owner"),
    )
    return access_id


def _rows(owner: psycopg.Connection, shop: Shop) -> list[Any]:
    return owner.execute(
        "SELECT id, admin_id, reason, starts_at, ends_at, closed_at, closed_by FROM support_access "
        "WHERE shop_id = %s ORDER BY starts_at",
        (shop.shop_id,),
    ).fetchall()


def _activity(owner: psycopg.Connection, shop: Shop) -> list[Any]:
    return owner.execute(
        "SELECT actor_kind, actor_id, action, subject_type, subject_id FROM activity WHERE shop_id = %s "
        "AND action LIKE 'support_access.%%' ORDER BY at",
        (shop.shop_id,),
    ).fetchall()


# --- may this administrator see this shop now -----------------------------------------------------------


def test_a_current_access_opens_the_shop_for_its_administrator_only(
    owner: psycopg.Connection, as_admin: AppSession, shop_a: Shop, shop_b: Shop
) -> None:
    admin, other = _admin(owner), _admin(owner)
    access = _access(owner, shop_a, admin)
    refused(as_admin, "SELECT count(*) FROM support_access")  # the role itself may not read the table
    with as_admin(None) as conn:
        assert conn.execute(OPEN_SHOP, (admin, shop_a.shop_id, NOW)).fetchall() == [(access, NOW + HOUR)]
        assert conn.execute(OPEN_SHOP, (admin, shop_b.shop_id, NOW)).fetchall() == [], "another shop"
        assert conn.execute(OPEN_SHOP, (other, shop_a.shop_id, NOW)).fetchall() == [], "another administrator"
        assert conn.execute(OPEN_SHOP, (shop_a.user_id, shop_a.shop_id, NOW)).fetchall() == [], "the owner"
        assert conn.execute(OPEN_SHOP, (None, shop_a.shop_id, NOW)).fetchall() == []


def test_an_access_opens_the_shop_from_its_start_until_just_before_its_end(
    owner: psycopg.Connection, as_admin: AppSession, shop_a: Shop
) -> None:
    admin = _admin(owner)
    start, end = NOW, NOW + 2 * HOUR
    _access(owner, shop_a, admin, starts=start, ends=end)
    tick = timedelta(microseconds=1)
    with as_admin(None) as conn:
        for at, opens in ((start - tick, False), (start, True), (end - tick, True), (end, False)):
            assert bool(conn.execute(OPEN_SHOP, (admin, shop_a.shop_id, at)).fetchall()) is opens, at


def test_a_closed_access_an_erased_shop_or_a_disabled_administrator_opens_nothing(
    owner: psycopg.Connection, as_admin: AppSession, shop_a: Shop, shop_b: Shop
) -> None:
    admin, disabled = _admin(owner), _admin(owner)
    _access(owner, shop_a, admin, closed=NOW - timedelta(minutes=1))
    _access(owner, shop_a, disabled)
    _access(owner, shop_b, admin)
    owner.execute("UPDATE admin_account SET status = 'disabled' WHERE user_id = %s", (disabled,))
    owner.execute("UPDATE shop SET status = 'erased' WHERE id = %s", (shop_b.shop_id,))
    with as_admin(None) as conn:
        assert conn.execute(OPEN_SHOP, (admin, shop_a.shop_id, NOW)).fetchall() == [], "closed"
        assert conn.execute(OPEN_SHOP, (disabled, shop_a.shop_id, NOW)).fetchall() == [], "disabled"
        assert conn.execute(OPEN_SHOP, (admin, shop_b.shop_id, NOW)).fetchall() == [], "erased"


# --- opening --------------------------------------------------------------------------------------------


def test_opening_writes_the_access_and_the_shops_activity_and_says_where_the_owner_is(
    owner: psycopg.Connection, as_admin: AppSession, shop_a: Shop, shop_b: Shop
) -> None:
    admin, access = _admin(owner), uuid.uuid4()
    owner.execute("UPDATE app_user SET lang = 'ru' WHERE id = %s", (shop_a.user_id,))
    owner_tg = owner.execute("SELECT tg_id FROM app_user WHERE id = %s", (shop_a.user_id,)).fetchone()
    assert owner_tg is not None
    with as_admin(None) as conn:
        opened = conn.execute(OPEN, (admin, shop_a.shop_id, access, "Egasi so'radi", NOW, NOW + 3 * HOUR)).fetchall()
    assert opened == [("opened", access, "Shop A", owner_tg[0], "ru")]
    assert _rows(owner, shop_a) == [(access, admin, "Egasi so'radi", NOW, NOW + 3 * HOUR, None, None)]
    assert _rows(owner, shop_b) == []
    assert _activity(owner, shop_a) == [("admin", admin, "support_access.opened", "support_access", access)]

    # A second one while the first is open is not written; the first is named.
    with as_admin(None) as conn:
        again = conn.execute(OPEN, (admin, shop_a.shop_id, uuid.uuid4(), "Yana", NOW, NOW + HOUR)).fetchall()
    assert [(row[0], row[1]) for row in again] == [("already_open", access)]
    assert len(_rows(owner, shop_a)) == 1
    assert len(_activity(owner, shop_a)) == 1
    # Another administrator has their own.
    other, second = _admin(owner), uuid.uuid4()
    with as_admin(None) as conn:
        assert conn.execute(OPEN, (other, shop_a.shop_id, second, "Boshqa", NOW, NOW + HOUR)).fetchone()[0] == "opened"  # type: ignore[index]
    assert len(_rows(owner, shop_a)) == 2


def test_after_one_ran_out_or_was_closed_a_new_one_can_be_opened(
    owner: psycopg.Connection, as_admin: AppSession, shop_a: Shop
) -> None:
    admin = _admin(owner)
    _access(owner, shop_a, admin, starts=NOW - 3 * HOUR, ends=NOW)
    _access(owner, shop_a, admin, closed=NOW - timedelta(minutes=1))
    with as_admin(None) as conn:
        row = conn.execute(OPEN, (admin, shop_a.shop_id, uuid.uuid4(), "Yangi", NOW, NOW + HOUR)).fetchone()
    assert row is not None
    assert row[0] == "opened"


@pytest.mark.parametrize("who", ["a stranger", "a disabled administrator", "the owner"])
def test_nobody_but_an_active_administrator_opens_one(
    owner: psycopg.Connection, as_admin: AppSession, shop_a: Shop, who: str
) -> None:
    caller = {
        "a stranger": _user(owner),
        "a disabled administrator": _admin(owner, "disabled"),
        "the owner": shop_a.user_id,
    }[who]
    with as_admin(None) as conn:
        assert conn.execute(OPEN, (caller, shop_a.shop_id, uuid.uuid4(), "Kirish", NOW, NOW + HOUR)).fetchall() == []
    assert _rows(owner, shop_a) == []
    assert _activity(owner, shop_a) == []


def test_no_access_is_opened_to_a_shop_that_does_not_exist_or_was_erased(
    owner: psycopg.Connection, as_admin: AppSession, shop_a: Shop
) -> None:
    admin = _admin(owner)
    owner.execute("UPDATE shop SET status = 'erased' WHERE id = %s", (shop_a.shop_id,))
    with as_admin(None) as conn:
        assert conn.execute(OPEN, (admin, shop_a.shop_id, uuid.uuid4(), "Kirish", NOW, NOW + HOUR)).fetchall() == []
        assert conn.execute(OPEN, (admin, uuid.uuid4(), uuid.uuid4(), "Kirish", NOW, NOW + HOUR)).fetchall() == []
    assert _rows(owner, shop_a) == []


def test_the_database_holds_the_limits_whatever_the_application_asks(
    owner: psycopg.Connection, as_admin: AppSession, shop_a: Shop
) -> None:
    admin = _admin(owner)
    with pytest.raises(errors.CheckViolation), as_admin(None) as conn:
        conn.execute(OPEN, (admin, shop_a.shop_id, uuid.uuid4(), "Uzoq", NOW, NOW + 24 * HOUR + timedelta(seconds=1)))
    with pytest.raises(errors.CheckViolation), as_admin(None) as conn:
        conn.execute(OPEN, (admin, shop_a.shop_id, uuid.uuid4(), "ab", NOW, NOW + HOUR))
    with as_admin(None) as conn:
        assert conn.execute(OPEN, (admin, shop_a.shop_id, uuid.uuid4(), "abc", NOW, NOW + 24 * HOUR)).fetchone()
    assert len(_rows(owner, shop_a)) == 1


# --- closing --------------------------------------------------------------------------------------------


def test_an_administrator_closes_their_own_open_access_and_only_that(
    owner: psycopg.Connection, as_admin: AppSession, shop_a: Shop, shop_b: Shop
) -> None:
    admin, other = _admin(owner), _admin(owner)
    mine, theirs, elsewhere = (
        _access(owner, shop_a, admin),
        _access(owner, shop_a, other),
        _access(owner, shop_b, admin),
    )
    old = _access(owner, shop_a, admin, starts=NOW - 5 * HOUR, ends=NOW - 4 * HOUR)
    with as_admin(None) as conn:
        closed = conn.execute(CLOSE, (admin, shop_a.shop_id, NOW)).fetchall()
    assert [(row[0], row[1]) for row in closed] == [(mine, "Shop A")]
    state = {row[0]: (row[5], row[6]) for row in _rows(owner, shop_a) + _rows(owner, shop_b)}
    assert state == {mine: (NOW, "admin"), theirs: (None, None), elsewhere: (None, None), old: (None, None)}
    assert _activity(owner, shop_a) == [("admin", admin, "support_access.closed", "support_access", mine)]
    with as_admin(None) as conn:
        assert conn.execute(CLOSE, (admin, shop_a.shop_id, NOW)).fetchall() == [], "nothing left to close"
        assert conn.execute(OPEN_SHOP, (admin, shop_a.shop_id, NOW)).fetchall() == []
        for caller in (_user(owner), _admin(owner, "disabled"), shop_a.user_id):
            assert conn.execute(CLOSE, (caller, shop_a.shop_id, NOW)).fetchall() == []
    assert len(_activity(owner, shop_a)) == 1


# --- the list -------------------------------------------------------------------------------------------


def test_the_list_is_for_administrators_newest_first_and_can_keep_only_the_open_ones(
    owner: psycopg.Connection, as_admin: AppSession, shop_a: Shop, shop_b: Shop
) -> None:
    admin, other = _admin(owner), _admin(owner)
    first = _access(owner, shop_a, admin, starts=NOW - 6 * HOUR, ends=NOW - 5 * HOUR)
    second = _access(owner, shop_a, other, starts=NOW - 2 * HOUR, closed=NOW - HOUR)
    third = _access(owner, shop_a, admin, starts=NOW - HOUR)
    _access(owner, shop_b, admin)
    with as_admin(None) as conn:
        everything = conn.execute(LIST, (admin, shop_a.shop_id, False, NOW, None, None, 50)).fetchall()
        assert [row[0] for row in everything] == [third, second, first]
        assert [row[2] for row in everything] == [admin, other, admin], "every administrator's, not only one's own"
        assert [row[0] for row in conn.execute(LIST, (admin, shop_a.shop_id, True, NOW, None, None, 50))] == [third]
        page = conn.execute(LIST, (admin, shop_a.shop_id, False, NOW, None, None, 2)).fetchall()
        assert [row[0] for row in page] == [third, second]
        rest = conn.execute(LIST, (admin, shop_a.shop_id, False, NOW, NOW - 2 * HOUR, second, 2)).fetchall()
        assert [row[0] for row in rest] == [first]
        for caller in (_user(owner), _admin(owner, "disabled"), shop_a.user_id):
            assert conn.execute(LIST, (caller, shop_a.shop_id, False, NOW, None, None, 50)).fetchall() == []
        assert len(conn.execute(LIST, (admin, None, False, NOW, None, None, 100000)).fetchall()) <= 101


# --- the table itself -----------------------------------------------------------------------------------


def test_the_application_may_end_an_access_and_change_nothing_else_of_it(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop
) -> None:
    admin = _admin(owner)
    access = _access(owner, shop_a, admin)
    for statement in (
        "UPDATE support_access SET reason = 'Boshqa sabab' WHERE id = %s",
        "UPDATE support_access SET ends_at = ends_at + interval '1 hour' WHERE id = %s",
        "UPDATE support_access SET admin_id = admin_id WHERE id = %s",
        "DELETE FROM support_access WHERE id = %s",
    ):
        with pytest.raises(errors.InsufficientPrivilege), as_app(shop_a.shop_id) as conn:
            conn.execute(statement, (access,))
    with pytest.raises(errors.InsufficientPrivilege), as_app(shop_a.shop_id) as conn:
        conn.execute("TRUNCATE support_access")
    with as_app(shop_a.shop_id) as conn:
        conn.execute("UPDATE support_access SET closed_at = %s, closed_by = 'owner' WHERE id = %s", (NOW, access))
    assert _rows(owner, shop_a) == [(access, admin, "Yordam uchun", NOW - HOUR, NOW + HOUR, NOW, "owner")]


def test_an_ending_names_who_ended_it(owner: psycopg.Connection, shop_a: Shop) -> None:
    access = _access(owner, shop_a, _admin(owner))
    for closed_at, closed_by in ((NOW, None), (None, "owner"), (NOW, "customer")):
        with pytest.raises(errors.CheckViolation):
            owner.execute(
                "UPDATE support_access SET closed_at = %s, closed_by = %s WHERE id = %s", (closed_at, closed_by, access)
            )


def test_one_shop_does_not_see_anothers_support_accesses(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop, shop_b: Shop
) -> None:
    admin = _admin(owner)
    mine = _access(owner, shop_a, admin)
    _access(owner, shop_b, admin)
    with as_app(shop_a.shop_id) as conn:
        assert conn.execute("SELECT id FROM support_access").fetchall() == [(mine,)]


def test_erasing_a_shop_erases_its_support_accesses_and_closes_the_door(
    owner: psycopg.Connection, as_admin: AppSession, as_worker: AppSession, shop_a: Shop, shop_b: Shop
) -> None:
    admin = _admin(owner)
    _access(owner, shop_a, admin)
    kept = _access(owner, shop_b, admin)
    owner.execute(
        "UPDATE shop SET status = 'deletion_pending', deletion_due = now() - interval '1 minute' WHERE id = %s",
        (shop_a.shop_id,),
    )
    with as_admin(None) as conn:
        assert conn.execute(OPEN_SHOP, (admin, shop_a.shop_id, NOW)).fetchall() != [], "open while it waits"
    with as_worker(None) as conn:  # erasure is the worker's
        assert conn.execute("SELECT erase_shop(%s)", (shop_a.shop_id,)).fetchone() == (True,)
    with as_admin(None) as conn:
        assert conn.execute(OPEN_SHOP, (admin, shop_a.shop_id, NOW)).fetchall() == []
        assert conn.execute(LIST, (admin, shop_a.shop_id, False, NOW, None, None, 50)).fetchall() == []
    assert _rows(owner, shop_a) == []
    assert [row[0] for row in _rows(owner, shop_b)] == [kept]


def test_a_disabled_administrator_cannot_close_even_their_own_open_access(
    owner: psycopg.Connection, as_admin: AppSession, shop_a: Shop
) -> None:
    """Nothing is done for someone who is no longer an administrator, not even tidying up after them."""
    admin = _admin(owner)
    access = _access(owner, shop_a, admin)
    owner.execute("UPDATE admin_account SET status = 'disabled' WHERE user_id = %s", (admin,))
    with as_admin(None) as conn:
        assert conn.execute(CLOSE, (admin, shop_a.shop_id, NOW)).fetchall() == []
    assert [(row[0], row[5]) for row in _rows(owner, shop_a)] == [(access, None)]
    assert _activity(owner, shop_a) == []


def test_ending_names_the_shop_itself_as_well_as_relying_on_row_level_security(
    database_url: str, owner: psycopg.Connection, shop_a: Shop, shop_b: Shop
) -> None:
    """Run as a role that bypasses row-level security, as a misconfigured deployment would: a session of
    shop B must still not end or read shop A's access (two controls, not one; REQ-N12)."""
    import asyncio

    from qarz.infrastructure.db import Database

    access = _access(owner, shop_a, _admin(owner))

    async def as_shop_b() -> Any:
        database = Database(database_url)
        try:
            async with database.tenant(shop_b.shop_id) as session:
                await session.end_support_access(access, NOW)
                return (
                    await session.support_access(access, for_update=False),
                    await session.support_accesses(before=None, limit=10),
                )
        finally:
            await database.dispose()

    assert asyncio.run(as_shop_b()) == (None, [])
    assert [(row[0], row[5]) for row in _rows(owner, shop_a)] == [(access, None)]

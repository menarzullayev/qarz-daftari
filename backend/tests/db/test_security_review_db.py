"""Database-level tests written by the security review of story S19.2 (docs/10-operations/security-review.md).

A test marked `xfail(strict=True, reason="security review finding N")` demonstrates a confirmed defect: it
states what must hold and fails today. The others pin down what the review checked and found sound.

The attacker in the marked tests is the application role itself, `qd_app`, running statements of its own:
what a flaw in the application would give. The specification promises that the ledger stays insert-only
and that one shop cannot reach another "below the application code as well as in it".
"""

import asyncio
import uuid

import psycopg
import pytest

from qarz.infrastructure.db import Database
from tests.conftest import AppSession, Shop, add_entry

pytestmark = pytest.mark.db


# --- finding 2: the application role can erase any shop's ledger at once ----------------------------------


def test_the_application_role_cannot_erase_a_ledger_before_the_waiting_period(
    as_app: AppSession, as_worker: AppSession, owner: psycopg.Connection, shop_a: Shop
) -> None:
    """`erase_shop` trusts `shop.status` and `shop.deletion_due`, and both are columns the application
    role may update. Two statements delete every entry of a shop: the insert-only ledger (REQ-N07) and
    the 30-day wait (BR-25) hold only as long as the application code is right."""
    add_entry(owner, shop_a, seq=1, amount=45_000)
    refused = psycopg.errors.InsufficientPrivilege
    for due in ("now() - interval '1 second'", "now() + interval '29 days'", "NULL"):
        with pytest.raises(refused, match="30 days"), as_app(shop_a.shop_id) as conn:
            conn.execute(f"UPDATE shop SET status = 'deletion_pending', deletion_due = {due}")
    with pytest.raises(refused, match="only by erase_shop"), as_app(shop_a.shop_id) as conn:
        conn.execute("UPDATE shop SET status = 'erased'")
    # Asked for properly, the wait cannot be shortened afterwards.
    with as_app(shop_a.shop_id) as conn:
        conn.execute("UPDATE shop SET status = 'deletion_pending', deletion_due = now() + interval '30 days'")
    with pytest.raises(refused, match="30 days"), as_app(shop_a.shop_id) as conn:
        conn.execute("UPDATE shop SET deletion_due = now()")
    # Since migration 0031 the application role may not call the function at all; the worker's role,
    # which may, is refused by the function itself while the wait lasts.
    with pytest.raises(refused, match="erase_shop"), as_app(None) as conn:
        conn.execute("SELECT erase_shop(%s)", (shop_a.shop_id,))
    with as_worker(None) as conn:
        assert conn.execute("SELECT erase_shop(%s)", (shop_a.shop_id,)).fetchone() == (False,)
    left = owner.execute("SELECT count(*) FROM ledger_entry WHERE shop_id = %s", (shop_a.shop_id,)).fetchone()
    assert left == (1,), "the entry must still be there"
    # Cancelling, and asking again, stay possible; so does everything else about the shop.
    with as_app(shop_a.shop_id) as conn:
        conn.execute("UPDATE shop SET status = 'active', deletion_due = NULL, name = 'Renamed'")
        conn.execute("UPDATE shop SET status = 'deletion_pending', deletion_due = now() + interval '31 days'")


def test_an_erased_shop_cannot_be_brought_back_by_the_application_role(
    as_app: AppSession, as_worker: AppSession, owner: psycopg.Connection, shop_a: Shop
) -> None:
    owner.execute(
        "UPDATE shop SET status = 'deletion_pending', deletion_due = now() - interval '1 hour' WHERE id = %s",
        (shop_a.shop_id,),
    )
    with as_worker(None) as conn:
        assert conn.execute("SELECT erase_shop(%s)", (shop_a.shop_id,)).fetchone() == (True,), (
            "erasure itself still works"
        )
    assert owner.execute("SELECT status FROM shop WHERE id = %s", (shop_a.shop_id,)).fetchone() == ("erased",)
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="stays erased"), as_app(shop_a.shop_id) as conn:
        changed = conn.execute("UPDATE shop SET status = 'active'")
        assert changed.rowcount == 1


def test_the_application_role_cannot_delete_an_entry_directly(
    as_app: AppSession, owner: psycopg.Connection, shop_a: Shop
) -> None:
    """The counterpart: the direct way is closed, which is what the test above shows a way around."""
    add_entry(owner, shop_a, seq=1, amount=45_000)
    with pytest.raises(psycopg.errors.InsufficientPrivilege), as_app(shop_a.shop_id) as conn:
        conn.execute("DELETE FROM ledger_entry")


# --- finding 3: a temporary table changes what a SECURITY DEFINER function reads ---------------------------


def test_a_temporary_table_cannot_stand_in_for_a_table_a_definer_function_reads(
    as_app: AppSession, owner: psycopg.Connection, shop_a: Shop, shop_b: Shop
) -> None:
    """The functions set `search_path = public`. PostgreSQL then still looks in the session's temporary
    schema first, and the application role may create temporary tables. A made-up `invitation` row makes
    `accept_staff_invitation` write a real manager membership in a shop of the attacker's choice."""
    attacker = shop_b.user_id
    with as_app(None) as conn:
        conn.execute("CREATE TEMP TABLE invitation (LIKE public.invitation)")
        conn.execute(
            "INSERT INTO invitation (token_hash, shop_id, kind, role, status, created_at) "
            "VALUES ('\\x01', %s, 'staff', 'manager', 'issued', now())",
            (shop_a.shop_id,),
        )
        conn.execute("SELECT accept_staff_invitation('\\x01', %s)", (attacker,))
    joined = owner.execute(
        "SELECT role FROM membership WHERE shop_id = %s AND user_id = %s", (shop_a.shop_id, attacker)
    ).fetchall()
    assert joined == [], "nobody may become a member of a shop without an invitation the shop issued"


# --- checked and found sound: how the SECURITY DEFINER functions are declared ------------------------------

_DEFINER_PROBLEMS = """
    SELECT p.oid::regprocedure::text AS name,
           NOT EXISTS (SELECT 1 FROM unnest(coalesce(p.proconfig, '{}')) AS setting
                        WHERE setting LIKE 'search_path=%%') AS no_search_path,
           has_function_privilege('public', p.oid, 'EXECUTE') AS public_may_execute,
           NOT EXISTS (SELECT 1 FROM unnest(coalesce(p.proconfig, '{}')) AS setting
                        WHERE setting ~ '^search_path=.*pg_temp$') AS temp_not_last
      FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
     WHERE p.prosecdef AND n.nspname IN ('public', 'measure')
"""


def _definer_problems(conn: psycopg.Connection) -> tuple[int, list[str]]:
    rows = conn.execute(_DEFINER_PROBLEMS).fetchall()
    return len(rows), sorted(name for name, no_path, public, temp in rows if no_path or public or temp)


def test_every_definer_function_pins_its_search_path_and_is_closed_to_public(owner: psycopg.Connection) -> None:
    """Each function that runs with its owner's rights names its search path and may be executed only by
    roles it was granted to, and the temporary schema comes last in it (finding 3)."""
    count, problems = _definer_problems(owner)
    assert count >= 14, "the migrations define at least fourteen such functions"
    assert problems == []


def test_the_definer_check_catches_a_function_declared_carelessly(database_url: str) -> None:
    """The counterpart: a function with neither precaution is reported. Rolled back, so it never exists."""
    with psycopg.connect(database_url) as conn:
        conn.execute("CREATE FUNCTION review_careless() RETURNS integer LANGUAGE sql SECURITY DEFINER AS 'SELECT 1'")
        _, problems = _definer_problems(conn)
        conn.rollback()
    assert problems == ["review_careless()"]


# --- finding 7: database errors carry the values that were being written -----------------------------------


def test_a_database_error_does_not_carry_personal_data(app_database_url: str, shop_a: Shop) -> None:
    """The webhook, the worker and the server all log unhandled exceptions with their text. SQLAlchemy puts
    the statement's parameters into that text unless told not to (`hide_parameters=True`), so a failed
    write logs the name and phone it was writing. The specification: logs hold "identifiers only"."""
    name, phone = "Maxfiy Ism " + "x" * 80, "+998901234567"

    async def failing_write() -> str:
        database = Database(app_database_url)
        try:
            async with database.tenant(shop_a.shop_id) as session:
                # 91 characters: refused by the length check of the table.
                await session.create_customer(
                    customer_id=uuid.uuid4(), display_name=name, name_norm="maxfiy", phone=phone
                )
        except Exception as error:
            return str(error)
        finally:
            await database.dispose()
        return ""

    text = asyncio.run(failing_write())
    assert text, "the write must have been refused"
    assert phone not in text
    assert "Maxfiy Ism" not in text


# --- finding 11: the application role's rights on the platform tables --------------------------------------

# Every table without a tenant policy: (table-level rights, columns that may be updated). A new platform
# table has to be listed here with what the application does with it, or the first test below fails.
_PLATFORM_RIGHTS: dict[str, tuple[set[str], set[str]]] = {
    "app_user": ({"SELECT", "INSERT"}, {"lang", "active_shop"}),
    "platform_setting": ({"SELECT"}, set()),
    # The administrators' tables belong to the administrators' role since migration 0031.
    "admin_account": (set(), set()),
    "admin_session": (set(), set()),
    "admin_audit": (set(), set()),
    "admin_request_key": (set(), set()),
    "user_session": ({"SELECT", "INSERT"}, {"revoked_at"}),
    "signin_replay": ({"SELECT", "INSERT"}, set()),
    # Queued only: delivering is the worker's. Of a queued row the role reads five columns and not the
    # recipient or the text (a column right, listed in tests/db/test_database_roles.py).
    "outbox_message": ({"INSERT"}, set()),
    "processed_update": ({"SELECT", "INSERT"}, set()),
    "job_run": ({"SELECT"}, set()),
    "chat_pending": ({"SELECT", "INSERT", "DELETE"}, set()),
    "alembic_version": (set(), set()),
    # The operations watch (migration 0035) is the worker's: the application holds nothing on its state.
    "ops_alert": (set(), set()),
    "ops_sample": (set(), set()),
}
_TABLE_RIGHTS = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")


def _rights_of_the_application(conn: psycopg.Connection) -> dict[str, tuple[set[str], set[str]]]:
    tables = conn.execute(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind = 'r' AND NOT c.relrowsecurity"
    ).fetchall()
    found: dict[str, tuple[set[str], set[str]]] = {}
    for (table,) in tables:
        held = {
            right
            for right in _TABLE_RIGHTS
            if conn.execute("SELECT has_table_privilege('qd_app', %s, %s)", (f"public.{table}", right)).fetchone()
            == (True,)
        }
        columns = conn.execute(
            "SELECT a.attname FROM pg_attribute a WHERE a.attrelid = %s::regclass AND a.attnum > 0 "
            "AND NOT a.attisdropped AND has_column_privilege('qd_app', a.attrelid, a.attnum, 'UPDATE')",
            (f"public.{table}",),
        ).fetchall()
        found[table] = (held, set() if "UPDATE" in held else {name for (name,) in columns})
    return found


def test_the_application_role_holds_exactly_these_rights_on_the_platform_tables(owner: psycopg.Connection) -> None:
    """Finding 11: per table, what the application does with it and nothing else."""
    assert _rights_of_the_application(owner) == _PLATFORM_RIGHTS


def test_the_rights_check_catches_a_table_granted_carelessly(database_url: str) -> None:
    """The counterpart: a new platform table handed over whole is reported. Rolled back, so it never exists."""
    with psycopg.connect(database_url) as conn:
        conn.execute("CREATE TABLE review_careless (id integer PRIMARY KEY)")
        conn.execute("GRANT ALL ON review_careless TO qd_app")
        found = _rights_of_the_application(conn)
        conn.rollback()
    assert found != _PLATFORM_RIGHTS
    assert found["review_careless"][0] == set(_TABLE_RIGHTS)


@pytest.mark.parametrize(
    "statement",
    [
        # The cards owners are told to pay to, the price, the switches.
        "INSERT INTO platform_setting (key, value, updated_by) VALUES ('payment_cards', '[]', 'x')",
        "UPDATE platform_setting SET value = '[]' WHERE key = 'payment_cards'",
        "DELETE FROM platform_setting",
        "TRUNCATE platform_setting",
        # Who a person is, and whether their one trial is used.
        "UPDATE app_user SET tg_id = 1 WHERE id = gen_random_uuid()",
        "UPDATE app_user SET trial_used_at = NULL",
        "DELETE FROM app_user WHERE id = gen_random_uuid()",
        # A session's owner and lifetime; removal belongs to the purge function.
        "UPDATE user_session SET expires_at = now() + interval '10 years'",
        "UPDATE user_session SET user_id = gen_random_uuid()",
        "UPDATE user_session SET token_hash = '\\x00'",
        "DELETE FROM user_session",
        "UPDATE admin_session SET expires_at = created_at + interval '8 hours'",
        "UPDATE admin_session SET user_id = gen_random_uuid()",
        "DELETE FROM admin_session",
        # Who is an administrator.
        "UPDATE admin_account SET status = 'active'",
        "DELETE FROM admin_account",
        "UPDATE admin_request_key SET response = '{}'",
        "DELETE FROM admin_request_key",
        # A queued message's recipient and text.
        "UPDATE outbox_message SET recipient = '1'",
        "UPDATE outbox_message SET payload = '{}'",
        "DELETE FROM outbox_message",
        "UPDATE processed_update SET update_id = update_id + 1",
        "DELETE FROM processed_update",
        # A used sign-in payload cannot be made usable again.
        "UPDATE signin_replay SET expires_at = now()",
        "DELETE FROM signin_replay",
        # Which migrations count as applied.
        "UPDATE alembic_version SET version_num = '0001'",
        "SELECT version_num FROM alembic_version",
    ],
)
def test_the_application_role_is_refused_what_it_no_longer_needs(as_app: AppSession, statement: str) -> None:
    """Finding 11, one removed right at a time: the application role tries it and the database refuses."""
    with pytest.raises(psycopg.errors.InsufficientPrivilege), as_app(None) as conn:
        conn.execute(statement)


def test_the_application_role_still_does_what_the_application_does(
    owner: psycopg.Connection, as_app: AppSession, as_worker: AppSession, shop_a: Shop
) -> None:
    """The other side of the same change: the statements the application really runs are not refused."""
    token, message = uuid.uuid4().bytes * 2, uuid.uuid4()
    with as_app(None) as conn:
        assert conn.execute("SELECT count(*) FROM platform_setting").fetchone() is not None
        conn.execute(
            "UPDATE app_user SET lang = 'ru', active_shop = %s WHERE id = %s", (shop_a.shop_id, shop_a.user_id)
        )
        conn.execute(
            "INSERT INTO user_session (id, token_hash, user_id, kind, expires_at) "
            "VALUES (gen_random_uuid(), %s, %s, 'webapp', now() + interval '1 hour')",
            (token, shop_a.user_id),
        )
        conn.execute("UPDATE user_session SET revoked_at = now() WHERE token_hash = %s", (token,))
        conn.execute(
            "INSERT INTO outbox_message (id, channel, recipient, payload) VALUES (%s, 'telegram', '1', '{}')",
            (message,),
        )
    with as_worker(None) as conn:  # marking a message is the worker's since migration 0031
        conn.execute("UPDATE outbox_message SET status = 'sent', sent_at = now() WHERE id = %s", (message,))
    assert owner.execute("SELECT lang FROM app_user WHERE id = %s", (shop_a.user_id,)).fetchone() == ("ru",)
    assert owner.execute("SELECT status FROM outbox_message WHERE id = %s", (message,)).fetchone() == ("sent",)


# --- finding 11: a setting is changed only by an administrator the database itself finds -------------------

_SET = "SELECT admin_set_platform_setting(%s, %s, %s::jsonb, %s, %s::jsonb, now())"


def _administrator(
    conn: psycopg.Connection, *, status: str = "active", confirmed: bool = True, session: str | None = "open"
) -> uuid.UUID:
    admin = uuid.uuid4()
    conn.execute("INSERT INTO app_user (id, tg_id) VALUES (%s, %s)", (admin, uuid.uuid4().int % 10**15))
    conn.execute(
        "INSERT INTO admin_account (user_id, totp_secret, status, confirmed_at) VALUES (%s, '\\x00', %s, %s)",
        (admin, status, "now()" if confirmed else None),
    )
    if session is not None:
        # created, expires, revoked: relative to now
        created, expires, revoked = {
            "open": ("-1 hour", "1 hour", None),
            "expired": ("-9 hours", "-1 hour", None),
            "revoked": ("-1 hour", "1 hour", "now()"),
        }[session]
        conn.execute(
            "INSERT INTO admin_session (id, token_hash, user_id, created_at, expires_at, revoked_at) "
            "VALUES (gen_random_uuid(), %s, %s, now() + %s::interval, now() + %s::interval, %s)",
            (uuid.uuid4().bytes * 2, admin, created, expires, revoked),
        )
    return admin


def _setting_rows(conn: psycopg.Connection, key: str) -> tuple[list[tuple[object, ...]], int]:
    stored = conn.execute("SELECT value, updated_by FROM platform_setting WHERE key = %s", (key,)).fetchall()
    audited = conn.execute(
        "SELECT count(*) FROM admin_audit WHERE target_type = 'setting' AND target_id = %s", (key,)
    ).fetchone()
    assert audited is not None
    return stored, int(audited[0])


def test_an_administrator_changes_a_setting_and_the_audit_row_is_written_with_it(
    owner: psycopg.Connection, as_admin: AppSession
) -> None:
    admin, key = _administrator(owner), f"review_{uuid.uuid4().hex}"
    try:
        with as_admin(None) as conn:
            first = conn.execute(_SET, (admin, key, "41", "why", '{"before": null, "after": 41}')).fetchone()
            second = conn.execute(_SET, (admin, key, "42", None, None)).fetchone()
        assert first == (True,) and second == (True,)
        assert _setting_rows(owner, key) == ([(42, str(admin))], 2)
        row = owner.execute(
            "SELECT admin_id, action, reason, detail FROM admin_audit WHERE target_id = %s ORDER BY reason NULLS LAST",
            (key,),
        ).fetchone()
        assert row == (admin, "setting.changed", "why", {"before": None, "after": 41})
    finally:
        owner.execute("DELETE FROM platform_setting WHERE key = %s", (key,))


@pytest.mark.parametrize(
    "who",
    [
        {"status": "disabled"},
        {"confirmed": False},
        {"session": None},
        {"session": "expired"},
        {"session": "revoked"},
        None,  # somebody who is no administrator at all
    ],
    ids=["disabled", "never confirmed", "no session", "expired session", "revoked session", "not an administrator"],
)
def test_nobody_else_changes_a_setting_through_the_function(
    owner: psycopg.Connection, as_admin: AppSession, shop_a: Shop, who: dict[str, object] | None
) -> None:
    """The check is the database's: the application role cannot write the table, and the function writes
    nothing, setting or audit row, for anybody but an active, confirmed administrator with an open session."""
    actor = shop_a.user_id if who is None else _administrator(owner, **who)  # type: ignore[arg-type]
    key = f"review_{uuid.uuid4().hex}"
    with as_admin(None) as conn:
        assert conn.execute(_SET, (actor, key, "1", None, None)).fetchone() == (False,)
    assert _setting_rows(owner, key) == ([], 0)


def test_only_one_role_may_call_each_of_the_new_functions(owner: psycopg.Connection) -> None:
    """Each belongs to one part of the application since migration 0031."""
    for function, role in (
        ("admin_set_platform_setting(uuid, text, jsonb, text, jsonb, timestamptz)", "qd_admin"),
        ("purge_expired_sign_ins()", "qd_worker"),
        ("claim_owned_shop(uuid, boolean)", "qd_app"),
    ):
        row = owner.execute(
            "SELECT array(SELECT r FROM unnest(ARRAY['qd_app', 'qd_admin', 'qd_worker', 'public']) AS r "
            "WHERE has_function_privilege(r, %s, 'EXECUTE'))",
            (function,),
        ).fetchone()
        assert row == ([role],), function


# --- finding 9: used sign-in data and dead sessions are purged ---------------------------------------------


def test_the_purge_removes_what_is_dead_and_keeps_what_is_alive(
    owner: psycopg.Connection, as_worker: AppSession, shop_a: Shop
) -> None:
    def session(expires: str, revoked: bool) -> bytes:
        token = uuid.uuid4().bytes * 2
        owner.execute(
            "INSERT INTO user_session (id, token_hash, user_id, kind, created_at, expires_at, revoked_at) "
            "VALUES (gen_random_uuid(), %s, %s, 'webapp', now() - interval '20 days', now() + %s::interval, %s)",
            (token, shop_a.user_id, expires, "now()" if revoked else None),
        )
        return token

    def replay(expires: str) -> bytes:
        used = uuid.uuid4().bytes * 2
        owner.execute(
            "INSERT INTO signin_replay (payload_hash, expires_at) VALUES (%s, now() + %s::interval)", (used, expires)
        )
        return used

    def request_key(age: str) -> str:
        key = uuid.uuid4().hex
        owner.execute(
            "INSERT INTO admin_request_key (admin_id, key, response, created_at) "
            "VALUES (%s, %s, '{}', now() - %s::interval)",
            (shop_a.user_id, key, age),
        )
        return key

    live, expired, revoked = session("1 hour", False), session("-1 minute", False), session("1 hour", True)
    fresh, stale = replay("10 minutes"), replay("-1 second")
    open_admin, expired_admin = _administrator(owner), _administrator(owner, session="expired")
    revoked_admin = _administrator(owner, session="revoked")
    new_key, old_key = request_key("29 days"), request_key("31 days")

    with as_worker(None) as conn:
        removed = conn.execute("SELECT purge_expired_sign_ins()").fetchone()
    assert removed is not None and removed[0] >= 6

    def left(table: str, column: str, values: list[object]) -> set[object]:
        rows = owner.execute(f"SELECT {column} FROM {table} WHERE {column} = ANY(%s)", (values,)).fetchall()
        return {bytes(row[0]) if isinstance(row[0], (bytes, memoryview)) else row[0] for row in rows}

    assert left("user_session", "token_hash", [live, expired, revoked]) == {live}
    assert left("signin_replay", "payload_hash", [fresh, stale]) == {fresh}
    assert left("admin_session", "user_id", [open_admin, expired_admin, revoked_admin]) == {open_admin}
    assert left("admin_request_key", "key", [new_key, old_key]) == {new_key}


# --- finding 13: one trial per person; no limit on shops (DEC-065, migration 0029) ---------------------------

_CLAIM = "SELECT claim_owned_shop(%s, %s)"


def _person(conn: psycopg.Connection) -> uuid.UUID:
    person = uuid.uuid4()
    conn.execute("INSERT INTO app_user (id, tg_id) VALUES (%s, %s)", (person, uuid.uuid4().int % 10**15))
    return person


def _owned_shop(conn: psycopg.Connection, person: uuid.UUID, *, role: str = "owner", status: str = "active") -> None:
    shop = uuid.uuid4()
    conn.execute("INSERT INTO shop (id, name, status) VALUES (%s, 'Shop', %s)", (shop, status))
    conn.execute(
        "INSERT INTO membership (id, shop_id, user_id, role) VALUES (gen_random_uuid(), %s, %s, %s)",
        (shop, person, role),
    )


def test_a_person_gets_one_trial(owner: psycopg.Connection, as_app: AppSession) -> None:
    person = _person(owner)
    with as_app(None) as conn:
        assert conn.execute(_CLAIM, (person, False)).fetchone() == ("limited",), "trials off: none is used up"
        assert conn.execute(_CLAIM, (person, True)).fetchone() == ("trial",)
        assert conn.execute(_CLAIM, (person, True)).fetchone() == ("limited",)
    assert owner.execute("SELECT trial_used_at IS NOT NULL FROM app_user WHERE id = %s", (person,)).fetchone() == (
        True,
    )


def test_a_trial_claimed_in_a_transaction_that_fails_is_not_used_up(
    owner: psycopg.Connection, database_url: str
) -> None:
    person = _person(owner)
    with psycopg.connect(database_url) as conn:
        conn.execute("SET ROLE qd_app")
        assert conn.execute(_CLAIM, (person, True)).fetchone() == ("trial",)
        conn.rollback()
    assert owner.execute("SELECT trial_used_at FROM app_user WHERE id = %s", (person,)).fetchone() == (None,)


def test_no_number_of_shops_is_refused(owner: psycopg.Connection, as_app: AppSession) -> None:
    """Migration 0027 answered 'refused' from the sixth shop on; 0029 took the limit away (DEC-065)."""
    person = _person(owner)
    for _ in range(12):
        _owned_shop(owner, person)
    with as_app(None) as conn:
        assert conn.execute(_CLAIM, (person, True)).fetchone() == ("trial",)
        assert conn.execute(_CLAIM, (person, True)).fetchone() == ("limited",)
        assert conn.execute(_CLAIM, (person, False)).fetchone() == ("limited",)
    source = owner.execute("SELECT prosrc FROM pg_proc WHERE proname = 'claim_owned_shop'").fetchone()
    assert source is not None and "refused" not in source[0] and "count(" not in source[0]
    reassign = owner.execute("SELECT prosrc FROM pg_proc WHERE proname = 'admin_reassign_owner'").fetchone()
    assert reassign is not None and "too_many_shops" not in reassign[0]


def test_two_claims_of_a_trial_are_taken_one_after_the_other_and_nothing_else_waits(
    owner: psycopg.Connection, database_url: str
) -> None:
    """One trial for a person even when two shops are opened at the same moment: the second claim of a
    trial waits for the first transaction to end. Nothing is counted any more (DEC-065), so a claim that
    asks for no trial, and anybody else's claim, wait for nothing."""
    person, other = _person(owner), _person(owner)
    with psycopg.connect(database_url) as first, psycopg.connect(database_url) as second:
        for conn in (first, second):
            conn.execute("SET ROLE qd_app")
        assert first.execute(_CLAIM, (person, True)).fetchone() == ("trial",)
        second.execute("SET lock_timeout = '300ms'")
        assert second.execute(_CLAIM, (other, True)).fetchone() == ("trial",)
        assert second.execute(_CLAIM, (person, False)).fetchone() == ("limited",)
        with pytest.raises(psycopg.errors.LockNotAvailable):
            second.execute(_CLAIM, (person, True))
        second.rollback()
        first.commit()
        second.execute("SET ROLE qd_app")
        assert second.execute(_CLAIM, (person, True)).fetchone() == ("limited",), "the trial was taken"
        second.rollback()

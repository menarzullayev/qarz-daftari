"""Separate database roles (migration 0031; security review, finding 11).

Three roles connect to the database, one for each part of the application:

- `qd_app`     the ordinary application: shop members, customers, sign-in, the bot's chat;
- `qd_admin`   the administrators' side;
- `qd_worker`  the worker.

Two tables in this file say everything each of them may do: `DEFINER_FUNCTIONS` (who may execute each
function that runs with its owner's rights) and `TABLE_RIGHTS` (what each role holds on each table). The
tests compare both with the database, so a function or a table added later has to be listed here with
the roles that really need it, and then try, connected as each role, everything the tables say it may
not do: the database must refuse each attempt.
"""

import re
import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

from ..conftest import AppSession, Shop, add_entry

pytestmark = pytest.mark.db

APP, ADMIN, WORKER = "qd_app", "qd_admin", "qd_worker"
ROLES = (APP, ADMIN, WORKER)
NOBODY: set[str] = set()
MIGRATION = Path(__file__).resolve().parents[2] / "migrations" / "sql" / "0031_separate_roles.sql"

# Every SECURITY DEFINER function, with the roles that may execute it. Nobody else may: not PUBLIC, and
# no role that is not one of the three. A function a trigger runs is called by nobody.
DEFINER_FUNCTIONS: dict[str, set[str]] = {
    # --- the ordinary application ---------------------------------------------------------------------
    "accept_staff_invitation(bytea,uuid)": {APP},
    "claim_owned_shop(uuid,boolean)": {APP},
    "customer_token_info(bytea)": {APP},
    "link_customer(bytea,uuid,smallint,text)": {APP},
    "end_my_link(uuid,uuid)": {APP},
    "my_accounts(uuid)": {APP},
    "my_link(uuid,uuid)": {APP},
    "my_memberships(uuid)": {APP},
    "mark_recipient_reachable(uuid)": {APP},
    "online_payment_shop(uuid)": {APP},
    "online_payment_shop_by_txn(text,text)": {APP},
    "payme_statement(bigint,bigint)": {APP},
    "subscription_receipt_copies(uuid)": {APP},
    # Whom to tell that a receipt waits: two columns of the administrators' accounts, nothing else.
    "admin_notice_recipients()": {APP},
    # A Telegram administrator of the review group decides a receipt in the chat (DEC-064); the chat is
    # the ordinary application's.
    "review_group_receipt(bigint,uuid,boolean)": {APP},
    "review_group_decide_receipt(bigint,bigint,uuid,text,smallint,text,text,date,text,jsonb,"
    "timestamp with time zone)": {APP},
    # --- the administrators' side ---------------------------------------------------------------------
    "admin_shop_search(uuid,date,text,text,uuid,timestamp with time zone,uuid,integer)": {ADMIN},
    "admin_shop_receipts(uuid,uuid)": {ADMIN},
    "admin_lock_subscription(uuid,uuid)": {ADMIN},
    "admin_store_subscription(uuid,uuid,text,date,date,text,timestamp with time zone)": {ADMIN},
    "admin_receipts(uuid,text,timestamp with time zone,uuid,integer)": {ADMIN},
    "admin_receipt(uuid,uuid,boolean)": {ADMIN},
    "admin_receipt_copies(uuid,uuid)": {ADMIN},
    "admin_decide_receipt(uuid,uuid,text,smallint,text,timestamp with time zone)": {ADMIN},
    "admin_shop_activity(uuid,uuid,text,uuid)": {ADMIN},
    "admin_open_shop(uuid,uuid,timestamp with time zone)": {ADMIN},
    "admin_support_open(uuid,uuid,uuid,text,timestamp with time zone,timestamp with time zone)": {ADMIN},
    "admin_support_close(uuid,uuid,timestamp with time zone)": {ADMIN},
    "admin_support_list(uuid,uuid,boolean,timestamp with time zone,timestamp with time zone,uuid,integer)": {ADMIN},
    "admin_reassign_owner(uuid,uuid,bigint,text,timestamp with time zone)": {ADMIN},
    "admin_set_platform_setting(uuid,text,jsonb,text,jsonb,timestamp with time zone)": {ADMIN},
    # --- the worker -----------------------------------------------------------------------------------
    "mark_recipient_unreachable(bigint)": {WORKER},
    "shops_due_for_reminders(smallint)": {WORKER},
    "subscriptions_to_review(date)": {WORKER},
    "shops_to_erase()": {WORKER},
    "erase_shop(uuid)": {WORKER},
    "claim_export_job(timestamp with time zone,timestamp with time zone)": {WORKER},
    "claim_import_batch(timestamp with time zone,timestamp with time zone)": {WORKER},
    "shops_with_receipt_work(timestamp with time zone,timestamp with time zone)": {WORKER},
    "purge_expired_sign_ins()": {WORKER},
    # The operations watch (migration 0035): in how many places the stored open debts differ from the
    # ledger. A count; the comparison itself stays closed to everybody.
    "open_debt_mismatch_count()": {WORKER},
    # --- held by two roles ----------------------------------------------------------------------------
    # How long the oldest receipt has waited: one moment. The API serves it at /metrics, and the worker's
    # operations watch reads it for the same rule (migration 0035).
    "oldest_waiting_receipt()": {APP, WORKER},
    # Completing a customer's removal forgets a person nobody else knows. The API completes a removal
    # when the customer asks or the debt is settled; the worker when undoing an import settles it.
    "forget_user_if_unused(uuid)": {APP, WORKER},
    # --- called by nobody: run by triggers, or used inside other functions ------------------------------
    "open_debt_after_entries()": NOBODY,
    "open_debt_after_promises()": NOBODY,
    "open_debt_mismatches(uuid)": NOBODY,
    "open_debts_of(uuid[])": NOBODY,
    "refresh_open_debts(uuid[])": NOBODY,
}

# Every table, and what each role holds on it: (qd_app, qd_admin, qd_worker). A right followed by column
# names is held on those columns only. An empty string is no right at all. No role holds TRUNCATE,
# REFERENCES or TRIGGER on anything; the test would show them here if one did.
TABLE_RIGHTS: dict[str, tuple[str, str, str]] = {
    # --- platform tables: no tenant, no row-level security ----------------------------------------------
    "app_user": ("SELECT; INSERT; UPDATE(lang, active_shop)", "SELECT(id, tg_id)", "SELECT(id, tg_id, lang)"),
    "platform_setting": ("SELECT", "SELECT", "SELECT"),
    "user_session": ("SELECT; INSERT; UPDATE(revoked_at)", "", ""),
    "signin_replay": ("SELECT; INSERT", "", ""),
    "processed_update": ("SELECT; INSERT", "", ""),
    "chat_pending": ("SELECT; INSERT; DELETE", "", ""),
    "admin_account": (
        "",
        "SELECT; INSERT; UPDATE(totp_secret, confirmed_at, failed_codes, locked_until, last_step)",
        "",
    ),
    "admin_session": ("", "SELECT; INSERT; UPDATE(revoked_at)", ""),
    "admin_audit": ("", "SELECT; INSERT", ""),
    "admin_request_key": ("", "SELECT; INSERT", ""),
    "outbox_message": (
        "INSERT; SELECT(id, channel, dedupe_key, status, next_try_at)",
        "INSERT; SELECT(id, dedupe_key)",
        "SELECT; INSERT; UPDATE(status, attempts, next_try_at, sent_at)",
    ),
    "job_run": ("SELECT", "", "SELECT; INSERT"),
    # The state of the operations watch and the samples it compares (migration 0035): the worker's alone.
    "ops_alert": ("", "", "SELECT; INSERT; UPDATE; DELETE"),
    "ops_sample": ("", "", "SELECT; INSERT; DELETE"),
    "alembic_version": ("", "", ""),
    "measure.event": ("SELECT; INSERT", "INSERT", "SELECT; INSERT"),
    "measure.weekly": ("", "", "SELECT; INSERT; UPDATE"),
    # --- tenant tables: one shop at a time, under row-level security -----------------------------------
    "shop": ("SELECT; INSERT; UPDATE; DELETE", "", "SELECT"),
    "membership": ("SELECT; INSERT; UPDATE; DELETE", "", "SELECT"),
    "invitation": ("SELECT; INSERT; UPDATE; DELETE", "", "SELECT; UPDATE"),
    "ownership_transfer": ("SELECT; INSERT; UPDATE", "", ""),
    "subscription": ("SELECT; INSERT; UPDATE; DELETE", "", "SELECT; UPDATE"),
    "subscription_receipt": ("SELECT; INSERT", "", ""),
    "online_payment": ("SELECT; INSERT; UPDATE", "", ""),
    "support_access": ("SELECT; INSERT; UPDATE(closed_at, closed_by)", "", ""),
    "request_key": ("SELECT; INSERT; UPDATE; DELETE", "", ""),
    "catalog_item": ("SELECT; INSERT; UPDATE; DELETE", "", ""),
    "customer": ("SELECT; INSERT; UPDATE; DELETE", "SELECT", "SELECT; INSERT; UPDATE"),
    "customer_link": ("SELECT; INSERT; UPDATE; DELETE", "", "SELECT; UPDATE"),
    "removal_request": ("SELECT; INSERT; UPDATE; DELETE", "", "SELECT; INSERT; UPDATE"),
    "ledger_entry": ("SELECT; INSERT", "SELECT", "SELECT; INSERT"),
    "goods_line": ("SELECT; INSERT", "SELECT", "SELECT"),
    "promise": ("SELECT; INSERT", "SELECT", "SELECT; INSERT"),
    "open_debt": ("SELECT", "", ""),
    "dispute": ("SELECT; INSERT; UPDATE; DELETE", "SELECT", "SELECT; UPDATE"),
    "date_change_request": ("SELECT; INSERT; UPDATE; DELETE", "SELECT", "SELECT; UPDATE"),
    "payment_notice": ("SELECT; INSERT; UPDATE; DELETE", "SELECT", "SELECT; UPDATE"),
    "stored_file": ("SELECT; INSERT; UPDATE; DELETE", "SELECT", "SELECT; INSERT; UPDATE; DELETE"),
    "reminder": ("SELECT; INSERT; UPDATE; DELETE", "", "SELECT; INSERT"),
    "export_job": ("SELECT; INSERT; UPDATE", "", "SELECT; UPDATE"),
    "import_batch": ("SELECT; INSERT; UPDATE; DELETE", "", "SELECT; UPDATE"),
    "activity": ("SELECT; INSERT", "INSERT", "INSERT"),
}

_TABLE_LEVEL = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
_COLUMN_LEVEL = ("SELECT", "INSERT", "UPDATE", "REFERENCES")


# --- reading what the database really grants ------------------------------------------------------------


def _function_grants(conn: psycopg.Connection) -> dict[str, set[str]]:
    """Each SECURITY DEFINER function with everyone who may execute it, the owner aside: the access list
    itself is read, so a grant to a role nobody thought of shows up too."""
    rows = conn.execute(
        "SELECT p.oid::regprocedure::text, "
        "       array(SELECT CASE WHEN a.grantee = 0 THEN 'public' ELSE a.grantee::regrole::text END "
        "               FROM aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a "
        "              WHERE a.privilege_type = 'EXECUTE' AND a.grantee <> p.proowner) "
        "  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
        " WHERE p.prosecdef AND n.nspname IN ('public', 'measure')"
    ).fetchall()
    return {name: set(grantees) for name, grantees in rows}


def _tables(conn: psycopg.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT CASE WHEN n.nspname = 'public' THEN c.relname ELSE n.nspname || '.' || c.relname END "
        "  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        " WHERE n.nspname IN ('public', 'measure') AND c.relkind IN ('r', 'p', 'v', 'm', 'f') ORDER BY 1"
    ).fetchall()
    return [name for (name,) in rows]


def _columns(conn: psycopg.Connection, table: str, *, writable: bool = False) -> list[str]:
    """The table's columns; with `writable`, without those the database computes itself, which nobody
    can assign to whatever their rights."""
    rows = conn.execute(
        "SELECT attname, attgenerated <> '' OR attidentity = 'a' FROM pg_attribute "
        "WHERE attrelid = %s::regclass AND attnum > 0 AND NOT attisdropped ORDER BY attnum",
        (table,),
    ).fetchall()
    return [name for name, computed in rows if not (writable and computed)]


def _held(conn: psycopg.Connection, role: str, table: str) -> str:
    """What the role holds on the table, in the words of TABLE_RIGHTS."""

    def has(right: str, column: str | None = None) -> bool:
        if column is None:
            row = conn.execute("SELECT has_table_privilege(%s, %s, %s)", (role, table, right)).fetchone()
        else:
            row = conn.execute("SELECT has_column_privilege(%s, %s, %s, %s)", (role, table, column, right)).fetchone()
        return row == (True,)

    whole = [right for right in _TABLE_LEVEL if has(right)]
    words = list(whole)
    for right in _COLUMN_LEVEL:
        if right not in whole:
            columns = [column for column in _columns(conn, table) if has(right, column)]
            if columns:
                words.append(f"{right}({', '.join(columns)})")
    return "; ".join(words)


def _table_rights(conn: psycopg.Connection) -> dict[str, tuple[str, ...]]:
    return {table: tuple(_held(conn, role, table) for role in ROLES) for table in _tables(conn)}


# --- the two tables agree with the database -------------------------------------------------------------


def test_every_definer_function_is_executable_by_exactly_the_roles_listed(owner: psycopg.Connection) -> None:
    """A function added later must be listed in DEFINER_FUNCTIONS with the roles that call it; one granted
    to a role that is not listed for it, to PUBLIC, or to a role that is none of the three, fails here."""
    assert _function_grants(owner) == DEFINER_FUNCTIONS


def test_the_function_check_catches_a_grant_that_is_too_wide(database_url: str) -> None:
    """The counterpart, four ways. All rolled back, so none of it ever exists."""
    with psycopg.connect(database_url) as conn:
        conn.execute("GRANT EXECUTE ON FUNCTION erase_shop(uuid) TO qd_app")
        assert _function_grants(conn)["erase_shop(uuid)"] == {WORKER, APP}
        conn.rollback()

        conn.execute(
            "GRANT EXECUTE ON FUNCTION admin_set_platform_setting(uuid,text,jsonb,text,jsonb,timestamptz) TO PUBLIC"
        )
        found = _function_grants(conn)
        assert found != DEFINER_FUNCTIONS
        assert "public" in found["admin_set_platform_setting(uuid,text,jsonb,text,jsonb,timestamp with time zone)"]
        conn.rollback()

        # A new function nobody listed, declared with the usual precautions and granted to the worker.
        conn.execute(
            "CREATE FUNCTION review_unlisted() RETURNS integer LANGUAGE sql SECURITY DEFINER "
            "SET search_path = public, pg_temp AS 'SELECT 1'"
        )
        conn.execute("REVOKE ALL ON FUNCTION review_unlisted() FROM PUBLIC")
        conn.execute("GRANT EXECUTE ON FUNCTION review_unlisted() TO qd_worker")
        found = _function_grants(conn)
        assert found != DEFINER_FUNCTIONS and found["review_unlisted()"] == {WORKER}
        conn.rollback()

        # A role that is none of the three.
        conn.execute("CREATE ROLE review_stranger NOLOGIN")
        conn.execute("GRANT EXECUTE ON FUNCTION purge_expired_sign_ins() TO review_stranger")
        assert _function_grants(conn)["purge_expired_sign_ins()"] == {WORKER, "review_stranger"}
        conn.rollback()
    with psycopg.connect(database_url) as conn:
        assert _function_grants(conn) == DEFINER_FUNCTIONS, "nothing of the above was kept"


def test_every_table_gives_each_role_exactly_the_rights_listed(owner: psycopg.Connection) -> None:
    """Per table and per role, what the code of that part does with it and nothing else. A new table has
    to be listed in TABLE_RIGHTS, or this fails."""
    assert _table_rights(owner) == TABLE_RIGHTS


def test_the_table_check_catches_a_right_granted_carelessly(database_url: str) -> None:
    """The counterpart: a table handed over whole, one more right on an existing table, one more column."""
    with psycopg.connect(database_url) as conn:
        conn.execute("CREATE TABLE review_careless (id integer PRIMARY KEY)")
        conn.execute("GRANT ALL ON review_careless TO qd_worker")
        found = _table_rights(conn)
        assert found != TABLE_RIGHTS
        assert found["review_careless"] == ("", "", "SELECT; INSERT; UPDATE; DELETE; TRUNCATE; REFERENCES; TRIGGER")
        conn.rollback()

        conn.execute("GRANT SELECT ON admin_account TO qd_app")
        assert _table_rights(conn)["admin_account"][0] == "SELECT"
        conn.rollback()

        conn.execute("GRANT SELECT (payload) ON outbox_message TO qd_app")
        assert (
            _table_rights(conn)["outbox_message"][0]
            == "INSERT; SELECT(id, channel, payload, dedupe_key, status, next_try_at)"
        )
        conn.rollback()


# --- what kind of roles they are ------------------------------------------------------------------------


def test_the_roles_cannot_bypass_row_level_security_and_are_members_of_nothing(owner: psycopg.Connection) -> None:
    """No special attribute, and no role is a member of another: a membership would hand one part the
    rights of another (which is exactly what the documented way back to one role does on purpose)."""
    rows = owner.execute(
        "SELECT rolname, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolreplication FROM pg_roles "
        "WHERE rolname = ANY(%s) ORDER BY rolname",
        (list(ROLES),),
    ).fetchall()
    assert rows == [(role, False, False, False, False, False) for role in sorted(ROLES)]
    memberships = owner.execute(
        "SELECT r.rolname, m.rolname FROM pg_auth_members a "
        "JOIN pg_roles r ON r.oid = a.roleid JOIN pg_roles m ON m.oid = a.member "
        "WHERE r.rolname = ANY(%s) OR m.rolname = ANY(%s)",
        (list(ROLES), list(ROLES)),
    ).fetchall()
    assert memberships == []


def test_the_migration_creates_the_roles_without_a_login_or_a_password() -> None:
    """Like qd_app (DEC-021): the login and the password are set at deploy, never in the repository. The
    test database gives the roles a login itself, so this reads the migration."""
    text = MIGRATION.read_text(encoding="utf-8")
    statements = re.sub(r"--[^\n]*", "", text)
    created = re.findall(r"CREATE ROLE (\w+) ([^;]*);", statements)
    assert created == [("qd_admin", "NOLOGIN NOBYPASSRLS"), ("qd_worker", "NOLOGIN NOBYPASSRLS")]
    assert "PASSWORD" not in statements.upper()
    assert not re.search(r"\bLOGIN\b", statements.upper().replace("NOLOGIN", ""))


# --- row-level security binds the new roles as it binds qd_app -------------------------------------------


@pytest.mark.parametrize("role", [ADMIN, WORKER])
def test_a_new_role_sees_one_shop_at_a_time_and_none_without_a_tenant(
    request: pytest.FixtureRequest, owner: psycopg.Connection, shop_a: Shop, shop_b: Shop, role: str
) -> None:
    session: AppSession = request.getfixturevalue("as_admin" if role == ADMIN else "as_worker")
    add_entry(owner, shop_a, seq=1, amount=10_000)
    add_entry(owner, shop_b, seq=1, amount=20_000)
    with session(shop_a.shop_id) as conn:
        assert conn.execute("SELECT id FROM customer").fetchall() == [(shop_a.customer_id,)]
        assert conn.execute("SELECT amount FROM ledger_entry").fetchall() == [(10_000,)]
        assert conn.execute("SELECT count(*) FROM customer WHERE shop_id = %s", (shop_b.shop_id,)).fetchone() == (0,)
    with session(None) as conn:
        assert conn.execute("SELECT count(*) FROM customer").fetchone() == (0,)
        assert conn.execute("SELECT count(*) FROM ledger_entry").fetchone() == (0,)


def test_the_worker_cannot_write_a_row_into_another_shop(as_worker: AppSession, shop_a: Shop, shop_b: Shop) -> None:
    with (
        pytest.raises(psycopg.errors.InsufficientPrivilege, match="row-level security"),
        as_worker(shop_a.shop_id) as conn,
    ):
        conn.execute(
            "INSERT INTO customer (id, shop_id, display_name, name_norm) VALUES (%s, %s, 'X', 'x')",
            (uuid.uuid4(), shop_b.shop_id),
        )


# --- connected as each role: everything the tables say it may not do is refused ---------------------------


def _call(signature: str) -> str:
    """A call of the function with a NULL of each parameter's type. The right to execute is checked
    before the function runs, so what it would do with NULLs never matters."""
    name, _, parameters = signature.partition("(")
    types = [part for part in parameters.rstrip(")").split(",") if part]
    return f"SELECT {name}({', '.join(f'NULL::{kind}' for kind in types)})"


def _probes(conn: psycopg.Connection, role: str) -> Iterator[tuple[str, str]]:
    """One statement for each right the role does not hold: (what it tries, the statement)."""
    index = ROLES.index(role)
    for signature, may in DEFINER_FUNCTIONS.items():
        if role not in may:
            yield f"execute {signature}", _call(signature)
    for table, rights in TABLE_RIGHTS.items():
        held = rights[index]
        columns = _columns(conn, table)
        on_columns = {right: re.search(rf"{right}\(([^)]*)\)", held) for right in ("SELECT", "UPDATE")}
        whole = {part.strip() for part in re.sub(r"\([^)]*\)", "()", held).split(";") if "(" not in part}
        if "SELECT" not in whole:
            match = on_columns["SELECT"]
            allowed = set() if match is None else set(match.group(1).split(", "))
            for column in columns:
                if column not in allowed:
                    yield f"read {table}.{column}", f'SELECT "{column}" FROM {table} LIMIT 1'
        if "INSERT" not in whole:
            yield f"insert into {table}", f"INSERT INTO {table} DEFAULT VALUES"
        if "UPDATE" not in whole:
            match = on_columns["UPDATE"]
            allowed = set() if match is None else set(match.group(1).split(", "))
            for column in _columns(conn, table, writable=True):
                if column not in allowed:
                    yield f"update {table}.{column}", f'UPDATE {table} SET "{column}" = "{column}"'
        if "DELETE" not in whole:
            yield f"delete from {table}", f"DELETE FROM {table}"
        yield f"truncate {table}", f"TRUNCATE {table}"


@pytest.mark.parametrize("role", ROLES)
def test_connected_as_the_role_everything_it_was_not_granted_is_refused(
    database_url: str, shop_a: Shop, role: str
) -> None:
    """Each statement is really run, as the role, with a shop set (so a refusal is the missing right and
    not an empty table). Nothing is committed. Any that is not refused for lack of privilege is listed."""
    not_refused: list[str] = []
    tried = 0
    with psycopg.connect(database_url) as conn:
        probes = list(_probes(conn, role))
        conn.execute(psycopg.sql.SQL("SET ROLE {}").format(psycopg.sql.Identifier(role)))
        conn.execute("SELECT set_config('qd.shop_id', %s, true)", (str(shop_a.shop_id),))
        for what, statement in probes:
            tried += 1
            conn.execute("SAVEPOINT probe")
            try:
                conn.execute(statement)  # type: ignore[arg-type]
            except psycopg.errors.InsufficientPrivilege as refusal:
                # "permission denied for ...", never the row-level security kind of refusal.
                if "permission denied" not in str(refusal):
                    not_refused.append(f"{what}: {refusal}")
            except psycopg.Error as other:
                not_refused.append(f"{what}: {type(other).__name__}")
            else:
                not_refused.append(what)
            conn.execute("ROLLBACK TO SAVEPOINT probe")
        conn.rollback()
    assert not_refused == []
    # The lists are long because the roles are narrow: a role that may do almost everything would pass
    # an empty list.
    assert tried > 150, tried


def test_the_refusal_check_catches_a_right_the_tables_do_not_know(database_url: str, shop_a: Shop) -> None:
    """The counterpart: with one function and one table handed to the ordinary role behind the tables'
    back, the same probes find exactly those. Rolled back."""
    with psycopg.connect(database_url) as conn:
        conn.execute("GRANT EXECUTE ON FUNCTION purge_expired_sign_ins() TO qd_app")
        conn.execute("GRANT SELECT ON admin_account TO qd_app")
        probes = list(_probes(conn, APP))
        conn.execute("SET ROLE qd_app")
        allowed: list[str] = []
        for what, statement in probes:
            conn.execute("SAVEPOINT probe")
            try:
                conn.execute(statement)  # type: ignore[arg-type]
            except psycopg.Error:
                pass
            else:
                allowed.append(what)
            conn.execute("ROLLBACK TO SAVEPOINT probe")
        conn.rollback()
    with psycopg.connect(database_url) as conn:
        columns = _columns(conn, "admin_account")
    assert "totp_secret" in columns
    assert sorted(allowed) == sorted(
        ["execute purge_expired_sign_ins()", *(f"read admin_account.{c}" for c in columns)]
    )


# --- the same, said one case at a time ------------------------------------------------------------------

# What a break-in into one part would try first. Every line is also one of the probes above; they are
# spelled out here so that the reason for each is written down.
REFUSED = [
    # The ordinary application: nothing of the administrators', nothing of the worker's.
    (APP, "SELECT totp_secret FROM admin_account"),
    (APP, "SELECT count(*) FROM admin_account"),
    (APP, "INSERT INTO admin_account (user_id, totp_secret) VALUES (gen_random_uuid(), '\\x00')"),
    (APP, "SELECT user_id FROM admin_session"),
    (
        APP,
        "INSERT INTO admin_session (id, token_hash, user_id, expires_at) "
        "VALUES (gen_random_uuid(), '\\x00', gen_random_uuid(), now() + interval '1 hour')",
    ),
    (APP, "SELECT reason FROM admin_audit"),
    (
        APP,
        "INSERT INTO admin_audit (id, admin_id, action, target_type, target_id) "
        "VALUES (gen_random_uuid(), gen_random_uuid(), 'shop.viewed', 'shop', 'x')",
    ),
    (APP, "SELECT response FROM admin_request_key"),
    (APP, "SELECT admin_set_platform_setting(gen_random_uuid(), 'payment_cards', '[]', NULL, NULL, now())"),
    (APP, "SELECT * FROM admin_shop_search(gen_random_uuid(), current_date, NULL, NULL, NULL, NULL, NULL, 10)"),
    (APP, "SELECT * FROM admin_open_shop(gen_random_uuid(), gen_random_uuid(), now())"),
    (APP, "SELECT * FROM admin_reassign_owner(gen_random_uuid(), gen_random_uuid(), 1, 'x', now())"),
    (APP, "SELECT admin_decide_receipt(gen_random_uuid(), gen_random_uuid(), 'approved', 1::smallint, NULL, now())"),
    (APP, "SELECT erase_shop(gen_random_uuid())"),
    (APP, "SELECT * FROM shops_to_erase()"),
    (APP, "SELECT purge_expired_sign_ins()"),
    (APP, "SELECT * FROM claim_export_job(now(), now())"),
    (APP, "SELECT * FROM claim_import_batch(now(), now())"),
    (APP, "SELECT * FROM subscriptions_to_review(current_date)"),
    (APP, "SELECT * FROM shops_due_for_reminders(9::smallint)"),
    (APP, "SELECT mark_recipient_unreachable(1)"),
    # A queued message's recipient and text, which are other shops' too; and delivering one.
    (APP, "SELECT recipient FROM outbox_message"),
    (APP, "SELECT payload FROM outbox_message"),
    (APP, "SELECT * FROM outbox_message"),
    (APP, "UPDATE outbox_message SET status = 'sent'"),
    (APP, "UPDATE outbox_message SET next_try_at = now() + interval '10 years'"),
    (APP, "INSERT INTO job_run (job, period) VALUES ('erasure', 'never')"),
    (APP, "SELECT * FROM measure.weekly"),
    # The administrators' side: no session of a person, nothing of the worker's, no shop's rows changed.
    (ADMIN, "SELECT token_hash FROM user_session"),
    (
        ADMIN,
        "INSERT INTO user_session (id, token_hash, user_id, kind, expires_at) "
        "VALUES (gen_random_uuid(), '\\x00', gen_random_uuid(), 'webapp', now() + interval '1 hour')",
    ),
    (ADMIN, "UPDATE user_session SET revoked_at = now()"),
    (ADMIN, "INSERT INTO signin_replay (payload_hash, expires_at) VALUES ('\\x00', now())"),
    (ADMIN, "UPDATE app_user SET lang = 'ru'"),
    (ADMIN, "SELECT lang FROM app_user"),
    (ADMIN, "INSERT INTO platform_setting (key, value, updated_by) VALUES ('payment_cards', '[]', 'x')"),
    (ADMIN, "UPDATE admin_account SET status = 'active'"),
    (ADMIN, "DELETE FROM admin_audit"),
    (ADMIN, "SELECT erase_shop(gen_random_uuid())"),
    (ADMIN, "SELECT purge_expired_sign_ins()"),
    (ADMIN, "SELECT * FROM claim_export_job(now(), now())"),
    (ADMIN, "SELECT mark_recipient_unreachable(1)"),
    (ADMIN, "SELECT * FROM my_memberships(gen_random_uuid())"),
    (ADMIN, "SELECT claim_owned_shop(gen_random_uuid(), true)"),
    (ADMIN, "SELECT payload FROM outbox_message"),
    (ADMIN, "UPDATE outbox_message SET status = 'failed'"),
    (ADMIN, "INSERT INTO job_run (job, period) VALUES ('erasure', 'never')"),
    (ADMIN, "SELECT * FROM shop"),
    (ADMIN, "SELECT * FROM membership"),
    (ADMIN, "SELECT * FROM subscription"),
    (ADMIN, "UPDATE customer SET display_name = 'x'"),
    (
        ADMIN,
        "INSERT INTO ledger_entry (id, shop_id, customer_id, seq, kind, amount, author_id) "
        "VALUES (gen_random_uuid(), gen_random_uuid(), gen_random_uuid(), 1, 'credit', 1, gen_random_uuid())",
    ),
    (ADMIN, "SELECT * FROM activity"),
    # The worker: no administrator's table or function, no session, no sign-in, no setting.
    (WORKER, "SELECT totp_secret FROM admin_account"),
    (WORKER, "SELECT user_id FROM admin_session"),
    (
        WORKER,
        "INSERT INTO admin_audit (id, admin_id, action, target_type, target_id) "
        "VALUES (gen_random_uuid(), gen_random_uuid(), 'shop.viewed', 'shop', 'x')",
    ),
    (WORKER, "SELECT admin_set_platform_setting(gen_random_uuid(), 'payment_cards', '[]', NULL, NULL, now())"),
    (WORKER, "SELECT * FROM admin_reassign_owner(gen_random_uuid(), gen_random_uuid(), 1, 'x', now())"),
    (
        WORKER,
        "SELECT admin_store_subscription(gen_random_uuid(), gen_random_uuid(), 'active', NULL, NULL, NULL, now())",
    ),
    (WORKER, "SELECT * FROM admin_open_shop(gen_random_uuid(), gen_random_uuid(), now())"),
    (WORKER, "SELECT token_hash FROM user_session"),
    (
        WORKER,
        "INSERT INTO user_session (id, token_hash, user_id, kind, expires_at) "
        "VALUES (gen_random_uuid(), '\\x00', gen_random_uuid(), 'webapp', now() + interval '1 hour')",
    ),
    (WORKER, "INSERT INTO app_user (id, tg_id) VALUES (gen_random_uuid(), 1)"),
    (WORKER, "UPDATE app_user SET tg_id = 1"),
    (WORKER, "UPDATE platform_setting SET value = '\"8600\"'"),
    (WORKER, "SELECT * FROM my_memberships(gen_random_uuid())"),
    (WORKER, "SELECT * FROM accept_staff_invitation('\\x00', gen_random_uuid())"),
    (WORKER, "SELECT claim_owned_shop(gen_random_uuid(), true)"),
    (WORKER, "SELECT * FROM review_group_receipt(1, gen_random_uuid(), false)"),
    (WORKER, "UPDATE outbox_message SET recipient = '1'"),
    (WORKER, "UPDATE outbox_message SET payload = '{}'"),
    (WORKER, "DELETE FROM outbox_message"),
    (WORKER, "UPDATE shop SET status = 'erased'"),
    (
        WORKER,
        "INSERT INTO membership (id, shop_id, user_id, role) "
        "VALUES (gen_random_uuid(), gen_random_uuid(), gen_random_uuid(), 'owner')",
    ),
    (WORKER, "UPDATE ledger_entry SET amount = 1"),
    (WORKER, "DELETE FROM ledger_entry"),
    (WORKER, "DELETE FROM customer"),
    (WORKER, "SELECT * FROM request_key"),
    (WORKER, "SELECT * FROM online_payment"),
]


@pytest.mark.parametrize(
    ("role", "statement"), REFUSED, ids=[f"{role}: {statement[:60]}" for role, statement in REFUSED]
)
def test_one_part_cannot_do_what_belongs_to_another(
    request: pytest.FixtureRequest, shop_a: Shop, role: str, statement: str
) -> None:
    session: AppSession = request.getfixturevalue({APP: "as_app", ADMIN: "as_admin", WORKER: "as_worker"}[role])
    for tenant in (None, shop_a.shop_id):
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="permission denied"), session(tenant) as conn:
            conn.execute(statement)  # type: ignore[arg-type]


# --- and each part still does its own work --------------------------------------------------------------


def test_the_ordinary_role_queues_a_message_and_reads_no_more_of_the_queue_than_that(
    owner: psycopg.Connection, as_app: AppSession
) -> None:
    key = f"roles:{uuid.uuid4()}"
    insert = (
        "INSERT INTO outbox_message (id, channel, recipient, payload, dedupe_key) "
        "VALUES (gen_random_uuid(), 'telegram', '1', '{\"text\": \"secret\"}', %s) "
        "ON CONFLICT (dedupe_key) DO NOTHING RETURNING id"
    )
    with as_app(None) as conn:
        first = conn.execute(insert, (key,)).fetchall()
        again = conn.execute(insert, (key,)).fetchall()
        waiting = conn.execute(
            "SELECT channel, status FROM outbox_message WHERE dedupe_key = %s AND next_try_at <= now()", (key,)
        ).fetchall()
    assert len(first) == 1 and again == [], "queued once: the second is the same message"
    assert waiting == [("telegram", "pending")]
    assert owner.execute("SELECT count(*) FROM outbox_message WHERE dedupe_key = %s", (key,)).fetchone() == (1,)


def test_the_administrators_role_queues_a_message_too_and_reads_nothing_of_the_queue(as_admin: AppSession) -> None:
    key = f"roles:{uuid.uuid4()}"
    with as_admin(None) as conn:
        queued = conn.execute(
            "INSERT INTO outbox_message (id, channel, recipient, payload, dedupe_key) "
            "VALUES (gen_random_uuid(), 'telegram', '1', '{}', %s) ON CONFLICT (dedupe_key) DO NOTHING RETURNING id",
            (key,),
        ).fetchall()
    assert len(queued) == 1


def test_the_announcement_function_gives_the_ordinary_role_two_columns_of_confirmed_administrators(
    owner: psycopg.Connection, as_app: AppSession
) -> None:
    """Active and confirmed administrators only, and of them the Telegram identifier and the language."""

    def administrator(status: str, confirmed: bool, lang: str) -> int:
        user, tg_id = uuid.uuid4(), uuid.uuid4().int % 10**15
        owner.execute("INSERT INTO app_user (id, tg_id, lang) VALUES (%s, %s, %s)", (user, tg_id, lang))
        owner.execute(
            "INSERT INTO admin_account (user_id, totp_secret, status, confirmed_at) VALUES (%s, '\\x5ec7e7', %s, %s)",
            (user, status, "now()" if confirmed else None),
        )
        return tg_id

    told = administrator("active", True, "ru")
    not_told = {administrator("disabled", True, "uz"), administrator("active", False, "uz")}
    with as_app(None) as conn:
        cursor = conn.execute("SELECT * FROM admin_notice_recipients()")
        assert cursor.description is not None
        assert [column.name for column in cursor.description] == ["tg_id", "lang"]
        rows = cursor.fetchall()
    assert (told, "ru") in rows
    assert not_told.isdisjoint({tg_id for tg_id, _ in rows})
    assert rows == sorted(rows), "ordered by Telegram identifier"


def test_the_way_back_to_one_role_that_the_runbook_names_really_gives_the_old_rights(database_url: str) -> None:
    """A release from before migration 0031 connects every part as qd_app. `deploy/production/README.md`
    says that `GRANT qd_admin, qd_worker TO qd_app` lets such a release run again. Rolled back."""
    with psycopg.connect(database_url) as conn:
        conn.execute("GRANT qd_admin, qd_worker TO qd_app")
        conn.execute("SET ROLE qd_app")
        assert conn.execute("SELECT erase_shop(%s)", (uuid.uuid4(),)).fetchone() == (False,)
        assert conn.execute("SELECT count(*) FROM admin_account").fetchone() is not None
        assert conn.execute(
            "SELECT count(*) FROM admin_shop_search(%s, current_date, NULL, NULL, NULL, NULL, NULL, 1)", (uuid.uuid4(),)
        ).fetchone() == (0,)
        conn.execute("UPDATE outbox_message SET status = 'sent', sent_at = now() WHERE false")
        conn.execute("SELECT payload, recipient FROM outbox_message LIMIT 1")
        conn.execute("SELECT purge_expired_sign_ins()")
        conn.rollback()
    with psycopg.connect(database_url) as conn:
        conn.execute("SET ROLE qd_app")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT erase_shop(%s)", (uuid.uuid4(),))

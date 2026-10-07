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
    as_app: AppSession, owner: psycopg.Connection, shop_a: Shop
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
    with as_app(None) as conn:
        assert conn.execute("SELECT erase_shop(%s)", (shop_a.shop_id,)).fetchone() == (False,)
    left = owner.execute("SELECT count(*) FROM ledger_entry WHERE shop_id = %s", (shop_a.shop_id,)).fetchone()
    assert left == (1,), "the entry must still be there"
    # Cancelling, and asking again, stay possible; so does everything else about the shop.
    with as_app(shop_a.shop_id) as conn:
        conn.execute("UPDATE shop SET status = 'active', deletion_due = NULL, name = 'Renamed'")
        conn.execute("UPDATE shop SET status = 'deletion_pending', deletion_due = now() + interval '31 days'")


def test_an_erased_shop_cannot_be_brought_back_by_the_application_role(
    as_app: AppSession, owner: psycopg.Connection, shop_a: Shop
) -> None:
    owner.execute(
        "UPDATE shop SET status = 'deletion_pending', deletion_due = now() - interval '1 hour' WHERE id = %s",
        (shop_a.shop_id,),
    )
    with as_app(None) as conn:
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

"""The database stores exactly the languages the application accepts (migration 0044).

A person's choice, a shop's language and the language recorded for a customer are each one of the six
tags of `qarz.domain.languages.LANGUAGES`, and nothing else.
"""

import re

import psycopg
import pytest
from psycopg import errors

from qarz.domain.languages import LANGUAGES

from ..conftest import Shop

pytestmark = pytest.mark.db

COLUMNS = ("app_user", "shop", "customer")


def allowed(owner: psycopg.Connection, table: str) -> set[str]:
    """The values the check on `table.lang` lists."""
    rows = owner.execute(
        "SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c "
        "WHERE c.conrelid = %s::regclass AND c.contype = 'c' AND c.conname = %s",
        (table, f"{table}_lang_check"),
    ).fetchall()
    assert len(rows) == 1, f"{table} must have exactly one check on lang"
    return set(re.findall(r"'([^']+)'", rows[0][0]))


@pytest.mark.parametrize("table", COLUMNS)
def test_the_check_lists_exactly_the_languages_of_the_application(owner: psycopg.Connection, table: str) -> None:
    assert allowed(owner, table) == set(LANGUAGES)


@pytest.mark.parametrize("lang", LANGUAGES)
def test_every_language_can_be_stored_in_each_column(owner: psycopg.Connection, shop_a: Shop, lang: str) -> None:
    owner.execute("UPDATE app_user SET lang = %s WHERE id = %s", (lang, shop_a.user_id))
    owner.execute("UPDATE shop SET lang = %s WHERE id = %s", (lang, shop_a.shop_id))
    owner.execute("UPDATE customer SET lang = %s WHERE id = %s", (lang, shop_a.customer_id))
    stored = owner.execute(
        "SELECT (SELECT lang FROM app_user WHERE id = %s), (SELECT lang FROM shop WHERE id = %s), "
        "(SELECT lang FROM customer WHERE id = %s)",
        (shop_a.user_id, shop_a.shop_id, shop_a.customer_id),
    ).fetchone()
    assert stored == (lang, lang, lang)


@pytest.mark.parametrize("lang", ["kk", "uz-cyrl", "UZ", "uz_Cyrl", "en-US", ""])
def test_anything_else_is_refused_by_each_column(owner: psycopg.Connection, shop_a: Shop, lang: str) -> None:
    """The counterpart: a tag the application does not have, or one of its tags written another way."""
    for table, row in (("app_user", shop_a.user_id), ("shop", shop_a.shop_id), ("customer", shop_a.customer_id)):
        with pytest.raises(errors.CheckViolation):
            owner.execute(f"UPDATE {table} SET lang = %s WHERE id = %s", (lang, row))


def test_a_customer_may_still_have_no_language_of_their_own(owner: psycopg.Connection, shop_a: Shop) -> None:
    owner.execute("UPDATE customer SET lang = NULL WHERE id = %s", (shop_a.customer_id,))

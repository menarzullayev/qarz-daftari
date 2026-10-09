"""Migration 0036: the one card number that was stored becomes a list of that one card.

The suite's database is built by the migrations from nothing, so the conversion finds no row there. Here
its own statements, taken from the migration file, are run against rows as an installation would hold
them, inside a transaction that is rolled back.
"""

from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg.types.json import Jsonb

from qarz.domain.platform_settings import payment_cards

pytestmark = pytest.mark.db

_SQL = Path(__file__).resolve().parents[2] / "migrations" / "sql" / "0036_payment_cards.sql"


def _conversion() -> str:
    """The part of the migration that moves the setting: everything before the receipt's new column."""
    text = _SQL.read_text(encoding="utf-8")
    head, found, _ = text.partition("ALTER TABLE subscription_receipt")
    assert found, "the migration no longer has the shape this test reads"
    assert "INSERT INTO platform_setting" in head and "DELETE FROM platform_setting WHERE key = 'card_number'" in head
    return head


def _converted(owner: psycopg.Connection, stored: dict[str, Any]) -> dict[str, Any]:
    """The settings after the conversion, given those before it. Nothing is kept."""

    class Undo(Exception):
        pass

    after: dict[str, Any] = {}
    try:
        with owner.transaction():
            owner.execute("DELETE FROM platform_setting WHERE key IN ('card_number', 'payment_cards')")
            for key, value in stored.items():
                owner.execute(
                    "INSERT INTO platform_setting (key, value, updated_by, updated_at) "
                    "VALUES (%s, %s, 'an-administrator', '2026-09-01T10:00:00+00:00')",
                    (key, Jsonb(value)),
                )
            owner.execute(_conversion())  # type: ignore[arg-type]
            after = {
                key: (value, by, at.isoformat())
                for key, value, by, at in owner.execute(
                    "SELECT key, value, updated_by, updated_at AT TIME ZONE 'UTC' FROM platform_setting "
                    "WHERE key IN ('card_number', 'payment_cards')"
                ).fetchall()
            }
            raise Undo
    except Undo:
        pass
    return after


@pytest.mark.parametrize("stored", ["8600123456789012", "8600 1234 5678 9012"])
def test_a_stored_card_number_becomes_a_list_of_that_one_card(owner: psycopg.Connection, stored: str) -> None:
    after = _converted(owner, {"card_number": stored})
    assert set(after) == {"payment_cards"}, "the old row is gone"
    value, by, at = after["payment_cards"]
    assert value == [{"number": "8600123456789012", "label": "Karta"}]
    # Who set the card and when stays with it.
    assert (by, at) == ("an-administrator", "2026-09-01T10:00:00")
    # And the application reads it as one card to pay to.
    assert payment_cards(value) == [{"number": "8600123456789012", "label": "Karta"}]


@pytest.mark.parametrize(
    "stored",
    [
        None,  # cleared
        "",
        "not a card",
        "860012345678901",  # fifteen digits
        "86001234567890123",
        "٨٦٠٠١٢٣٤٥٦٧٨٩٠١٢",  # digits a card does not carry
        8600123456789012,  # a number, not a text
        ["8600123456789012"],
    ],
)
def test_a_stored_value_that_is_no_card_number_becomes_no_list_and_the_old_row_goes(
    owner: psycopg.Connection, stored: Any
) -> None:
    assert _converted(owner, {"card_number": stored}) == {}


def test_with_no_card_stored_nothing_appears(owner: psycopg.Connection) -> None:
    assert _converted(owner, {}) == {}


def test_a_list_that_is_already_there_is_not_replaced(owner: psycopg.Connection) -> None:
    cards = [{"number": "5614681234567890", "label": "Uzcard · Kapitalbank"}]
    after = _converted(owner, {"card_number": "8600123456789012", "payment_cards": cards})
    assert set(after) == {"payment_cards"}
    assert after["payment_cards"][0] == cards

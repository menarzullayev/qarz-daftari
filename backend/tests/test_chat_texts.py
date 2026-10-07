"""The chat catalogs: Russian mirrors Uzbek key for key and placeholder for placeholder (ADR-021)."""

import string
from datetime import date

import pytest

from qarz.application.chat import _PARSE_TEXTS, _QUICK, callback
from qarz.application.chat_texts import LANGUAGE_NAMES, RU, UZ, day, money, say
from qarz.interface.errors import _STATUS


def _placeholders(template: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


def test_both_languages_have_the_same_keys() -> None:
    assert set(RU) == set(UZ)
    assert set(LANGUAGE_NAMES) == {"uz", "ru"}


@pytest.mark.parametrize("key", sorted(UZ))
def test_both_languages_use_the_same_placeholders(key: str) -> None:
    assert _placeholders(RU[key]) == _placeholders(UZ[key])
    assert UZ[key].strip() and RU[key].strip()


def test_every_text_the_chat_can_ask_for_exists() -> None:
    needed = set(_PARSE_TEXTS.values()) | {choice.value for choice in _QUICK.values()}
    assert needed <= set(UZ)


def test_every_refusal_the_ledger_can_give_has_a_chat_text() -> None:
    """A domain refusal must reach the seller in words, not as the generic error."""
    spoken_elsewhere = {
        "IMPORT_NOT_APPLICABLE",  # an import is made in the Mini App, not in the chat
        "IMPORT_UNDO_REFUSED",
        "UNAUTHENTICATED",  # the chat has no sign-in step
        "RATE_LIMITED",  # the API's limit; the chat is not called through the API
        "NOT_FOUND",  # said as "not_found"
        "FORBIDDEN_ROLE",  # said as "forbidden"
        "VALIDATION",  # said with the field's own hint
        "IDEMPOTENCY_KEY_REUSED",  # keys come from update identifiers, never from a person
        "ALREADY_MEMBER",  # said as "already_member"
        "SHOP_LIMIT_REACHED",  # said as "shop_limit_reached" where a shop is opened
        "OWNER_MEMBERSHIP_FIXED",
        "TRANSFER_PENDING",
        "TRANSFER_TARGET_INVALID",
        "NOT_TRANSFER_TARGET",
        "CUSTOMER_HAS_BALANCE",  # archiving is not a chat action
        "PROMISE_ALREADY_SET",  # said as "promise_closed"
        "DELETION_ALREADY_REQUESTED",  # deleting a shop is done in the Mini App or the panel
        "DELETION_NOT_REQUESTED",
        "ONLINE_PAY_OFF",  # paying online starts in the Mini App or the panel
        "REMINDERS_OFF",  # reminders are managed in the Mini App, not in the chat
        "REMINDER_NOT_DUE",
        "REMINDER_LIMIT_REACHED",
        "CUSTOMER_UNREACHABLE",
        "CUSTOMER_ALREADY_LINKED",  # linking is offered in the Mini App; the customer hears "link_taken"
        "CATALOG_NAME_TAKEN",  # the catalog is managed in the Mini App, not in the chat
        "CATALOG_ITEM_NOT_LEARNED",
        "CATALOG_MERGE_TARGET_INVALID",
        "LINES_ALREADY_ADDED",  # goods lines are entered in the Mini App, not in the chat
        "LINES_SUM_MISMATCH",
        "LINES_WINDOW_CLOSED",
        "SECOND_FACTOR_INVALID",  # the administrator's side is a web panel; none of it is in the chat
        "SECOND_FACTOR_LOCKED",
        "ADMIN_ALREADY_ENROLLED",
        "ADMIN_NOT_ENROLLED",
        "SUBSCRIPTION_CHANGE_REFUSED",
        "OWNER_REASSIGNMENT_REFUSED",
        "SUPPORT_ACCESS_REQUIRED",
        "SUPPORT_ACCESS_ALREADY_OPEN",
        "SUPPORT_ACCESS_NOT_OPEN",  # the owner ends it in the panel
        "RECEIPT_ALREADY_DECIDED",  # receipts are decided in the administrator's panel
    }
    assert set(_STATUS) - spoken_elsewhere <= set(UZ)


def test_an_unknown_language_is_answered_in_uzbek() -> None:
    assert say("en", "cancelled") == UZ["cancelled"]


def test_a_missing_value_is_an_error_not_a_blank() -> None:
    with pytest.raises(KeyError):
        say("uz", "shop_switched")


def test_money_and_dates() -> None:
    assert money("uz", 45000) == "45 000 so'm"
    assert money("ru", 1_250_000) == "1 250 000 сум"
    assert money("uz", 100) == "100 so'm"
    assert day(date(2026, 10, 6)) == "06.10.2026"


def test_callback_data_is_versioned_and_bounded() -> None:
    assert callback("pd", "0" * 32, "t") == "v2:pd:" + "0" * 32 + ":t"
    with pytest.raises(ValueError, match="64 bytes"):
        callback("pk", "0" * 32, "x" * 40)

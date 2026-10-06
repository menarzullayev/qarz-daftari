from datetime import UTC, date, datetime, timedelta

import pytest

from qarz.domain.date_requests import (
    REASON_MAX,
    REPEAT_AFTER_DECLINE,
    DateRequestRefusal,
    PromiseChangeRefusal,
    may_change_promise,
    may_request,
    reason_fits,
    request_is_met,
    tidy_reason,
)
from qarz.domain.ledger import EntryKind
from qarz.domain.promise import PromiseDateError

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
SALE = date(2026, 10, 1)
CURRENT = date(2026, 10, 31)


def ask(**overrides: object) -> DateRequestRefusal | PromiseDateError | None:
    arguments: dict[str, object] = {
        "kind": EntryKind.CREDIT,
        "is_reversed": False,
        "remaining": 50_000,
        "has_open": False,
        "sale_date": SALE,
        "current": CURRENT,
        "requested": CURRENT + timedelta(days=10),
        "last_declined_at": None,
        "now": NOW,
    }
    arguments.update(overrides)
    return may_request(**arguments)  # type: ignore[arg-type]


def change(**overrides: object) -> PromiseChangeRefusal | PromiseDateError | None:
    arguments: dict[str, object] = {
        "kind": EntryKind.CREDIT,
        "is_reversed": False,
        "sale_date": SALE,
        "current": CURRENT,
        "chosen": CURRENT + timedelta(days=10),
    }
    arguments.update(overrides)
    return may_change_promise(**arguments)  # type: ignore[arg-type]


# --- a customer's request (BR-15) ---------------------------------------------------------------------


@pytest.mark.parametrize("kind", [EntryKind.CREDIT, EntryKind.OPENING])
def test_a_date_may_be_asked_for_on_an_entry_that_increases_the_debt(kind: EntryKind) -> None:
    assert ask(kind=kind) is None


@pytest.mark.parametrize("kind", [EntryKind.PAYMENT, EntryKind.REVERSAL])
def test_an_entry_without_a_promised_date_has_nothing_to_move(kind: EntryKind) -> None:
    assert ask(kind=kind, current=None) is DateRequestRefusal.NOT_A_DEBT


def test_a_reversed_entry_has_nothing_to_move() -> None:
    assert ask(is_reversed=True) is DateRequestRefusal.REVERSED


def test_a_fully_paid_entry_has_nothing_to_move() -> None:
    assert ask(remaining=0) is DateRequestRefusal.FULLY_PAID
    assert ask(remaining=1) is None, "one sum still owed is still a debt with a date"


def test_only_one_request_is_open_per_entry() -> None:
    assert ask(has_open=True) is DateRequestRefusal.ALREADY_OPEN


def test_the_requested_date_must_be_after_the_current_one() -> None:
    assert ask(requested=CURRENT - timedelta(days=1)) is DateRequestRefusal.NOT_LATER
    assert ask(requested=CURRENT) is DateRequestRefusal.NOT_LATER
    assert ask(requested=CURRENT + timedelta(days=1)) is None


def test_the_requested_date_is_bound_from_the_sale_like_every_promised_date() -> None:
    assert ask(requested=SALE + timedelta(days=365)) is None
    assert ask(requested=SALE + timedelta(days=366)) is PromiseDateError.TOO_FAR
    # Only an imported promise can lie before its entry; a later date that is still before it is refused.
    early = SALE - timedelta(days=20)
    assert ask(current=early, requested=SALE - timedelta(days=1)) is PromiseDateError.BEFORE_SALE
    assert ask(current=early, requested=SALE) is None


def test_a_declined_request_cannot_be_repeated_within_seven_days() -> None:
    assert timedelta(days=7) == REPEAT_AFTER_DECLINE
    assert ask(last_declined_at=NOW) is DateRequestRefusal.DECLINED_RECENTLY
    assert ask(last_declined_at=NOW - timedelta(days=7) + timedelta(seconds=1)) is DateRequestRefusal.DECLINED_RECENTLY
    assert ask(last_declined_at=NOW - timedelta(days=7)) is None, "exactly seven days later it may be asked again"
    assert ask(last_declined_at=NOW - timedelta(days=30)) is None


def test_the_first_reason_that_applies_to_a_request_is_given() -> None:
    everything = {
        "is_reversed": True,
        "remaining": 0,
        "has_open": True,
        "requested": CURRENT,
        "last_declined_at": NOW,
    }
    assert ask(kind=EntryKind.PAYMENT, **everything) is DateRequestRefusal.NOT_A_DEBT
    assert ask(**everything) is DateRequestRefusal.REVERSED
    assert ask(**{**everything, "is_reversed": False}) is DateRequestRefusal.FULLY_PAID
    assert ask(has_open=True, requested=CURRENT, last_declined_at=NOW) is DateRequestRefusal.ALREADY_OPEN
    assert ask(requested=CURRENT, last_declined_at=NOW) is DateRequestRefusal.NOT_LATER
    assert ask(requested=SALE + timedelta(days=366), last_declined_at=NOW) is PromiseDateError.TOO_FAR


# --- a date changed by the shop (REQ-067) -------------------------------------------------------------


@pytest.mark.parametrize("kind", [EntryKind.CREDIT, EntryKind.OPENING])
def test_the_shop_may_move_the_date_of_a_debt_later_or_earlier(kind: EntryKind) -> None:
    assert change(kind=kind) is None
    assert change(kind=kind, chosen=CURRENT - timedelta(days=5)) is None


@pytest.mark.parametrize("kind", [EntryKind.PAYMENT, EntryKind.REVERSAL])
def test_the_shop_cannot_give_a_date_to_an_entry_that_has_none(kind: EntryKind) -> None:
    assert change(kind=kind, current=None) is PromiseChangeRefusal.NOT_A_DEBT


def test_the_shop_cannot_move_the_date_of_a_reversed_entry() -> None:
    assert change(is_reversed=True) is PromiseChangeRefusal.REVERSED


def test_setting_the_date_an_entry_already_has_is_not_a_change() -> None:
    assert change(chosen=CURRENT) is PromiseChangeRefusal.UNCHANGED


def test_a_changed_date_is_bound_from_the_sale() -> None:
    assert change(chosen=SALE) is None
    assert change(chosen=SALE - timedelta(days=1)) is PromiseDateError.BEFORE_SALE
    assert change(chosen=SALE + timedelta(days=365)) is None
    assert change(chosen=SALE + timedelta(days=366)) is PromiseDateError.TOO_FAR


def test_the_first_reason_that_applies_to_a_change_is_given() -> None:
    assert change(kind=EntryKind.PAYMENT, is_reversed=True, chosen=CURRENT) is PromiseChangeRefusal.NOT_A_DEBT
    assert change(is_reversed=True, chosen=CURRENT) is PromiseChangeRefusal.REVERSED


def test_a_request_is_met_by_its_own_date_or_a_later_one() -> None:
    asked = date(2026, 11, 10)
    assert request_is_met(asked, asked)
    assert request_is_met(asked, asked + timedelta(days=1))
    assert not request_is_met(asked, asked - timedelta(days=1))


# --- the optional reason --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "tidied"),
    [(None, None), ("", None), ("   \n ", None), ("  oylik   kechikdi ", "oylik kechikdi"), ("a", "a")],
)
def test_a_reason_is_optional_and_tidied(raw: str | None, tidied: str | None) -> None:
    assert tidy_reason(raw) == tidied


def test_a_reason_is_at_most_three_hundred_characters() -> None:
    assert REASON_MAX == 300
    assert reason_fits(None)
    assert reason_fits("x" * 300)
    assert not reason_fits("x" * 301)

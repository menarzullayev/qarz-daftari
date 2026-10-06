"""The pure rules of payment notices (BR-14, INV-15; domain model lifecycle: expired after 14 days)."""

from datetime import UTC, datetime, timedelta

import pytest

from qarz.domain.chat_entry import ParseError, ParseErrorCode, parse_amount
from qarz.domain.payment_notices import (
    MAX_AMOUNT,
    MAX_OPEN_NOTICES,
    MIN_AMOUNT,
    NOTICE_LIFETIME,
    NoticeRefusal,
    effective_status,
    is_stale,
    may_send,
    receipt_bound,
    valid_amount,
)

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


def test_the_numbers_the_documents_fix() -> None:
    assert (MIN_AMOUNT, MAX_AMOUNT) == (100, 100_000_000)
    assert timedelta(days=14) == NOTICE_LIFETIME
    assert MAX_OPEN_NOTICES == 3


@pytest.mark.parametrize("amount", [100, 101, 50_000, 99_999_999, 100_000_000])
def test_amounts_within_the_entry_bounds(amount: int) -> None:
    assert valid_amount(amount)


@pytest.mark.parametrize("amount", [99, 0, -100, 100_000_001, 100.0, 5000.5, "5000", None, True, False, [5000]])
def test_amounts_outside_them_or_not_whole_numbers(amount: object) -> None:
    assert not valid_amount(amount)


def test_a_notice_may_be_sent_for_up_to_what_is_owed() -> None:
    assert may_send(amount=50_000, balance=50_000, open_notices=0) is None
    assert may_send(amount=50_001, balance=50_000, open_notices=0) is NoticeRefusal.EXCEEDS_BALANCE
    assert may_send(amount=100, balance=0, open_notices=0) is NoticeRefusal.EXCEEDS_BALANCE
    assert may_send(amount=100, balance=100, open_notices=0) is None


def test_the_amount_bounds_are_checked_before_the_balance() -> None:
    assert may_send(amount=99, balance=50_000, open_notices=0) is NoticeRefusal.AMOUNT_OUT_OF_RANGE
    assert may_send(amount=100_000_001, balance=10**12, open_notices=0) is NoticeRefusal.AMOUNT_OUT_OF_RANGE
    assert may_send(amount=0, balance=0, open_notices=9) is NoticeRefusal.AMOUNT_OUT_OF_RANGE


def test_at_most_three_notices_wait_at_once() -> None:
    assert may_send(amount=1000, balance=50_000, open_notices=2) is None
    assert may_send(amount=1000, balance=50_000, open_notices=3) is NoticeRefusal.TOO_MANY_OPEN
    assert may_send(amount=1000, balance=50_000, open_notices=4) is NoticeRefusal.TOO_MANY_OPEN
    # What is owed is said first: a fourth notice above the balance is refused for the amount.
    assert may_send(amount=60_000, balance=50_000, open_notices=3) is NoticeRefusal.EXCEEDS_BALANCE


def test_a_notice_is_stale_after_fourteen_days_and_not_a_moment_earlier() -> None:
    assert not is_stale(NOW, NOW)
    assert not is_stale(NOW - timedelta(days=14), NOW), "the last moment of the fourteenth day is still in time"
    assert is_stale(NOW - timedelta(days=14, microseconds=1), NOW)
    assert not is_stale(NOW - timedelta(days=13, hours=23, minutes=59), NOW)


def test_only_a_waiting_notice_expires() -> None:
    old = NOW - timedelta(days=30)
    assert effective_status("sent", old, NOW) == "expired"
    assert effective_status("sent", NOW - timedelta(days=14), NOW) == "sent"
    for decided in ("accepted", "declined", "expired"):
        assert effective_status(decided, old, NOW) == decided
        assert effective_status(decided, NOW, NOW) == decided


def test_a_receipt_is_never_needed_longer_than_the_lifetime_plus_the_retention() -> None:
    assert receipt_bound(NOW) == NOW + timedelta(days=104)


@pytest.mark.parametrize(
    ("text", "amount"),
    [
        ("50000", 50_000),
        ("  50000  ", 50_000),
        ("50 000", 50_000),
        ("50.000", 50_000),
        ("1 250 000", 1_250_000),
        ("50k", 50_000),
        ("50 ming", 50_000),
        ("50 тыс", 50_000),
        ("50000 so'm", 50_000),
        ("50 ming so'm", 50_000),
        ("50000 сум", 50_000),
        ("100", 100),
        ("100000000", 100_000_000),
    ],
)
def test_a_plain_amount_is_read_in_the_entry_grammar(text: str, amount: int) -> None:
    assert parse_amount(text) == amount


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("", ParseErrorCode.EMPTY),
        ("   ", ParseErrorCode.EMPTY),
        ("Ali 50000", ParseErrorCode.NO_AMOUNT),
        ("-50000", ParseErrorCode.NO_AMOUNT),
        ("−50000", ParseErrorCode.NO_AMOUNT),
        ("+50000", ParseErrorCode.NO_AMOUNT),
        ("/toladim", ParseErrorCode.NO_AMOUNT),
        ("ellik ming", ParseErrorCode.NO_AMOUNT),
        ("50000 berdi", ParseErrorCode.AMBIGUOUS),  # a payment word makes it an entry, not an amount
        ("50000 to'ladi", ParseErrorCode.AMBIGUOUS),
        ("50000 non uchun", ParseErrorCode.AMBIGUOUS),  # a note
        ("50000 20000", ParseErrorCode.AMBIGUOUS),
        ("50000 so'm rahmat", ParseErrorCode.AMBIGUOUS),
        ("50000.5", ParseErrorCode.AMOUNT_NOT_WHOLE),
        ("50,5", ParseErrorCode.AMOUNT_NOT_WHOLE),
        ("50000abc", ParseErrorCode.NO_AMOUNT),
        ("99", ParseErrorCode.AMOUNT_TOO_SMALL),
        ("100000001", ParseErrorCode.AMOUNT_TOO_LARGE),
        ("101 000k", ParseErrorCode.AMOUNT_TOO_LARGE),
        ("50000\n20000", ParseErrorCode.INVALID_CHARACTERS),
        ("50​000", ParseErrorCode.INVALID_CHARACTERS),  # a zero-width space hidden in the number
        ("5" * 501, ParseErrorCode.TOO_LONG),
    ],
)
def test_anything_else_is_refused_rather_than_guessed_at(text: str, code: ParseErrorCode) -> None:
    assert parse_amount(text) == ParseError(code)

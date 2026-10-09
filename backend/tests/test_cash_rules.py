"""Pure rules of the cash book (qarz.domain.cash). No database."""

from datetime import date, timedelta

import pytest

from qarz.domain import cash
from qarz.domain.cash import DayProblem, Direction, Line, Method, Sum
from qarz.domain.money import Currency

UZS, USD = Currency.UZS, Currency.USD
IN, OUT = Direction.INCOME, Direction.EXPENSE
TODAY = date(2026, 10, 9)


def test_the_default_categories_exist_in_both_languages_and_one_is_the_ledgers() -> None:
    uz, ru = cash.default_names("uz"), cash.default_names("ru")
    assert len(uz) == len(ru) == len(cash.DEFAULT_CATEGORIES) == 10
    assert [(direction, key) for direction, _, key in uz] == [(direction, key) for direction, _, key in ru]
    assert [name for _, name, key in uz if key == cash.DEBT_REPAID] == ["Qarz qaytdi"]
    assert [name for _, name, key in ru if key == cash.DEBT_REPAID] == ["Возврат долга"]
    assert sum(1 for _, _, key in uz if key is not None) == 1
    # A language the service does not have is named in Uzbek, like every other text.
    assert cash.default_names("en") == uz
    for names in (uz, ru):
        # Every default is a name a shop could have typed, and no two of a direction are the same name.
        cleaned = [(direction, cash.category_name(name)[1]) for direction, name, _ in names]
        assert len(set(cleaned)) == len(cleaned)
    assert {direction for direction, _, _ in uz} == {IN, OUT}


@pytest.mark.parametrize(
    ("raw", "shown", "norm"),
    [
        ("  Ijara  ", "Ijara", "ijara"),
        ("Soliq   to'lovi", "Soliq to'lovi", "soliq to'lovi"),
        ("Soliq to‘lovi", "Soliq to‘lovi", "soliq to'lovi"),
        ("Ижара", "Ижара", "ijara"),
        ("a" * 60, "a" * 60, "a" * 60),
        # The matching form may come out longer than the name; it is cut to what is stored.
        ("ш" * 60, "ш" * 60, ("sh" * 60)[:60]),
        ("2-ombor", "2-ombor", "2-ombor"),
    ],
)
def test_a_category_name_is_tidied_and_matched_whatever_the_case_apostrophe_or_alphabet(
    raw: str, shown: str, norm: str
) -> None:
    assert cash.category_name(raw) == (shown, norm)


@pytest.mark.parametrize("raw", ["", "   ", "x" * 61, "ь", "-", "..."])
def test_a_name_that_cannot_be_one_is_refused(raw: str) -> None:
    with pytest.raises(ValueError):
        cash.category_name(raw)


@pytest.mark.parametrize(
    ("day", "problem"),
    [
        (TODAY, None),
        (TODAY - timedelta(days=cash.BACKDATE_DAYS), None),
        (TODAY - timedelta(days=cash.BACKDATE_DAYS + 1), DayProblem.TOO_OLD),
        (TODAY + timedelta(days=1), DayProblem.IN_FUTURE),
    ],
)
def test_an_entry_is_dated_today_or_within_the_last_month(day: date, problem: DayProblem | None) -> None:
    assert cash.day_problem(day, TODAY) is problem


@pytest.mark.parametrize(
    ("note", "method"),
    [
        ("karta", Method.CARD),
        ("Karta", Method.CARD),
        ("  karta   orqali ", Method.CARD),
        ("картой", Method.CARD),
        ("на карту", Method.CARD),
        ("naqd", Method.CASH),
        ("наличными", Method.CASH),
        ("o'tkazma", Method.TRANSFER),
        ("o‘tkazma", Method.TRANSFER),
        ("перевод", Method.TRANSFER),
        # Anything more than the word itself names no method: nothing is guessed.
        ("karta emas", None),
        ("kartasi yo'q", None),
        ("non karta", None),
        ("karta.", None),
        ("", None),
        (None, None),
    ],
)
def test_only_a_note_that_is_exactly_a_way_of_paying_names_one(note: str | None, method: Method | None) -> None:
    assert cash.method_from_note(note) is method


def test_nobody_writes_by_hand_into_the_category_of_payments() -> None:
    assert cash.may_write_by_hand(None) is True
    assert cash.may_write_by_hand("supplier_payment") is True
    assert cash.may_write_by_hand(cash.DEBT_REPAID) is False


@pytest.mark.parametrize(
    ("text", "tidy"), [(None, None), ("", None), ("   ", None), ("  a   b ", "a b"), ("Ijara\nuchun", "Ijara uchun")]
)
def test_a_note_is_tidied_and_an_empty_one_is_none(text: str | None, tidy: str | None) -> None:
    assert cash.tidy(text) == tidy


@pytest.mark.parametrize("value", ["both", "", "INCOME", None, 1])
def test_a_direction_and_a_method_are_exactly_their_words(value: object) -> None:
    assert cash.parse_direction(value) is None
    assert cash.parse_method(value) is None
    assert cash.parse_direction("income") is IN and cash.parse_method("transfer") is Method.TRANSFER


def test_a_book_has_a_line_for_each_method_and_closes_with_opening_plus_income_minus_expense() -> None:
    before = [Sum(Method.CASH, UZS, IN, 400_000, 2), Sum(Method.CASH, UZS, OUT, 150_000, 1)]
    during = [
        Sum(Method.CASH, UZS, IN, 500_000, 2),
        Sum(Method.CASH, UZS, OUT, 300_000, 1),
        Sum(Method.CARD, UZS, IN, 120_000, 1),
    ]
    lines = cash.book(before, during, [UZS])
    assert lines == [
        Line(UZS, Method.CASH, 250_000, 500_000, 300_000, 3),
        Line(UZS, Method.CARD, 0, 120_000, 0, 1),
        Line(UZS, Method.TRANSFER, 0, 0, 0, 0),
    ]
    assert [line.closing for line in lines] == [450_000, 120_000, 0]


def test_a_balance_may_be_below_zero() -> None:
    (line, *_rest) = cash.book([], [Sum(Method.CASH, UZS, OUT, 50_000, 1)], [UZS])
    assert (line.opening, line.closing) == (0, -50_000)


def test_two_currencies_are_two_sets_of_lines_and_two_totals_never_one() -> None:
    during = [Sum(Method.CASH, UZS, IN, 500_000, 1), Sum(Method.CASH, USD, IN, 125_050, 1)]
    lines = cash.book([], during, [UZS, USD])
    assert [(line.currency, line.method) for line in lines] == [
        (UZS, Method.CASH),
        (UZS, Method.CARD),
        (UZS, Method.TRANSFER),
        (USD, Method.CASH),
        (USD, Method.CARD),
        (USD, Method.TRANSFER),
    ]
    totals = cash.totals_by_currency(lines)
    assert {currency: (total.income, total.closing) for currency, total in totals.items()} == {
        UZS: (500_000, 500_000),
        USD: (125_050, 125_050),
    }
    assert 625_050 not in [value for total in totals.values() for value in (total.income, total.closing)]


def test_a_currency_the_shop_no_longer_works_in_is_still_shown_while_the_book_holds_it() -> None:
    held = cash.book([Sum(Method.CASH, USD, IN, 125_050, 1)], [], [UZS])
    assert {line.currency for line in held} == {UZS, USD}
    # And one it never held is not shown at all.
    assert {line.currency for line in cash.book([], [], [UZS])} == {UZS}


def test_a_total_adds_the_methods_of_one_currency() -> None:
    lines = cash.book(
        [Sum(Method.CARD, UZS, IN, 10, 1)],
        [Sum(Method.CASH, UZS, IN, 100, 2), Sum(Method.TRANSFER, UZS, OUT, 30, 1)],
        [UZS],
    )
    total = cash.totals_by_currency(lines)[UZS]
    assert (total.opening, total.income, total.expense, total.closing, total.count) == (10, 100, 30, 80, 3)

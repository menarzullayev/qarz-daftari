"""Currencies and amounts (qarz.domain.money): minor units, ranges, reading and writing, and never mixing."""

from typing import Any

import pytest

from qarz.domain.money import (
    DEFAULT,
    NBSP,
    RULES,
    Currency,
    CurrencyMismatch,
    Money,
    format_amount,
    format_money,
    parse_code,
    plain,
    to_minor,
    totals,
    valid_entry_amount,
    valid_limit,
)

UZS, USD = Currency.UZS, Currency.USD


def test_som_is_the_default_and_has_no_minor_unit_and_a_dollar_has_a_hundred_cents() -> None:
    assert DEFAULT is UZS
    assert (RULES[UZS].exponent, RULES[UZS].scale) == (0, 1)
    assert (RULES[USD].exponent, RULES[USD].scale) == (2, 100)
    assert set(RULES) == set(Currency), "every currency has its rules"


def test_the_range_of_one_entry() -> None:
    # So'm: as before dollars existed.
    assert (RULES[UZS].min_entry, RULES[UZS].max_entry) == (100, 100_000_000)
    # Dollars: one cent to ten thousand dollars, in cents.
    assert (RULES[USD].min_entry, RULES[USD].max_entry) == (1, 1_000_000)
    assert valid_entry_amount(UZS, 100) and valid_entry_amount(UZS, 100_000_000)
    assert valid_entry_amount(USD, 1) and valid_entry_amount(USD, 1_000_000)


@pytest.mark.parametrize(
    ("currency", "amount"),
    [
        (UZS, 99),
        (UZS, 100_000_001),
        (USD, 0),
        (USD, 1_000_001),
        (USD, -5),
        (USD, 12.5),
        (USD, "1250"),
        (USD, True),
        (UZS, None),
    ],
)
def test_what_is_not_an_amount_of_one_entry(currency: Currency, amount: Any) -> None:
    assert valid_entry_amount(currency, amount) is False


def test_each_currency_has_a_limit_range_of_its_own() -> None:
    assert valid_limit(UZS, 1_000) and valid_limit(UZS, 10_000_000_000)
    assert valid_limit(USD, 100) and valid_limit(USD, 100_000_000)
    # A dollar limit of 99 cents, a so'm limit of 999, a float and a boolean are not limits.
    assert not valid_limit(USD, 99) and not valid_limit(UZS, 999)
    assert not valid_limit(USD, 100_000_001) and not valid_limit(USD, 150.0) and not valid_limit(USD, True)


def test_only_the_two_codes_name_a_currency() -> None:
    assert parse_code("UZS") is UZS and parse_code("USD") is USD
    for other in ("usd", "EUR", "$", "", " USD", None, 840, True):
        assert parse_code(other) is None, other


def test_amounts_are_written_for_a_person_with_grouped_thousands_and_the_unit_after() -> None:
    assert format_money(UZS, 45_000, "uz") == f"45{NBSP}000{NBSP}so'm"
    assert format_money(UZS, 45_000, "ru") == f"45{NBSP}000{NBSP}сум"
    assert format_money(USD, 125_050, "uz") == f"1{NBSP}250.50{NBSP}$"
    assert format_money(USD, 125_050, "ru") == f"1{NBSP}250.50{NBSP}$"
    # Dollars always show both decimals; an unknown language is written as Uzbek.
    assert format_amount(USD, 5_000) == "50.00" and format_amount(USD, 5) == "0.05"
    assert format_amount(USD, 100_000_000) == f"1{NBSP}000{NBSP}000.00"
    assert format_money(UZS, 100, "en") == f"100{NBSP}so'm"
    assert format_amount(USD, -1_250) == "-12.50" and format_amount(UZS, -45_000) == f"-45{NBSP}000"


def test_an_amount_is_written_for_a_form_without_grouping_or_unit() -> None:
    assert plain(UZS, 45_000) == "45000"
    assert plain(USD, 125_050) == "1250.50" and plain(USD, 5) == "0.05" and plain(USD, -150) == "-1.50"


@pytest.mark.parametrize(
    ("currency", "typed", "minor"),
    [
        (UZS, "45000", 45_000),
        (UZS, "45 000", 45_000),
        (USD, "50", 5_000),
        (USD, "50.5", 5_050),
        (USD, "50,05", 5_005),
        (USD, "1 250.50", 125_050),
        (USD, "0.01", 1),
        (USD, "19.99", 1_999),  # 19.99 * 100 is 1998.9999999999998 as a float: nothing here is a float
        (USD, "0.29", 29),
    ],
)
def test_what_a_person_typed_is_read_exactly(currency: Currency, typed: str, minor: int) -> None:
    assert to_minor(currency, typed) == minor


@pytest.mark.parametrize(
    ("currency", "typed"),
    [
        (UZS, "45000.5"),  # so'm has no decimals
        (UZS, "45000.0"),
        (USD, "50.123"),  # a third decimal is not rounded away
        (USD, "50."),
        (USD, ".5"),
        (USD, "-5"),
        (USD, "+5"),
        (USD, "5$"),
        (USD, "1,250.50"),  # two marks: not guessed
        (USD, "1e3"),
        (USD, ""),
        (USD, "   "),
        (USD, "٥٠"),  # digits of another script
    ],
)
def test_what_is_not_exactly_an_amount_is_refused_and_never_rounded(currency: Currency, typed: str) -> None:
    assert to_minor(currency, typed) is None


def test_reading_and_writing_agree() -> None:
    for currency in Currency:
        for amount in (1, 99, 100, 101, 12_345, 1_000_000, 99_999_999):
            assert to_minor(currency, plain(currency, amount)) == amount
            assert to_minor(currency, format_amount(currency, amount).replace(NBSP, " ")) == amount


def test_money_of_one_currency_adds_and_subtracts() -> None:
    assert Money(500, USD) + Money(250, USD) == Money(750, USD)
    assert Money(45_000) - Money(5_000) == Money(40_000, UZS)
    assert Money(125_050, USD).format("ru") == f"1{NBSP}250.50{NBSP}$"


def test_money_of_two_currencies_is_never_combined() -> None:
    with pytest.raises(CurrencyMismatch):
        _ = Money(45_000, UZS) + Money(500, USD)
    with pytest.raises(CurrencyMismatch):
        _ = Money(500, USD) - Money(45_000, UZS)


def test_totals_are_one_sum_for_each_currency_and_never_one_of_them_all() -> None:
    mixed = [Money(500, USD), Money(45_000), Money(250, USD), Money(5_000)]
    assert totals(mixed) == {UZS: 50_000, USD: 750}
    assert list(totals(mixed)) == [UZS, USD], "so'm first, whatever the order they came in"
    assert totals([Money(1, USD)]) == {USD: 1}, "a currency that does not occur has no total, not a zero"
    assert totals([]) == {}

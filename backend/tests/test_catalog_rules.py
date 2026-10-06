"""Pure catalog rules: item names, units and price bounds (REQ-039)."""

import pytest

from qarz.domain.catalog import DEFAULT_UNIT, MAX_PRICE, MAX_UNIT_LENGTH, check_price, item_name, normalize_unit


@pytest.mark.parametrize(
    ("typed", "stored"),
    [("Non", "Non"), ("  Coca-Cola   1 l ", "Coca-Cola 1 l"), ("x" * 80, "x" * 80), ("7", "7"), ("Сут 1л", "Сут 1л")],
)
def test_a_name_is_trimmed_and_keeps_its_spelling(typed: str, stored: str) -> None:
    assert item_name(typed) == stored


@pytest.mark.parametrize("typed", ["", "   ", "\t\n", "x" * 81, "ь", " ь ь "])
def test_an_unusable_name_is_refused(typed: str) -> None:
    with pytest.raises(ValueError):
        item_name(typed)


def test_the_length_limit_counts_the_trimmed_name() -> None:
    assert item_name("  " + "x" * 80 + "  ") == "x" * 80


@pytest.mark.parametrize("typed", [None, "", "   "])
def test_a_missing_unit_is_a_piece(typed: str | None) -> None:
    assert normalize_unit(typed) == DEFAULT_UNIT == "dona"


@pytest.mark.parametrize(
    ("typed", "stored"),
    [
        ("dona", "dona"),
        ("ШТ", "dona"),
        ("шт.", "dona"),
        ("ta", "dona"),
        ("Дона", "dona"),
        ("KG", "kg"),
        (" кг ", "kg"),
        ("kilo", "kg"),
        ("гр", "g"),
        ("Gramm", "g"),
        ("Л", "l"),
        ("litr", "l"),
        ("МЛ", "ml"),
        ("метр", "m"),
        ("қути", "quti"),
        ("коробка", "quti"),
        ("Пакет", "paket"),
    ],
)
def test_known_spellings_of_a_unit_become_one_unit(typed: str, stored: str) -> None:
    assert normalize_unit(typed) == stored


@pytest.mark.parametrize(
    ("typed", "stored"),
    [("Blok", "blok"), ("bog‘", "bog'"), ("BOG`", "bog'"), ("  qop ", "qop"), ("0.5 l", "0.5 l"), ("x" * 12, "x" * 12)],
)
def test_a_shops_own_unit_is_kept_in_lower_case(typed: str, stored: str) -> None:
    assert normalize_unit(typed) == stored


@pytest.mark.parametrize("typed", ["x" * (MAX_UNIT_LENGTH + 1), "kg/m", "<b>", "dona;", "12", "...", "'"])
def test_an_unusable_unit_is_refused(typed: str) -> None:
    with pytest.raises(ValueError):
        normalize_unit(typed)


@pytest.mark.parametrize("price", [1, 4000, MAX_PRICE])
def test_a_price_within_the_bounds_is_accepted(price: int) -> None:
    assert check_price(price) == price


@pytest.mark.parametrize("price", [0, -1, -4000, MAX_PRICE + 1, True, False, 4000.0, 0.5, "4000", None])
def test_a_price_outside_the_bounds_or_not_whole_is_refused(price: object) -> None:
    with pytest.raises(ValueError):
        check_price(price)  # type: ignore[arg-type]


def test_the_largest_price_is_the_largest_single_sale() -> None:
    from qarz.application.ledger_service import MAX_AMOUNT

    assert MAX_PRICE == MAX_AMOUNT

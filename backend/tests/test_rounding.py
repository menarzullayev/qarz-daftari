from decimal import Decimal

import pytest

from qarz.domain.rounding import line_total


@pytest.mark.parametrize(
    ("qty", "price", "expected"),
    [
        ("1", 4_000, 4_000),
        ("5", 4_000, 20_000),
        ("0.5", 25_000, 12_500),
        ("0.333", 10_000, 3_330),
        ("0.125", 4, 1),  # 0.5 rounds up
        ("0.375", 4, 2),  # 1.5 rounds up, not to even
        ("0.625", 4, 3),  # 2.5 rounds up, not to even
        ("1.234", 999, 1_233),  # 1232.766 rounds up
        ("1.001", 1_000, 1_001),
    ],
)
def test_line_total(qty: str, price: int, expected: int) -> None:
    assert line_total(Decimal(qty), price) == expected


@pytest.mark.parametrize(
    ("qty", "price"),
    [
        ("0", 1_000),
        ("-1", 1_000),
        ("1", 0),
        ("1", -5),
        ("1.0001", 1_000),  # four decimals
        ("0.001", 100),  # rounds to zero UZS
    ],
)
def test_line_total_rejects_invalid_input(qty: str, price: int) -> None:
    with pytest.raises(ValueError):
        line_total(Decimal(qty), price)

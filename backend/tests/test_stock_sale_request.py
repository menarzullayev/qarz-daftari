"""The shape of a cash sale, checked before anything is stored (`qarz.application.stock_sales.clean_sale`)."""

import uuid
from decimal import Decimal

import pytest

from qarz.application.errors import ValidationFailed
from qarz.application.stock_sales import SaleLineRequest, SaleRequest, clean_sale
from qarz.domain import stock
from qarz.domain.cash import Method

ITEM = uuid.uuid4()


def request(lines: list[SaleLineRequest] | None = None, **change: str | None) -> SaleRequest:
    given = {"method": None, "currency": None, "note": None, **change}
    return SaleRequest(lines if lines is not None else [SaleLineRequest(ITEM, "1", None)], **given)


def test_a_sale_that_says_nothing_about_money_is_for_cash_at_the_items_price() -> None:
    clean = clean_sale(request())
    assert clean.method is Method.CASH and clean.note is None
    assert [(line.item_id, line.qty, line.price) for line in clean.lines] == [(ITEM, Decimal(1), None)]


def test_what_a_sale_says_is_kept() -> None:
    clean = clean_sale(request([SaleLineRequest(ITEM, "0.250", 12_000)], method="card", currency="UZS", note=" x "))
    assert (clean.method, clean.note) == (Method.CARD, "x")
    assert (clean.lines[0].qty, clean.lines[0].price) == (Decimal("0.250"), 12_000)


@pytest.mark.parametrize(
    ("wrong", "field"),
    [
        (request([]), "lines"),
        (request([SaleLineRequest(uuid.uuid4(), "1", None)] * (stock.MAX_SALE_LINES + 1)), "lines"),
        (request([SaleLineRequest(ITEM, "1", None), SaleLineRequest(ITEM, "2", None)]), "lines.1.item_id"),
        (request([SaleLineRequest(ITEM, "0", None)]), "lines.0.qty"),
        (request([SaleLineRequest(ITEM, "1,5", None)]), "lines.0.qty"),
        (request([SaleLineRequest(ITEM, "1", 0)]), "lines.0.price"),
        (request(method="barter"), "method"),
        (request(currency="USD"), "currency"),
        (request(currency="uzs"), "currency"),
        (request(note="x" * 201), "note"),
    ],
)
def test_a_sale_of_the_wrong_shape_is_refused_by_field(wrong: SaleRequest, field: str) -> None:
    with pytest.raises(ValidationFailed) as refused:
        clean_sale(wrong)
    assert field in refused.value.fields

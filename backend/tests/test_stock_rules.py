"""Pure rules of the stock: barcodes, quantities, and the weighted-average cost (qarz.domain.stock).

Each rule has the case that must work and the case that must be refused.
"""

import random
from decimal import Decimal

import pytest

from qarz.domain import stock
from qarz.domain.stock import EMPTY, Level

D = Decimal


def level(on_hand: str, value: int, currency: str | None = "UZS", last: str | None = None) -> Level:
    return Level(D(on_hand), value, currency, None if last is None else D(last))


# --- units and reasons ----------------------------------------------------------------------------------


def test_the_units_and_the_reasons_are_keys_and_their_names_are_texts() -> None:
    """What a unit or a reason is called is a text of the catalogs, in every language
    (tests/test_text_tables.py); the domain holds the keys alone."""
    assert len(stock.UNIT_KEYS) == len(stock.UNITS), "no unit twice"
    assert set(stock.WRITE_OFF_REASONS) == stock.WRITE_OFF_KEYS == {"damaged", "expired", "lost", "own_use"}
    assert {unit.key for unit in stock.UNITS if unit.weighed} == {"kg", "g", "l", "ml", "m"}


def test_the_units_are_the_forms_the_catalogue_folds_spellings_to() -> None:
    """A counted item's unit must be one the catalogue itself would store for the usual spellings."""
    from qarz.domain.catalog import normalize_unit

    for unit in stock.UNITS:
        assert normalize_unit(unit.key) == unit.key
    assert normalize_unit("кг") in stock.UNIT_KEYS and normalize_unit("шт") in stock.UNIT_KEYS
    assert normalize_unit("bog'") not in stock.UNIT_KEYS, "the catalogue keeps a unit the stock does not count in"


# --- barcodes -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code",
    [
        "4006381333931",  # EAN-13
        "96385074",  # EAN-8
        "036000291452",  # UPC-A
        "ABC-123",  # Code-128 text
        "SKU 00042/A",
        "12345",  # digits of a length no retail code has: a label's own number
        "00012345678905",  # fourteen digits: a carton code, taken as text
        "x" * 48,
    ],
)
def test_a_barcode_that_can_be_one_is_kept_as_it_is(code: str) -> None:
    assert stock.barcode(code) == code
    assert stock.barcode(f"  {code} ") == code, "spaces around it are dropped"


@pytest.mark.parametrize(
    ("code", "why"),
    [
        ("4006381333932", "check digit"),  # EAN-13 with the last digit wrong
        ("96385075", "check digit"),
        ("036000291453", "check digit"),
        ("", "empty"),
        ("   ", "empty"),
        ("x" * 49, "at most"),
        ("Нон-12", "Latin"),
        ("tab\there", "Latin"),
        ("кг", "Latin"),
    ],
)
def test_a_barcode_that_cannot_be_one_is_refused(code: str, why: str) -> None:
    with pytest.raises(ValueError, match=why):
        stock.barcode(code)


def test_the_check_digit_is_the_gs1_one() -> None:
    assert stock.gs1_check_digit("400638133393") == 1
    assert stock.gs1_check_digit("9638507") == 4
    assert stock.gs1_check_digit("03600029145") == 2


def test_an_items_barcodes_are_each_valid_and_none_twice() -> None:
    assert stock.barcodes(["96385074", " ABC-1 "]) == ["96385074", "ABC-1"]
    assert stock.barcodes([]) == []
    with pytest.raises(ValueError, match="barcode 2: listed twice"):
        stock.barcodes(["ABC-1", "ABC-1 "])
    with pytest.raises(ValueError, match="barcode 2: the check digit"):
        stock.barcodes(["ABC-1", "96385075"])
    with pytest.raises(ValueError, match="at most 10"):
        stock.barcodes([f"C{n}" for n in range(11)])


# --- quantities and money -------------------------------------------------------------------------------


def test_a_quantity_has_at_most_three_decimals_and_is_above_zero() -> None:
    assert stock.quantity("1.5") == D("1.5")
    assert stock.quantity("0.001") == D("0.001")
    assert stock.counted("0") == D(0), "a stocktake may count nothing"
    assert stock.low_stock("0") == D(0)
    for wrong in ("0", "0.000", "-1", "1,5", "1e3", "1.2345", "", " 1", "abc", "1.", ".5"):
        with pytest.raises(ValueError):
            stock.quantity(wrong)
    for wrong in ("-1", "1,5", "1e3", "1.2345", "", "abc"):
        with pytest.raises(ValueError):
            stock.counted(wrong)


def test_a_unit_cost_is_a_whole_number_of_minor_units_and_may_be_zero() -> None:
    assert stock.unit_cost(0) == 0
    assert stock.unit_cost(stock.MAX_UNIT_COST) == stock.MAX_UNIT_COST
    for wrong in (-1, stock.MAX_UNIT_COST + 1, 1.5, "100", True, None):
        with pytest.raises(ValueError):
            stock.unit_cost(wrong)


def test_a_line_is_rounded_half_up_once() -> None:
    assert stock.line_cost(D("1.5"), 333) == 500  # 499.5
    assert stock.line_cost(D("0.001"), 499) == 0  # 0.499
    assert stock.line_cost(D("0.001"), 500) == 1  # 0.5
    assert stock.line_cost(D("3"), 1000) == 3000


# --- the cost rule --------------------------------------------------------------------------------------


def test_a_receipt_adds_its_quantity_and_its_cost_and_the_average_is_weighted() -> None:
    first = stock.receive(EMPTY, D(10), 1000, "UZS")
    assert (first.qty, first.value_delta, first.cost_total) == (D(10), 10_000, 10_000)
    assert first.after == level("10", 10_000, last="1000")
    second = stock.receive(first.after, D(30), 2000, "UZS")
    assert second.after.on_hand == D(40) and second.after.value == 70_000
    assert second.after.average == D("1750")


def test_goods_leave_at_the_average_and_the_average_stays() -> None:
    held = level("40", 70_000)
    out = stock.go_out(held, D(10), may_go_negative=False)
    assert (out.qty, out.value_delta, out.cost_total) == (D(-10), -17_500, 17_500)
    assert out.after.on_hand == D(30) and out.after.value == 52_500
    assert out.after.average == D("1750")


def test_when_everything_leaves_no_value_is_left_behind() -> None:
    """Three units worth 1 000 leave one at a time: 333, 334 (half of 667 rounds up), and the last takes
    what is left, so the value is exactly zero whatever the rounding did on the way."""
    held = level("3", 1000)
    taken = []
    for _ in range(3):
        out = stock.go_out(held, D(1), may_go_negative=False)
        taken.append(-out.value_delta)
        held = out.after
    assert taken == [333, 334, 333]
    assert held.on_hand == 0 and held.value == 0
    assert held.last_cost is not None, "the last known cost is remembered for the margin"


def test_a_sale_may_go_below_zero_and_then_takes_no_value() -> None:
    held = level("2", 2000)
    out = stock.go_out(held, D(5), may_go_negative=True)
    assert out.after.on_hand == D(-3) and out.after.value == 0
    assert out.value_delta == -2000
    assert out.cost_total == 5000, "all five carry the average for the margin report"
    again = stock.go_out(out.after, D(1), may_go_negative=True)
    assert again.value_delta == 0 and again.after.on_hand == D(-4)
    assert again.cost_total == 1000, "the last known cost"


def test_goods_that_may_not_go_below_zero_are_refused_beyond_what_is_on_hand() -> None:
    held = level("2", 2000)
    with pytest.raises(stock.NotEnoughOnHand) as refused:
        stock.go_out(held, D("2.001"), may_go_negative=False)
    assert (refused.value.on_hand, refused.value.wanted) == (D(2), D("2.001"))
    assert stock.go_out(held, D(2), may_go_negative=False).after.on_hand == 0, "exactly what is on hand is fine"


def test_a_receipt_into_a_shortfall_values_only_what_remains() -> None:
    """Sold three more than were on hand, then twelve arrive at 200: nine are on hand, worth 1 800.
    The three that covered the shortfall are not in stock and carry no value."""
    short = level("-3", 0, last="100")
    came = stock.receive(short, D(12), 200, "UZS")
    assert came.after.on_hand == D(9) and came.after.value == 1800
    assert came.cost_total == 2400, "what was paid for the twelve"
    assert came.after.average == D(200)
    still_short = stock.receive(short, D(2), 200, "UZS")
    assert still_short.after.on_hand == D(-1) and still_short.after.value == 0


def test_goods_without_a_price_come_in_at_the_average_which_does_not_move() -> None:
    held = level("4", 6000)  # 1 500 each
    found = stock.find_more(held, D(2))
    assert found.after.on_hand == D(6) and found.after.value == 9000
    assert found.after.average == D(1500)
    never_costed = stock.find_more(EMPTY, D(2))
    assert never_costed.after.on_hand == D(2) and never_costed.after.value == 0 and never_costed.cost_total is None
    from_memory = stock.find_more(level("0", 0, last="700"), D(2))
    assert from_memory.after.value == 1400, "nothing on hand: the last known cost"


def test_a_stocktake_corrects_the_books_to_the_count() -> None:
    held = level("10", 10_000)
    assert stock.correct_to(held, D(10)) is None, "the books agree: nothing moves"
    less = stock.correct_to(held, D(7))
    assert less is not None and less.qty == D(-3) and less.after.value == 7000
    more = stock.correct_to(held, D(12))
    assert more is not None and more.qty == D(2) and more.after.value == 12_000
    below = stock.correct_to(level("-4", 0, last="500"), D(1))
    assert below is not None and below.qty == D(5) and below.after.on_hand == D(1) and below.after.value == 500


def test_cancelling_a_receipt_restores_the_average_it_disturbed() -> None:
    before = level("10", 10_000)
    wrong = stock.receive(before, D(10), 3000, "UZS")
    assert wrong.after.average == D(2000)
    undone = stock.take_back(wrong.after, D(10), wrong.value_delta)
    assert undone.after.on_hand == D(10) and undone.after.value == 10_000
    assert undone.after.average == D(1000)


def test_a_receipt_whose_goods_have_left_cannot_be_cancelled() -> None:
    with pytest.raises(stock.NotEnoughOnHand):
        stock.take_back(level("4", 4000), D(5), 5000)


def test_taking_back_never_takes_more_value_than_the_stock_holds() -> None:
    """10 at 100 and 20 at 10 are received (30 worth 1 200), 15 are sold at the average of 40. Cancelling
    the first receipt would take 1 000 out of the 600 that are left: it leaves at the average instead."""
    held = level("15", 600)
    undone = stock.take_back(held, D(10), 1000)
    assert undone.after.on_hand == D(5) and undone.after.value == 200
    emptied = stock.take_back(level("10", 600), D(10), 1000)
    assert emptied.after.on_hand == 0 and emptied.after.value == 0


def test_a_cancelled_sale_comes_back_at_the_cost_it_left_with() -> None:
    held = level("10", 10_000)
    sold = stock.go_out(held, D(4), may_go_negative=False)
    # A dearer receipt in between moves the average; the cancelled sale still returns its own 4 000.
    dearer = stock.receive(sold.after, D(6), 3000, "UZS")
    back = stock.bring_back(dearer.after, D(4), sold.cost_total, "UZS")
    assert back.after.on_hand == D(16) and back.after.value == dearer.after.value + 4000
    # A cost in a currency the item no longer keeps, or none at all: at the average, like any other find.
    other = stock.bring_back(level("2", 2000, "USD"), D(1), 500, "UZS")
    assert other.after.value == 3000 and other.after.currency == "USD"
    unknown = stock.bring_back(level("2", 2000), D(1), None, None)
    assert unknown.after.value == 3000


def test_the_cost_of_an_item_is_kept_in_one_currency_at_a_time() -> None:
    held = level("5", 5000, "UZS")
    with pytest.raises(stock.CostCurrencyMismatch) as refused:
        stock.receive(held, D(1), 100, "USD")
    assert (refused.value.held, refused.value.given) == ("UZS", "USD")
    # Once nothing is on hand the next receipt starts the cost again in its own currency.
    empty = stock.go_out(held, D(5), may_go_negative=False).after
    fresh = stock.receive(empty, D(2), 150, "USD")
    assert fresh.after == Level(D(2), 300, "USD", D(150))


def test_margin_is_price_less_average_and_never_across_currencies() -> None:
    assert stock.margin(1500, level("4", 4000)) == 500
    assert stock.margin(900, level("4", 4000)) == -100, "sold below cost shows as a loss"
    assert stock.margin(1500, level("0", 0, last="1200")) == 300, "nothing on hand: the last known cost"
    assert stock.margin(1500, EMPTY) is None
    assert stock.margin(1500, level("4", 400, "USD")) is None, "so'm less dollars is no number"


def test_running_low_needs_a_threshold() -> None:
    assert stock.is_low(D(3), D(3)) and stock.is_low(D(0), D(0)) and stock.is_low(D(-1), D(0))
    assert not stock.is_low(D("3.001"), D(3))
    assert not stock.is_low(D(0), None)


def test_any_sequence_of_movements_keeps_the_two_facts_of_a_level() -> None:
    """Whatever happens, in any order: nothing on hand is worth nothing, the value is never negative,
    and the level after a movement is the level before it plus what the movement says it did."""
    rng = random.Random(20261009)  # noqa: S311 - a fixed seed for repeatable test data, not a secret
    for _ in range(300):
        held = EMPTY
        for _ in range(40):
            qty = D(rng.randint(1, 5000)) / 1000
            choice = rng.randint(0, 4)
            try:
                if choice == 0:
                    effect = stock.receive(held, qty, rng.randint(0, 50_000), held.currency or "UZS")
                elif choice == 1:
                    effect = stock.go_out(held, qty, may_go_negative=rng.random() < 0.5)
                elif choice == 2:
                    effect = stock.find_more(held, qty)
                elif choice == 3:
                    effect = stock.take_back(held, qty, rng.randint(0, 100_000))
                else:
                    effect = stock.bring_back(held, qty, rng.randint(0, 100_000), "UZS")
            except stock.NotEnoughOnHand:
                continue
            after = effect.after
            assert after.on_hand == held.on_hand + effect.qty
            assert after.value == held.value + effect.value_delta
            assert after.value >= 0
            assert after.on_hand > 0 or after.value == 0
            assert effect.cost_total is None or effect.cost_total >= 0
            held = after


# --- a sale for cash (BR-98 to BR-104) --------------------------------------------------------------------


def test_a_cash_sale_is_stored_as_a_kind_nobody_writes_as_a_document() -> None:
    assert stock.DOC_SALE not in stock.DOCUMENT_KINDS
    assert (*stock.DOCUMENT_KINDS, "sale") == stock.STORED_KINDS


@pytest.mark.parametrize("price", [1, 15_000, 100_000_000])
def test_a_selling_price_is_a_price_of_the_catalogue(price: int) -> None:
    assert stock.sale_price(price) == price


@pytest.mark.parametrize("price", [0, -1, 100_000_001, 1.5, "1000", None, True])
def test_a_selling_price_outside_the_catalogues_bounds_is_refused(price: object) -> None:
    with pytest.raises(ValueError):
        stock.sale_price(price)


def test_a_sold_line_comes_to_quantity_times_price_rounded_half_up_once() -> None:
    assert stock.sale_line_total(Decimal("2.5"), 15_000) == 37_500
    assert stock.sale_line_total(Decimal("0.333"), 10_000) == 3_330
    assert stock.sale_line_total(Decimal("0.0005") * 1000, 1) == 1  # half a so'm rounds up to one
    with pytest.raises(ValueError, match="less than one"):
        stock.sale_line_total(Decimal("0.001"), 1)


def test_the_margin_of_a_sold_line_is_never_a_difference_of_two_currencies() -> None:
    assert stock.sale_margin(30_000, 20_000, "UZS") == 10_000
    assert stock.sale_margin(20_000, 32_000, "UZS") == -12_000, "sold below what it cost"
    assert stock.sale_margin(30_000, None, None) is None, "an item never received has no cost"
    assert stock.sale_margin(30_000, 250, "USD") is None


def test_a_cash_sale_leaves_at_the_average_and_leaves_the_average_where_it_was() -> None:
    level = stock.receive(stock.EMPTY, Decimal(10), 10_000, "UZS").after
    level = stock.receive(level, Decimal(10), 20_000, "UZS").after
    out = stock.go_out(level, Decimal(4), may_go_negative=True)
    assert (out.cost_total, out.after.on_hand, out.after.average) == (60_000, Decimal(16), Decimal("15000.0000"))
    back = stock.bring_back(out.after, Decimal(4), out.cost_total, "UZS")
    assert (back.after.on_hand, back.after.value) == (level.on_hand, level.value), "cancelling undoes it exactly"
    with pytest.raises(stock.NotEnoughOnHand):
        stock.go_out(level, Decimal(21), may_go_negative=False)

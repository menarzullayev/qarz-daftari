"""Pure rules of the stock: units, barcodes, quantities, and the cost of what is on hand.

The stock of a shop is a ledger of movements, never a number someone edits. Each movement changes how
much of one item is on hand and what that stock is worth at cost; this module is the one place that says
by how much. The storage layer keeps, per item, the sums of those changes (the *level*), and the database
refuses a movement whose figures do not continue the level.

Quantities. A quantity is a decimal with exactly three places (`numeric(14,3)`), the scale goods lines
have always had: a gram of a kilogram, a millilitre of a litre. Never a float.

Money. A cost is a whole number of the currency's minor unit (so'm, or cents), as everywhere else
(`qarz.domain.money`). Wherever a quantity is multiplied by a cost the result is rounded half up to a
whole minor unit, once, and stored; nothing is rounded twice.

The cost rule: weighted average, kept as a pool. A level is the quantity on hand `Q`, the value `V` of
that quantity at cost, and the currency of `V`. The average cost is `V / Q` and is derived, never stored
as the truth. Two facts hold after every movement: `Q <= 0` implies `V = 0`, and `V >= 0`.

- Goods come in at a cost `c` (a purchase receipt): `Q' = Q + q`, `V' = V + round(q * c)`. If the item
  had been sold below zero (`Q < 0`), the part of the receipt that covers the shortfall is not on hand,
  so only what remains is valued: `V' = round(max(Q', 0) * c)`.
- Goods go out (a sale, a write-off, a return to the supplier, a stocktake that found less): they leave
  at the average. `V' = V - round(V * q / Q)`, and when everything on hand leaves, `V' = 0` exactly, so
  no residue of rounding stays behind. What leaves beyond zero carries the last known cost for the
  margin report and changes no value.
- Goods come back without a price of their own (a stocktake that found more): at the current average,
  or at the last known cost while nothing is on hand. The average does not move.
- A movement that is cancelled is undone by an opposite movement, never edited:
  an outgoing movement comes back at the cost it left with (`bring_back`);
  an incoming movement leaves with the value it brought (`take_back`), so cancelling a wrong receipt
  restores the average it disturbed. It is refused when less than its quantity is on hand: those goods
  have already left, and history would no longer add up.
- One currency at a time. The cost of an item is kept in the currency of its receipts. While something
  of it is on hand, a receipt in the other currency is refused (there is no exchange rate anywhere); once
  nothing is on hand the next receipt starts the cost again in its own currency.

Negative stock. A sale may take an item below zero: shops sell before they write the receipt down. The
answer then carries a warning, and a shop that prefers a refusal turns on "refuse sales beyond stock".
Write-offs and returns to a supplier never go below zero: nobody throws away goods the books do not hold.
"""

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from qarz.domain.catalog import MAX_PRICE, MIN_PRICE
from qarz.domain.goods import parse_qty

QTY_PLACES = 3
_QTY_STEP = Decimal("0.001")
_COST_STEP = Decimal("0.0001")
ZERO = Decimal(0)

# The largest cost of one unit and the largest total of one document, in minor units of its currency.
MAX_UNIT_COST = 1_000_000_000
MAX_DOCUMENT_TOTAL = 1_000_000_000_000
MAX_DOCUMENT_LINES = 200
MAX_NOTE = 200
MAX_REASON = 200
MAX_LOW_STOCK = Decimal("999999999.999")

SWITCH = "stock_on"

# --- units ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Unit:
    key: str  # the stored form, the same the catalogue has always folded spellings to
    uz: str
    ru: str
    weighed: bool  # sold by measure: a client offers a decimal quantity


# The units a stock-tracked item may have. The catalogue itself still accepts any short unit a shop
# types; an item is only counted in stock in one of these.
UNITS: tuple[Unit, ...] = (
    Unit("dona", "dona", "шт", False),
    Unit("kg", "kg", "кг", True),
    Unit("g", "g", "г", True),
    Unit("l", "litr", "л", True),
    Unit("ml", "ml", "мл", True),
    Unit("m", "metr", "м", True),
    Unit("quti", "quti", "коробка", False),
    Unit("paket", "paket", "пакет", False),
    Unit("juft", "juft", "пара", False),
    Unit("qop", "qop", "мешок", False),
    Unit("blok", "blok", "блок", False),
)
UNIT_KEYS = frozenset(unit.key for unit in UNITS)

# --- why goods were written off ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Reason:
    key: str
    uz: str
    ru: str


WRITE_OFF_REASONS: tuple[Reason, ...] = (
    Reason("damaged", "Shikastlangan", "Повреждён"),
    Reason("expired", "Muddati o'tgan", "Истёк срок"),
    Reason("lost", "Yo'qolgan", "Утерян"),
    Reason("own_use", "O'z ehtiyojiga", "Для себя"),
)
WRITE_OFF_KEYS = frozenset(reason.key for reason in WRITE_OFF_REASONS)

# --- kinds --------------------------------------------------------------------------------------------------

RECEIPT, SALE, CUSTOMER_RETURN, SUPPLIER_RETURN, WRITE_OFF, CORRECTION, REVERSAL = (
    "receipt",
    "sale",
    "customer_return",
    "supplier_return",
    "write_off",
    "correction",
    "reversal",
)
MOVEMENT_KINDS = (RECEIPT, SALE, CUSTOMER_RETURN, SUPPLIER_RETURN, WRITE_OFF, CORRECTION, REVERSAL)

# A document is what a person writes; posting it makes the movements.
DOC_RECEIPT, DOC_CUSTOMER_RETURN, DOC_SUPPLIER_RETURN, DOC_WRITE_OFF, DOC_STOCKTAKE = (
    "receipt",
    "customer_return",
    "supplier_return",
    "write_off",
    "stocktake",
)
DOCUMENT_KINDS = (DOC_RECEIPT, DOC_CUSTOMER_RETURN, DOC_SUPPLIER_RETURN, DOC_WRITE_OFF, DOC_STOCKTAKE)
# The kinds whose lines carry a price: what was paid for the goods, or what is given back for them.
PRICED_KINDS = frozenset({DOC_RECEIPT, DOC_CUSTOMER_RETURN, DOC_SUPPLIER_RETURN})
DRAFT, POSTED, CANCELLED = "draft", "posted", "cancelled"
# A sale for cash, without a customer, is stored as a document too, but nobody writes it as one: it is
# made at the counter in one step (`qarz.application.stock_sales`) and is never a draft. The routes of
# the documents above do not know it.
DOC_SALE = "sale"
STORED_KINDS = (*DOCUMENT_KINDS, DOC_SALE)
MAX_SALE_LINES = 100

# --- barcodes -------------------------------------------------------------------------------------------

MAX_BARCODES = 10
MAX_BARCODE_LENGTH = 48
# The retail codes with a check digit: EAN-8, UPC-A, EAN-13.
_CHECKED_LENGTHS = frozenset({8, 12, 13})
_CODE128 = re.compile(r"[\x21-\x7e](?:[\x20-\x7e]*[\x21-\x7e])?")


def gs1_check_digit(body: str) -> int:
    """The check digit of a GS1 code whose digits, without the check digit, are `body`."""
    total = sum(int(digit) * (3 if position % 2 == 0 else 1) for position, digit in enumerate(reversed(body)))
    return (10 - total % 10) % 10


def barcode(raw: str) -> str:
    """The barcode as it is stored. Raises ValueError when it cannot be one.

    Eight, twelve or thirteen digits are an EAN-8, a UPC-A or an EAN-13 and must carry a right check
    digit: a mistyped digit is caught here instead of finding nothing at the counter. Anything else is
    taken as the text of a Code-128 label: printable ASCII, at most 48 characters, no space at either end.
    """
    code = raw.strip()
    if not code:
        raise ValueError("must not be empty")
    if len(code) > MAX_BARCODE_LENGTH:
        raise ValueError(f"at most {MAX_BARCODE_LENGTH} characters")
    if code.isascii() and code.isdigit() and len(code) in _CHECKED_LENGTHS:
        if gs1_check_digit(code[:-1]) != int(code[-1]):
            raise ValueError("the check digit is wrong")
        return code
    if not _CODE128.fullmatch(code):
        raise ValueError("digits, Latin letters and punctuation only")
    return code


def barcodes(raw: list[str]) -> list[str]:
    """An item's barcodes as stored: each valid, none twice, in the order given."""
    if len(raw) > MAX_BARCODES:
        raise ValueError(f"at most {MAX_BARCODES} barcodes")
    cleaned: list[str] = []
    for position, value in enumerate(raw, start=1):
        try:
            code = barcode(value)
        except ValueError as error:
            raise ValueError(f"barcode {position}: {error}") from error
        if code in cleaned:
            raise ValueError(f"barcode {position}: listed twice")
        cleaned.append(code)
    return cleaned


# --- quantities and money -------------------------------------------------------------------------------

_COUNT = re.compile(r"[0-9]{1,9}(\.[0-9]{1,3})?")


def quantity(raw: str) -> Decimal:
    """A quantity that moves: a decimal string with at most three places, above zero."""
    return parse_qty(raw)


def counted(raw: str) -> Decimal:
    """What a stocktake counted: like a quantity, and zero is an answer too."""
    if not _COUNT.fullmatch(raw):
        raise ValueError("a decimal number with at most three decimals, for example 12 or 1.5")
    return Decimal(raw)


def low_stock(raw: str) -> Decimal:
    """The quantity at or below which an item is reported as running low."""
    value = counted(raw)
    if value > MAX_LOW_STOCK:
        raise ValueError("too large")
    return value


def unit_cost(value: object) -> int:
    """The cost of one unit in minor units of the document's currency. Zero is allowed: goods given free."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("must be a whole number of the currency's smallest unit")
    if not 0 <= value <= MAX_UNIT_COST:
        raise ValueError(f"between 0 and {MAX_UNIT_COST}")
    return value


def money(amount: Decimal) -> int:
    """A product of a quantity and a cost as stored: a whole minor unit, rounded half up."""
    return int(amount.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def line_cost(qty: Decimal, cost: int) -> int:
    """What a line of a document comes to: quantity times unit cost, rounded half up once."""
    return money(qty * cost)


def sale_price(value: object) -> int:
    """What one unit is sold for at the counter, in so'm: a price of the catalogue's bounds."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("must be a whole number of UZS")
    if not MIN_PRICE <= value <= MAX_PRICE:
        raise ValueError(f"a whole amount between {MIN_PRICE} and {MAX_PRICE} UZS")
    return value


def sale_line_total(qty: Decimal, price: int) -> int:
    """What a line of a cash sale comes to. A line that rounds to nothing is no sale."""
    total = line_cost(qty, price)
    if total < 1:
        raise ValueError("the line comes to less than one so'm")
    return total


def sale_margin(sale_total: int, cost_total: int | None, cost_currency: str | None) -> int | None:
    """What a sold line earned over its cost. None when the cost is unknown or kept in dollars: amounts
    of two currencies are never subtracted."""
    if cost_total is None or cost_currency != "UZS":
        return None
    return sale_total - cost_total


# --- the level and what a movement does to it -------------------------------------------------------------


@dataclass(frozen=True)
class Level:
    """What is on hand of one item and what it is worth at cost. An item nothing moved yet is `EMPTY`."""

    on_hand: Decimal = ZERO
    value: int = 0
    currency: str | None = None  # of `value` and of `last_cost`
    last_cost: Decimal | None = None  # the average the last time something was on hand, per unit

    @property
    def average(self) -> Decimal | None:
        """The cost of one unit now: the average while something is on hand, otherwise the last known."""
        if self.on_hand > 0:
            return (Decimal(self.value) / self.on_hand).quantize(_COST_STEP, rounding=ROUND_HALF_UP)
        return self.last_cost


EMPTY = Level()


@dataclass(frozen=True)
class Effect:
    """One movement, computed: the signed quantity, what it does to the value, and the level after it."""

    qty: Decimal  # signed: above zero comes in, below zero goes out
    value_delta: int
    # The cost attributed to the quantity that moved, for margins and for undoing it. None when unknown
    # (the item was never received) or when the movement carries no cost of its own currency.
    cost_total: int | None
    after: Level


class NotEnoughOnHand(ValueError):
    """Undoing an incoming movement, or writing goods off, needs that quantity on hand."""

    def __init__(self, on_hand: Decimal, wanted: Decimal) -> None:
        super().__init__("not enough on hand")
        self.on_hand = on_hand
        self.wanted = wanted


class CostCurrencyMismatch(ValueError):
    """Goods priced in one currency met a stock whose cost is kept in the other."""

    def __init__(self, held: str, given: str) -> None:
        super().__init__("the item's cost is kept in another currency")
        self.held = held
        self.given = given


def _after(level: Level, on_hand: Decimal, value: int, currency: str | None) -> Level:
    last = level.last_cost if currency == level.currency else None
    if on_hand > 0 and currency is not None:
        last = (Decimal(value) / on_hand).quantize(_COST_STEP, rounding=ROUND_HALF_UP)
    return Level(on_hand, value, currency, last)


def _come_in(level: Level, qty: Decimal, cost: Decimal, currency: str, cost_total: int) -> Effect:
    on_hand = level.on_hand + qty
    value = level.value + money(qty * cost) if level.on_hand >= 0 else money(max(on_hand, ZERO) * cost)
    return Effect(qty, value - level.value, cost_total, _after(level, on_hand, value, currency))


def receive(level: Level, qty: Decimal, cost: int, currency: str) -> Effect:
    """Goods bought at `cost` a unit come in (a purchase receipt)."""
    if level.currency is not None and level.currency != currency and level.on_hand > 0:
        raise CostCurrencyMismatch(level.currency, currency)
    return _come_in(level, qty, Decimal(cost), currency, line_cost(qty, cost))


def find_more(level: Level, qty: Decimal) -> Effect:
    """Goods with no price of their own come in (a stocktake found more, a customer brought goods back):
    at the average, which therefore stays where it was; at no cost while none is known."""
    average = level.average
    if average is None or level.currency is None:
        return Effect(qty, 0, None, Level(level.on_hand + qty, level.value, level.currency, level.last_cost))
    return _come_in(level, qty, average, level.currency, money(qty * average))


def go_out(level: Level, qty: Decimal, *, may_go_negative: bool) -> Effect:
    """Goods leave at the average cost. `qty` is the quantity leaving, above zero."""
    if qty > level.on_hand and not may_go_negative:
        raise NotEnoughOnHand(level.on_hand, qty)
    average = level.average
    cost_total = None if average is None else money(qty * average)
    if level.on_hand <= 0:
        # Already at or below zero: there is no value left to take.
        return Effect(-qty, 0, cost_total, Level(level.on_hand - qty, 0, level.currency, average))
    if qty >= level.on_hand:
        # The average as it was is what the stock remembers once nothing is left.
        return Effect(-qty, -level.value, cost_total, Level(level.on_hand - qty, 0, level.currency, average))
    taken = money(Decimal(level.value) * qty / level.on_hand)
    return Effect(-qty, -taken, taken, _after(level, level.on_hand - qty, level.value - taken, level.currency))


def bring_back(level: Level, qty: Decimal, cost_total: int | None, currency: str | None) -> Effect:
    """Undo an outgoing movement: its quantity comes back at the cost it left with.

    When that cost is unknown, or was in a currency the item's cost is no longer kept in, the goods come
    back at the average like anything else without a price.
    """
    if cost_total is None or currency is None or (level.currency is not None and level.currency != currency):
        return find_more(level, qty)
    return _come_in(level, qty, Decimal(cost_total) / qty, currency, cost_total)


def take_back(level: Level, qty: Decimal, value: int) -> Effect:
    """Undo an incoming movement: its quantity leaves with the value it brought.

    Refused when less than that is on hand. When the stock no longer holds that much value (part of it
    left since, at an average the movement had raised), what leaves is valued at the average instead, so
    the value never goes below zero.
    """
    if qty > level.on_hand:
        raise NotEnoughOnHand(level.on_hand, qty)
    on_hand = level.on_hand - qty
    if on_hand == 0:
        return Effect(-qty, -level.value, level.value, Level(on_hand, 0, level.currency, level.average))
    if value > level.value:
        return go_out(level, qty, may_go_negative=False)
    return Effect(-qty, -value, value, _after(level, on_hand, level.value - value, level.currency))


def correct_to(level: Level, count: Decimal) -> Effect | None:
    """A stocktake counted `count`: the movement that takes the books there, or None when they agree."""
    difference = count - level.on_hand
    if difference == 0:
        return None
    if difference > 0:
        return find_more(level, difference)
    return go_out(level, -difference, may_go_negative=True)


def is_low(on_hand: Decimal, threshold: Decimal | None) -> bool:
    """Running low: a threshold is set and no more than it is on hand."""
    return threshold is not None and on_hand <= threshold


def margin(price: int, level: Level, price_currency: str = "UZS") -> int | None:
    """Selling price less average cost, for one unit. None when no cost is known or it is in another
    currency: amounts of two currencies are never subtracted."""
    average = level.average
    if average is None or level.currency != price_currency:
        return None
    return price - money(average)

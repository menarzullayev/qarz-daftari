"""Pure rules for goods lines (DOM-012; REQ-037, REQ-038, REQ-N06; INV-7, INV-8).

A goods line is one good of a credit sale: a name, a quantity, a unit, a unit price and a line total.
The total itself is `qarz.domain.rounding.line_total` (BR-7).
"""

import re
from datetime import datetime, time, timedelta
from decimal import Decimal

from qarz.domain.promise import TASHKENT, tashkent_date

MIN_LINES = 1
MAX_LINES = 50

# numeric(12,3): up to nine digits before the point and three after. A plain decimal only: no sign,
# no exponent, no thousands separator, so "1e3" or "1,5" cannot be read as something the seller did not mean.
_QTY = re.compile(r"[0-9]{1,9}(\.[0-9]{1,3})?")


def parse_qty(raw: str) -> Decimal:
    """A quantity as the API carries it: a decimal string with at most three decimals, above zero."""
    if not _QTY.fullmatch(raw):
        raise ValueError("a decimal number with at most three decimals, for example 1.5")
    qty = Decimal(raw)
    if qty <= 0:
        raise ValueError("must be greater than zero")
    return qty


def format_qty(qty: Decimal) -> str:
    """The quantity as a decimal string without trailing zeros: 2, 1.5, 0.125."""
    return format(qty.normalize(), "f")


def line_count_allowed(count: int) -> bool:
    return MIN_LINES <= count <= MAX_LINES


def lines_window_open(created_at: datetime, now: datetime) -> bool:
    """INV-8: lines may be added until the end of the day after the sale, in Tashkent time.

    The first instant of the second day after the sale is already too late. The database trigger
    `goods_line_guard` applies the same limit.
    """
    closes_at = datetime.combine(tashkent_date(created_at) + timedelta(days=2), time.min, tzinfo=TASHKENT)
    return now < closes_at

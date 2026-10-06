"""Money arithmetic for goods lines (domain rule BR-7, requirement REQ-N06)."""

from decimal import ROUND_HALF_UP, Decimal

_QTY_STEP = Decimal("0.001")


def line_total(qty: Decimal, unit_price: int) -> int:
    """Quantity times unit price, rounded to whole UZS with halves rounded up.

    Quantity may carry at most three decimals and must be positive; price is whole UZS and positive.
    """
    if unit_price <= 0:
        raise ValueError("unit price must be a positive whole number of UZS")
    if qty <= 0:
        raise ValueError("quantity must be positive")
    if qty != qty.quantize(_QTY_STEP):
        raise ValueError("quantity may have at most three decimals")
    total = int((qty * unit_price).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    if total <= 0:
        raise ValueError("line total must be at least 1 UZS")
    return total

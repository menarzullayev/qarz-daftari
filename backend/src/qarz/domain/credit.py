"""Credit limits (REQ-044, domain rule BR-8).

A limit never blocks a manager or an owner: it warns them. Whether it blocks a seller is the shop's choice.
"""

from enum import StrEnum

MIN_LIMIT = 1_000
MAX_LIMIT = 10_000_000_000  # UZS; far above any shop's single customer, well inside a bigint


class LimitOutcome(StrEnum):
    WITHIN = "within"
    WARN = "warn"  # above the limit; recorded, and the author is shown balance and limit
    REFUSE = "refuse"  # above the limit and this author may not proceed


def effective_limit(customer_limit: int | None, shop_default: int | None) -> int | None:
    """The customer's own limit if one is set, else the shop's default, else none."""
    return customer_limit if customer_limit is not None else shop_default


def check_limit(limit: int | None, balance_after: int, *, may_manage: bool, sellers_may_exceed: bool) -> LimitOutcome:
    """What a credit sale that would bring the balance to `balance_after` meets.

    A balance equal to the limit is within it.
    """
    if limit is None or balance_after <= limit:
        return LimitOutcome.WITHIN
    if may_manage or sellers_may_exceed:
        return LimitOutcome.WARN
    return LimitOutcome.REFUSE


def valid_limit(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and MIN_LIMIT <= value <= MAX_LIMIT

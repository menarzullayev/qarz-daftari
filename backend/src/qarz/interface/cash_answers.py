"""The cash book's answers, field by field (see `qarz.interface.answers` for what a model does).

Every amount is a whole number of its currency's minor unit, and every figure is of one currency: the
`currency` beside it says which.
"""

from pydantic import Field

from qarz.interface.answers import Answer


class CashCategory(Answer):
    id: str
    direction: str
    name: str
    archived: bool
    # The category a customer's payment lands in: renamed freely, never written to by hand, archived
    # or deleted.
    fixed: bool


class CashCategories(Answer):
    items: list[CashCategory]
    # The currencies the shop works in now, so'm first: what a new entry may be written in.
    currencies: list[str]


class CashEntryCategory(Answer):
    id: str
    name: str


class CashEntryCustomer(Answer):
    id: str
    display_name: str | None


class CashCancellation(Answer):
    at: str
    by: str | None
    # Null when the ledger cancelled the entry: the customer's payment was reversed.
    reason: str | None


class CashEntry(Answer):
    id: str
    direction: str
    method: str
    currency: str
    amount: int
    category: CashEntryCategory
    note: str | None
    day: str
    created_at: str
    author_id: str
    # "manual", or "ledger" for a customer's payment.
    source: str
    # Whose payment it is; null for a manual entry, and for a reader who may not see the customers.
    customer: CashEntryCustomer | None
    cancelled: CashCancellation | None


class CashLine(Answer):
    """One method of one currency: `opening + income - expense = closing`."""

    currency: str
    method: str
    opening: int
    income: int
    expense: int
    closing: int
    count: int


class CashTotal(Answer):
    """One currency over all its methods."""

    currency: str
    opening: int
    income: int
    expense: int
    closing: int
    count: int


class CashDay(Answer):
    date: str
    balances: list[CashLine]
    totals: list[CashTotal]
    entries: list[CashEntry]
    next_cursor: str | None


class CashCategoryTotal(Answer):
    category: CashCategory
    currency: str
    amount: int
    count: int


class CashDayTotal(Answer):
    date: str
    currency: str
    income: int
    expense: int


class CashSummary(Answer):
    # `from` is a word of the language: the answer names the field by its alias.
    from_: str = Field(alias="from")
    to: str
    balances: list[CashLine]
    totals: list[CashTotal]
    categories: list[CashCategoryTotal]
    days: list[CashDayTotal]

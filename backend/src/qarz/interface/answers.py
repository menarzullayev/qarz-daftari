"""Response bodies that the API description names field by field (ADR-012, ADR-013).

A route answers with what its application service returns. Giving it one of these models writes that
shape into `openapi.json`, from which the front end's types are generated, and checks every answer against
it. The models are closed and strict and have no defaults, so nothing is dropped, converted or filled in:
an answer that is not exactly the declared shape fails loudly instead of leaving changed. Fields are in
the order the services write them, which keeps the body the same byte for byte
(tests/api/test_typed_answers.py).

Only reads carry a model. A write may answer with a result stored by an earlier version of the code
(ADR-006), which a model declared today could refuse.

Dollars. A shop that works in US dollars beside so'm gets further fields: `usd` objects holding, in
cents, the same figures as the so'm ones beside them, and `currency` on an entry or a notice that is in
dollars. These are the only fields with a default, and a route with such a model leaves an unset field
out of the answer (`response_model_exclude_unset`): the answer of a shop without dollars has none of
them, and is byte for byte what it was before dollars existed. No field anywhere is a sum of so'm and
dollars.
"""

from pydantic import BaseModel, ConfigDict


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Overdue(Answer):
    amount: int
    since: str | None
    days: int
    due_today: int


class PaymentHistory(Answer):
    on_time_percent: int
    on_time_amount: int
    due_amount: int
    longest_delay_days: int


class CustomerDollars(Answer):
    """What a customer owes in US dollars: whole cents, beside the so'm figures and never added to them."""

    # Whole cents the customer owes; below zero when they are in credit in dollars.
    balance: int
    # Whole cents; null when the shop's default dollar limit applies.
    credit_limit: int | None
    # In the list of debtors and on the customer's own page: what of the dollar debt is overdue.
    overdue: Overdue | None = None
    # On the customer's own page: the payment history of the dollar debt; null while none has fallen due.
    payment_history: PaymentHistory | None = None


class Customer(Answer):
    id: str
    display_name: str
    phone: str | None
    status: str
    reminders_off: bool
    # Whole UZS; null when the shop's default applies.
    credit_limit: int | None
    # Whole UZS the customer owes. Below zero when the customer is in credit: they have paid that much
    # more than they owe, and the shop holds it as their advance (only in a shop that accepts advances).
    balance: int
    # Only in a shop that works in dollars.
    usd: CustomerDollars | None = None


class CustomerPage(Answer):
    items: list[Customer]
    next_cursor: str | None


class Debtor(Customer):
    overdue: Overdue


class DebtorPage(Answer):
    items: list[Debtor]
    next_cursor: str | None


class EntryLine(Answer):
    line_no: int
    catalog_item_id: str | None
    name: str
    # A decimal as text: "1.5".
    qty: str
    unit: str
    unit_price: int
    line_total: int


class Promise(Answer):
    promised_date: str
    actor: str
    reason: str | None
    created_at: str


class DateRequest(Answer):
    id: str
    entry_id: str
    status: str
    requested_date: str
    reason: str | None
    decline_reason: str | None
    created_at: str
    closed_at: str | None


class Entry(Answer):
    id: str
    seq: int
    kind: str
    amount: int
    note: str | None
    created_at: str
    promised_date: str | None
    reverses_id: str | None
    reversed: bool
    disputed: bool
    author_id: str
    import_id: str | None
    lines: list[EntryLine]
    promises: list[Promise]
    date_request: DateRequest | None
    # "USD" on an entry in dollars, whose amount is then whole cents. Absent: so'm.
    currency: str | None = None


class StaffPaymentNotice(Answer):
    id: str
    status: str
    amount: int
    recorded_amount: int | None
    payment_entry_id: str | None
    has_receipt: bool
    decline_reason: str | None
    created_at: str
    closed_at: str | None
    expires_at: str
    # "USD" on a notice of dollars paid, whose amounts are then whole cents. Absent: so'm.
    currency: str | None = None
    receipt_seen_before: bool


class CustomerDetail(Customer):
    overdue: Overdue
    payment_history: PaymentHistory | None
    entries: list[Entry]
    entries_total: int
    payment_notices: list[StaffPaymentNotice]


class OverviewOverdue(Answer):
    amount: int
    customers: int


class OverviewAdvances(Answer):
    # What the shop holds of customers who have paid more than they owe, and how many they are.
    amount: int
    customers: int


class OverviewFigures(Answer):
    # What the customers who owe add up to. A customer in credit is not in it and takes nothing from it.
    outstanding: int
    debtors: int
    overdue: OverviewOverdue
    due_today: int
    # Only while the shop holds an advance of at least one customer.
    advances: OverviewAdvances | None = None


class Overview(OverviewFigures):
    # Only in a shop that works in dollars: the same figures of the dollar debts, in cents.
    usd: OverviewFigures | None = None


class Shop(Answer):
    id: str
    name: str
    lang: str
    default_promise_days: int
    # Only while the platform offers dollars: whether this shop also works in them.
    usd_on: bool | None = None


class MyShop(Answer):
    shop_id: str
    name: str
    role: str
    membership_id: str


class MyShops(Answer):
    items: list[MyShop]
    active_shop: str | None


class ShareState(Answer):
    """What staff are told about a customer's read-only link. The link itself is never among it."""

    exists: bool
    expired: bool
    created_at: str | None
    expires_at: str | None
    last_opened_at: str | None


class ShareContact(Answer):
    phone: str | None


class SharedOverdue(Answer):
    amount: int
    due_today: int


class SharedLine(Answer):
    name: str
    qty: str
    unit: str
    unit_price: int
    line_total: int


class SharedEntry(Answer):
    kind: str
    amount: int
    created_at: str
    promised_date: str | None
    reversed: bool
    lines: list[SharedLine]
    # "USD" on an entry in dollars, whose amount is then whole cents. Absent: so'm.
    currency: str | None = None


class SharedDollars(Answer):
    """What the customer owes in US dollars, in whole cents: beside the so'm figures, never added to them."""

    balance: int
    overdue: SharedOverdue


class SharedAccount(Answer):
    """The page behind a customer's read-only link. Closed: a field added to the service's answer and not
    declared here fails the request instead of reaching whoever holds the link."""

    shop_name: str
    shop_phone: str | None
    first_name: str
    lang: str
    balance: int
    overdue: SharedOverdue
    # Only for a shop that works in dollars.
    usd: SharedDollars | None = None
    expires_at: str
    entries: list[SharedEntry]
    entries_total: int

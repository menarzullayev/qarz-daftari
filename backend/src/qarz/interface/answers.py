"""Response bodies that the API description names field by field (ADR-012, ADR-013).

A route answers with what its application service returns. Giving it one of these models writes that
shape into `openapi.json`, from which the front end's types are generated, and checks every answer against
it. The models are closed and strict and have no defaults, so nothing is dropped, converted or filled in:
an answer that is not exactly the declared shape fails loudly instead of leaving changed. Fields are in
the order the services write them, which keeps the body the same byte for byte
(tests/api/test_typed_answers.py).

Only reads carry a model. A write may answer with a result stored by an earlier version of the code
(ADR-006), which a model declared today could refuse.
"""

from pydantic import BaseModel, ConfigDict


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Customer(Answer):
    id: str
    display_name: str
    phone: str | None
    status: str
    reminders_off: bool
    # Whole UZS; null when the shop's default applies.
    credit_limit: int | None
    # Whole UZS the customer owes.
    balance: int


class CustomerPage(Answer):
    items: list[Customer]
    next_cursor: str | None


class Overdue(Answer):
    amount: int
    since: str | None
    days: int
    due_today: int


class Debtor(Customer):
    overdue: Overdue


class DebtorPage(Answer):
    items: list[Debtor]
    next_cursor: str | None


class PaymentHistory(Answer):
    on_time_percent: int
    on_time_amount: int
    due_amount: int
    longest_delay_days: int


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


class Overview(Answer):
    outstanding: int
    debtors: int
    overdue: OverviewOverdue
    due_today: int


class Shop(Answer):
    id: str
    name: str
    lang: str
    default_promise_days: int


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


class SharedAccount(Answer):
    """The page behind a customer's read-only link. Closed: a field added to the service's answer and not
    declared here fails the request instead of reaching whoever holds the link."""

    shop_name: str
    shop_phone: str | None
    first_name: str
    lang: str
    balance: int
    overdue: SharedOverdue
    expires_at: str
    entries: list[SharedEntry]
    entries_total: int

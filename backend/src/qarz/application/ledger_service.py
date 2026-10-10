"""Recording credit sales and payments, reversals, customer detail and the shop overview.

Balances, allocation, overdue status and the payment history indicator come from the pure functions in
`qarz.domain.ledger`; this module loads an account, asks the domain whether a new entry may be added, and
stores it. Every write locks the customer row first, so two sales to one customer cannot interleave.

The `*_in` functions do one write inside a tenant transaction that the caller has opened and authorized.
The HTTP API and the chat both use them, each wrapping them in its own idempotent request.
"""

import time
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from qarz.application import cash_feed, idempotency, notify, removal
from qarz.application.authorization import may, require_permission
from qarz.application.credit import LimitReached
from qarz.application.currencies import (
    NOT_IN_DOLLARS,
    USD,
    UZS,
    GoodsNotInDollars,
    amount_hint,
    dollars_on,
    require_currency,
    tag,
)
from qarz.application.customer_address import address_of
from qarz.application.customer_address import switched_on as address_on
from qarz.application.customers import (
    MAX_PAGE,
    CustomerArchived,
    customer_body,
    decode_cursor,
    encode_cursor,
    require_viewable,
    require_writable,
)
from qarz.application.errors import AppError, NotFound, ValidationFailed
from qarz.application.goods_lines import (
    ADD_LINES,
    CleanLine,
    LineRequest,
    add_lines_in,
    clean_lines,
    line_body,
    lines_request,
    lines_sum,
    require_sum,
    store_lines_in,
)
from qarz.application.notice_view import open_notices_of
from qarz.application.operations import operation
from qarz.application.ports import (
    DateRequestRecord,
    DebtFigures,
    EntryRow,
    GoodsLineRecord,
    Membership,
    PromiseRecord,
    ShopTotals,
    Storage,
    TenantSession,
)
from qarz.application.shops import require_member
from qarz.application.stock_moves import before_entry_reversed, draw_for_sale
from qarz.domain import cash, ledger, permissions
from qarz.domain.access import Capability
from qarz.domain.credit import LimitOutcome, check_limit, effective_limit
from qarz.domain.date_requests import (
    PromiseChangeRefusal,
    may_change_promise,
    reason_fits,
    request_is_met,
    tidy_reason,
)
from qarz.domain.ledger import Entry, EntryKind, Refusal
from qarz.domain.money import RULES, Currency, valid_entry_amount
from qarz.domain.promise import PromiseDateError, default_promise_date, tashkent_date, validate_promise_date

RECORD_ENTRY = operation("ledger.entry.create", Capability.RECORD)
REVERSE_ENTRY = operation("ledger.entry.reverse", Capability.MANAGE)
CHOOSE_PROMISE = operation("ledger.entry.promise.choose", Capability.RECORD)
CHANGE_PROMISE = operation("ledger.entry.promise.change", Capability.MANAGE)
READ_CUSTOMER = operation("customers.read", Capability.RECORD)
READ_OVERVIEW = operation("overview.read", Capability.RECORD)
LIST_DEBTORS = operation("overview.debtors", Capability.RECORD)

# The range of one so'm entry, under the names it had before dollars existed (qarz.domain.money).
MIN_AMOUNT = RULES[UZS].min_entry
MAX_AMOUNT = RULES[UZS].max_entry
HISTORY_PAGE = 100
# How long after a sale its author may still pick the promised date with one tap (REQ-008).
PROMISE_CHOICE_WINDOW = timedelta(hours=24)
# Who set a promise row: the shop default, or a person.
DEFAULT_ACTOR = "default"
STAFF_ACTOR = "staff"
# A date the customer asked for and a manager or owner accepted (REQ-067).
CUSTOMER_REQUEST_ACTOR = "customer_request"

_REFUSAL_CODES = {
    Refusal.EXCEEDS_BALANCE: "EXCEEDS_BALANCE",
    Refusal.ALREADY_REVERSED: "ALREADY_REVERSED",
    Refusal.REVERSAL_OF_REVERSAL: "CANNOT_REVERSE_REVERSAL",
    Refusal.NEGATIVE_BALANCE: "WOULD_GO_NEGATIVE",
    Refusal.ADVANCE_TOO_LARGE: "ADVANCE_TOO_LARGE",
}


class Advance(StrEnum):
    """What a writer of a payment wants done when the payment is larger than the debt.

    Whatever it asks, a shop that does not accept advances refuses such a payment as it always did.
    """

    REFUSE = "refuse"  # EXCEEDS_BALANCE, as in a shop that does not accept advances
    ASK = "ask"  # ADVANCE_NOT_CONFIRMED with the two amounts: the author is to be asked first
    ACCEPT = "accept"  # the author was asked, or the act itself is the confirmation


def advance_cap(currency: Currency) -> int:
    """The most one customer may be in credit by in a currency: what one entry of it may be.

    One payment is already bounded by that. The same bound on what stands keeps a second and a third
    mistyped payment from piling up, and keeps every sum of advances as far inside a bigint as every
    sum of debts is.
    """
    return RULES[currency].max_entry


async def close_dispute_on_reversal(session: TenantSession, actor: Membership, entry_id: UUID, now: datetime) -> None:
    """Reversing a disputed entry is the shop agreeing with the customer: the dispute ends with it (BR-12)."""
    record = await session.dispute_of_entry(entry_id)
    if record is not None and record.status == "open":
        await session.close_dispute(
            record.dispute_id, status="reversed", decline_reason=None, decided_by=actor.membership_id, now=now
        )


async def expire_settled_date_requests(
    session: TenantSession, customer_id: UUID, entries: Sequence[Entry], now: datetime
) -> None:
    """An open date request on an entry that is now reversed or fully paid has nothing left to ask.

    `entries` is the account as it stands after the entry just written. A reversed credit frees the
    payments that covered it, so other entries may have become fully paid as well.
    """
    waiting = [r for r in await session.date_requests_of_customer(customer_id) if r.status == "open"]
    if not waiting:
        return
    owed = {
        allocation.entry_id
        for currency in ledger.currencies_of(entries)
        for allocation in ledger.allocate(ledger.in_currency(entries, currency))
        if allocation.remaining > 0
    }
    for request in waiting:
        if request.entry_id not in owed:
            await session.close_date_request(
                request.request_id, status="expired", decline_reason=None, decided_by=None, now=now
            )


async def close_met_date_request(
    session: TenantSession,
    customer_id: UUID,
    entry_id: UUID,
    new_date: date,
    *,
    status: str,
    decided_by: UUID | None,
    now: datetime,
) -> DateRequestRecord | None:
    """Close the entry's open date request when the date just set is the one asked for, or later.

    So an open request always asks for a date after the current one, and accepting it can only move
    the promise forward.
    """
    for request in await session.date_requests_of_customer(customer_id):
        if (
            request.entry_id == entry_id
            and request.status == "open"
            and request_is_met(request.requested_date, new_date)
        ):
            return await session.close_date_request(
                request.request_id, status=status, decline_reason=None, decided_by=decided_by, now=now
            )
    return None


def promise_body(record: PromiseRecord) -> dict[str, Any]:
    return {
        "promised_date": record.promised_date.isoformat(),
        "actor": record.actor,
        "reason": record.reason,
        "created_at": record.created_at.isoformat(),
    }


def date_request_body(record: DateRequestRecord) -> dict[str, Any]:
    return {
        "id": str(record.request_id),
        "entry_id": str(record.entry_id),
        "status": record.status,
        "requested_date": record.requested_date.isoformat(),
        "reason": record.reason,
        "decline_reason": record.decline_reason,
        "created_at": record.created_at.isoformat(),
        "closed_at": None if record.closed_at is None else record.closed_at.isoformat(),
    }


def latest_date_requests(records: Sequence[DateRequestRecord]) -> dict[UUID, DateRequestRecord]:
    """The newest request of each entry, from records given oldest first."""
    return {record.entry_id: record for record in records}


class LedgerRefused(AppError):
    """The domain rules do not allow this entry. The code says which rule."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__()


class AdvanceNotConfirmed(AppError):
    """The payment is larger than the debt, the shop accepts advances, and nobody has confirmed this one.

    Its fields say what the customer owes now and what would be kept as their advance, in the payment's
    currency, so that the question put to the author names real amounts.
    """

    code = "ADVANCE_NOT_CONFIRMED"


class PromiseAlreadySet(AppError):
    """The one-tap choice is open only while the entry still carries the shop default, for a day."""

    code = "PROMISE_ALREADY_SET"


class PromiseNotChangeable(AppError):
    """Only a credit sale or an opening balance that still stands has a promised date to change."""

    code = "PROMISE_NOT_CHANGEABLE"


def _refuse(refusal: Refusal) -> AppError:
    code = _REFUSAL_CODES.get(refusal)
    if code is None:
        # Anything else means the request itself was malformed.
        return ValidationFailed({"entry": refusal.value})
    return LedgerRefused(code)


def balances_of(entries: Sequence[Entry]) -> dict[Currency, int]:
    """The account's balance in every currency: each from its own book, never one from another's."""
    return {currency: ledger.balance(ledger.in_currency(entries, currency)) for currency in Currency}


def account_body(customer: Any, balances: dict[Currency, int], dollars: bool) -> dict[str, Any]:
    """`customer_body` from the balances of an account; the dollar figures only for a shop with dollars."""
    return customer_body(customer, balances[UZS], balances[USD] if dollars else None)


async def require_shown(session: TenantSession, currency: Currency) -> bool:
    """Whether the shop works in dollars; an entry in dollars of a shop that does not is not found.

    Such an entry was recorded while dollars were on. It is kept, and nothing is done to it until they
    are on again.
    """
    dollars = await dollars_on(session, lock=currency is USD)
    if currency is USD and not dollars:
        raise NotFound()
    return dollars


def _entry_body(
    row: EntryRow,
    reversed_ids: set[UUID],
    lines: Sequence[GoodsLineRecord] = (),
    promises: Sequence[PromiseRecord] = (),
    date_request: DateRequestRecord | None = None,
) -> dict[str, Any]:
    entry = row.entry
    return tag(_entry_fields(row, reversed_ids, lines, promises, date_request), entry.currency)


def _entry_fields(
    row: EntryRow,
    reversed_ids: set[UUID],
    lines: Sequence[GoodsLineRecord],
    promises: Sequence[PromiseRecord],
    date_request: DateRequestRecord | None,
) -> dict[str, Any]:
    entry = row.entry
    return {
        "id": str(entry.id),
        "seq": entry.seq,
        "kind": entry.kind.value,
        "amount": entry.amount,
        "note": row.note,
        "created_at": entry.created_at.isoformat(),
        "promised_date": None if entry.promised_date is None else entry.promised_date.isoformat(),
        "reverses_id": None if entry.reverses_id is None else str(entry.reverses_id),
        "reversed": entry.id in reversed_ids,
        "disputed": entry.disputed,
        "author_id": str(row.author_id),
        # Set on an opening balance that came from an import (REQ-063); null otherwise.
        "import_id": None if row.import_batch_id is None else str(row.import_batch_id),
        "lines": [line_body(line) for line in lines],
        # Every promised date the entry has carried, oldest first; the last one is the current date.
        "promises": [promise_body(promise) for promise in promises],
        "date_request": None if date_request is None else date_request_body(date_request),
    }


def _overdue_body(status: ledger.OverdueStatus) -> dict[str, Any]:
    return {
        "amount": status.overdue_amount,
        "since": None
        if status.earliest_unmet_promised_date is None
        else status.earliest_unmet_promised_date.isoformat(),
        "days": status.days_overdue,
        "due_today": status.due_today_amount,
    }


def clean_entry(
    kind: str, amount: int, note: str | None, promised_date: date | None, currency: Currency = UZS
) -> tuple[EntryKind, str | None]:
    """Check the shape of a new entry. Returns its kind and its tidied note.

    `amount` is in the currency's minor unit and inside that currency's range for one entry.
    """
    fields: dict[str, str] = {}
    if kind not in ("credit", "payment"):
        fields["kind"] = "must be credit or payment"
    if not valid_entry_amount(currency, amount):
        fields["amount"] = amount_hint(currency)
    text = " ".join(note.split()) if note else None
    if text is not None and len(text) > 200:
        fields["note"] = "at most 200 characters"
    if kind == "payment" and promised_date is not None:
        fields["promised_date"] = "only a credit sale has a promised date"
    if fields:
        raise ValidationFailed(fields)
    return EntryKind(kind), text


def clean_sale(
    kind: str,
    amount: int | None,
    note: str | None,
    promised_date: date | None,
    lines: Sequence[LineRequest] | None,
    currency: Currency = UZS,
) -> tuple[EntryKind, str | None, int, list[CleanLine] | None]:
    """Check the shape of a new entry that may carry goods lines (REQ-037).

    Returns the kind, the tidied note, the entry total and the checked lines. With lines the total may be
    left out, and is then their sum; a total that is given must equal that sum.

    Goods lines are so'm only: the catalog's prices are so'm, and a line total is rounded to whole so'm
    (BR-7). A sale in dollars is recorded by its amount.
    """
    fields: dict[str, str] = {}
    cleaned: list[CleanLine] | None = None
    if lines is not None and currency is not UZS:
        fields["lines"] = NOT_IN_DOLLARS
        lines = None
        amount = RULES[currency].min_entry if amount is None else amount
    if lines is not None:
        if kind == "credit":
            try:
                cleaned = clean_lines(lines)
            except ValidationFailed as error:
                fields.update(error.fields)
        else:
            fields["lines"] = "only a credit sale has goods lines"
    total = amount
    if total is None and cleaned is not None:
        total = lines_sum(cleaned)
    checked: tuple[EntryKind, str | None] | None = None
    try:
        # Without a total to check (it is missing, or the lines it would come from are wrong) the other
        # fields are still checked, against the smallest valid amount.
        checked = clean_entry(
            kind, RULES[currency].min_entry if total is None else total, note, promised_date, currency
        )
    except ValidationFailed as error:
        fields = {**error.fields, **fields}
    if amount is None and lines is None and "lines" not in fields:
        fields["amount"] = "required when there are no goods lines"
    if fields.get("lines") == NOT_IN_DOLLARS:
        raise GoodsNotInDollars(fields)
    if fields or checked is None or total is None:
        raise ValidationFailed(fields)
    if cleaned is not None:
        require_sum(cleaned, total)
    return checked[0], checked[1], total, cleaned


def require_kind(actor: Membership, kind: EntryKind) -> None:
    """Recording an entry is one operation with two permissions: a credit sale needs one, a payment the other."""
    wanted = permissions.CREDITS_RECORD if kind is EntryKind.CREDIT else permissions.PAYMENTS_RECORD
    require_permission(actor, wanted)


async def append_entry_in(
    session: TenantSession,
    actor: Membership,
    customer_id: UUID,
    *,
    kind: EntryKind,
    amount: int,
    note: str | None,
    promised_date: date | None,
    now: datetime,
    lines: Sequence[CleanLine] | None = None,
    started: float | None = None,
    currency: Currency = UZS,
    method: cash.Method | None = None,
    money_received: bool = True,
    advance: Advance = Advance.REFUSE,
) -> dict[str, Any]:
    """Add a credit sale or a payment to one customer's account, in one currency.

    `advance` is what to do with a payment larger than the debt in a shop that accepts advances (INV-3):
    the excess stays as the customer's advance, a balance below zero in that currency's book, which the
    next credit sales use up. Every path that does not say otherwise refuses it as before.

    `method` is how a payment was made (cash unless it says otherwise). It matters only while the cash
    book is on, where the payment is written as money received (`qarz.application.cash_feed`).

    `lines` are the goods of an itemized credit sale, already checked by `clean_sale`: they sum to `amount`.
    The entry joins the book of its currency: a payment is checked against, and reduces, what is owed in
    that currency only, and a sale is compared with that currency's limit only.
    """
    dollars = await require_shown(session, currency)
    customer = await session.get_customer(customer_id, for_update=True)
    if customer is None or customer.status == "anonymized":
        raise NotFound()
    if customer.status == "archived":
        raise CustomerArchived()

    account = await session.entries_of(customer_id)
    everything = [row.entry for row in account]
    now = ledger.not_before(everything, now)
    book = ledger.in_currency(everything, currency)
    refusal = ledger.validate_new_entry(book, kind, amount)
    # The setting is asked only now, when it decides something, and is held until this transaction ends.
    if (
        refusal is Refusal.EXCEEDS_BALANCE
        and advance is not Advance.REFUSE
        and await session.accepts_advances(lock=True)
    ):
        refusal = ledger.validate_new_entry(book, kind, amount, advance_cap=advance_cap(currency))
        if refusal is None and advance is Advance.ASK:
            owed = ledger.balance(book)
            # What they owe now, how much of this payment is beyond it, and what their advance would
            # then be in all (more than `over` when they are in credit already).
            raise AdvanceNotConfirmed(
                {"debt": str(max(owed, 0)), "over": str(amount - max(owed, 0)), "advance": str(amount - owed)}
            )
    if refusal is not None:
        raise _refuse(refusal)

    balances = balances_of(everything)
    balances[currency] += amount if kind is EntryKind.CREDIT else -amount
    balance = balances[currency]
    limit_warning: dict[str, int] | None = None
    if kind is EntryKind.CREDIT:
        # BR-8: a manager or owner is warned; a seller is warned or stopped, as the shop has chosen.
        # Each currency has a limit of its own, compared with the balance in that currency.
        credit = await session.credit_settings()
        limit = (
            effective_limit(customer.credit_limit, credit.default_limit)
            if currency is UZS
            else effective_limit(customer.credit_limit_usd, credit.default_limit_usd)
        )
        outcome = check_limit(
            limit,
            balance,
            may_manage=may(actor, permissions.ENTRIES_OVER_LIMIT),
            sellers_may_exceed=credit.sellers_may_exceed,
        )
        if outcome is not LimitOutcome.WITHIN and limit is not None:
            limit_warning = {"limit": limit, "balance": balance}
        if outcome is LimitOutcome.REFUSE:
            raise LimitReached({key: str(value) for key, value in (limit_warning or {}).items()})

    promised: date | None = None
    promise_actor = STAFF_ACTOR
    if kind is EntryKind.CREDIT:
        if promised_date is None:
            settings = await session.shop_settings()
            if settings is None:
                raise NotFound()
            promised = default_promise_date(now, settings.default_promise_days)
            promise_actor = DEFAULT_ACTOR
        else:
            problem = validate_promise_date(tashkent_date(now), promised_date)
            if problem is not None:
                raise ValidationFailed({"promised_date": problem.value})
            promised = promised_date

    entry_id = uuid4()
    # One sequence for the account, whatever the currency: the entries of both books keep their order.
    seq = max((row.entry.seq for row in account), default=0) + 1
    await session.append_entry(
        entry_id=entry_id,
        customer_id=customer_id,
        seq=seq,
        kind=kind.value,
        amount=amount,
        note=note,
        reverses_id=None,
        author_id=actor.membership_id,
        created_at=now,
        currency=currency,
    )
    paid_by: cash.Method | None = None
    if kind is EntryKind.PAYMENT and money_received:
        # Money received: while the cash book is on, it is in the book from this same transaction. A
        # payment that a return of goods writes lowers the debt with goods: no money came in.
        paid_by = await cash_feed.payment_recorded_in(
            session, actor, entry_id=entry_id, amount=amount, currency=currency, method=method, now=now
        )
    if promised is not None:
        await session.add_promise(entry_id=entry_id, promised_date=promised, actor=promise_actor, created_at=now)
    stored_lines = await store_lines_in(session, actor, entry_id, lines) if lines else []
    # A line of a counted item takes its quantity out of the stock (nothing while `stock_on` is off).
    stock_warnings = await draw_for_sale(session, actor, entry_id, stored_lines, now=now)
    await session.record_activity(
        membership_id=actor.membership_id,
        action=f"ledger.{kind.value}_recorded",
        subject_type="customer",
        subject_id=customer_id,
    )
    handle_ms = None if started is None else max(0, round((time.perf_counter() - started) * 1000))
    await session.record_measure(
        kind=kind.value, entry_ref=entry_id, amount=amount, promised=promised, handle_ms=handle_ms, currency=currency
    )
    if kind is EntryKind.PAYMENT:
        # METRIC-001: how much of what was lent comes back within the agreed term.
        paid = Entry(id=entry_id, seq=seq, kind=kind, amount=amount, created_at=now, currency=currency)
        in_time, late = ledger.payment_timeliness([*book, paid], entry_id)
        if in_time:
            await session.record_measure(
                kind="repaid_in_time", entry_ref=entry_id, amount=in_time, promised=None, currency=currency
            )
        if late:
            await session.record_measure(
                kind="repaid_late", entry_ref=entry_id, amount=late, promised=None, currency=currency
            )
    written = Entry(
        id=entry_id, seq=seq, kind=kind, amount=amount, created_at=now, promised_date=promised, currency=currency
    )
    await expire_settled_date_requests(session, customer_id, [*everything, written], now)

    body: dict[str, Any] = {
        "entry": tag(
            {
                "id": str(entry_id),
                "seq": seq,
                "kind": kind.value,
                "amount": amount,
                "note": note,
                "created_at": now.isoformat(),
                "promised_date": None if promised is None else promised.isoformat(),
                "lines": [line_body(line) for line in stored_lines],
            },
            currency,
        ),
        "customer": account_body(customer, balances, dollars),
    }
    if limit_warning is not None:
        # For the author only: the customer's message says nothing of limits. Both figures are in the
        # entry's currency.
        body["limit_warning"] = limit_warning
    await notify.entry_recorded(session, customer_id, body)
    if stock_warnings:
        # For the author only, added after the customer's message is made: it says nothing of the stock.
        body["stock_warnings"] = stock_warnings
    await removal.complete_if_due(session, customer_id, any(balances.values()), now)
    if paid_by is not None:
        # For the author only, and only while the cash book is on: which balance of it the money went to.
        body["entry"]["method"] = paid_by.value
    return body


async def reverse_entry_in(
    session: TenantSession, actor: Membership, entry_id: UUID, *, now: datetime
) -> dict[str, Any]:
    """Cancel an entry by adding its reversal. The original stays as it is (REQ-011)."""
    customer_id = await session.customer_of_entry(entry_id)
    if customer_id is None:
        raise NotFound()
    customer = await session.get_customer(customer_id, for_update=True)
    if customer is None:
        raise NotFound()
    account = await session.entries_of(customer_id)
    everything = [row.entry for row in account]
    now = ledger.not_before(everything, now)
    original = next((entry for entry in everything if entry.id == entry_id), None)
    if original is None:
        raise NotFound()
    currency = original.currency
    dollars = await require_shown(session, currency)
    book = ledger.in_currency(everything, currency)
    refusal = ledger.validate_new_entry(book, EntryKind.REVERSAL, original.amount, entry_id)
    if refusal is Refusal.NEGATIVE_BALANCE and await session.accepts_advances(lock=True):
        # A sale that payments already cover is cancelled: in a shop that accepts advances what was
        # paid for it stays as the customer's advance. Elsewhere the later payment is cancelled first.
        refusal = ledger.validate_new_entry(
            book, EntryKind.REVERSAL, original.amount, entry_id, advance_cap=advance_cap(currency)
        )
    if refusal is not None:
        raise _refuse(refusal)

    # What the entry took out of the stock comes back first: if it cannot, the entry is not cancelled.
    await before_entry_reversed(session, actor, entry_id, now=now)

    reversal_id = uuid4()
    seq = max(row.entry.seq for row in account) + 1
    await session.append_entry(
        entry_id=reversal_id,
        customer_id=customer_id,
        seq=seq,
        kind=EntryKind.REVERSAL.value,
        amount=original.amount,
        note=None,
        reverses_id=entry_id,
        author_id=actor.membership_id,
        created_at=now,
        currency=currency,
    )
    if original.kind is EntryKind.PAYMENT:
        # The money is no longer received: its entry in the cash book, if it has one, is cancelled with it.
        await cash_feed.payment_reversed_in(session, actor, entry_id, now)
    await session.record_activity(
        membership_id=actor.membership_id,
        action="ledger.entry_reversed",
        subject_type="customer",
        subject_id=customer_id,
    )
    await session.record_measure(
        kind="reversal", entry_ref=reversal_id, amount=original.amount, promised=None, currency=currency
    )
    await close_dispute_on_reversal(session, actor, entry_id, now)
    reversal = Entry(
        id=reversal_id,
        seq=seq,
        kind=EntryKind.REVERSAL,
        amount=original.amount,
        created_at=now,
        reverses_id=entry_id,
        currency=currency,
    )
    await expire_settled_date_requests(session, customer_id, [*everything, reversal], now)

    debt_increasing = original.kind in (EntryKind.CREDIT, EntryKind.OPENING)
    balances = balances_of(everything)
    balances[currency] += -original.amount if debt_increasing else original.amount
    body = {
        "entry": tag(
            {
                "id": str(reversal_id),
                "seq": seq,
                "kind": "reversal",
                "amount": original.amount,
                "reverses_id": str(entry_id),
                "created_at": now.isoformat(),
            },
            currency,
        ),
        "customer": account_body(customer, balances, dollars),
    }
    await notify.entry_reversed(session, customer_id, body, original.kind.value)
    await removal.complete_if_due(session, customer_id, any(balances.values()), now)
    return body


async def choose_promise_in(
    session: TenantSession, actor: Membership, entry_id: UUID, chosen: date, *, now: datetime
) -> dict[str, Any]:
    """The one-tap promised date after a sale (REQ-008).

    Open to the entry's author, and to managers and owners, while the entry still carries the shop
    default and for a day after the sale. Later changes are a different operation (REQ-067).
    """
    customer_id = await session.customer_of_entry(entry_id)
    if customer_id is None:
        raise NotFound()
    customer = await session.get_customer(customer_id, for_update=True)
    if customer is None:
        raise NotFound()
    account = await session.entries_of(customer_id)
    row = next((candidate for candidate in account if candidate.entry.id == entry_id), None)
    if row is None:
        raise NotFound()
    if row.author_id != actor.membership_id:
        require_permission(actor, permissions.ENTRIES_OTHERS)

    entry = row.entry
    dollars = await require_shown(session, entry.currency)
    reversed_ids = {other.entry.reverses_id for other in account}
    if (
        entry.kind is not EntryKind.CREDIT
        or entry.id in reversed_ids
        or now - entry.created_at > PROMISE_CHOICE_WINDOW
        or await session.promise_actors(entry_id) != [DEFAULT_ACTOR]
    ):
        raise PromiseAlreadySet()
    problem = validate_promise_date(tashkent_date(entry.created_at), chosen)
    if problem is not None:
        raise ValidationFailed({"promised_date": problem.value})

    await session.add_promise(entry_id=entry_id, promised_date=chosen, actor=STAFF_ACTOR, created_at=now)
    # Not a decision on the request: the date it asked for has simply been reached or passed.
    await close_met_date_request(session, customer_id, entry_id, chosen, status="expired", decided_by=None, now=now)
    await session.record_activity(
        membership_id=actor.membership_id,
        action="ledger.promise_chosen",
        subject_type="customer",
        subject_id=customer_id,
    )
    await session.record_measure(
        kind="promise_chosen", entry_ref=entry_id, amount=entry.amount, promised=chosen, currency=entry.currency
    )
    body = {
        "entry": tag(
            {"id": str(entry_id), "amount": entry.amount, "promised_date": chosen.isoformat()}, entry.currency
        ),
        "customer": account_body(customer, balances_of([other.entry for other in account]), dollars),
    }
    await notify.promise_chosen(session, customer_id, body)
    return body


async def change_promise_in(
    session: TenantSession, actor: Membership, entry_id: UUID, chosen: date, reason: str | None, *, now: datetime
) -> dict[str, Any]:
    """A manager or owner moves the promised date of a debt that still stands (REQ-067).

    The date it replaces stays in the promise history (INV-9). An open date request on the entry is
    closed as accepted when the new date is the one asked for, or later; otherwise it stays open.
    """
    customer_id = await session.customer_of_entry(entry_id)
    if customer_id is None:
        raise NotFound()
    customer = await session.get_customer(customer_id, for_update=True)
    if customer is None:
        raise NotFound()
    account = await session.entries_of(customer_id)
    entry = {row.entry.id: row.entry for row in account}[entry_id]
    dollars = await require_shown(session, entry.currency)
    refusal = may_change_promise(
        kind=entry.kind,
        is_reversed=entry_id in {other.entry.reverses_id for other in account},
        sale_date=tashkent_date(entry.created_at),
        current=entry.promised_date,
        chosen=chosen,
    )
    if isinstance(refusal, PromiseDateError):
        raise ValidationFailed({"promised_date": refusal.value})
    if refusal is PromiseChangeRefusal.UNCHANGED:
        raise ValidationFailed({"promised_date": "PROMISE_UNCHANGED"})
    if refusal is not None:
        raise PromiseNotChangeable({"reason": refusal.value})
    previous = entry.promised_date
    assert previous is not None  # a credit or opening entry always has a promised date (INV-9)

    await session.add_promise(entry_id=entry_id, promised_date=chosen, actor=STAFF_ACTOR, created_at=now, reason=reason)
    closed = await close_met_date_request(
        session, customer_id, entry_id, chosen, status="accepted", decided_by=actor.membership_id, now=now
    )
    await session.record_activity(
        membership_id=actor.membership_id,
        action="ledger.promise_changed",
        subject_type="customer",
        subject_id=customer_id,
    )
    await session.record_measure(
        kind="promise_changed", entry_ref=entry_id, amount=entry.amount, promised=chosen, currency=entry.currency
    )
    await notify.promise_changed(
        session,
        customer_id,
        entry_id=entry_id,
        name=customer.display_name,
        amount=entry.amount,
        previous=previous,
        promised=chosen,
        reason=reason,
        at=now,
        currency=entry.currency,
    )
    return {
        "entry": tag(
            {
                "id": str(entry_id),
                "amount": entry.amount,
                "promised_date": chosen.isoformat(),
                "previous_date": previous.isoformat(),
            },
            entry.currency,
        ),
        "customer": account_body(customer, balances_of([other.entry for other in account]), dollars),
        "date_request": None if closed is None else date_request_body(closed),
    }


def payment_history_body(history: ledger.PaymentHistory | None) -> dict[str, int] | None:
    """The payment history indicator (BR-9) as the API gives it; None while nothing has fallen due."""
    if history is None:
        return None
    return {
        "on_time_percent": history.on_time_percent,
        "on_time_amount": history.on_time_amount,
        "due_amount": history.due_amount,
        "longest_delay_days": history.longest_delay_days,
    }


async def customer_detail_in(
    session: TenantSession, customer_id: UUID, today: date, now: datetime, lang: str = "uz"
) -> dict[str, Any]:
    """One customer with balance, history and entries. The caller has already decided who may see them.

    While the platform switch `address_on` is on the customer's address is with them, its places named
    in `lang`; while it is off the body has no `address` at all."""
    customer = await session.get_customer(customer_id, for_update=False)
    if customer is None or customer.status == "anonymized":
        raise NotFound()
    dollars = await dollars_on(session)
    # Without dollars the shop sees its so'm book and nothing else, as it always did.
    account = [row for row in await session.entries_of(customer_id) if dollars or row.entry.currency is UZS]
    entries = ledger.in_currency([row.entry for row in account], UZS)
    in_dollars = ledger.in_currency([row.entry for row in account], USD)
    history = ledger.payment_history(entries, today)
    reversed_ids = {row.entry.reverses_id for row in account if row.entry.reverses_id is not None}
    newest_first = sorted(account, key=lambda row: row.entry.seq, reverse=True)
    shown = newest_first[:HISTORY_PAGE]
    lines = await session.goods_lines_of([row.entry.id for row in shown])
    promises = await session.promises_of([row.entry.id for row in shown])
    requests = latest_date_requests(await session.date_requests_of_customer(customer_id))
    body = customer_body(customer, ledger.balance(entries), ledger.balance(in_dollars) if dollars else None)
    if dollars:
        # The same figures as beside it, from the dollar book alone.
        body["usd"].update(
            overdue=_overdue_body(ledger.overdue(in_dollars, today)),
            payment_history=payment_history_body(ledger.payment_history(in_dollars, today)),
        )
    if await address_on(session):
        body["address"] = await address_of(session, customer_id, lang)
    return {
        **body,
        "overdue": _overdue_body(ledger.overdue(entries, today)),
        # Derived from this shop's records only; shown to its staff and, on their own page, to the
        # customer it is about (REQ-045; DEC-066).
        "payment_history": payment_history_body(history),
        "entries": [
            _entry_body(
                row,
                reversed_ids,
                lines.get(row.entry.id, ()),
                promises.get(row.entry.id, ()),
                requests.get(row.entry.id),
            )
            for row in shown
        ],
        "entries_total": len(account),
        # Payment notices of this customer that wait for a decision (REQ-061).
        "payment_notices": await open_notices_of(session, customer_id, now),
    }


class LedgerService:
    def __init__(self, storage: Storage, now: Callable[[], datetime] | None = None) -> None:
        self._storage = storage
        self._now = now or (lambda: datetime.now(UTC))

    def _today(self) -> date:
        return tashkent_date(self._now())

    async def record(
        self,
        user_id: UUID,
        shop_id: UUID,
        customer_id: UUID,
        *,
        kind: str,
        amount: int | None,
        note: str | None,
        promised_date: date | None,
        request_key: str | None,
        lines: Sequence[LineRequest] | None = None,
        currency: str | None = None,
        method: str | None = None,
        advance: bool = False,
    ) -> dict[str, Any]:
        """Record a credit sale or a payment.

        `advance` is the author's answer to "this is more than the debt: keep the rest as an advance?".
        Without it such a payment is answered with `ADVANCE_NOT_CONFIRMED` and the two amounts in a shop
        that accepts advances, and refused as it always was in one that does not.
        """
        started = time.perf_counter()
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, RECORD_ENTRY)
            key = idempotency.validate_key(request_key)
            money = await require_currency(session, currency)
            entry_kind, text, total, goods = clean_sale(kind, amount, note, promised_date, lines, money)
            paid_by = cash_feed.clean_method(kind, method)
            if advance and entry_kind is not EntryKind.PAYMENT:
                raise ValidationFailed({"advance": "only a payment can be kept as an advance"})
            require_kind(actor, entry_kind)
            await require_writable(session, self._today(), new_credit=entry_kind is EntryKind.CREDIT)

            async def apply() -> dict[str, Any]:
                return await append_entry_in(
                    session,
                    actor,
                    customer_id,
                    kind=entry_kind,
                    amount=total,
                    note=text,
                    promised_date=promised_date,
                    now=self._now(),
                    started=started,
                    lines=goods,
                    currency=money,
                    method=paid_by,
                    advance=Advance.ACCEPT if advance else Advance.ASK,
                )

            # Only an entry in dollars carries its currency, so a so'm request keeps its fingerprint.
            request: dict[str, Any] = tag(
                {
                    "customer": str(customer_id),
                    "kind": kind,
                    "amount": amount,
                    "note": text,
                    "promised_date": promised_date,
                },
                money,
            )
            if goods is not None:
                # Only an itemized sale carries the key, so an amount-only request keeps its fingerprint.
                request["lines"] = lines_request(goods)
            if paid_by is not None:
                # Likewise only a payment that names its method.
                request["method"] = paid_by.value
            if advance:
                # And only a payment confirmed as an advance.
                request["advance"] = True
            return await idempotency.run_once(
                session, key=key, operation=RECORD_ENTRY.name, user_id=user_id, request=request, action=apply
            )

    async def cash_book_on(self) -> bool:
        """Whether the cash book is on: only then does recording a payment take a method."""
        async with self._storage.platform() as session:
            return await session.platform_setting(cash.SWITCH) is True

    async def add_lines(
        self, user_id: UUID, shop_id: UUID, entry_id: UUID, lines: Sequence[LineRequest], request_key: str | None
    ) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, ADD_LINES)
            key = idempotency.validate_key(request_key)
            goods = clean_lines(lines)
            # The sale itself was recorded earlier; completing it is not a new credit sale (BR-29).
            await require_writable(session, self._today(), new_credit=False)

            async def apply() -> dict[str, Any]:
                return await add_lines_in(session, actor, entry_id, goods, now=self._now())

            return await idempotency.run_once(
                session,
                key=key,
                operation=ADD_LINES.name,
                user_id=user_id,
                request={"entry": str(entry_id), "lines": lines_request(goods)},
                action=apply,
            )

    async def reverse(self, user_id: UUID, shop_id: UUID, entry_id: UUID, request_key: str | None) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, REVERSE_ENTRY)
            key = idempotency.validate_key(request_key)
            # A reversal is allowed in limited mode (BR-29); only a suspended shop is refused.
            await require_writable(session, self._today(), new_credit=False)

            async def apply() -> dict[str, Any]:
                return await reverse_entry_in(session, actor, entry_id, now=self._now())

            return await idempotency.run_once(
                session,
                key=key,
                operation=REVERSE_ENTRY.name,
                user_id=user_id,
                request={"entry": str(entry_id)},
                action=apply,
            )

    async def choose_promise(
        self, user_id: UUID, shop_id: UUID, entry_id: UUID, promised_date: date, request_key: str | None
    ) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, CHOOSE_PROMISE)
            key = idempotency.validate_key(request_key)
            await require_writable(session, self._today(), new_credit=False)

            async def apply() -> dict[str, Any]:
                return await choose_promise_in(session, actor, entry_id, promised_date, now=self._now())

            return await idempotency.run_once(
                session,
                key=key,
                operation=CHOOSE_PROMISE.name,
                user_id=user_id,
                request={"entry": str(entry_id), "promised_date": promised_date},
                action=apply,
            )

    async def change_promise(
        self,
        user_id: UUID,
        shop_id: UUID,
        entry_id: UUID,
        promised_date: date,
        reason: str | None,
        request_key: str | None,
    ) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, CHANGE_PROMISE)
            key = idempotency.validate_key(request_key)
            text = tidy_reason(reason)
            if not reason_fits(text):
                raise ValidationFailed({"reason": "at most 300 characters"})
            # Moving a date is not a new credit sale, so it stays possible in limited mode (BR-29).
            await require_writable(session, self._today(), new_credit=False)

            async def apply() -> dict[str, Any]:
                return await change_promise_in(session, actor, entry_id, promised_date, text, now=self._now())

            return await idempotency.run_once(
                session,
                key=key,
                operation=CHANGE_PROMISE.name,
                user_id=user_id,
                request={"entry": str(entry_id), "promised_date": promised_date, "reason": text},
                action=apply,
            )

    async def customer_detail(
        self, user_id: UUID, shop_id: UUID, customer_id: UUID, *, lang: str = "uz"
    ) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, READ_CUSTOMER)
            await require_viewable(session, actor, self._today())
            return await customer_detail_in(session, customer_id, self._today(), self._now(), lang)

    async def overview(self, user_id: UUID, shop_id: UUID) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, READ_OVERVIEW)
            await require_viewable(session, actor, self._today())
            body = _totals_body(await session.shop_totals(self._today()))
            _add_advances(body, await session.advance_totals())
            if await dollars_on(session):
                # What is owed in dollars, counted on its own: a customer who owes both is in both counts.
                body["usd"] = _totals_body(await session.shop_totals(self._today(), USD))
                _add_advances(body["usd"], await session.advance_totals(USD))
            return body

    async def in_credit(
        self, user_id: UUID, shop_id: UUID, *, cursor: str | None, limit: int, currency: str | None = None
    ) -> dict[str, Any]:
        """Who the shop holds an advance of, largest first, in one currency: so'm unless dollars are asked.

        The other side of `debtors`, and never mixed with it: an item's `balance` is below zero by the
        advance. In a shop that works in dollars every item also has the customer's balance in the
        other currency, whichever way that one stands.
        """
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, LIST_DEBTORS)
            await require_viewable(session, actor, self._today())
            if not 1 <= limit <= MAX_PAGE:
                raise ValidationFailed({"limit": f"must be between 1 and {MAX_PAGE}"})
            dollars = await dollars_on(session)
            if currency not in (None, UZS.value) and not (dollars and currency == USD.value):
                raise ValidationFailed({"currency": "must be UZS or USD" if dollars else "must be UZS"})
            listed = USD if currency == USD.value else UZS
            before: tuple[int, UUID] | None = None
            if cursor:
                amount_text, customer_id = decode_cursor(cursor, 2)
                try:
                    before = (int(amount_text), UUID(customer_id))
                except ValueError as error:
                    raise ValidationFailed({"cursor": "not a cursor returned by this API"}) from error
            rows = await session.advances_page(before=before, limit=limit + 1, currency=listed)
            page, more = rows[:limit], len(rows) > limit
            today = self._today()
            owed: dict[UUID, DebtFigures] = {}
            held: dict[UUID, int] = {}
            if dollars and page:
                # The page's customers in the other currency, whichever way each stands there.
                ids, other = [customer.customer_id for customer, _ in page], UZS if listed is USD else USD
                owed, held = await session.debt_figures(ids, today, other), await session.advances_of(ids, other)
            items: list[dict[str, Any]] = []
            for customer, amount in page:
                # Nothing is overdue in the currency the customer is in credit in.
                here = DebtFigures(-amount, 0, None, 0)
                beside = owed.get(customer.customer_id) or DebtFigures(-held.get(customer.customer_id, 0), 0, None, 0)
                in_som, in_usd = (beside, here) if listed is USD else (here, beside)
                item = {
                    **customer_body(customer, in_som.balance, in_usd.balance if dollars else None),
                    "overdue": _figures_overdue(in_som, today),
                }
                if dollars:
                    item["usd"]["overdue"] = _figures_overdue(in_usd, today)
                items.append(item)
            return {
                "items": items,
                "next_cursor": encode_cursor(page[-1][1], page[-1][0].customer_id) if more else None,
            }

    async def debtors(
        self,
        user_id: UUID,
        shop_id: UUID,
        *,
        only_overdue: bool,
        cursor: str | None,
        limit: int,
        currency: str | None = None,
    ) -> dict[str, Any]:
        """Who owes, largest debt first, in one currency: so'm unless the request asks for dollars.

        In a shop that works in dollars every item also has the customer's figures in the other
        currency, so a row shows both balances whichever list it is in.
        """
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, LIST_DEBTORS)
            await require_viewable(session, actor, self._today())
            if not 1 <= limit <= MAX_PAGE:
                raise ValidationFailed({"limit": f"must be between 1 and {MAX_PAGE}"})
            dollars = await dollars_on(session)
            if currency not in (None, UZS.value) and not (dollars and currency == USD.value):
                raise ValidationFailed({"currency": "must be UZS or USD" if dollars else "must be UZS"})
            listed = USD if currency == USD.value else UZS
            before: tuple[int, UUID] | None = None
            if cursor:
                balance_text, customer_id = decode_cursor(cursor, 2)
                try:
                    before = (int(balance_text), UUID(customer_id))
                except ValueError as error:
                    raise ValidationFailed({"cursor": "not a cursor returned by this API"}) from error

            today = self._today()
            rows = await session.debtors_page(
                today=today, only_overdue=only_overdue, before=before, limit=limit + 1, currency=listed
            )
            page, more = rows[:limit], len(rows) > limit
            nothing = DebtFigures(0, 0, None, 0)
            nothing = DebtFigures(0, 0, None, 0)
            other: dict[UUID, DebtFigures] = {}
            if dollars and page:
                # The page's customers in the currency the list is not sorted by.
                other = await session.debt_figures(
                    [customer.customer_id for customer, _ in page], today, UZS if listed is USD else USD
                )
            items: list[dict[str, Any]] = []
            for customer, figures in page:
                beside = other.get(customer.customer_id, nothing)
                in_som, in_usd = (beside, figures) if listed is USD else (figures, beside)
                item = {
                    **customer_body(customer, in_som.balance, in_usd.balance if dollars else None),
                    "overdue": _figures_overdue(in_som, today),
                }
                if dollars:
                    item["usd"]["overdue"] = _figures_overdue(in_usd, today)
                items.append(item)
            return {
                "items": items,
                "next_cursor": encode_cursor(page[-1][1].balance, page[-1][0].customer_id) if more else None,
            }


def _totals_body(totals: ShopTotals) -> dict[str, Any]:
    return {
        "outstanding": totals.outstanding,
        "debtors": totals.debtors,
        "overdue": {"amount": totals.overdue_amount, "customers": totals.overdue_customers},
        "due_today": totals.due_today_amount,
    }


def _add_advances(body: dict[str, Any], totals: tuple[int, int]) -> None:
    """What the shop holds of its customers' money, beside what they owe it and never taken from it.

    Only when there is any, so the overview of a shop that holds no advance is what it always was.
    """
    amount, customers = totals
    if customers:
        body["advances"] = {"amount": amount, "customers": customers}


def _figures_overdue(figures: DebtFigures, today: date) -> dict[str, Any]:
    return {
        "amount": figures.overdue_amount,
        "since": None if figures.overdue_since is None else figures.overdue_since.isoformat(),
        "days": 0 if figures.overdue_since is None else (today - figures.overdue_since).days,
        "due_today": figures.due_today_amount,
    }

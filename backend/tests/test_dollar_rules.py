"""The domain rules of US dollars beside so'm: the ledger's two books, the chat grammar, reminders, notices.

Dollars are held in whole cents. A book is the entries of one currency; nothing is ever calculated across
two. Whatever is read with dollars off is read exactly as before dollars existed.
"""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

from qarz.domain import ledger
from qarz.domain.chat_entry import EntryKind as ChatKind
from qarz.domain.chat_entry import ParsedEntry, ParseError, ParseErrorCode, parse_amount, parse_entry, parse_money
from qarz.domain.ledger import Entry, EntryKind, LedgerIntegrityError, Refusal
from qarz.domain.money import Currency
from qarz.domain.payment_notices import NoticeRefusal, may_send, valid_amount
from qarz.domain.reminders import ReminderKind, plan_automatic, plan_manual

UZS, USD = Currency.UZS, Currency.USD
AT = datetime(2050, 1, 1, tzinfo=UTC)
TODAY = date(2050, 3, 1)


def entry(
    seq: int,
    kind: EntryKind,
    amount: int,
    currency: Currency = UZS,
    *,
    promised: date | None = None,
    reverses: uuid.UUID | None = None,
) -> Entry:
    if kind in (EntryKind.CREDIT, EntryKind.OPENING) and promised is None:
        promised = date(2050, 2, 1)
    return Entry(uuid.uuid4(), seq, kind, amount, AT + timedelta(minutes=seq), reverses, promised, currency=currency)


# --- the ledger: two books in one account ----------------------------------------------------------------


def account() -> list[Entry]:
    """So'm: 50 000 lent, 20 000 paid. Dollars: 120.00 lent, 20.00 paid. One sequence."""
    return [
        entry(1, EntryKind.CREDIT, 50_000),
        entry(2, EntryKind.CREDIT, 12_000, USD),
        entry(3, EntryKind.PAYMENT, 20_000),
        entry(4, EntryKind.PAYMENT, 2_000, USD),
    ]


def test_an_entry_is_som_unless_it_says_otherwise() -> None:
    assert Entry(uuid.uuid4(), 1, EntryKind.PAYMENT, 100, AT).currency is UZS


def test_each_book_has_a_balance_of_its_own() -> None:
    entries = account()
    assert ledger.balance(ledger.in_currency(entries, UZS)) == 30_000
    assert ledger.balance(ledger.in_currency(entries, USD)) == 10_000
    assert ledger.currencies_of(entries) == [UZS, USD]
    assert ledger.currencies_of(ledger.in_currency(entries, USD)) == [USD]
    assert ledger.balance(ledger.in_currency([], USD)) == 0


@pytest.mark.parametrize(
    "calculate",
    [
        ledger.balance,
        ledger.allocate,
        lambda entries: ledger.overdue(entries, TODAY),
        lambda entries: ledger.payment_history(entries, TODAY),
        lambda entries: ledger.validate_new_entry(entries, EntryKind.CREDIT, 1_000),
    ],
)
def test_no_calculation_takes_entries_of_two_currencies(calculate: object) -> None:
    """The guard behind "totals never mix": a caller that forgot to pick a book fails, it does not add."""
    with pytest.raises(LedgerIntegrityError, match="different currencies"):
        calculate(account())  # type: ignore[operator]


def test_a_payment_covers_debt_of_its_own_currency_only() -> None:
    entries = account()
    som = ledger.allocate(ledger.in_currency(entries, UZS))
    dollars = ledger.allocate(ledger.in_currency(entries, USD))
    assert [(a.covered, a.remaining) for a in som] == [(20_000, 30_000)]
    assert [(a.covered, a.remaining) for a in dollars] == [(2_000, 10_000)]
    # A payment larger than the dollar debt is refused though far more is owed in so'm (and the reverse).
    assert ledger.validate_new_entry(ledger.in_currency(entries, USD), EntryKind.PAYMENT, 10_001) is (
        Refusal.EXCEEDS_BALANCE
    )
    assert ledger.validate_new_entry(ledger.in_currency(entries, USD), EntryKind.PAYMENT, 10_000) is None
    assert ledger.validate_new_entry(ledger.in_currency(entries, UZS), EntryKind.PAYMENT, 30_001) is (
        Refusal.EXCEEDS_BALANCE
    )


def test_overdue_and_the_payment_history_are_per_book() -> None:
    entries = [
        entry(1, EntryKind.CREDIT, 50_000, promised=date(2050, 2, 1)),  # overdue on 1 March
        entry(2, EntryKind.CREDIT, 12_000, USD, promised=TODAY),  # due today
        entry(3, EntryKind.CREDIT, 7_000, USD, promised=date(2050, 4, 1)),  # not yet due
    ]
    som = ledger.overdue(ledger.in_currency(entries, UZS), TODAY)
    dollars = ledger.overdue(ledger.in_currency(entries, USD), TODAY)
    assert (som.overdue_amount, som.due_today_amount, som.days_overdue) == (50_000, 0, 28)
    assert (dollars.overdue_amount, dollars.due_today_amount, dollars.is_overdue) == (0, 12_000, False)
    assert ledger.payment_history(ledger.in_currency(entries, USD), TODAY) is None, "no dollar debt has fallen due"
    history = ledger.payment_history(ledger.in_currency(entries, UZS), TODAY)
    assert history is not None and (history.due_amount, history.on_time_amount) == (50_000, 0)


def test_a_reversal_belongs_to_the_book_of_the_entry_it_reverses() -> None:
    entries = account()
    target = entries[1]  # the dollar sale
    reversal = entry(5, EntryKind.REVERSAL, target.amount, USD, reverses=target.id)
    # In its own book it cancels the sale: the 20.00 paid would then stand as an advance (INV-3), a state
    # the ledger reads and does not reject...
    assert ledger.balance(ledger.in_currency([*entries, reversal], USD)) < 0
    # ...and asked properly, in a shop that does not accept advances, it is refused for that; so'm has no say.
    assert ledger.validate_new_entry(
        ledger.in_currency(entries, USD), EntryKind.REVERSAL, target.amount, target.id
    ) is (Refusal.NEGATIVE_BALANCE)
    # In the other book the entry does not exist.
    assert ledger.validate_new_entry(
        ledger.in_currency(entries, UZS), EntryKind.REVERSAL, target.amount, target.id
    ) is (Refusal.REVERSAL_TARGET_NOT_FOUND)


# --- the chat: "$" is dollars only in a shop that works in them --------------------------------------------


def dollars(text: str) -> ParsedEntry | ParseError:
    return parse_entry(text, dollars=True)


@pytest.mark.parametrize(
    ("text", "kind", "cents", "note"),
    [
        ("Ali 50$", ChatKind.CREDIT, 5_000, None),
        ("Ali $50", ChatKind.CREDIT, 5_000, None),
        ("Ali 50 usd", ChatKind.CREDIT, 5_000, None),
        ("Ali 50 USD", ChatKind.CREDIT, 5_000, None),
        ("Ali 50.5$", ChatKind.CREDIT, 5_050, None),
        ("Ali 50,05 $", ChatKind.CREDIT, 5_005, None),
        ("Ali 0.5$", ChatKind.CREDIT, 50, None),
        ("Ali 1 250$", ChatKind.CREDIT, 125_000, None),
        ("Ali 1 250.50 dollar", ChatKind.CREDIT, 125_050, None),
        ("Ali 1,250.50$", ChatKind.CREDIT, 125_050, None),
        ("Ali 1.250,50$", ChatKind.CREDIT, 125_050, None),
        ("Али 50 долларов", ChatKind.CREDIT, 5_000, None),
        ("Ali 50$ non uchun", ChatKind.CREDIT, 5_000, "non uchun"),
        ("Ali 5 k usd", ChatKind.CREDIT, 500_000, None),
        ("Ali -20$", ChatKind.PAYMENT, 2_000, None),
        ("Ali -$20", ChatKind.PAYMENT, 2_000, None),
        ("Ali 20$ berdi", ChatKind.PAYMENT, 2_000, None),
        ("Ali 20 usd to'ladi", ChatKind.PAYMENT, 2_000, None),
        ("Ali 10000$", ChatKind.CREDIT, 1_000_000, None),  # the cap itself
        ("Ali 0.01$", ChatKind.CREDIT, 1, None),  # the smallest
    ],
)
def test_an_amount_with_a_dollar_mark_is_dollars_in_cents(
    text: str, kind: ChatKind, cents: int, note: str | None
) -> None:
    parsed = dollars(text)
    assert isinstance(parsed, ParsedEntry), parsed
    assert (parsed.kind, parsed.amount, parsed.currency, parsed.note) == (kind, cents, USD, note)


@pytest.mark.parametrize("text", ["Ali 45000", "Ali 45 000 so'm", "Ali 45k", "Ali -20000", "Ali 20000 berdi"])
def test_an_amount_without_a_dollar_mark_stays_som_in_a_shop_with_dollars(text: str) -> None:
    parsed, before = dollars(text), parse_entry(text)
    assert isinstance(parsed, ParsedEntry) and parsed.currency is UZS
    assert parsed == before, "the mark decides, never the size of the number or the shop's setting"


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("Ali 1.250$", ParseErrorCode.AMBIGUOUS),  # 1 250 dollars, or one and a quarter?
        ("Ali 1,250$", ParseErrorCode.AMBIGUOUS),
        ("Ali 50.123$", ParseErrorCode.AMBIGUOUS),
        ("Ali 50$ so'm", ParseErrorCode.AMBIGUOUS),  # which currency?
        ("Ali 50 usd so'm", ParseErrorCode.AMBIGUOUS),
        ("Ali 50$ kecha berdi", ParseErrorCode.AMBIGUOUS),  # a debt, or a payment?
        ("Ali 1.2.3$", ParseErrorCode.AMBIGUOUS),
        ("Ali 50.1234$", ParseErrorCode.AMOUNT_TOO_PRECISE),
        ("Ali 0$", ParseErrorCode.AMOUNT_TOO_SMALL),
        ("Ali 0.001$", ParseErrorCode.AMBIGUOUS),
        ("Ali 10000.01$", ParseErrorCode.AMOUNT_TOO_LARGE),
        ("Ali 20000$", ParseErrorCode.AMOUNT_TOO_LARGE),
        ("Ali 45000 dollar", ParseErrorCode.AMOUNT_TOO_LARGE),  # never quietly booked as 45 000 so'm
    ],
)
def test_a_dollar_amount_that_cannot_be_read_one_way_is_asked_about_never_guessed(
    text: str, code: ParseErrorCode
) -> None:
    result = dollars(text)
    assert isinstance(result, ParseError), result
    assert (result.code, result.currency) == (code, USD)


@pytest.mark.parametrize(
    "text",
    [
        "Ali 50$",
        "Ali $50",
        "Ali 50 usd",
        "Ali 50.5$",
        "Ali -20$",
        "Ali 20$ berdi",
        "Ali 1 250.50 dollar",
        "Ali 5 k usd",
        "Ali 50 $ non",
    ],
)
def test_without_dollars_a_dollar_mark_is_not_a_currency(text: str) -> None:
    """The shop does not work in dollars: nothing is ever recorded in dollars, whatever is typed."""
    result = parse_entry(text)
    assert not (isinstance(result, ParsedEntry) and result.currency is USD)
    assert parse_entry(text, dollars=False) == result


def test_without_dollars_these_messages_mean_what_they_always_meant() -> None:
    # Not understood at all: "$" is no part of an amount.
    for text in ("Ali 50$", "Ali $50", "Ali -20$", "Ali 20$ berdi"):
        refused = parse_entry(text)
        assert isinstance(refused, ParseError) and refused.code is ParseErrorCode.NO_AMOUNT, text
    # "usd" is a word like any other: here it is the note of a so'm entry, or the amount is too small.
    noted = parse_entry("Ali 5 k usd")
    assert isinstance(noted, ParsedEntry) and (noted.amount, noted.currency, noted.note) == (5_000, UZS, "usd")
    small = parse_entry("Ali 50 usd")
    assert isinstance(small, ParseError) and small.code is ParseErrorCode.AMOUNT_TOO_SMALL
    whole = parse_entry("Ali 50.5$")
    assert isinstance(whole, ParseError) and whole.code is ParseErrorCode.AMOUNT_NOT_WHOLE


def test_an_amount_alone_is_read_the_same_way() -> None:
    assert parse_money("50$", dollars=True) == (USD, 5_000)
    assert parse_money("$50.25", dollars=True) == (USD, 5_025)
    assert parse_money("1 250.50 usd", dollars=True) == (USD, 125_050)
    assert parse_money("50000", dollars=True) == (UZS, 50_000)
    assert parse_money("50000") == (UZS, 50_000) == (UZS, parse_amount("50000"))
    # A payment word, a sign or a note make it something other than an amount.
    for text in ("5$ berdi", "-5$", "5$ non"):
        refused = parse_money(text, dollars=True)
        assert isinstance(refused, ParseError), text
    # Without dollars it is `parse_amount`, error for error.
    for text in ("50$", "$50", "50.5 usd", "50", "abc", ""):
        assert parse_money(text) == parse_amount(text), text
        assert isinstance(parse_money(text), ParseError), text


# --- reminders: one per customer, stating each currency ----------------------------------------------------


def status(overdue: int = 0, due_today: int = 0) -> ledger.OverdueStatus:
    return ledger.OverdueStatus(overdue, None, 0, due_today, overdue, due_today)


def test_a_reminder_states_both_amounts_and_one_ground_is_enough() -> None:
    plan = plan_manual(status(overdue=50_000), status(due_today=1_200))
    assert plan is not None
    assert (plan.kind, plan.amount, plan.amount_usd) == (ReminderKind.OVERDUE, 50_000, 1_200)
    only_dollars = plan_manual(status(), status(overdue=1_200))
    assert only_dollars is not None
    assert (only_dollars.kind, only_dollars.amount, only_dollars.amount_usd) == (ReminderKind.OVERDUE, 0, 1_200)
    due = plan_automatic(status(), None, TODAY, status(due_today=700))
    assert due is not None and (due.kind, due.amount, due.amount_usd) == (ReminderKind.DUE_TODAY, 0, 700)


def test_nothing_due_in_either_currency_is_no_reminder() -> None:
    assert plan_manual(status(), status()) is None
    assert plan_automatic(status(), None, TODAY, status()) is None


def test_the_limits_are_per_customer_not_per_currency() -> None:
    """INV-14: an overdue dollar debt does not buy a second reminder in the same week or on the same day."""
    yesterday = TODAY - timedelta(days=1)
    assert plan_automatic(status(overdue=50_000), yesterday, TODAY, status(overdue=1_200)) is None
    assert plan_automatic(status(), yesterday, TODAY, status(overdue=1_200)) is None
    assert plan_automatic(status(), TODAY, TODAY, status(due_today=1_200)) is None
    assert plan_automatic(status(), TODAY - timedelta(days=7), TODAY, status(overdue=1_200)) is not None


def test_without_dollars_a_plan_is_what_it_was() -> None:
    plan = plan_manual(status(overdue=50_000, due_today=1_000))
    assert plan is not None and (plan.amount, plan.amount_usd) == (51_000, 0)
    assert plan == plan_manual(status(overdue=50_000, due_today=1_000), None)


# --- payment notices ---------------------------------------------------------------------------------------


def test_a_notice_is_compared_with_the_debt_of_its_own_currency() -> None:
    assert may_send(amount=5_000, balance=5_000, open_notices=0, currency=USD) is None
    assert may_send(amount=5_001, balance=5_000, open_notices=0, currency=USD) is NoticeRefusal.EXCEEDS_BALANCE
    # 50 cents is a dollar amount and is not a so'm amount; 1 000 001 cents is above the dollar cap.
    assert valid_amount(50, USD) and not valid_amount(50)
    assert may_send(amount=1_000_001, balance=10**9, open_notices=0, currency=USD) is (
        NoticeRefusal.AMOUNT_OUT_OF_RANGE
    )
    assert may_send(amount=50, balance=10**9, open_notices=0) is NoticeRefusal.AMOUNT_OUT_OF_RANGE

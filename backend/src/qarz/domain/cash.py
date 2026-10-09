"""Pure rules of the cash book (expansion module H; BR-44 to BR-51).

A shop's cash book is every amount of money that came in or went out: which way it was paid, in which
currency, under which category, on which Tashkent day. Nothing here reads a clock or a database.

What the rules fix:

- an entry is income or expense, paid in cash, by card or by transfer, in one currency, with an amount in
  that currency's minor unit and inside its range (`qarz.domain.money`), under a category of its own
  direction, on a day that is not in the future and not older than `BACKDATE_DAYS`;
- an entry is never changed: a wrong one is cancelled with a reason and stays in the book. A cancelled
  entry counts in no total and no balance;
- a balance is kept per method and per currency, and is what came in minus what went out among the
  entries that stand. Amounts of two currencies are never added; neither are the balances of two methods
  unless a reader asks for the total of one currency;
- a balance may be below zero. The book records what was written into it: a shop that starts its book
  with money already in the till, or forgets an income, is not stopped from writing an expense;
- a customer's payment in the ledger is income of the category `DEBT_REPAID`, written by the ledger in
  the same transaction and cancelled by reversing that payment. Nobody writes to that category by hand
  and nobody cancels such an entry by hand: the ledger is the one source of both.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum

from qarz.domain.money import Currency
from qarz.domain.names import normalize_name, unify_apostrophes

# The platform setting that turns the cash book on (off by default).
SWITCH = "cash_book_on"

MAX_NOTE = 200
# A reason for cancelling an entry: like every reason the service takes (a dispute's, a decline's).
MIN_REASON = 3
MAX_REASON = 300
MAX_CATEGORY_NAME = 60
# Categories one shop may have, of both directions together, archived ones included.
MAX_CATEGORIES = 100
# How far back an entry may be dated: a month's book can still be completed, an old one cannot be rewritten.
BACKDATE_DAYS = 31
# The longest period of one summary, like the reports' (qarz.domain.reports.MAX_PERIOD_DAYS).
MAX_PERIOD_DAYS = 366
PAGE = 50
MAX_PAGE = 100
# The most entries one export of a period holds. It is written while the member waits, by the service
# that answers everyone else, so its size is bounded; a busier period is exported in shorter stretches.
MAX_EXPORT_ENTRIES = 50_000

# The category a customer's payment lands in, whatever the shop has renamed it to.
DEBT_REPAID = "debt_repaid"
# The stock's own two (expansion module I), made for a shop the first time the stock pays money out:
# what is paid for goods, to a supplier or on a purchase for cash; and what is handed back to a
# customer for goods returned. Their totals are then exactly what the stock's documents say.
GOODS_PURCHASE = "goods_purchase"
CUSTOMER_REFUND = "customer_refund"
# Each is named, in the shop's language when it is made, by the text `cash_category_<system key>` of
# `qarz.application.export_texts`.
STOCK_CATEGORIES: tuple[str, ...] = (GOODS_PURCHASE, CUSTOMER_REFUND)
# The categories only the service writes under: a person records nothing there by hand.
_WRITTEN_BY_THE_SERVICE = frozenset({DEBT_REPAID, GOODS_PURCHASE, CUSTOMER_REFUND})


class Direction(StrEnum):
    INCOME = "income"
    EXPENSE = "expense"


class Method(StrEnum):
    CASH = "cash"
    CARD = "card"
    TRANSFER = "transfer"


# What a payment recorded without a method is taken to be (BR-49).
DEFAULT_METHOD = Method.CASH


class DayProblem(StrEnum):
    """Why the day of an entry is refused. The values are what the API reports for the field."""

    IN_FUTURE = "IN_FUTURE"
    TOO_OLD = "TOO_OLD"


@dataclass(frozen=True, slots=True)
class DefaultCategory:
    direction: Direction
    key: str  # its name is the text `cash_category_<key>` of `qarz.application.export_texts`
    system_key: str | None = None


# Created for a shop the first time its cash book is used, named in the shop's language at that moment
# (`qarz.application.cash_feed.default_names`). A name is then the shop's own data: the shop renames,
# archives and adds to them as it likes, and nothing renames them when the shop's language changes. Only
# the one with a system key is the service's own.
DEFAULT_CATEGORIES: tuple[DefaultCategory, ...] = (
    DefaultCategory(Direction.INCOME, "sales"),
    DefaultCategory(Direction.INCOME, "debt_repaid", DEBT_REPAID),
    DefaultCategory(Direction.INCOME, "opening_balance"),
    DefaultCategory(Direction.INCOME, "other_income"),
    DefaultCategory(Direction.EXPENSE, "purchase"),
    DefaultCategory(Direction.EXPENSE, "rent"),
    DefaultCategory(Direction.EXPENSE, "wages"),
    DefaultCategory(Direction.EXPENSE, "transport"),
    DefaultCategory(Direction.EXPENSE, "utilities"),
    DefaultCategory(Direction.EXPENSE, "other_expense"),
)


def parse_direction(value: object) -> Direction | None:
    try:
        return Direction(value) if isinstance(value, str) else None
    except ValueError:
        return None


def parse_method(value: object) -> Method | None:
    try:
        return Method(value) if isinstance(value, str) else None
    except ValueError:
        return None


def tidy(text: str | None) -> str | None:
    """A note or a reason as it is stored: trimmed, single spaces; None when nothing is left."""
    if text is None:
        return None
    return " ".join(text.split()) or None


def category_name(raw: str) -> tuple[str, str]:
    """A category's name as it is shown, and its matching form. Raises ValueError when unusable.

    Two names that differ only in case, in the kind of apostrophe or in the alphabet are the same name:
    a shop cannot have "Ijara" and "ijara", or "Ijara" and "Ижара".
    """
    name = " ".join(raw.split())
    if not 1 <= len(name) <= MAX_CATEGORY_NAME:
        raise ValueError(f"length must be between 1 and {MAX_CATEGORY_NAME}")
    norm = normalize_name(name)
    if not norm or not any(ch.isalnum() for ch in norm):
        raise ValueError("must contain a letter or a digit")
    # Transliteration can lengthen a name (ш -> sh); the matching form is cut to what is stored.
    return name, norm[:MAX_CATEGORY_NAME]


def day_problem(day: date, today: date) -> DayProblem | None:
    """Whether an entry may be dated `day`. `today` is the Tashkent calendar date."""
    if day > today:
        return DayProblem.IN_FUTURE
    if day < today - timedelta(days=BACKDATE_DAYS):
        return DayProblem.TOO_OLD
    return None


def may_write_by_hand(system_key: str | None) -> bool:
    """Whether a person may record an entry under the category. Not under the ledger's own (BR-48),
    nor under the stock's."""
    return system_key not in _WRITTEN_BY_THE_SERVICE


_METHOD_WORDS: dict[str, Method] = {
    "naqd": Method.CASH,
    "naqd pul": Method.CASH,
    "нал": Method.CASH,
    "наличные": Method.CASH,
    "наличными": Method.CASH,
    "нақд": Method.CASH,
    "karta": Method.CARD,
    "kartaga": Method.CARD,
    "kartadan": Method.CARD,
    "karta orqali": Method.CARD,
    "карта": Method.CARD,
    "картой": Method.CARD,
    "на карту": Method.CARD,
    "по карте": Method.CARD,
    "plastik": Method.CARD,
    "пластик": Method.CARD,
    "o'tkazma": Method.TRANSFER,
    "otkazma": Method.TRANSFER,
    "perechisleniye": Method.TRANSFER,
    "перевод": Method.TRANSFER,
    "переводом": Method.TRANSFER,
    "перечисление": Method.TRANSFER,
    "ўтказма": Method.TRANSFER,
}


def method_from_note(note: str | None) -> Method | None:
    """The method a payment's note names, when the note is exactly one of the known words (BR-49).

    "Ali -45000 karta" is a payment by card. A note that says anything more ("karta emas", "kartasi
    yo'q edi") names no method: guessing one would put money into the wrong balance.
    """
    if note is None:
        return None
    return _METHOD_WORDS.get(unify_apostrophes(" ".join(note.split())).lower())


@dataclass(frozen=True, slots=True)
class Sum:
    """What the entries that stand add up to for one method, currency and direction."""

    method: Method
    currency: Currency
    direction: Direction
    amount: int
    count: int = 0


@dataclass(frozen=True, slots=True)
class Line:
    """One method of one currency over a stretch of days: `opening + income - expense = closing`."""

    currency: Currency
    method: Method
    opening: int
    income: int
    expense: int
    count: int

    @property
    def closing(self) -> int:
        return self.opening + self.income - self.expense


def signed(direction: Direction, amount: int) -> int:
    return amount if direction is Direction.INCOME else -amount


def book(before: Iterable[Sum], during: Iterable[Sum], currencies: Iterable[Currency]) -> list[Line]:
    """The balances of a stretch of days, one line for each method of each currency the shop works in.

    `before` are the sums of every standing entry dated before the stretch, `during` those dated in it.
    A currency the shop does not work in now is shown only when the book holds something in it: money
    written in dollars does not vanish from the book when dollars are turned off.
    """
    opening: dict[tuple[Currency, Method], int] = {}
    income: dict[tuple[Currency, Method], int] = {}
    expense: dict[tuple[Currency, Method], int] = {}
    count: dict[tuple[Currency, Method], int] = {}
    for item in before:
        key = (item.currency, item.method)
        opening[key] = opening.get(key, 0) + signed(item.direction, item.amount)
    for item in during:
        key = (item.currency, item.method)
        target = income if item.direction is Direction.INCOME else expense
        target[key] = target.get(key, 0) + item.amount
        count[key] = count.get(key, 0) + item.count
    shown = set(currencies) | {currency for currency, _ in (*opening, *income, *expense)}
    return [
        Line(
            currency=currency,
            method=method,
            opening=opening.get((currency, method), 0),
            income=income.get((currency, method), 0),
            expense=expense.get((currency, method), 0),
            count=count.get((currency, method), 0),
        )
        for currency in Currency
        if currency in shown
        for method in Method
    ]


def totals_by_currency(lines: Iterable[Line]) -> dict[Currency, Line]:
    """The lines of each currency added over its methods. One line per currency: never one of them all."""
    summed: dict[Currency, Line] = {}
    for line in lines:
        held = summed.get(line.currency)
        summed[line.currency] = (
            line
            if held is None
            else Line(
                currency=line.currency,
                method=held.method,
                opening=held.opening + line.opening,
                income=held.income + line.income,
                expense=held.expense + line.expense,
                count=held.count + line.count,
            )
        )
    return summed

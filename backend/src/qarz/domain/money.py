"""Currencies and amounts of money: the one place that knows what a currency is.

The service keeps debts in Uzbek so'm and, for the shops that work in them, in US dollars. The two are
separate books. There is no exchange rate anywhere: a dollar amount is never turned into so'm or the
other way round, and no figure is ever the sum of amounts in different currencies.

The contract every module builds on (the ledger today; the cash book, the stock and the suppliers next):

- **An amount is a whole number of the currency's minor unit**, held in a Python `int` and a PostgreSQL
  `bigint`. So'm has no minor unit (exponent 0): 45 000 so'm is `45000`. A dollar has a hundred cents
  (exponent 2): 1 250.50 $ is `125050`. Nothing is a float or a decimal; nothing is ever rounded, because
  nothing is ever divided or converted.
- **A currency travels with every amount.** A stored row has a `currency` column (`'UZS'` by default, so
  rows written before dollars existed are so'm); an amount in the API has a `currency` field beside it,
  left out when it is so'm. A function that takes amounts of one currency takes the currency too.
- **Amounts of different currencies are never added, compared or netted.** `Money` refuses with
  `CurrencyMismatch`; `totals` gives one sum per currency. A reader that shows several currencies shows
  them side by side.
- **The rules of a currency are data** (`RULES`): its minor units, the smallest and largest amount of one
  entry, the range of a credit limit, and how it is written. A new currency is a new row there, a value
  in the `currency` check constraints of the database, and the texts that name it.
- **Reading and writing**: `to_minor` reads what a person typed into a form ("1250.5" -> 125050) and
  refuses what it cannot represent exactly; `plain` writes it back for a form or a spreadsheet;
  `format_money` writes it for a person ("45 000 so'm", "1 250.50 $").

Which currencies a shop may use is not decided here: so'm always, dollars when the platform switch
`usd_on` and the shop's own setting are both on (`qarz.application.currencies`).

Per-entry limits. So'm: 100 to 100 000 000 (as before dollars existed). Dollars: 0.01 $ to 10 000.00 $,
that is 1 to 1 000 000 cents.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

# Between groups of thousands and before the unit: a no-break space, so an amount never wraps.
NBSP = " "


class Currency(StrEnum):
    UZS = "UZS"
    USD = "USD"


DEFAULT = Currency.UZS


@dataclass(frozen=True, slots=True)
class CurrencyRules:
    """What is fixed about one currency. Every amount here is in its minor unit."""

    code: Currency
    exponent: int  # digits of the minor unit: 0 for so'm, 2 for dollars
    min_entry: int  # the smallest amount of one ledger entry
    max_entry: int  # the largest amount of one ledger entry
    min_limit: int  # the smallest credit limit that may be set
    max_limit: int  # the largest
    units: dict[str, str]  # the word or sign written after an amount, by language

    @property
    def scale(self) -> int:
        """How many minor units make one whole unit: 1 for so'm, 100 for dollars."""
        return int(10**self.exponent)


RULES: dict[Currency, CurrencyRules] = {
    Currency.UZS: CurrencyRules(
        code=Currency.UZS,
        exponent=0,
        min_entry=100,
        max_entry=100_000_000,
        min_limit=1_000,
        max_limit=10_000_000_000,
        units={"uz": "so'm", "ru": "сум"},
    ),
    Currency.USD: CurrencyRules(
        code=Currency.USD,
        exponent=2,
        min_entry=1,  # 0.01 $
        max_entry=1_000_000,  # 10 000.00 $
        min_limit=100,  # 1.00 $
        max_limit=100_000_000,  # 1 000 000.00 $
        units={"uz": "$", "ru": "$"},
    ),
}


class CurrencyMismatch(ValueError):
    """Amounts of two currencies met in one calculation. A programming error, never a business refusal."""


def rules(currency: Currency) -> CurrencyRules:
    return RULES[currency]


def parse_code(value: object) -> Currency | None:
    """The currency a stored or sent code names: exactly "UZS" or "USD". Anything else is None."""
    if not isinstance(value, str):
        return None
    try:
        return Currency(value)
    except ValueError:
        return None


def _is_whole(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def valid_entry_amount(currency: Currency, amount: object) -> bool:
    """Whether one ledger entry may carry this amount: a whole number of minor units inside the range."""
    limits = RULES[currency]
    return _is_whole(amount) and limits.min_entry <= amount <= limits.max_entry  # type: ignore[operator]


def valid_limit(currency: Currency, amount: object) -> bool:
    """Whether a credit limit may be set to this amount."""
    limits = RULES[currency]
    return _is_whole(amount) and limits.min_limit <= amount <= limits.max_limit  # type: ignore[operator]


def to_minor(currency: Currency, text: str) -> int | None:
    """What a person typed into an amount field, in minor units; None when it is not exactly an amount.

    Digits, with spaces between groups allowed, and for a currency with a minor unit at most that many
    decimals after one "." or ",": "1250.5" and "1 250,50" are 125050 cents. So'm takes no decimals at
    all. A sign, a third decimal, letters or an empty text give None: nothing is rounded or guessed.
    """
    compact = "".join(text.split())
    whole, mark, fraction = compact.replace(",", ".").partition(".")
    exponent = RULES[currency].exponent
    if not whole or not whole.isascii() or not whole.isdigit():
        return None
    if mark and (not fraction or not fraction.isascii() or not fraction.isdigit() or len(fraction) > exponent):
        return None
    return int(whole) * RULES[currency].scale + (int(fraction.ljust(exponent, "0")) if fraction else 0)


def plain(currency: Currency, amount: int) -> str:
    """The amount as a form or a spreadsheet cell takes it: "45000", "1250.50". No grouping, no unit."""
    limits = RULES[currency]
    if limits.exponent == 0:
        return str(amount)
    sign = "-" if amount < 0 else ""
    whole, minor = divmod(abs(amount), limits.scale)
    return f"{sign}{whole}.{minor:0{limits.exponent}d}"


def format_amount(currency: Currency, amount: int) -> str:
    """The amount with its thousands grouped: "45 000", "1 250.50". Dollars always show both decimals."""
    limits = RULES[currency]
    sign = "-" if amount < 0 else ""
    whole, minor = divmod(abs(amount), limits.scale)
    grouped = f"{whole:,}".replace(",", NBSP)
    return f"{sign}{grouped}" if limits.exponent == 0 else f"{sign}{grouped}.{minor:0{limits.exponent}d}"


def format_money(currency: Currency, amount: int, lang: str) -> str:
    """The amount as a person reads it: "45 000 so'm", "45 000 сум", "1 250.50 $".

    An unknown language is written as Uzbek, like every other text of the service.
    """
    limits = RULES[currency]
    return f"{format_amount(currency, amount)}{NBSP}{limits.units.get(lang, limits.units['uz'])}"


@dataclass(frozen=True, slots=True)
class Money:
    """An amount with its currency. Adding or subtracting another currency raises `CurrencyMismatch`."""

    amount: int
    currency: Currency = DEFAULT

    def _same(self, other: "Money") -> None:
        if other.currency is not self.currency:
            raise CurrencyMismatch(f"{self.currency} and {other.currency} cannot be combined")

    def __add__(self, other: "Money") -> "Money":
        self._same(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: "Money") -> "Money":
        self._same(other)
        return Money(self.amount - other.amount, self.currency)

    def format(self, lang: str) -> str:
        return format_money(self.currency, self.amount, lang)


def totals(amounts: Iterable[Money]) -> dict[Currency, int]:
    """One sum for each currency that occurs, in the order of `Currency`. Never one sum of them all."""
    sums: dict[Currency, int] = {}
    for money in amounts:
        sums[money.currency] = sums.get(money.currency, 0) + money.amount
    return {currency: sums[currency] for currency in Currency if currency in sums}

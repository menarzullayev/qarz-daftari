"""Which currencies a shop works in, and what every reader and writer of money shares about them.

So'm always. Dollars when two things are on: the platform switch `usd_on` (the administrator's, off by
default) and the shop's own setting (the owner's, off by default). With either off the shop behaves as
it did before dollars existed: no answer carries a dollar figure, no request may name dollars, and the
bot does not read "$" as a currency. Amounts already recorded in dollars are kept and are shown again
when both are on; the rules that protect a debt (INV-13: no archiving, no removal while something is
owed) count them all the same.

How an answer carries dollars, so that an answer of a shop without them keeps its shape exactly:

- a so'm amount is written as it always was, with no currency beside it;
- an entry, a notice or a request in dollars has `"currency": "USD"` beside its amount (`tag`);
- where an answer has figures per customer or per shop (a balance, what is overdue, a total), the
  dollar figures of the same names are in a `usd` object beside the so'm ones, present only while the
  shop works in dollars. Nothing in any answer is a sum of the two.

The rules of the currencies themselves are in `qarz.domain.money`.
"""

from collections.abc import Mapping
from typing import Any, Protocol

from qarz.application.errors import AppError, ValidationFailed
from qarz.domain import platform_settings
from qarz.domain.money import RULES, Currency, parse_code

UZS = Currency.UZS
USD = Currency.USD

# What a part of the service answers when it does not work in dollars yet. A validation error on the
# field that asked for it, so that nothing is ever half done in the wrong currency.
NOT_IN_DOLLARS = "not available in dollars yet"


class DollarBalanceOpen(AppError):
    """Dollars cannot be turned off for a shop while any customer still owes dollars."""

    code = "USD_BALANCE_OPEN"


class DollarSupplierBalanceOpen(AppError):
    """Nor while the shop's account with any supplier is open in dollars, either way."""

    code = "USD_SUPPLIER_BALANCE_OPEN"


class DollarStockOpen(AppError):
    """Nor while goods bought for dollars are still on hand: their cost is kept in dollars."""

    code = "USD_STOCK_OPEN"


class _Settings(Protocol):
    async def platform_setting(self, key: str) -> Any | None: ...


class _Shop(_Settings, Protocol):
    async def dollars_setting(self, *, lock: bool = False) -> bool: ...


async def platform_dollars(session: _Settings) -> bool:
    """Whether the platform switch `usd_on` is on."""
    return platform_settings.effective("usd_on", await session.platform_setting("usd_on")) is True


async def dollars_on(session: _Shop, *, lock: bool = False) -> bool:
    """Whether this shop works in dollars now: the platform switch and the shop's own setting.

    A writer of a dollar amount passes `lock`, which holds the setting until its transaction ends.
    """
    return await platform_dollars(session) and await session.dollars_setting(lock=lock)


async def shop_currencies(session: _Shop) -> tuple[Currency, ...]:
    """The currencies this shop works in, so'm first."""
    return (UZS, USD) if await dollars_on(session) else (UZS,)


async def require_currency(session: _Shop, code: object) -> Currency:
    """The currency a request names, for a write: so'm when it names none.

    An unknown code, and dollars in a shop that does not work in them, are the same validation error on
    `currency`; with dollars off it does not say that dollars exist. Dollars are returned with the
    shop's setting held (see `dollars_on`).
    """
    if code is None or code == UZS.value:
        return UZS
    if parse_code(code) is USD and await dollars_on(session, lock=True):
        return USD
    raise ValidationFailed({"currency": "must be UZS or USD" if await dollars_on(session) else "must be UZS"})


def amount_hint(currency: Currency) -> str:
    """What a refused amount is told: the range of one entry in the currency's minor unit."""
    limits = RULES[currency]
    if currency is UZS:
        return f"a whole amount between {limits.min_entry} and {limits.max_entry} UZS"
    return f"a whole number of cents between {limits.min_entry} and {limits.max_entry} (0.01 to 10000.00 USD)"


def limit_hint(currency: Currency) -> str:
    limits = RULES[currency]
    if currency is UZS:
        return f"a whole amount between {limits.min_limit} and {limits.max_limit} UZS, or null"
    return f"a whole number of cents between {limits.min_limit} and {limits.max_limit}, or null"


def tag(body: dict[str, Any], currency: Currency) -> dict[str, Any]:
    """The body with its currency beside the amount. So'm is the absence of the key, as it always was."""
    if currency is not UZS:
        body["currency"] = currency.value
    return body


def currency_of(body: Mapping[str, Any]) -> Currency:
    """The currency of a body written by `tag`."""
    return parse_code(body.get("currency")) or UZS


def balance_in(customer: Mapping[str, Any], currency: Currency) -> int:
    """A customer body's balance in one currency: `balance` for so'm, `usd.balance` for dollars."""
    if currency is UZS:
        return int(customer["balance"])
    return int((customer.get("usd") or {}).get("balance", 0))

"""Which currencies the stock and the suppliers of a shop work in.

The rule is the ledger's own (`qarz.application.currencies`): so'm always, dollars when the platform
switch `usd_on` and the shop's own setting are both on. Here the codes travel as plain text, as the
stock's tables store them.
"""

from qarz.application import currencies
from qarz.application.ports import TenantSession

UZS = currencies.UZS.value
USD = currencies.USD.value


async def shop_currencies(session: TenantSession) -> tuple[str, ...]:
    """The currencies this shop works in, so'm first."""
    return tuple(currency.value for currency in await currencies.shop_currencies(session))


async def require_currency(session: TenantSession, code: object) -> str:
    """The currency a request names, for a write: so'm when it names none. Dollars in a shop that does
    not work in them are refused like an unknown code."""
    return (await currencies.require_currency(session, code)).value

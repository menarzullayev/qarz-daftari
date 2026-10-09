"""Which currencies the stock and the suppliers of a shop work in.

One place, so that the rule is the ledger's own: so'm always, dollars when the shop works in them.
"""

from qarz.application.errors import ValidationFailed
from qarz.application.ports import TenantSession

UZS = "UZS"
USD = "USD"


async def shop_currencies(session: TenantSession) -> tuple[str, ...]:
    """The currencies this shop works in, so'm first."""
    return (UZS,)


async def require_currency(session: TenantSession, code: object) -> str:
    """The currency a request names, for a write: so'm when it names none."""
    if code is None or code == UZS:
        return UZS
    raise ValidationFailed({"currency": "must be UZS"})

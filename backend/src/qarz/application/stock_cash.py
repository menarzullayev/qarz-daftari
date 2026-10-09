"""Where the stock and the suppliers meet the cash book (module H of the expansion).

Money that leaves the till for goods is an expense of the cash book in the category "goods purchase":
a payment to a supplier, or what is paid at once on a purchase receipt. While the cash book is on, that
expense is written in the same transaction and linked both ways, and cancelling one cancels the other.
While it is off, the payment is in the supplier's account alone.
"""

from datetime import datetime
from uuid import UUID

from qarz.application.ports import Membership, TenantSession


async def cash_book_on(session: TenantSession) -> bool:
    return False


async def purchase_expense(
    session: TenantSession, actor: Membership, *, amount: int, currency: str, note: str | None, now: datetime
) -> UUID | None:
    """Write the expense and return its identifier; None while the cash book is off."""
    return None


async def cancel_expense(
    session: TenantSession, actor: Membership, cash_entry_id: UUID | None, *, reason: str, now: datetime
) -> None:
    """Cancel the expense written with a payment that is now cancelled."""
    return None

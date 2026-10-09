"""Where the stock and the suppliers meet the cash book (module H of the expansion).

Money that leaves the till for goods is an expense of the cash book: a payment to a supplier, what is
paid at once on a purchase receipt, and what is handed back to a customer for goods returned. While the
cash book is on (`cash_book_on`), that expense is written here in the same transaction as the payment or
the document, under a category of the stock's own, and it says which payment or document it is (the
way a customer's payment does in `qarz.application.cash_feed`). While the cash book is off nothing is
written: the payment is in the supplier's account alone.

Cancelling goes one way. Cancelling the payment, or the document, cancels its cash entry with the same
reason. The cash book refuses to cancel such an entry on its own (`CASH_ENTRY_OF_STOCK`): it is cancelled
where it was written, like a customer's payment. And that does not ask the switch: an entry written
while the cash book was on must not stay standing because its payment was cancelled while it was off.
"""

from datetime import datetime
from uuid import UUID, uuid4

from qarz.application.cash_feed import ensure_categories, switched_on
from qarz.application.cash_ports import CashCategoryRecord, NewCashCategory
from qarz.application.errors import ValidationFailed
from qarz.application.export_texts import word
from qarz.application.ports import Membership, TenantSession
from qarz.domain import cash
from qarz.domain.cash import Direction, Method
from qarz.domain.promise import tashkent_date

cash_book_on = switched_on


def clean_method(method: str | None) -> Method | None:
    """How money was paid, as a request names it; None when it names none (it is then taken as cash)."""
    if method is None:
        return None
    parsed = cash.parse_method(method)
    if parsed is None:
        raise ValidationFailed({"method": "must be cash, card or transfer"})
    return parsed


async def _category(session: TenantSession, system_key: str, now: datetime) -> CashCategoryRecord:
    """The stock's own category of the cash book, made the first time money goes out under it."""
    await ensure_categories(session, now)
    found = await session.cash_system_category(system_key)
    if found is not None:
        return found
    settings = await session.shop_settings()
    lang = "uz" if settings is None else settings.lang
    assert system_key in cash.STOCK_CATEGORIES
    await session.lock_cash_categories()
    found = await session.cash_system_category(system_key)
    if found is not None:
        return found
    # Named in the shop's language now; from then on the name is the shop's own data.
    wanted = word(lang, f"cash_category_{system_key}")
    for attempt in range(1, 50):
        # A shop may have named a category of its own exactly so: the stock's then carries a number.
        shown, norm = cash.category_name(wanted if attempt == 1 else f"{wanted} {attempt}")
        if await session.cash_category_named(Direction.EXPENSE.value, norm) is None:
            await session.add_cash_categories(
                [NewCashCategory(uuid4(), Direction.EXPENSE.value, shown, norm, system_key)], now
            )
            break
    found = await session.cash_system_category(system_key)
    assert found is not None
    return found


async def record_expense(
    session: TenantSession,
    actor: Membership,
    *,
    system_key: str,
    amount: int,
    currency: str,
    method: Method | None,
    note: str | None,
    now: datetime,
    supplier_entry_id: UUID | None = None,
    stock_document_id: UUID | None = None,
) -> bool:
    """Write the cash-book expense of money the stock paid out. False, and nothing written, while the
    cash book is off."""
    if not amount or not await switched_on(session):
        return False
    category = await _category(session, system_key, now)
    await session.add_stock_cash_entry(
        entry_id=uuid4(),
        method=(method or cash.DEFAULT_METHOD).value,
        currency=currency,
        amount=amount,
        category_id=category.category_id,
        note=note,
        day=tashkent_date(now),
        author_id=actor.membership_id,
        supplier_entry_id=supplier_entry_id,
        stock_document_id=stock_document_id,
        now=now,
    )
    return True


async def cancel_expense(
    session: TenantSession,
    actor: Membership,
    *,
    reason: str,
    now: datetime,
    supplier_entry_id: UUID | None = None,
    stock_document_id: UUID | None = None,
) -> None:
    """Cancel the expense written with a payment or a document that is now cancelled, if there is one."""
    await session.cancel_stock_cash_entries(
        supplier_entry_id=supplier_entry_id,
        stock_document_id=stock_document_id,
        by=actor.membership_id,
        reason=reason,
        now=now,
    )

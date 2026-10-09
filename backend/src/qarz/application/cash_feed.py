"""How the ledger feeds the cash book (BR-48, BR-49).

A customer's payment is money the shop received. While the cash book is on, the ledger writes it into
the book as income of the category "debt repaid", in the transaction that writes the payment itself, and
cancels that entry in the transaction that reverses the payment.

Stored and linked, not worked out when the book is read. A cash book that added the ledger's payments in
on every read would have no row to carry the method the money came by, would change its past whenever
the switch was turned, and would make every balance a join over two tables of different shapes. A stored
row has the method, is counted by the same statement as every other entry, and stays what it was. What
keeps the two from drifting apart is the database: one cash entry per payment at most (a unique index),
carrying that payment's amount and currency (a trigger), written and cancelled only by the functions
below, inside the ledger's own transaction. A credit sale is not money and writes nothing here.

Payments recorded while the switch was off are not in the book. The owner may copy them in once
(`qarz.application.cash_book.CashBookService.backfill`); nothing does it unasked.
"""

from datetime import datetime
from uuid import UUID, uuid4

from qarz.application.cash_ports import CashCategoryRecord, NewCashCategory
from qarz.application.errors import NotFound, ValidationFailed
from qarz.application.ports import Membership, TenantSession
from qarz.domain import cash
from qarz.domain.cash import Direction, Method
from qarz.domain.money import Currency
from qarz.domain.promise import tashkent_date

# What a request that names a method is told while the cash book is off: exactly what it was told before
# the field existed, so that nothing about an answer says the module is there.
UNKNOWN_FIELD = "Extra inputs are not permitted"


async def switched_on(session: TenantSession) -> bool:
    # Only the JSON value `true` turns it on: absent, null, "true" and 1 all mean off.
    return await session.platform_setting(cash.SWITCH) is True


def clean_method(kind: str, method: object) -> Method | None:
    """The method a new ledger entry names, or None when it names none. Only a payment has one."""
    if method is None:
        return None
    if kind != "payment":
        raise ValidationFailed({"method": "only a payment has a method"})
    parsed = cash.parse_method(method)
    if parsed is None:
        raise ValidationFailed({"method": "must be cash, card or transfer"})
    return parsed


async def ensure_categories(session: TenantSession, now: datetime) -> CashCategoryRecord:
    """The shop's categories exist: the default set is written the first time the book is used.

    Returns the category payments land in. It is the one category that can be neither archived nor
    deleted, so finding it means the set was written before, whatever the shop has done to it since.
    """
    found = await session.cash_system_category(cash.DEBT_REPAID)
    if found is not None:
        return found
    settings = await session.shop_settings()
    if settings is None:
        raise NotFound()
    await session.lock_cash_categories()
    defaults = []
    for direction, name, system_key in cash.default_names(settings.lang):
        shown, norm = cash.category_name(name)
        defaults.append(NewCashCategory(uuid4(), direction.value, shown, norm, system_key))
    await session.add_cash_categories(defaults, now)
    found = await session.cash_system_category(cash.DEBT_REPAID)
    assert found is not None
    return found


async def payment_recorded_in(
    session: TenantSession,
    actor: Membership,
    *,
    entry_id: UUID,
    amount: int,
    currency: Currency,
    method: Method | None,
    now: datetime,
) -> Method | None:
    """Write the cash entry of a payment just added to the ledger. Returns the method it was written
    with, or None when the cash book is off and nothing was written."""
    if not await switched_on(session):
        return None
    category = await ensure_categories(session, now)
    paid_by = method or cash.DEFAULT_METHOD
    await session.add_cash_entry(
        entry_id=uuid4(),
        direction=Direction.INCOME.value,
        method=paid_by.value,
        currency=currency.value,
        amount=amount,
        category_id=category.category_id,
        note=None,
        day=tashkent_date(now),
        author_id=actor.membership_id,
        ledger_entry_id=entry_id,
        now=now,
    )
    return paid_by


async def payment_reversed_in(session: TenantSession, actor: Membership, entry_id: UUID, now: datetime) -> None:
    """Cancel the cash entry of a payment that was just reversed, if it has one.

    Not asked of the switch: an entry written while the book was on must not stay standing because the
    payment was reversed while it was off. In a shop that never used the book this touches no row.
    """
    await session.cancel_cash_entry_of_payment(entry_id, by=actor.membership_id, now=now)

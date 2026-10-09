"""The cash book of a shop (expansion module H; BR-44 to BR-51).

Income and expense, by cash, card or transfer, in each currency the shop works in, under categories the
shop names itself. The rules are in `qarz.domain.cash`; how a customer's payment gets here is in
`qarz.application.cash_feed`.

Who may, by default (each is a permission of the catalogue, so the owner may decide otherwise for one
member while `permissions_on` is on):

- read the book and its summary: managers and the owner (`cash.view`). The book is the shop's money; a
  seller at the counter does not need it to do their work;
- record income, record expense: managers and the owner, as two permissions (`cash.record_income`,
  `cash.record_expense`), so that an owner can let a seller write down cash sales without letting them
  write money out of the till;
- cancel an entry, anyone's, with a reason: managers and the owner (`cash.cancel`), as with reversing an
  entry of the ledger;
- change the categories: managers and the owner (`cash.categories`);
- copy past payments of the ledger into the book: the owner alone (`cash.backfill`).

Everything is behind the platform switch `cash_book_on`. While it is off these operations answer exactly
as a route that does not exist, to everyone, before the caller is even asked who they are.
"""

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from qarz.application import currencies, idempotency
from qarz.application.authorization import may, require_permission
from qarz.application.cash_feed import ensure_categories
from qarz.application.cash_ports import CashCategoryRecord, CashEntryRecord, NewCashCategory
from qarz.application.customers import decode_cursor, encode_cursor, require_viewable, require_writable
from qarz.application.errors import AppError, NotFound, ValidationFailed
from qarz.application.operations import operation
from qarz.application.ports import Membership, Storage, TenantSession
from qarz.application.shops import require_member
from qarz.domain import cash, permissions
from qarz.domain.access import Capability
from qarz.domain.cash import Direction, Line
from qarz.domain.money import valid_entry_amount
from qarz.domain.promise import tashkent_date
from qarz.domain.reports import PeriodProblem, parse_day

READ_DAY = operation("cash.day", Capability.MANAGE)
READ_SUMMARY = operation("cash.summary", Capability.MANAGE)
RECORD_CASH = operation("cash.entry.create", Capability.MANAGE)
CANCEL_CASH = operation("cash.entry.cancel", Capability.MANAGE)
LIST_CATEGORIES = operation("cash.categories.list", Capability.MANAGE)
CREATE_CATEGORY = operation("cash.categories.create", Capability.MANAGE)
UPDATE_CATEGORY = operation("cash.categories.update", Capability.MANAGE)
DELETE_CATEGORY = operation("cash.categories.delete", Capability.MANAGE)
BACKFILL = operation("cash.backfill", Capability.ADMINISTER_SHOP)


class CashEntryOfLedger(AppError):
    """The entry is a customer's payment: it is cancelled by reversing that payment in the ledger."""

    code = "CASH_ENTRY_OF_LEDGER"


class CashEntryOfStock(AppError):
    """The entry is money the stock paid out (a payment to a supplier, a purchase, a refund): it is
    cancelled by cancelling that payment or that document."""

    code = "CASH_ENTRY_OF_STOCK"


class CashEntryCancelled(AppError):
    code = "CASH_ENTRY_CANCELLED"


class CashCategoryArchived(AppError):
    """An archived category takes no new entry."""

    code = "CASH_CATEGORY_ARCHIVED"


class CashCategoryNameTaken(AppError):
    code = "CASH_CATEGORY_NAME_TAKEN"


class CashCategoryFixed(AppError):
    """The category payments land in can be renamed, and nothing else."""

    code = "CASH_CATEGORY_FIXED"


class CashCategoryInUse(AppError):
    """A category with entries under it is archived, not deleted: the entries keep their category."""

    code = "CASH_CATEGORY_IN_USE"


def category_body(record: CashCategoryRecord) -> dict[str, Any]:
    return {
        "id": str(record.category_id),
        "direction": record.direction,
        "name": record.name,
        "archived": record.archived_at is not None,
        # True for the category the ledger writes to: no entry by hand, no archiving, no deleting.
        "fixed": record.system_key is not None,
    }


def entry_body(record: CashEntryRecord, *, names: bool) -> dict[str, Any]:
    """One entry. `names` says whether the reader may see whose payment an entry of the ledger is."""
    customer: dict[str, Any] | None = None
    if names and record.customer_id is not None:
        customer = {"id": str(record.customer_id), "display_name": record.customer_name}
    cancelled: dict[str, Any] | None = None
    if record.cancelled_at is not None:
        cancelled = {
            "at": record.cancelled_at.isoformat(),
            "by": None if record.cancelled_by is None else str(record.cancelled_by),
            # Null when the ledger cancelled it: the payment was reversed.
            "reason": record.cancel_reason,
        }
    return {
        "id": str(record.entry_id),
        "direction": record.direction,
        "method": record.method,
        "currency": record.currency,
        "amount": record.amount,
        "category": {"id": str(record.category_id), "name": record.category_name},
        "note": record.note,
        "day": record.day.isoformat(),
        "created_at": record.created_at.isoformat(),
        "author_id": str(record.author_id),
        "source": _source(record),
        "customer": customer,
        "cancelled": cancelled,
    }


def _source(record: CashEntryRecord) -> str:
    """Who wrote the entry: a person, the customers' ledger, or the stock. Only the first is
    cancelled in the cash book itself."""
    if record.ledger_entry_id is not None:
        return "ledger"
    if record.supplier_entry_id is not None or record.stock_document_id is not None:
        return "stock"
    return "manual"


def line_body(line: Line) -> dict[str, Any]:
    return {
        "currency": line.currency.value,
        "method": line.method.value,
        "opening": line.opening,
        "income": line.income,
        "expense": line.expense,
        "closing": line.closing,
        "count": line.count,
    }


def total_body(line: Line) -> dict[str, Any]:
    """A currency's lines added over its methods. Never a figure of two currencies."""
    body = line_body(line)
    del body["method"]
    return body


async def book_in(session: TenantSession, first: date, last: date) -> list[Line]:
    """The balances of the days from `first` to `last`: one line for each method of each currency."""
    after = last + timedelta(days=1)
    return cash.book(
        await session.cash_sums(first=None, before=first),
        await session.cash_sums(first=first, before=after),
        await currencies.shop_currencies(session),
    )


def balances_body(lines: list[Line]) -> dict[str, Any]:
    return {
        "balances": [line_body(line) for line in lines],
        "totals": [total_body(line) for line in cash.totals_by_currency(lines).values()],
    }


def period_of(raw_first: str | None, raw_last: str | None, today: date) -> tuple[date, date]:
    first, last = parse_day(raw_first), parse_day(raw_last)
    fields = {name: PeriodProblem.DATE_INVALID.value for name, day in (("from", first), ("to", last)) if day is None}
    if first is None or last is None:
        raise ValidationFailed(fields)
    if first > last:
        raise ValidationFailed({"from": PeriodProblem.FROM_AFTER_TO.value})
    if last > today:
        raise ValidationFailed({"to": PeriodProblem.IN_FUTURE.value})
    if (last - first).days + 1 > cash.MAX_PERIOD_DAYS:
        raise ValidationFailed({"to": PeriodProblem.TOO_LONG.value})
    return first, last


class CashBookService:
    def __init__(self, storage: Storage, now: Callable[[], datetime] | None = None) -> None:
        self._storage = storage
        self._now = now or (lambda: datetime.now(UTC))

    def _today(self) -> date:
        return tashkent_date(self._now())

    async def switched_on(self) -> bool:
        async with self._storage.platform() as session:
            # Only the JSON value `true` turns it on: absent, null, "true" and 1 all mean off.
            return await session.platform_setting(cash.SWITCH) is True

    async def require_on(self) -> None:
        """Called for every route of this module before anything else: off is "no such route"."""
        if not await self.switched_on():
            raise NotFound()

    # --- reading ---------------------------------------------------------------------------------------

    async def day(
        self, user_id: UUID, shop_id: UUID, *, raw_day: str | None, cursor: str | None, limit: int
    ) -> dict[str, Any]:
        """One day's book: what each method of each currency opened with, took in, paid out and closed
        with, and a page of the day's entries, newest first, cancelled ones included."""
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, READ_DAY)
            today = self._today()
            await require_viewable(session, actor, today)
            fields: dict[str, str] = {}
            day = today if raw_day is None else parse_day(raw_day)
            if day is None:
                fields["date"] = PeriodProblem.DATE_INVALID.value
            elif day > today:
                fields["date"] = PeriodProblem.IN_FUTURE.value
            if not 1 <= limit <= cash.MAX_PAGE:
                fields["limit"] = f"must be between 1 and {cash.MAX_PAGE}"
            if fields or day is None:
                raise ValidationFailed(fields)
            after: tuple[datetime, UUID] | None = None
            if cursor is not None:
                at, last_id = decode_cursor(cursor, 2)
                try:
                    after = (datetime.fromisoformat(at), UUID(last_id))
                except ValueError as error:
                    raise ValidationFailed({"cursor": "not a cursor returned by this API"}) from error
            page = await session.cash_entries_of_day(day, after=after, limit=limit + 1)
            more = len(page) > limit
            shown = page[:limit]
            names = may(actor, permissions.LEDGER_VIEW)
            return {
                "date": day.isoformat(),
                **balances_body(await book_in(session, day, day)),
                "entries": [entry_body(record, names=names) for record in shown],
                "next_cursor": encode_cursor(shown[-1].created_at.isoformat(), shown[-1].entry_id) if more else None,
            }

    async def summary(
        self, user_id: UUID, shop_id: UUID, *, raw_first: str | None, raw_last: str | None
    ) -> dict[str, Any]:
        """A period of Tashkent days: the balances over it, and what stands in it by category and by day.

        Every figure is of one currency. A cancelled entry is in none of them.
        """
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, READ_SUMMARY)
            today = self._today()
            await require_viewable(session, actor, today)
            first, last = period_of(raw_first, raw_last, today)
            after = last + timedelta(days=1)
            names = {record.category_id: record for record in await session.cash_categories()}
            by_category = sorted(
                await session.cash_category_sums(first=first, before=after),
                key=lambda row: (
                    row.currency,
                    row.direction != Direction.INCOME.value,
                    -row.amount,
                    str(row.category_id),
                ),
            )
            days: dict[tuple[date, str], dict[str, int]] = {}
            for row in await session.cash_day_sums(first=first, before=after):
                days.setdefault((row.day, row.currency), {"income": 0, "expense": 0})[row.direction] = row.amount
            return {
                "from": first.isoformat(),
                "to": last.isoformat(),
                **balances_body(await book_in(session, first, last)),
                "categories": [
                    {
                        "category": category_body(names[row.category_id]),
                        "currency": row.currency,
                        "amount": row.amount,
                        "count": row.count,
                    }
                    for row in by_category
                ],
                # Only the days something stands on, oldest first.
                "days": [
                    {"date": day.isoformat(), "currency": currency, **figures}
                    for (day, currency), figures in sorted(days.items())
                ],
            }

    # --- entries ---------------------------------------------------------------------------------------

    async def record(
        self,
        user_id: UUID,
        shop_id: UUID,
        *,
        direction: str,
        method: str,
        currency: str | None,
        amount: int,
        category_id: UUID,
        note: str | None,
        day: date | None,
        request_key: str | None,
    ) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, RECORD_CASH)
            key = idempotency.validate_key(request_key)
            way = cash.parse_direction(direction)
            if way is None:
                raise ValidationFailed({"direction": "must be income or expense"})
            # One operation with two permissions: taking money in needs one, paying it out the other.
            require_permission(
                actor, permissions.CASH_RECORD_INCOME if way is Direction.INCOME else permissions.CASH_RECORD_EXPENSE
            )
            today = self._today()
            await require_writable(session, today, new_credit=False)
            money = await currencies.require_currency(session, currency)
            fields: dict[str, str] = {}
            paid_by = cash.parse_method(method)
            if paid_by is None:
                fields["method"] = "must be cash, card or transfer"
            if not valid_entry_amount(money, amount):
                fields["amount"] = currencies.amount_hint(money)
            text = cash.tidy(note)
            if text is not None and len(text) > cash.MAX_NOTE:
                fields["note"] = f"at most {cash.MAX_NOTE} characters"
            dated = day or today
            problem = cash.day_problem(dated, today)
            if problem is not None:
                fields["day"] = problem.value
            if fields or paid_by is None:
                raise ValidationFailed(fields)

            async def apply() -> dict[str, Any]:
                now = self._now()
                await ensure_categories(session, now)
                # Held until this write ends: the category cannot be archived or deleted under it.
                category = await session.cash_category(category_id, lock="share")
                if category is None or category.direction != way.value:
                    raise ValidationFailed({"category_id": "not a category of this direction"})
                if not cash.may_write_by_hand(category.system_key):
                    raise ValidationFailed({"category_id": "written by the ledger only"})
                if category.archived_at is not None:
                    raise CashCategoryArchived()
                entry_id = uuid4()
                await session.add_cash_entry(
                    entry_id=entry_id,
                    direction=way.value,
                    method=paid_by.value,
                    currency=money.value,
                    amount=amount,
                    category_id=category_id,
                    note=text,
                    day=dated,
                    author_id=actor.membership_id,
                    ledger_entry_id=None,
                    now=now,
                )
                await session.record_activity(
                    membership_id=actor.membership_id,
                    action=f"cash.{way.value}_recorded",
                    subject_type="cash_entry",
                    subject_id=entry_id,
                )
                return {"entry": await self._entry(session, actor, entry_id)}

            return await idempotency.run_once(
                session,
                key=key,
                operation=RECORD_CASH.name,
                user_id=user_id,
                request={
                    "direction": way.value,
                    "method": paid_by.value,
                    "currency": money.value,
                    "amount": amount,
                    "category": str(category_id),
                    "note": text,
                    "day": dated.isoformat(),
                },
                action=apply,
            )

    async def _entry(self, session: TenantSession, actor: Membership, entry_id: UUID) -> dict[str, Any]:
        record = await session.cash_entry(entry_id)
        if record is None:
            raise NotFound()
        return entry_body(record, names=may(actor, permissions.LEDGER_VIEW))

    async def cancel(
        self, user_id: UUID, shop_id: UUID, entry_id: UUID, reason: str, request_key: str | None
    ) -> dict[str, Any]:
        """Cancel an entry: it stays in the book, marked, and counts in nothing from now on."""
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, CANCEL_CASH)
            key = idempotency.validate_key(request_key)
            await require_writable(session, self._today(), new_credit=False)
            why = cash.tidy(reason)
            if why is None or not cash.MIN_REASON <= len(why) <= cash.MAX_REASON:
                raise ValidationFailed({"reason": f"between {cash.MIN_REASON} and {cash.MAX_REASON} characters"})

            async def apply() -> dict[str, Any]:
                record = await session.cash_entry(entry_id, for_update=True)
                if record is None:
                    raise NotFound()
                if record.ledger_entry_id is not None:
                    raise CashEntryOfLedger()
                if record.supplier_entry_id is not None or record.stock_document_id is not None:
                    raise CashEntryOfStock()
                if record.cancelled_at is not None or not await session.cancel_cash_entry(
                    entry_id, by=actor.membership_id, reason=why, now=self._now()
                ):
                    raise CashEntryCancelled()
                await session.record_activity(
                    membership_id=actor.membership_id,
                    action="cash.entry_cancelled",
                    subject_type="cash_entry",
                    subject_id=entry_id,
                )
                return {"entry": await self._entry(session, actor, entry_id)}

            return await idempotency.run_once(
                session,
                key=key,
                operation=CANCEL_CASH.name,
                user_id=user_id,
                request={"entry": str(entry_id), "reason": why},
                action=apply,
            )

    # --- categories ------------------------------------------------------------------------------------

    async def categories(self, user_id: UUID, shop_id: UUID) -> dict[str, Any]:
        """Every category of the shop, and the currencies an entry may be written in. The first use of the
        cash book writes the default set of categories."""
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, LIST_CATEGORIES)
            await require_viewable(session, actor, self._today())
            await ensure_categories(session, self._now())
            return {
                "items": [category_body(record) for record in await session.cash_categories()],
                # What an entry may be written in now: so'm, and dollars while the shop works in them.
                "currencies": [currency.value for currency in await currencies.shop_currencies(session)],
            }

    async def create_category(
        self, user_id: UUID, shop_id: UUID, *, direction: str, name: str, request_key: str | None
    ) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, CREATE_CATEGORY)
            key = idempotency.validate_key(request_key)
            await require_writable(session, self._today(), new_credit=False)
            way = cash.parse_direction(direction)
            if way is None:
                raise ValidationFailed({"direction": "must be income or expense"})
            shown, norm = _clean_name(name)

            async def apply() -> dict[str, Any]:
                now = self._now()
                await ensure_categories(session, now)
                await session.lock_cash_categories()
                if len(await session.cash_categories()) >= cash.MAX_CATEGORIES:
                    raise ValidationFailed({"name": f"at most {cash.MAX_CATEGORIES} categories"})
                if await session.cash_category_named(way.value, norm) is not None:
                    raise CashCategoryNameTaken()
                category_id = uuid4()
                await session.add_cash_categories([NewCashCategory(category_id, way.value, shown, norm)], now)
                await session.record_activity(
                    membership_id=actor.membership_id,
                    action="cash.category_created",
                    subject_type="cash_category",
                    subject_id=category_id,
                )
                return category_body(await _category(session, category_id))

            return await idempotency.run_once(
                session,
                key=key,
                operation=CREATE_CATEGORY.name,
                user_id=user_id,
                request={"direction": way.value, "name": shown},
                action=apply,
            )

    async def update_category(
        self,
        user_id: UUID,
        shop_id: UUID,
        category_id: UUID,
        *,
        name: str | None,
        archived: bool | None,
        request_key: str | None,
    ) -> dict[str, Any]:
        """Rename a category, archive it or bring it back. What is left out stays as it is."""
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, UPDATE_CATEGORY)
            key = idempotency.validate_key(request_key)
            await require_writable(session, self._today(), new_credit=False)
            renamed = None if name is None else _clean_name(name)
            if renamed is None and archived is None:
                raise ValidationFailed({"_": "nothing to change"})

            async def apply() -> dict[str, Any]:
                await session.lock_cash_categories()
                record = await session.cash_category(category_id, lock="update")
                if record is None:
                    raise NotFound()
                if renamed is not None:
                    holder = await session.cash_category_named(record.direction, renamed[1])
                    if holder is not None and holder != category_id:
                        raise CashCategoryNameTaken()
                    await session.rename_cash_category(category_id, renamed[0], renamed[1])
                if archived is not None and archived != (record.archived_at is not None):
                    if record.system_key is not None:
                        raise CashCategoryFixed()
                    await session.set_cash_category_archived(category_id, self._now() if archived else None)
                await session.record_activity(
                    membership_id=actor.membership_id,
                    action="cash.category_changed",
                    subject_type="cash_category",
                    subject_id=category_id,
                )
                return category_body(await _category(session, category_id))

            return await idempotency.run_once(
                session,
                key=key,
                operation=UPDATE_CATEGORY.name,
                user_id=user_id,
                request={
                    "category": str(category_id),
                    "name": None if renamed is None else renamed[0],
                    "archived": archived,
                },
                action=apply,
            )

    async def delete_category(
        self, user_id: UUID, shop_id: UUID, category_id: UUID, request_key: str | None
    ) -> dict[str, Any]:
        """Delete a category nothing was ever written under. One that was used is archived instead."""
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, DELETE_CATEGORY)
            key = idempotency.validate_key(request_key)
            await require_writable(session, self._today(), new_credit=False)

            async def apply() -> dict[str, Any]:
                await session.lock_cash_categories()
                # The row lock waits for a writer of an entry under the category, so the check below
                # sees that entry; and no entry can be started under it until this ends.
                record = await session.cash_category(category_id, lock="update")
                if record is None:
                    raise NotFound()
                if record.system_key is not None:
                    raise CashCategoryFixed()
                if await session.cash_category_used(category_id):
                    raise CashCategoryInUse()
                await session.delete_cash_category(category_id)
                await session.record_activity(
                    membership_id=actor.membership_id,
                    action="cash.category_deleted",
                    subject_type="cash_category",
                    subject_id=category_id,
                )
                return {"deleted": True}

            return await idempotency.run_once(
                session,
                key=key,
                operation=DELETE_CATEGORY.name,
                user_id=user_id,
                request={"category": str(category_id)},
                action=apply,
            )

    # --- the ledger's past -----------------------------------------------------------------------------

    async def backfill(
        self, user_id: UUID, shop_id: UUID, *, since: date | None, request_key: str | None
    ) -> dict[str, Any]:
        """Copy the ledger's payments that stand and are not in the book into it, as cash received.

        For the owner, when they decide the book should hold what customers paid before it was turned
        on; `since` leaves out what was paid before a day. Nothing runs it unasked. Running it again
        writes nothing: a payment is in the book once (BR-50).
        """
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, BACKFILL)
            key = idempotency.validate_key(request_key)
            today = self._today()
            await require_writable(session, today, new_credit=False)
            if since is not None and since > today:
                raise ValidationFailed({"since": PeriodProblem.IN_FUTURE.value})

            async def apply() -> dict[str, Any]:
                now = self._now()
                category = await ensure_categories(session, now)
                written = await session.backfill_cash_payments(category_id=category.category_id, since=since, now=now)
                await session.record_activity(
                    membership_id=actor.membership_id,
                    action="cash.backfilled",
                    subject_type="shop",
                    subject_id=shop_id,
                    detail={"entries": written},
                )
                return {"written": written}

            return await idempotency.run_once(
                session,
                key=key,
                operation=BACKFILL.name,
                user_id=user_id,
                request={"since": None if since is None else since.isoformat()},
                action=apply,
            )


def _clean_name(raw: str) -> tuple[str, str]:
    try:
        return cash.category_name(raw)
    except ValueError as error:
        raise ValidationFailed({"name": str(error)}) from error


async def _category(session: TenantSession, category_id: UUID) -> CashCategoryRecord:
    record = await session.cash_category(category_id)
    if record is None:
        raise NotFound()
    return record

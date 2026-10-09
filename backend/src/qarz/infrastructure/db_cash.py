"""The cash book's statements (expansion module H): a part of `PgTenantSession`.

In a file of its own for the reason `qarz.application.cash_ports` is. Every statement names the shop as
well as relying on row-level security: the indexes lead with the shop, and a connection made with a role
that bypasses the policies still reads one shop.

Every SQL text here is a constant; values travel as bound parameters.
"""

from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from qarz.application.cash_ports import (
    CashCategoryRecord,
    CashCategorySum,
    CashDaySum,
    CashEntryRecord,
    CashExportRow,
    NewCashCategory,
)
from qarz.domain.cash import Direction, Method, Sum
from qarz.domain.money import Currency

# The open ends of a stretch of days: a statement always has both bounds, so its plan never depends on
# which were given.
_FIRST_DAY = date(1, 1, 1)
_LAST_DAY = date(9999, 12, 31)
_AFTER_EVERYTHING = datetime(9999, 12, 31, tzinfo=UTC)
_BEFORE_EVERYTHING = datetime(1, 1, 1, tzinfo=UTC)
_FIRST_UUID = UUID(int=0)
_LAST_UUID = UUID(int=2**128 - 1)

_CATEGORY_SELECT = "SELECT id, direction, name, system_key, archived_at FROM cash_category "
_CATEGORIES = (
    _CATEGORY_SELECT + "WHERE shop_id = :shop_id ORDER BY direction DESC, archived_at IS NOT NULL, name_norm, id"
)
_CATEGORY_BY_ID = _CATEGORY_SELECT + "WHERE id = :id AND shop_id = :shop_id"
# By the lock a caller asks for: none, shared with other writers of entries, or the category's own change.
_CATEGORY_LOCKED = {
    None: _CATEGORY_BY_ID,
    "share": _CATEGORY_BY_ID + " FOR SHARE",
    "update": _CATEGORY_BY_ID + " FOR UPDATE",
}
_SYSTEM_CATEGORY = _CATEGORY_SELECT + "WHERE shop_id = :shop_id AND system_key = :key"

_ENTRY_SELECT = (
    "SELECT e.id, e.direction, e.method, e.currency, e.amount, e.category_id, c.name AS category_name, e.note, "
    "e.day, e.created_at, e.author_id, e.ledger_entry_id, e.cancelled_at, e.cancelled_by, e.cancel_reason, "
    "e.supplier_entry_id, e.stock_document_id, "
    "l.customer_id, p.display_name AS customer_name "
    "FROM cash_entry e JOIN cash_category c ON c.id = e.category_id "
    "LEFT JOIN ledger_entry l ON l.id = e.ledger_entry_id "
    "LEFT JOIN customer p ON p.id = l.customer_id "
)
_ENTRY_BY_ID = _ENTRY_SELECT + "WHERE e.id = :id AND e.shop_id = :shop_id"
_ENTRY_BY_ID_LOCKED = _ENTRY_BY_ID + " FOR UPDATE OF e"
_ENTRIES_OF_DAY = (
    _ENTRY_SELECT + "WHERE e.shop_id = :shop_id AND e.day = :day AND (e.created_at, e.id) < (:after_at, :after_id) "
    "ORDER BY e.created_at DESC, e.id DESC LIMIT :limit"
)


# A page of a period for its export, by the index `cash_entry_day (shop_id, day, created_at, id)`.
_ENTRIES_OF_PERIOD = (
    _ENTRY_SELECT + "WHERE e.shop_id = :shop_id AND e.day >= :first AND e.day < :before "
    "AND (e.day, e.created_at, e.id) > (:after_day, :after_at, :after_id) AND e.created_at <= :until "
    "ORDER BY e.day, e.created_at, e.id LIMIT :limit"
)


def _category(row: Any) -> CashCategoryRecord:
    return CashCategoryRecord(row.id, str(row.direction), str(row.name), row.system_key, row.archived_at)


def _entry(row: Any) -> CashEntryRecord:
    return CashEntryRecord(
        entry_id=row.id,
        direction=str(row.direction),
        method=str(row.method),
        currency=str(row.currency),
        amount=int(row.amount),
        category_id=row.category_id,
        category_name=str(row.category_name),
        note=row.note,
        day=row.day,
        created_at=row.created_at,
        author_id=row.author_id,
        ledger_entry_id=row.ledger_entry_id,
        customer_id=row.customer_id,
        customer_name=None if row.customer_name is None else str(row.customer_name),
        cancelled_at=row.cancelled_at,
        cancelled_by=row.cancelled_by,
        cancel_reason=row.cancel_reason,
        supplier_entry_id=row.supplier_entry_id,
        stock_document_id=row.stock_document_id,
    )


class CashStatements:
    _conn: AsyncConnection
    _shop_id: UUID

    # --- categories ------------------------------------------------------------------------------------

    async def lock_cash_categories(self) -> None:
        await self._conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
            {"scope": "cash_categories:" + str(self._shop_id)},
        )

    async def cash_categories(self) -> list[CashCategoryRecord]:
        rows = (
            await self._conn.execute(
                text(_CATEGORIES),
                {"shop_id": self._shop_id},
            )
        ).all()
        return [_category(row) for row in rows]

    async def cash_category(self, category_id: UUID, *, lock: str | None = None) -> CashCategoryRecord | None:
        row = (
            await self._conn.execute(
                text(_CATEGORY_LOCKED[lock]),
                {"id": category_id, "shop_id": self._shop_id},
            )
        ).first()
        return None if row is None else _category(row)

    async def cash_system_category(self, system_key: str) -> CashCategoryRecord | None:
        row = (
            await self._conn.execute(
                text(_SYSTEM_CATEGORY),
                {"shop_id": self._shop_id, "key": system_key},
            )
        ).first()
        return None if row is None else _category(row)

    async def cash_category_named(self, direction: str, name_norm: str) -> UUID | None:
        row = (
            await self._conn.execute(
                text(
                    "SELECT id FROM cash_category "
                    "WHERE shop_id = :shop_id AND direction = :direction AND name_norm = :name_norm"
                ),
                {"shop_id": self._shop_id, "direction": direction, "name_norm": name_norm},
            )
        ).first()
        return None if row is None else row.id

    async def add_cash_categories(self, categories: list[NewCashCategory], now: datetime) -> None:
        for category in categories:
            await self._conn.execute(
                text(
                    "INSERT INTO cash_category (id, shop_id, direction, name, name_norm, system_key, created_at) "
                    "VALUES (:id, :shop_id, :direction, :name, :name_norm, :system_key, :now) "
                    "ON CONFLICT DO NOTHING"
                ),
                {
                    "id": category.category_id,
                    "shop_id": self._shop_id,
                    "direction": category.direction,
                    "name": category.name,
                    "name_norm": category.name_norm,
                    "system_key": category.system_key,
                    "now": now,
                },
            )

    async def rename_cash_category(self, category_id: UUID, name: str, name_norm: str) -> None:
        await self._conn.execute(
            text("UPDATE cash_category SET name = :name, name_norm = :name_norm WHERE id = :id AND shop_id = :shop_id"),
            {"id": category_id, "shop_id": self._shop_id, "name": name, "name_norm": name_norm},
        )

    async def set_cash_category_archived(self, category_id: UUID, at: datetime | None) -> None:
        await self._conn.execute(
            text("UPDATE cash_category SET archived_at = :at WHERE id = :id AND shop_id = :shop_id"),
            {"id": category_id, "shop_id": self._shop_id, "at": at},
        )

    async def cash_category_used(self, category_id: UUID) -> bool:
        row = (
            await self._conn.execute(
                text("SELECT EXISTS (SELECT 1 FROM cash_entry WHERE category_id = :id) AS used"), {"id": category_id}
            )
        ).one()
        return bool(row.used)

    async def delete_cash_category(self, category_id: UUID) -> None:
        await self._conn.execute(
            text("DELETE FROM cash_category WHERE id = :id AND shop_id = :shop_id"),
            {"id": category_id, "shop_id": self._shop_id},
        )

    # --- entries ---------------------------------------------------------------------------------------

    async def add_cash_entry(
        self,
        *,
        entry_id: UUID,
        direction: str,
        method: str,
        currency: str,
        amount: int,
        category_id: UUID,
        note: str | None,
        day: date,
        author_id: UUID,
        ledger_entry_id: UUID | None,
        now: datetime,
    ) -> None:
        await self._conn.execute(
            text(
                "INSERT INTO cash_entry (id, shop_id, direction, method, currency, amount, category_id, note, day, "
                "created_at, author_id, ledger_entry_id) VALUES (:id, :shop_id, :direction, :method, :currency, "
                ":amount, :category_id, :note, :day, :now, :author_id, :ledger_entry_id)"
            ),
            {
                "id": entry_id,
                "shop_id": self._shop_id,
                "direction": direction,
                "method": method,
                "currency": currency,
                "amount": amount,
                "category_id": category_id,
                "note": note,
                "day": day,
                "now": now,
                "author_id": author_id,
                "ledger_entry_id": ledger_entry_id,
            },
        )

    async def cash_entry(self, entry_id: UUID, *, for_update: bool = False) -> CashEntryRecord | None:
        row = (
            await self._conn.execute(
                text(_ENTRY_BY_ID_LOCKED if for_update else _ENTRY_BY_ID),
                {"id": entry_id, "shop_id": self._shop_id},
            )
        ).first()
        return None if row is None else _entry(row)

    async def cancel_cash_entry(self, entry_id: UUID, *, by: UUID, reason: str, now: datetime) -> bool:
        result = await self._conn.execute(
            text(
                "UPDATE cash_entry SET cancelled_at = :now, cancelled_by = :by, cancel_reason = :reason "
                "WHERE id = :id AND shop_id = :shop_id AND cancelled_at IS NULL"
            ),
            {"id": entry_id, "shop_id": self._shop_id, "by": by, "reason": reason, "now": now},
        )
        return int(result.rowcount) == 1

    async def cancel_cash_entry_of_payment(self, ledger_entry_id: UUID, *, by: UUID, now: datetime) -> bool:
        result = await self._conn.execute(
            text(
                "UPDATE cash_entry SET cancelled_at = :now, cancelled_by = :by "
                "WHERE ledger_entry_id = :ledger_entry_id AND shop_id = :shop_id AND cancelled_at IS NULL"
            ),
            {"ledger_entry_id": ledger_entry_id, "shop_id": self._shop_id, "by": by, "now": now},
        )
        return int(result.rowcount) == 1

    async def cash_entries_of_day(
        self, day: date, *, after: tuple[datetime, UUID] | None, limit: int
    ) -> list[CashEntryRecord]:
        after_at, after_id = after or (_AFTER_EVERYTHING, _LAST_UUID)
        rows = (
            await self._conn.execute(
                text(_ENTRIES_OF_DAY),
                {"shop_id": self._shop_id, "day": day, "after_at": after_at, "after_id": after_id, "limit": limit},
            )
        ).all()
        return [_entry(row) for row in rows]

    async def cash_sums(self, *, first: date | None, before: date | None) -> list[Sum]:
        rows = (
            await self._conn.execute(
                text(
                    "SELECT method, currency, direction, sum(amount)::bigint AS amount, count(*) AS entries "
                    "FROM cash_entry WHERE shop_id = :shop_id AND day >= :first AND day < :before "
                    "AND cancelled_at IS NULL GROUP BY method, currency, direction"
                ),
                {"shop_id": self._shop_id, "first": first or _FIRST_DAY, "before": before or _LAST_DAY},
            )
        ).all()
        return [
            Sum(Method(row.method), Currency(row.currency), Direction(row.direction), int(row.amount), int(row.entries))
            for row in rows
        ]

    async def cash_category_sums(self, *, first: date, before: date) -> list[CashCategorySum]:
        rows = (
            await self._conn.execute(
                text(
                    "SELECT category_id, direction, currency, sum(amount)::bigint AS amount, count(*) AS entries "
                    "FROM cash_entry WHERE shop_id = :shop_id AND day >= :first AND day < :before "
                    "AND cancelled_at IS NULL GROUP BY category_id, direction, currency"
                ),
                {"shop_id": self._shop_id, "first": first, "before": before},
            )
        ).all()
        return [
            CashCategorySum(row.category_id, str(row.direction), str(row.currency), int(row.amount), int(row.entries))
            for row in rows
        ]

    async def cash_day_sums(self, *, first: date, before: date) -> list[CashDaySum]:
        rows = (
            await self._conn.execute(
                text(
                    "SELECT day, direction, currency, sum(amount)::bigint AS amount "
                    "FROM cash_entry WHERE shop_id = :shop_id AND day >= :first AND day < :before "
                    "AND cancelled_at IS NULL GROUP BY day, direction, currency"
                ),
                {"shop_id": self._shop_id, "first": first, "before": before},
            )
        ).all()
        return [CashDaySum(row.day, str(row.direction), str(row.currency), int(row.amount)) for row in rows]

    async def backfill_cash_payments(self, *, category_id: UUID, since: date | None, now: datetime) -> int:
        # The day an entry belongs to is the Tashkent day of the payment; "when it was written" is now.
        # A payment that was reversed is not money the shop has, and one already in the book stays as it
        # is: the unique index on the payment is what makes a second run write nothing.
        result = await self._conn.execute(
            text(
                "INSERT INTO cash_entry (id, shop_id, direction, method, currency, amount, category_id, day, "
                "created_at, author_id, ledger_entry_id) "
                "SELECT gen_random_uuid(), e.shop_id, 'income', 'cash', e.currency, e.amount, :category_id, "
                "(e.created_at AT TIME ZONE 'Asia/Tashkent')::date, :now, e.author_id, e.id "
                "FROM ledger_entry e WHERE e.shop_id = :shop_id AND e.kind = 'payment' "
                "AND (e.created_at AT TIME ZONE 'Asia/Tashkent')::date >= :since "
                "AND NOT EXISTS (SELECT 1 FROM ledger_entry r WHERE r.reverses_id = e.id) "
                "AND NOT EXISTS (SELECT 1 FROM cash_entry c WHERE c.ledger_entry_id = e.id) "
                # A payment that a return of goods wrote lowered the debt with goods, not money (module I).
                "AND NOT EXISTS (SELECT 1 FROM stock_document d WHERE d.ledger_entry_id = e.id) "
                "ON CONFLICT DO NOTHING"
            ),
            {"shop_id": self._shop_id, "category_id": category_id, "since": since or _FIRST_DAY, "now": now},
        )
        return int(result.rowcount)

    async def cash_entries_exist(self) -> bool:
        row = (
            await self._conn.execute(
                text("SELECT EXISTS (SELECT 1 FROM cash_entry WHERE shop_id = :shop_id) AS found"),
                {"shop_id": self._shop_id},
            )
        ).one()
        return bool(row.found)

    async def count_cash_entries(self, *, first: date, before: date) -> int:
        row = (
            await self._conn.execute(
                text(
                    "SELECT count(*) AS entries FROM cash_entry "
                    "WHERE shop_id = :shop_id AND day >= :first AND day < :before"
                ),
                {"shop_id": self._shop_id, "first": first, "before": before},
            )
        ).one()
        return int(row.entries)

    async def cash_entries_of_period(
        self, *, first: date, before: date, until: datetime, after: tuple[date, datetime, UUID] | None, limit: int
    ) -> list[CashEntryRecord]:
        after_day, after_at, after_id = after or (_FIRST_DAY, _BEFORE_EVERYTHING, _FIRST_UUID)
        rows = (
            await self._conn.execute(
                text(_ENTRIES_OF_PERIOD),
                {
                    "shop_id": self._shop_id,
                    "first": first,
                    "before": before,
                    "until": until,
                    "after_day": after_day,
                    "after_at": after_at,
                    "after_id": after_id,
                    "limit": limit,
                },
            )
        ).all()
        return [_entry(row) for row in rows]

    async def export_cash_entries(
        self, *, until: datetime, after: tuple[date, datetime, UUID] | None, limit: int
    ) -> list[CashExportRow]:
        after_day, after_at, after_id = after or (_FIRST_DAY, _BEFORE_EVERYTHING, _FIRST_UUID)
        rows = (
            await self._conn.execute(
                text(
                    "SELECT e.id, e.day, e.created_at, e.direction, e.method, e.currency, e.amount, "
                    "c.name AS category_name, e.note, e.author_id, m.role AS author_role, e.ledger_entry_id, "
                    "e.cancelled_at, e.cancel_reason "
                    "FROM cash_entry e JOIN cash_category c ON c.id = e.category_id "
                    "JOIN membership m ON m.id = e.author_id "
                    "WHERE e.shop_id = :shop_id AND e.created_at <= :until "
                    "AND (e.day, e.created_at, e.id) > (:after_day, :after_at, :after_id) "
                    "ORDER BY e.day, e.created_at, e.id LIMIT :limit"
                ),
                {
                    "shop_id": self._shop_id,
                    "until": until,
                    "after_day": after_day,
                    "after_at": after_at,
                    "after_id": after_id,
                    "limit": limit,
                },
            )
        ).all()
        return [
            CashExportRow(
                entry_id=row.id,
                day=row.day,
                created_at=row.created_at,
                direction=str(row.direction),
                method=str(row.method),
                currency=str(row.currency),
                amount=int(row.amount),
                category_name=str(row.category_name),
                note=row.note,
                author_id=row.author_id,
                author_role=str(row.author_role),
                ledger_entry_id=row.ledger_entry_id,
                cancelled_at=row.cancelled_at,
                cancel_reason=row.cancel_reason,
            )
            for row in rows
        ]

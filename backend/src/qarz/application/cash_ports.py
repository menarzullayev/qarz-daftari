"""What the cash book asks of storage (expansion module H).

A part of `qarz.application.ports.TenantSession`, kept in a file of its own so that modules built at the
same time do not all write into one. Every method runs inside the shop's transaction and row-level
security, like the rest of a tenant session.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol
from uuid import UUID

from qarz.domain.cash import Sum


@dataclass(frozen=True)
class CashCategoryRecord:
    category_id: UUID
    direction: str
    name: str
    system_key: str | None
    archived_at: datetime | None


@dataclass(frozen=True)
class NewCashCategory:
    category_id: UUID
    direction: str
    name: str
    name_norm: str
    system_key: str | None = None


@dataclass(frozen=True)
class CashEntryRecord:
    entry_id: UUID
    direction: str
    method: str
    currency: str
    amount: int
    category_id: UUID
    category_name: str
    note: str | None
    day: date
    created_at: datetime
    author_id: UUID
    # Set on an entry the ledger wrote, with the customer whose payment it is.
    ledger_entry_id: UUID | None
    customer_id: UUID | None
    customer_name: str | None
    cancelled_at: datetime | None
    cancelled_by: UUID | None
    cancel_reason: str | None


@dataclass(frozen=True)
class CashCategorySum:
    """What the standing entries of one category add up to in one currency over a stretch of days."""

    category_id: UUID
    direction: str
    currency: str
    amount: int
    count: int


@dataclass(frozen=True)
class CashDaySum:
    day: date
    direction: str
    currency: str
    amount: int


@dataclass(frozen=True)
class CashExportRow:
    """One entry as the shop's export writes it."""

    entry_id: UUID
    day: date
    created_at: datetime
    direction: str
    method: str
    currency: str
    amount: int
    category_name: str
    note: str | None
    author_id: UUID
    author_role: str
    ledger_entry_id: UUID | None
    cancelled_at: datetime | None
    cancel_reason: str | None


class CashSession(Protocol):
    # --- categories ------------------------------------------------------------------------------------

    async def lock_cash_categories(self) -> None:
        """Let one request at a time change the shop's categories, until the transaction ends."""
        ...

    async def cash_categories(self) -> list[CashCategoryRecord]:
        """Every category of the shop: income first, the ones in use before the archived, by name."""
        ...

    async def cash_category(self, category_id: UUID, *, lock: str | None = None) -> CashCategoryRecord | None:
        """One category. `lock` is "share" for a writer of an entry under it (the category cannot be
        archived or deleted until that write ends) and "update" for a change of the category itself."""
        ...

    async def cash_system_category(self, system_key: str) -> CashCategoryRecord | None: ...

    async def cash_category_named(self, direction: str, name_norm: str) -> UUID | None: ...

    async def add_cash_categories(self, categories: list[NewCashCategory], now: datetime) -> None:
        """Add the categories that are not there yet (by system key, then by name). Never raises for one
        that is."""
        ...

    async def rename_cash_category(self, category_id: UUID, name: str, name_norm: str) -> None: ...

    async def set_cash_category_archived(self, category_id: UUID, at: datetime | None) -> None: ...

    async def cash_category_used(self, category_id: UUID) -> bool:
        """Whether any entry, cancelled or not, is under the category."""
        ...

    async def delete_cash_category(self, category_id: UUID) -> None: ...

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
    ) -> None: ...

    async def cash_entry(self, entry_id: UUID, *, for_update: bool = False) -> CashEntryRecord | None: ...

    async def cancel_cash_entry(self, entry_id: UUID, *, by: UUID, reason: str, now: datetime) -> bool:
        """Cancel an entry that still stands. False when it was cancelled already."""
        ...

    async def cancel_cash_entry_of_payment(self, ledger_entry_id: UUID, *, by: UUID, now: datetime) -> bool:
        """Cancel the standing entry the ledger wrote for this payment, if there is one."""
        ...

    async def cash_entries_of_day(
        self, day: date, *, after: tuple[datetime, UUID] | None, limit: int
    ) -> list[CashEntryRecord]:
        """A page of one day's entries, cancelled ones included, newest first. `after` is the last row of
        the page before."""
        ...

    async def cash_sums(self, *, first: date | None, before: date | None) -> list[Sum]:
        """The standing entries dated from `first` up to but not including `before`, added up by method,
        currency and direction. An end left out is open."""
        ...

    async def cash_category_sums(self, *, first: date, before: date) -> list[CashCategorySum]: ...

    async def cash_day_sums(self, *, first: date, before: date) -> list[CashDaySum]: ...

    async def backfill_cash_payments(self, *, category_id: UUID, since: date | None, now: datetime) -> int:
        """Write a cash entry for every payment of the ledger that stands and has none, dated the Tashkent
        day it was recorded, paid in cash. Returns how many it wrote."""
        ...

    async def cash_entries_exist(self) -> bool: ...

    async def export_cash_entries(
        self, *, until: datetime, after: tuple[date, datetime, UUID] | None, limit: int
    ) -> list[CashExportRow]:
        """A page of the shop's entries written up to `until`, by day and then by when they were written."""
        ...

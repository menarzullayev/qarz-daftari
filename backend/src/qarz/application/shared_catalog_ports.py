"""What the shared catalogue asks of storage (the founder's decisions of 2026-10-10).

In a file of its own, like the stock's and the cash book's: `TenantSession` and `PlatformSession`
inherit the two protocols below.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True)
class SharedItem:
    """An item of the platform's catalogue. Nothing in it belongs to a shop."""

    item_id: UUID
    name_ru: str | None
    name_uz: str | None
    search_norm: str
    category: str
    subcategory: str | None
    amount: str | None
    unit: str
    price_hint: int | None
    image_key: str | None


@dataclass(frozen=True)
class PickedItem:
    """The shop's own item that was picked from the catalogue."""

    item_id: UUID
    name: str
    unit: str
    price: int
    status: str


@dataclass(frozen=True)
class SuggestionRow:
    """A suggestion as the administrators' queue shows it: never which shop it came from."""

    suggestion_id: UUID
    kind: str
    name: str | None
    unit: str | None
    barcode: str | None
    shared_item_id: UUID | None
    item_name_ru: str | None
    item_name_uz: str | None
    item_amount: str | None
    status: str
    created_at: datetime
    decided_at: datetime | None
    same: int


@dataclass(frozen=True)
class Decision:
    outcome: str
    kind: str | None
    shared_item_id: UUID | None


@dataclass(frozen=True)
class SeedItem:
    """A row of the seed file, checked, as the import stores it."""

    source_key: str
    name_ru: str | None
    name_uz: str | None
    search_norm: str
    category: str
    subcategory: str | None
    amount: str | None
    price_hint: int | None
    image_key: str | None


class SharedCatalogSession(Protocol):
    """The catalogue from inside a shop's transaction: read it, pick from it, propose for it."""

    async def shared_items(
        self, *, terms: list[str], category: str | None, after: tuple[str, UUID] | None, limit: int
    ) -> list[SharedItem]:
        """A page of shown items whose names hold every term, by name."""
        ...

    async def shared_item(self, item_id: UUID) -> SharedItem | None: ...

    async def shared_item_by_barcode(self, code: str) -> SharedItem | None: ...

    async def shared_barcodes(self, item_id: UUID) -> list[str]: ...

    async def picked_items(self, shared_ids: list[UUID]) -> dict[UUID, PickedItem]:
        """The shop's own items that were picked from these catalogue items."""
        ...

    async def insert_picked_item(
        self, *, item_id: UUID, shared_item_id: UUID, name: str, name_norm: str, unit: str, price: int
    ) -> PickedItem | None:
        """Add the shop's item for a catalogue item. None when the shop already has an item of that name,
        or already holds this catalogue item."""
        ...

    async def shared_link_of(self, item_id: UUID) -> UUID | None:
        """The catalogue item the shop's item was picked from, if it was."""
        ...

    async def add_free_barcodes(self, item_id: UUID, codes: list[str]) -> None:
        """Give the item those of the codes that no item of the shop has yet."""
        ...

    async def suggest_shared_item(
        self,
        *,
        suggestion_id: UUID,
        item_id: UUID,
        name: str,
        name_norm: str,
        unit: str,
        barcode: str | None,
        max_pending: int,
    ) -> bool:
        """Propose the shop's item for the catalogue. False when it proposed that name before, or too
        many of its proposals wait."""
        ...

    async def suggest_shared_barcode(
        self, *, suggestion_id: UUID, item_id: UUID, shared_item_id: UUID, barcode: str, max_pending: int
    ) -> bool:
        """Propose a barcode for a catalogue item. False when the catalogue already has the code, the
        shop proposed it before, or too many of its proposals wait."""
        ...


class SharedCatalogAdminSession(Protocol):
    """The catalogue on the administrators' side: the queue of suggestions and the import."""

    async def admin_shared_suggestions(
        self, admin_id: UUID, *, status: str, after: tuple[datetime, UUID] | None, limit: int
    ) -> list[SuggestionRow]: ...

    async def admin_shared_decide(
        self,
        admin_id: UUID,
        suggestion_id: UUID,
        *,
        approve: bool,
        item_id: UUID,
        name_ru: str | None,
        name_uz: str | None,
        search_norm: str | None,
        category: str | None,
        now: datetime,
    ) -> Decision: ...

    async def shared_image_keys(self) -> dict[str, str | None]:
        """Every seeded item's key in the seed file with the photo it has now."""
        ...

    async def upsert_seed_items(self, items: list[SeedItem]) -> int:
        """Store these rows by their seed key: new ones are added, known ones brought up to date. A photo
        an item already has is kept when the row brings none. Returns how many were new."""
        ...

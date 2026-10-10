"""PostgreSQL storage of the shared catalogue (migration 0050).

`PgTenantSession` inherits `SharedCatalogQueries` and `PgPlatformSession` inherits
`SharedCatalogAdminQueries`. SQL here is composed only from the module-level constants below, with every
value bound as a parameter (tests/test_sql_composition.py).
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from qarz.application.shared_catalog_ports import Decision, PickedItem, SeedItem, SharedItem, SuggestionRow
from qarz.domain.shared_catalog import MAX_TERMS

_ITEM_COLUMNS = (
    "s.id, s.name_ru, s.name_uz, s.search_norm, s.category, s.subcategory, s.amount, s.unit, s.price_hint, s.image_key"
)
_ITEM_BY_ID = f"SELECT {_ITEM_COLUMNS} FROM shared_item s WHERE s.id = :id AND s.status = 'active'"
_ITEM_BY_BARCODE = (
    f"SELECT {_ITEM_COLUMNS} FROM shared_barcode b JOIN shared_item s ON s.id = b.item_id "
    "WHERE b.code = :code AND s.status = 'active'"
)
_PAGE = (
    "AND (CAST(:category AS text) IS NULL OR s.category = CAST(:category AS text)) "
    "AND (CAST(:after_name AS text) IS NULL "
    "     OR (s.search_norm, s.id) > (CAST(:after_name AS text), CAST(:after_id AS uuid))) "
    "ORDER BY s.search_norm, s.id LIMIT :limit"
)
_SEARCH = f"SELECT {_ITEM_COLUMNS} FROM shared_item s WHERE s.status = 'active' "
_TERM_0 = "AND s.search_norm LIKE :term0 "
_TERM_1 = "AND s.search_norm LIKE :term1 "
_TERM_2 = "AND s.search_norm LIKE :term2 "
_TERM_3 = "AND s.search_norm LIKE :term3 "
# One statement for each number of typed words, each a plain LIKE the trigram index can answer.
_SEARCHES = (
    _SEARCH + _PAGE,
    _SEARCH + _TERM_0 + _PAGE,
    _SEARCH + _TERM_0 + _TERM_1 + _PAGE,
    _SEARCH + _TERM_0 + _TERM_1 + _TERM_2 + _PAGE,
    _SEARCH + _TERM_0 + _TERM_1 + _TERM_2 + _TERM_3 + _PAGE,
)
assert len(_SEARCHES) == MAX_TERMS + 1
_PICKED_COLUMNS = "i.id, i.shared_item_id, i.name, i.unit, i.price, i.status"
# The proposals of the shop that still wait: row-level security leaves no other shop's to count.
_ROOM = "(SELECT count(*) FROM shared_suggestion w WHERE w.status = 'pending') < :max_pending"

_SEED_COLUMNS = "source_key, name_ru, name_uz, search_norm, category, subcategory, amount, price_hint, image_key"
_SEED_UPSERT = (
    f"INSERT INTO shared_item AS s (id, {_SEED_COLUMNS}) "
    "SELECT gen_random_uuid(), r.source_key, r.name_ru, r.name_uz, r.search_norm, r.category, r.subcategory, "
    "       r.amount, r.price_hint, r.image_key "
    "FROM unnest(CAST(:keys AS text[]), CAST(:names_ru AS text[]), CAST(:names_uz AS text[]), "
    "            CAST(:norms AS text[]), CAST(:categories AS text[]), CAST(:subcategories AS text[]), "
    "            CAST(:amounts AS text[]), CAST(:hints AS bigint[]), CAST(:images AS text[])) "
    f"  AS r({_SEED_COLUMNS}) "
    "ON CONFLICT (source_key) DO UPDATE SET name_ru = EXCLUDED.name_ru, name_uz = EXCLUDED.name_uz, "
    "  search_norm = EXCLUDED.search_norm, category = EXCLUDED.category, subcategory = EXCLUDED.subcategory, "
    "  amount = EXCLUDED.amount, price_hint = EXCLUDED.price_hint, "
    "  image_key = coalesce(EXCLUDED.image_key, s.image_key) "
    "RETURNING (xmax = 0) AS added"
)


def _like(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _item(row: Any) -> SharedItem:
    return SharedItem(
        row.id,
        row.name_ru,
        row.name_uz,
        row.search_norm,
        row.category,
        row.subcategory,
        row.amount,
        row.unit,
        None if row.price_hint is None else int(row.price_hint),
        row.image_key,
    )


def _picked(row: Any) -> PickedItem:
    return PickedItem(row.id, row.name, row.unit, int(row.price), row.status)


class SharedCatalogQueries:
    _conn: AsyncConnection
    _shop_id: UUID

    async def shared_items(
        self, *, terms: list[str], category: str | None, after: tuple[str, UUID] | None, limit: int
    ) -> list[SharedItem]:
        terms = terms[:MAX_TERMS]
        values: dict[str, Any] = {
            "category": category,
            "after_name": after[0] if after else None,
            "after_id": after[1] if after else None,
            "limit": limit,
        }
        values.update({"term" + str(place): _like(term) for place, term in enumerate(terms)})
        rows = (await self._conn.execute(text(_SEARCHES[len(terms)]), values)).all()
        return [_item(row) for row in rows]

    async def shared_item(self, item_id: UUID) -> SharedItem | None:
        row = (await self._conn.execute(text(_ITEM_BY_ID), {"id": item_id})).first()
        return None if row is None else _item(row)

    async def shared_item_by_barcode(self, code: str) -> SharedItem | None:
        row = (await self._conn.execute(text(_ITEM_BY_BARCODE), {"code": code})).first()
        return None if row is None else _item(row)

    async def shared_barcodes(self, item_id: UUID) -> list[str]:
        rows = (
            await self._conn.execute(
                text("SELECT b.code FROM shared_barcode b WHERE b.item_id = :id ORDER BY b.created_at, b.code"),
                {"id": item_id},
            )
        ).all()
        return [str(row.code) for row in rows]

    async def picked_items(self, shared_ids: list[UUID]) -> dict[UUID, PickedItem]:
        if not shared_ids:
            return {}
        rows = (
            await self._conn.execute(
                text(
                    f"SELECT {_PICKED_COLUMNS} FROM catalog_item i "
                    "WHERE i.shop_id = :shop_id AND i.shared_item_id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"shop_id": self._shop_id, "ids": shared_ids},
            )
        ).all()
        return {row.shared_item_id: _picked(row) for row in rows}

    async def insert_picked_item(
        self, *, item_id: UUID, shared_item_id: UUID, name: str, name_norm: str, unit: str, price: int
    ) -> PickedItem | None:
        try:
            # A savepoint, so that a name or a catalogue item the shop already has leaves the
            # transaction usable.
            async with self._conn.begin_nested():
                row = (
                    await self._conn.execute(
                        text(
                            "INSERT INTO catalog_item AS i (id, shop_id, name, name_norm, unit, price, shared_item_id) "
                            "VALUES (:id, :shop_id, :name, :norm, :unit, :price, :shared) "
                            f"RETURNING {_PICKED_COLUMNS}"
                        ),
                        {
                            "id": item_id,
                            "shop_id": self._shop_id,
                            "name": name,
                            "norm": name_norm,
                            "unit": unit,
                            "price": price,
                            "shared": shared_item_id,
                        },
                    )
                ).one()
        except IntegrityError as error:
            if "catalog_item_shop_id_name_norm_key" in str(error.orig) or "catalog_item_shared_once" in str(error.orig):
                return None
            raise
        return _picked(row)

    async def shared_link_of(self, item_id: UUID) -> UUID | None:
        row = (
            await self._conn.execute(
                text("SELECT i.shared_item_id FROM catalog_item i WHERE i.shop_id = :shop_id AND i.id = :id"),
                {"shop_id": self._shop_id, "id": item_id},
            )
        ).first()
        return None if row is None else row.shared_item_id

    async def add_free_barcodes(self, item_id: UUID, codes: list[str]) -> None:
        if not codes:
            return
        await self._conn.execute(
            text(
                "INSERT INTO catalog_barcode (shop_id, code, item_id, created_at) "
                "SELECT :shop_id, c.code, :item, now() + c.place * interval '1 microsecond' "
                "FROM unnest(CAST(:codes AS text[])) WITH ORDINALITY AS c(code, place) "
                "ON CONFLICT (shop_id, code) DO NOTHING"
            ),
            {"shop_id": self._shop_id, "item": item_id, "codes": codes},
        )

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
        result = await self._conn.execute(
            text(
                "INSERT INTO shared_suggestion (id, shop_id, kind, name, name_norm, unit, barcode, item_id) "
                "SELECT :id, :shop_id, 'item', :name, :norm, :unit, CAST(:barcode AS text), :item "
                f"WHERE {_ROOM} "
                "ON CONFLICT (shop_id, name_norm) WHERE kind = 'item' DO NOTHING"
            ),
            {
                "id": suggestion_id,
                "shop_id": self._shop_id,
                "name": name,
                "norm": name_norm,
                "unit": unit,
                "barcode": barcode,
                "item": item_id,
                "max_pending": max_pending,
            },
        )
        return int(result.rowcount) == 1

    async def suggest_shared_barcode(
        self, *, suggestion_id: UUID, item_id: UUID, shared_item_id: UUID, barcode: str, max_pending: int
    ) -> bool:
        result = await self._conn.execute(
            text(
                "INSERT INTO shared_suggestion (id, shop_id, kind, barcode, shared_item_id, item_id) "
                "SELECT :id, :shop_id, 'barcode', :barcode, :shared, :item "
                f"WHERE {_ROOM} AND NOT EXISTS (SELECT 1 FROM shared_barcode b WHERE b.code = :barcode) "
                "ON CONFLICT (shop_id, barcode, shared_item_id) WHERE kind = 'barcode' DO NOTHING"
            ),
            {
                "id": suggestion_id,
                "shop_id": self._shop_id,
                "barcode": barcode,
                "shared": shared_item_id,
                "item": item_id,
                "max_pending": max_pending,
            },
        )
        return int(result.rowcount) == 1


class SharedCatalogAdminQueries:
    _conn: AsyncConnection

    async def admin_shared_suggestions(
        self, admin_id: UUID, *, status: str, after: tuple[datetime, UUID] | None, limit: int
    ) -> list[SuggestionRow]:
        rows = (
            await self._conn.execute(
                text(
                    "SELECT suggestion_id, kind, name, unit, barcode, shared_item_id, item_name_ru, item_name_uz, "
                    "item_amount, status, created_at, decided_at, same FROM admin_shared_suggestions(:admin, :status, "
                    "CAST(:after_created AS timestamptz), CAST(:after_id AS uuid), :limit)"
                ),
                {
                    "admin": admin_id,
                    "status": status,
                    "after_created": after[0] if after else None,
                    "after_id": after[1] if after else None,
                    "limit": limit,
                },
            )
        ).all()
        return [
            SuggestionRow(
                row.suggestion_id,
                row.kind,
                row.name,
                row.unit,
                row.barcode,
                row.shared_item_id,
                row.item_name_ru,
                row.item_name_uz,
                row.item_amount,
                row.status,
                row.created_at,
                row.decided_at,
                int(row.same),
            )
            for row in rows
        ]

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
    ) -> Decision:
        row = (
            await self._conn.execute(
                text(
                    "SELECT outcome, kind, shared_item_id FROM admin_shared_decide(:admin, :suggestion, :approve, "
                    ":item, CAST(:name_ru AS text), CAST(:name_uz AS text), CAST(:norm AS text), "
                    "CAST(:category AS text), :now)"
                ),
                {
                    "admin": admin_id,
                    "suggestion": suggestion_id,
                    "approve": approve,
                    "item": item_id,
                    "name_ru": name_ru,
                    "name_uz": name_uz,
                    "norm": search_norm,
                    "category": category,
                    "now": now,
                },
            )
        ).one()
        return Decision(row.outcome, row.kind, row.shared_item_id)

    async def shared_image_keys(self) -> dict[str, str | None]:
        rows = (
            await self._conn.execute(
                text("SELECT s.source_key, s.image_key FROM shared_item s WHERE s.source_key IS NOT NULL")
            )
        ).all()
        return {str(row.source_key): row.image_key for row in rows}

    async def upsert_seed_items(self, items: list[SeedItem]) -> int:
        if not items:
            return 0
        rows = (
            await self._conn.execute(
                text(_SEED_UPSERT),
                {
                    "keys": [item.source_key for item in items],
                    "names_ru": [item.name_ru for item in items],
                    "names_uz": [item.name_uz for item in items],
                    "norms": [item.search_norm for item in items],
                    "categories": [item.category for item in items],
                    "subcategories": [item.subcategory for item in items],
                    "amounts": [item.amount for item in items],
                    "hints": [item.price_hint for item in items],
                    "images": [item.image_key for item in items],
                },
            )
        ).all()
        return sum(1 for row in rows if row.added)

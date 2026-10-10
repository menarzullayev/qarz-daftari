"""The shared product catalogue: search it, pick from it, propose for it, and the administrators' queue
(the founder's seven decisions of 2026-10-10).

Everything here is behind the platform switch `catalog_on`: while it is off every operation answers as a
route that does not exist, and the two hooks the shop's own catalogue and the stock call
(`qarz.application.shared_catalog_feed`) do nothing.

What a shop adds by hand works in the shop at once. It reaches the catalogue only when an administrator
approves it, and the proposal carries the item's name, unit and barcode and nothing else of the shop:
no price, no quantity, and the queue is not told which shop it came from. The approximate price the
catalogue holds is advice a client shows under the price field; nothing here ever writes it to a shop.
"""

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

from qarz.application import idempotency
from qarz.application.admin import MAX_PAGE as ADMIN_MAX_PAGE
from qarz.application.admin import _decode_cursor, _encode_cursor, _iso
from qarz.application.admin_access import AdminRequestKeys
from qarz.application.catalog import CatalogNameTaken, item_body
from qarz.application.customers import MAX_PAGE, decode_cursor, encode_cursor, require_viewable, require_writable
from qarz.application.errors import AppError, NotFound, ValidationFailed
from qarz.application.files import FileStoreUnavailable
from qarz.application.operations import admin_operation, operation
from qarz.application.ports import FileMissing, FileStore, FileStoreError, Storage
from qarz.application.shared_catalog_feed import require_on, switched_on
from qarz.application.shared_catalog_ports import PickedItem, SharedItem, SuggestionRow
from qarz.application.shops import require_member
from qarz.domain import shared_catalog, stock
from qarz.domain.access import Capability
from qarz.domain.catalog import check_price, normalize_unit
from qarz.domain.files import sniff
from qarz.domain.names import normalize_name
from qarz.domain.promise import tashkent_date

SEARCH_SHARED = operation("catalog.shared.search", Capability.MANAGE)
LOOKUP_SHARED = operation("catalog.shared.lookup", Capability.MANAGE)
PICK_SHARED = operation("catalog.shared.pick", Capability.MANAGE)
LIST_SUGGESTIONS = admin_operation("admin.catalog.suggestions.list")
APPROVE_SUGGESTION = admin_operation("admin.catalog.suggestions.approve")
REJECT_SUGGESTION = admin_operation("admin.catalog.suggestions.reject")

# Where a photo of the catalogue is served, beside the signed links of the shops' own files.
IMAGE_PATH = "/files/catalog"


class SuggestionAlreadyDecided(AppError):
    """The suggestion was approved or rejected before. Nothing was changed."""

    code = "SUGGESTION_ALREADY_DECIDED"


class SharedBarcodeTaken(AppError):
    """The barcode already names another item of the catalogue. Nothing was changed."""

    code = "SHARED_BARCODE_TAKEN"


def shared_body(item: SharedItem, lang: str, picked: PickedItem | None) -> dict[str, Any]:
    return {
        "id": str(item.item_id),
        # In the reader's language, the other name standing in for a missing one.
        "name": shared_catalog.display_name(lang, item.name_ru, item.name_uz),
        "name_ru": item.name_ru,
        "name_uz": item.name_uz,
        "amount": item.amount,
        "category": item.category,
        "subcategory": item.subcategory,
        "unit": item.unit,
        # Advice for the price field, in so'm; null when the catalogue has none.
        "price_hint": item.price_hint,
        "image": None if item.image_key is None else f"{IMAGE_PATH}/{item.image_key}",
        # The shop's own item, when it has picked this one already.
        "picked": None
        if picked is None
        else {"id": str(picked.item_id), "name": picked.name, "price": picked.price, "status": picked.status},
    }


def suggestion_body(row: SuggestionRow) -> dict[str, Any]:
    return {
        "id": str(row.suggestion_id),
        "kind": row.kind,
        "name": row.name,
        "unit": row.unit,
        "barcode": row.barcode,
        # A barcode: the catalogue item it is proposed for. An approved item: the item it became.
        "shared_item": None
        if row.shared_item_id is None
        else {
            "id": str(row.shared_item_id),
            "name_ru": row.item_name_ru,
            "name_uz": row.item_name_uz,
            "amount": row.item_amount,
        },
        "status": row.status,
        "created_at": row.created_at.isoformat(),
        "decided_at": _iso(row.decided_at),
        # How many other shops wait with the same name, or the same barcode. Never which shops.
        "same": row.same,
    }


class SharedCatalogService:
    def __init__(
        self, storage: Storage, store: FileStore | None = None, now: Callable[[], datetime] | None = None
    ) -> None:
        self._storage = storage
        self._store = store
        self._now = now or (lambda: datetime.now(UTC))

    def _today(self) -> date:
        return tashkent_date(self._now())

    async def require_on(self) -> None:
        """Called for every route of the catalogue before anything else."""
        async with self._storage.platform() as session:
            await require_on(session)

    async def switched_on(self) -> bool:
        async with self._storage.platform() as session:
            return await switched_on(session)

    async def search(
        self,
        user_id: UUID,
        shop_id: UUID,
        *,
        query: str | None,
        category: str | None,
        cursor: str | None,
        limit: int,
        lang: str,
    ) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, SEARCH_SHARED)
            await require_viewable(session, actor, self._today())
            fields: dict[str, str] = {}
            terms: list[str] = []
            if not 1 <= limit <= MAX_PAGE:
                fields["limit"] = f"must be between 1 and {MAX_PAGE}"
            if category is not None and category not in shared_catalog.CATEGORIES:
                fields["category"] = "must be one of the catalogue's categories"
            try:
                terms = shared_catalog.search_terms(query)
            except ValueError as error:
                fields["q"] = str(error)
            if fields:
                raise ValidationFailed(fields)
            after: tuple[str, UUID] | None = None
            if cursor:
                name, item_id = decode_cursor(cursor, 2)
                try:
                    after = (name, UUID(item_id))
                except ValueError as error:
                    raise ValidationFailed({"cursor": "not a cursor returned by this API"}) from error
            rows = await session.shared_items(terms=terms, category=category, after=after, limit=limit + 1)
            page, more = rows[:limit], len(rows) > limit
            picked = await session.picked_items([item.item_id for item in page])
            return {
                "items": [shared_body(item, lang, picked.get(item.item_id)) for item in page],
                "next_cursor": encode_cursor(page[-1].search_norm, page[-1].item_id) if more else None,
                "categories": list(shared_catalog.CATEGORIES),
            }

    async def lookup(self, user_id: UUID, shop_id: UUID, code: str, *, lang: str) -> dict[str, Any]:
        """The catalogue item an approved barcode names: what a scan falls through to when no item of
        the shop has the code. An unknown code is "not found"."""
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, LOOKUP_SHARED)
            await require_viewable(session, actor, self._today())
            try:
                clean = stock.barcode(code)
            except ValueError as error:
                raise ValidationFailed({"code": str(error)}) from error
            item = await session.shared_item_by_barcode(clean)
            if item is None:
                raise NotFound()
            picked = await session.picked_items([item.item_id])
            return shared_body(item, lang, picked.get(item.item_id))

    async def pick(
        self,
        user_id: UUID,
        shop_id: UUID,
        shared_item_id: UUID,
        *,
        price: int,
        unit: str | None,
        lang: str,
        request_key: str | None,
    ) -> dict[str, Any]:
        """Make a catalogue item an item of the shop, at the shop's own price.

        The shop's item is named in the caller's language with the package size, and gets the barcodes
        the catalogue has for it. Picking what the shop already holds answers with the item it has,
        unchanged: nothing is added twice. A name another item of the shop already has is refused as
        for an item typed by hand.
        """
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, PICK_SHARED)
            key = idempotency.validate_key(request_key)
            fields: dict[str, str] = {}
            clean_unit: str | None = None
            try:
                check_price(price)
            except ValueError as error:
                fields["price"] = str(error)
            if unit is not None:
                try:
                    clean_unit = normalize_unit(unit)
                except ValueError as error:
                    fields["unit"] = str(error)
            if fields:
                raise ValidationFailed(fields)
            await require_writable(session, self._today(), new_credit=False)

            async def apply() -> dict[str, Any]:
                shared = await session.shared_item(shared_item_id)
                if shared is None:
                    raise NotFound()
                held = (await session.picked_items([shared_item_id])).get(shared_item_id)
                if held is None:
                    try:
                        name = shared_catalog.picked_name(
                            shared_catalog.display_name(lang, shared.name_ru, shared.name_uz), shared.amount
                        )
                    except ValueError as error:
                        raise ValidationFailed({"name": str(error)}) from error
                    name_norm = normalize_name(name)
                    held = await session.insert_picked_item(
                        item_id=uuid4(),
                        shared_item_id=shared_item_id,
                        name=name,
                        name_norm=name_norm,
                        unit=clean_unit or shared.unit,
                        price=price,
                    )
                    if held is None:
                        # Either the name is another item's, or the same pick landed a moment ago.
                        held = (await session.picked_items([shared_item_id])).get(shared_item_id)
                        if held is None:
                            existing = await session.catalog_item_by_norm(name_norm)
                            raise CatalogNameTaken(
                                {}
                                if existing is None
                                else {"existing_id": str(existing.item_id), "existing_status": existing.status}
                            )
                    else:
                        await session.add_free_barcodes(held.item_id, await session.shared_barcodes(shared_item_id))
                        await session.record_activity(
                            membership_id=actor.membership_id,
                            action="catalog.item.picked",
                            subject_type="catalog_item",
                            subject_id=held.item_id,
                        )
                item = await session.get_catalog_item(held.item_id, for_update=False)
                if item is None:
                    raise NotFound()
                return item_body(item)

            return await idempotency.run_once(
                session,
                key=key,
                operation=PICK_SHARED.name,
                user_id=user_id,
                request={"shared": str(shared_item_id), "price": price, "unit": clean_unit},
                action=apply,
            )

    async def image(self, key: str) -> tuple[str, bytes]:
        """Type and content of a photo of the catalogue, for whoever asks: a photo of a product is
        nobody's data. Not found while the catalogue is off, for a key that is not one, and for content
        that is not the image its key names."""
        if not shared_catalog.is_image_key(key):
            raise NotFound()
        await self.require_on()
        if self._store is None:
            raise FileStoreUnavailable()
        try:
            data = await self._store.get(shared_catalog.image_object_key(key))
        except FileMissing:
            raise NotFound() from None
        except FileStoreError:
            raise FileStoreUnavailable() from None
        mime = sniff(data)
        if mime not in shared_catalog.IMAGE_TYPES:
            raise NotFound()
        return mime, data


class AdminSharedCatalogService:
    """The queue of what shops propose. Every method assumes the caller passed `AdminAccess.require_admin`."""

    def __init__(self, storage: Storage, now: Callable[[], datetime] | None = None) -> None:
        self._storage = storage
        self._now = now or (lambda: datetime.now(UTC))

    async def require_on(self) -> None:
        async with self._storage.platform() as session:
            await require_on(session)

    async def list_suggestions(
        self, admin_id: UUID, *, status: str | None, cursor: str | None, limit: int
    ) -> dict[str, Any]:
        """Suggestions of one status across all shops, oldest first; those that wait by default."""
        fields: dict[str, str] = {}
        if not 1 <= limit <= ADMIN_MAX_PAGE:
            fields["limit"] = f"must be between 1 and {ADMIN_MAX_PAGE}"
        wanted = shared_catalog.PENDING if status is None else status
        if wanted not in shared_catalog.STATUSES:
            fields["status"] = "must be pending, approved or rejected"
        if fields:
            raise ValidationFailed(fields)
        after = _decode_cursor(cursor) if cursor else None
        async with self._storage.platform() as session:
            await require_on(session)
            rows = await session.admin_shared_suggestions(admin_id, status=wanted, after=after, limit=limit + 1)
        page, more = rows[:limit], len(rows) > limit
        return {
            "items": [suggestion_body(row) for row in page],
            "next_cursor": _encode_cursor(page[-1].created_at, page[-1].suggestion_id) if more else None,
            "categories": list(shared_catalog.CATEGORIES),
        }

    async def _decide(
        self,
        admin_id: UUID,
        suggestion_id: UUID,
        request_key: str | None,
        *,
        approve: bool,
        name_ru: str | None = None,
        name_uz: str | None = None,
        category: str | None = None,
    ) -> dict[str, Any]:
        key = idempotency.validate_key(request_key)
        now = self._now()
        op = APPROVE_SUGGESTION if approve else REJECT_SUGGESTION
        async with self._storage.platform() as session:
            await require_on(session)

            async def apply() -> dict[str, Any]:
                decision = await session.admin_shared_decide(
                    admin_id,
                    suggestion_id,
                    approve=approve,
                    item_id=uuid4(),
                    name_ru=name_ru,
                    name_uz=name_uz,
                    search_norm=shared_catalog.search_norm(name_ru, name_uz, None) or None,
                    category=category,
                    now=now,
                )
                if decision.outcome == "missing":
                    raise NotFound()
                if decision.outcome == "decided":
                    raise SuggestionAlreadyDecided()
                if decision.outcome == "barcode_taken":
                    raise SharedBarcodeTaken()
                if decision.outcome == "unnamed":
                    raise ValidationFailed({"name_uz": "an item needs a Russian or an Uzbek name"})
                await session.add_admin_audit(
                    admin_id=admin_id,
                    action=f"catalog.suggestion_{decision.outcome}",
                    target_type="suggestion",
                    target_id=str(suggestion_id),
                    # Not tied to a shop: the audit, like the queue, does not say where it came from.
                    shop_id=None,
                    reason=None,
                    detail={
                        "kind": decision.kind,
                        "shared_item": None if decision.shared_item_id is None else str(decision.shared_item_id),
                    },
                    now=now,
                )
                return {
                    "id": str(suggestion_id),
                    "kind": decision.kind,
                    "status": decision.outcome,
                    "shared_item_id": None if decision.shared_item_id is None else str(decision.shared_item_id),
                }

            return await idempotency.run_once(
                AdminRequestKeys(session, admin_id),
                key=key,
                operation=op.name,
                user_id=admin_id,
                request={
                    "suggestion": str(suggestion_id),
                    "name_ru": name_ru,
                    "name_uz": name_uz,
                    "category": category,
                },
                action=apply,
            )

    async def approve(
        self,
        admin_id: UUID,
        suggestion_id: UUID,
        *,
        name_ru: str | None,
        name_uz: str | None,
        category: str | None,
        request_key: str | None,
    ) -> dict[str, Any]:
        """Approve a waiting suggestion.

        For an item the administrator gives the names it gets in the catalogue (at least one of the
        Russian and the Uzbek) and its category; a barcode needs none of them and they are not read.
        """
        fields: dict[str, str] = {}
        clean: dict[str, str | None] = {"name_ru": None, "name_uz": None}
        for field, raw in (("name_ru", name_ru), ("name_uz", name_uz)):
            try:
                clean[field] = shared_catalog.shared_name(raw)
            except ValueError as error:
                fields[field] = str(error)
        try:
            clean_category = shared_catalog.category(category)
        except ValueError as error:
            fields["category"] = str(error)
            clean_category = shared_catalog.OTHER
        if fields:
            raise ValidationFailed(fields)
        return await self._decide(
            admin_id,
            suggestion_id,
            request_key,
            approve=True,
            name_ru=clean["name_ru"],
            name_uz=clean["name_uz"],
            category=clean_category,
        )

    async def reject(self, admin_id: UUID, suggestion_id: UUID, request_key: str | None) -> dict[str, Any]:
        """Reject a waiting suggestion. The shop's own item is untouched and the shop is not told."""
        return await self._decide(admin_id, suggestion_id, request_key, approve=False)

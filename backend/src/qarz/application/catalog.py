"""The shop's catalog of goods and the review of learned items (REQ-039, REQ-040, REQ-041).

The catalog holds names, units and current prices only. A goods line keeps its own name and price, so
nothing done here can alter a saved entry (INV-17).
"""

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

from qarz.application import idempotency
from qarz.application.customers import MAX_PAGE, decode_cursor, encode_cursor, require_viewable, require_writable
from qarz.application.errors import AppError, NotFound, ValidationFailed
from qarz.application.operations import Operation, operation
from qarz.application.ports import CatalogItemRecord, Membership, Storage, TenantSession
from qarz.application.shops import require_member
from qarz.domain.access import Capability
from qarz.domain.catalog import check_price, item_name, normalize_unit
from qarz.domain.names import normalize_name
from qarz.domain.promise import tashkent_date
from qarz.domain.stock import UNIT_KEYS

LIST_CATALOG = operation("catalog.list", Capability.RECORD)
CREATE_ITEM = operation("catalog.create", Capability.MANAGE)
UPDATE_ITEM = operation("catalog.update", Capability.MANAGE)
HIDE_ITEM = operation("catalog.hide", Capability.MANAGE)
UNHIDE_ITEM = operation("catalog.unhide", Capability.MANAGE)
ACCEPT_LEARNED = operation("catalog.learned.accept", Capability.MANAGE)
DISMISS_LEARNED = operation("catalog.learned.dismiss", Capability.MANAGE)
MERGE_LEARNED = operation("catalog.learned.merge", Capability.MANAGE)


class CatalogNameTaken(AppError):
    """Another item of the shop, shown or hidden, has the same name after normalization."""

    code = "CATALOG_NAME_TAKEN"


class CatalogItemNotLearned(AppError):
    """Only an item still flagged as learned can be accepted, merged or dismissed."""

    code = "CATALOG_ITEM_NOT_LEARNED"


class CatalogMergeTargetInvalid(AppError):
    """A learned item can be merged only into a shown item that has itself been reviewed."""

    code = "CATALOG_MERGE_TARGET_INVALID"


def item_body(item: CatalogItemRecord) -> dict[str, Any]:
    return {
        "id": str(item.item_id),
        "name": item.name,
        "unit": item.unit,
        "price": item.price,
        "learned": item.learned,
        "status": item.status,
        "merged_into": None if item.merged_into is None else str(item.merged_into),
    }


def _clean(*, name: str | None, unit: str | None, price: int | None, unit_given: bool) -> tuple[Any, Any, Any]:
    """Validated name, unit and price; a part that was not given stays None."""
    fields: dict[str, str] = {}
    cleaned: list[Any] = [None, None, None]
    checks: tuple[tuple[str, bool, Callable[[], Any]], ...] = (
        ("name", name is not None, lambda: item_name(name or "")),
        ("unit", unit_given, lambda: normalize_unit(unit)),
        ("price", price is not None, lambda: check_price(price)),  # type: ignore[arg-type]
    )
    for position, (field, given, check) in enumerate(checks):
        if not given:
            continue
        try:
            cleaned[position] = check()
        except ValueError as error:
            fields[field] = str(error)
    if fields:
        raise ValidationFailed(fields)
    return cleaned[0], cleaned[1], cleaned[2]


async def _name_taken(session: TenantSession, name_norm: str) -> CatalogNameTaken:
    existing = await session.catalog_item_by_norm(name_norm)
    if existing is None:
        return CatalogNameTaken()
    return CatalogNameTaken({"existing_id": str(existing.item_id), "existing_status": existing.status})


async def register_learned(
    session: TenantSession, actor: Membership, *, name: str, unit: str | None, price: int
) -> UUID:
    """Make sure a typed good exists in the catalog and return the item a goods line should refer to (BR-6).

    For the itemized sale: call it inside the tenant transaction that writes the goods lines, after that
    operation has authorized the caller. It authorizes nothing itself, because a seller who may record a
    sale may teach the catalog a good by typing it.

    - A name the catalog does not know (after normalization) is added as a learned item with the typed
      unit and price, shown at once and flagged for a manager's review.
    - A name the catalog already knows returns that item unchanged: a typed price belongs to the line and
      never rewrites the catalog price, and a hidden or dismissed item stays hidden.
    - A name a manager merged into another item returns the item it was merged into.

    Raises ValidationFailed with the fields `name`, `unit` or `price` when the typed good is unusable.
    """
    clean_name, clean_unit, clean_price = _clean(name=name, unit=unit, price=price, unit_given=True)
    name_norm = normalize_name(clean_name)
    created = await session.insert_catalog_item(
        item_id=uuid4(), name=clean_name, name_norm=name_norm, unit=clean_unit, price=clean_price, learned=True
    )
    if created is not None:
        await session.record_activity(
            membership_id=actor.membership_id,
            action="catalog.item.learned",
            subject_type="catalog_item",
            subject_id=created.item_id,
        )
        return created.item_id
    existing = await session.catalog_item_by_norm(name_norm)
    if existing is None:
        raise RuntimeError("the catalog refused a name it does not hold")
    return existing.merged_into or existing.item_id


class CatalogService:
    def __init__(self, storage: Storage, now: Callable[[], datetime] | None = None) -> None:
        self._storage = storage
        self._now = now or (lambda: datetime.now(UTC))

    def _today(self) -> date:
        return tashkent_date(self._now())

    async def list(
        self,
        user_id: UUID,
        shop_id: UUID,
        *,
        query: str | None,
        status: str,
        learned: bool | None,
        cursor: str | None,
        limit: int,
    ) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, LIST_CATALOG)
            await require_viewable(session, actor, self._today())
            fields: dict[str, str] = {}
            if not 1 <= limit <= MAX_PAGE:
                fields["limit"] = f"must be between 1 and {MAX_PAGE}"
            if status not in ("active", "hidden"):
                fields["status"] = "must be active or hidden"
            if query is not None and len(query) > 80:
                fields["q"] = "at most 80 characters"
            if fields:
                raise ValidationFailed(fields)
            after: tuple[str, UUID] | None = None
            if cursor:
                name_norm, item_id = decode_cursor(cursor, 2)
                try:
                    after = (name_norm, UUID(item_id))
                except ValueError as error:
                    raise ValidationFailed({"cursor": "not a cursor returned by this API"}) from error

            name_part = normalize_name(query or "")
            rows = await session.search_catalog(
                name_part=name_part or None, status=status, learned=learned, after=after, limit=limit + 1
            )
            page, more = rows[:limit], len(rows) > limit
            return {
                "items": [item_body(item) for item in page],
                "next_cursor": encode_cursor(page[-1].name_norm, page[-1].item_id) if more else None,
            }

    async def create(
        self, user_id: UUID, shop_id: UUID, name: str, unit: str | None, price: int, request_key: str | None
    ) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, CREATE_ITEM)
            key = idempotency.validate_key(request_key)
            clean_name, clean_unit, clean_price = _clean(name=name, unit=unit, price=price, unit_given=True)
            await require_writable(session, self._today(), new_credit=False)

            async def apply() -> dict[str, Any]:
                name_norm = normalize_name(clean_name)
                item = await session.insert_catalog_item(
                    item_id=uuid4(),
                    name=clean_name,
                    name_norm=name_norm,
                    unit=clean_unit,
                    price=clean_price,
                    learned=False,
                )
                if item is None:
                    raise await _name_taken(session, name_norm)
                await session.record_activity(
                    membership_id=actor.membership_id,
                    action="catalog.item.created",
                    subject_type="catalog_item",
                    subject_id=item.item_id,
                )
                return item_body(item)

            return await idempotency.run_once(
                session,
                key=key,
                operation=CREATE_ITEM.name,
                user_id=user_id,
                request={"name": clean_name, "unit": clean_unit, "price": clean_price},
                action=apply,
            )

    async def update(
        self,
        user_id: UUID,
        shop_id: UUID,
        item_id: UUID,
        *,
        name: str | None,
        unit: str | None,
        price: int | None,
        request_key: str | None,
    ) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, UPDATE_ITEM)
            key = idempotency.validate_key(request_key)
            if name is None and unit is None and price is None:
                raise ValidationFailed({"_": "nothing to change"})
            clean_name, clean_unit, clean_price = _clean(name=name, unit=unit, price=price, unit_given=unit is not None)
            await require_writable(session, self._today(), new_credit=False)

            async def apply() -> dict[str, Any]:
                if await session.get_catalog_item(item_id, for_update=True) is None:
                    raise NotFound()
                if clean_unit is not None:
                    # A counted item keeps a unit the stock knows, and the unit its quantities are in.
                    counted = await session.stock_item(item_id, for_update=False)
                    if counted is not None and counted.tracked and clean_unit != counted.unit:
                        if clean_unit not in UNIT_KEYS:
                            raise ValidationFailed({"unit": "a stock-counted item keeps one of the stock units"})
                        if counted.last_seq:
                            raise ValidationFailed({"unit": "the unit cannot change once the item has stock movements"})
                name_norm = normalize_name(clean_name) if clean_name is not None else None
                updated = await session.update_catalog_item(
                    item_id, name=clean_name, name_norm=name_norm, unit=clean_unit, price=clean_price
                )
                if updated is None:
                    raise await _name_taken(session, name_norm or "")
                await session.record_activity(
                    membership_id=actor.membership_id,
                    action="catalog.item.updated",
                    subject_type="catalog_item",
                    subject_id=item_id,
                )
                return item_body(updated)

            return await idempotency.run_once(
                session,
                key=key,
                operation=UPDATE_ITEM.name,
                user_id=user_id,
                request={"item": str(item_id), "name": clean_name, "unit": clean_unit, "price": clean_price},
                action=apply,
            )

    async def _change_state(
        self,
        user_id: UUID,
        shop_id: UUID,
        item_id: UUID,
        op: Operation,
        request_key: str | None,
        *,
        action: str,
        only_learned: bool,
        merge_into: UUID | None = None,
        new_state: Callable[[CatalogItemRecord], tuple[str, bool, UUID | None]],
    ) -> dict[str, Any]:
        """Authorize, then move one item to the status, learned flag and merge target `new_state` returns."""
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, op)
            key = idempotency.validate_key(request_key)
            if merge_into == item_id:
                raise ValidationFailed({"into": "must be a different item"})
            await require_writable(session, self._today(), new_credit=False)

            async def apply() -> dict[str, Any]:
                item = await session.get_catalog_item(item_id, for_update=True)
                if item is None:
                    raise NotFound()
                if only_learned and not item.learned:
                    raise CatalogItemNotLearned()
                if merge_into is not None:
                    target = await session.get_catalog_item(merge_into, for_update=True)
                    if target is None:
                        raise NotFound()
                    # A shown, reviewed item. An alias is always hidden (a database constraint), and only a
                    # learned item can be merged, so a merge never forms a chain.
                    if target.status != "active" or target.learned:
                        raise CatalogMergeTargetInvalid()
                status, learned, merged_into = new_state(item)
                updated = await session.set_catalog_item_state(
                    item_id, status=status, learned=learned, merged_into=merged_into
                )
                await session.record_activity(
                    membership_id=actor.membership_id, action=action, subject_type="catalog_item", subject_id=item_id
                )
                return item_body(updated)

            return await idempotency.run_once(
                session,
                key=key,
                operation=op.name,
                user_id=user_id,
                request={"item": str(item_id), "into": None if merge_into is None else str(merge_into)},
                action=apply,
            )

    async def set_hidden(
        self, user_id: UUID, shop_id: UUID, item_id: UUID, *, hidden: bool, request_key: str | None
    ) -> dict[str, Any]:
        """Hide an item from the list sellers choose from, or show it again. Showing a merged item unmerges it."""
        if hidden:
            return await self._change_state(
                user_id,
                shop_id,
                item_id,
                HIDE_ITEM,
                request_key,
                action="catalog.item.hidden",
                only_learned=False,
                new_state=lambda item: ("hidden", item.learned, item.merged_into),
            )
        return await self._change_state(
            user_id,
            shop_id,
            item_id,
            UNHIDE_ITEM,
            request_key,
            action="catalog.item.unhidden",
            only_learned=False,
            new_state=lambda item: ("active", item.learned, None),
        )

    async def accept_learned(
        self, user_id: UUID, shop_id: UUID, item_id: UUID, request_key: str | None
    ) -> dict[str, Any]:
        """The learned item is a real good of the shop: it stays as it is and is no longer flagged."""
        return await self._change_state(
            user_id,
            shop_id,
            item_id,
            ACCEPT_LEARNED,
            request_key,
            action="catalog.learned.accepted",
            only_learned=True,
            new_state=lambda item: (item.status, False, None),
        )

    async def dismiss_learned(
        self, user_id: UUID, shop_id: UUID, item_id: UUID, request_key: str | None
    ) -> dict[str, Any]:
        """The learned item does not belong in the catalog: it is hidden and no longer flagged.

        The row stays, because goods lines may refer to it, and so the same typed name is not learned again.
        """
        return await self._change_state(
            user_id,
            shop_id,
            item_id,
            DISMISS_LEARNED,
            request_key,
            action="catalog.learned.dismissed",
            only_learned=True,
            new_state=lambda item: ("hidden", False, None),
        )

    async def merge_learned(
        self, user_id: UUID, shop_id: UUID, item_id: UUID, into: UUID, request_key: str | None
    ) -> dict[str, Any]:
        """The learned item is another spelling of an existing item: it becomes a hidden alias of it."""
        return await self._change_state(
            user_id,
            shop_id,
            item_id,
            MERGE_LEARNED,
            request_key,
            action="catalog.learned.merged",
            only_learned=True,
            merge_into=into,
            new_state=lambda item: ("hidden", False, into),
        )

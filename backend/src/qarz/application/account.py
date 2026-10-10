"""A signed-in user's own shops and active shop (REQ-064), and the owner's activity log (REQ-047)."""

import base64
import json
from datetime import datetime
from typing import Any
from uuid import UUID

from qarz.application.authorization import SWITCH
from qarz.application.errors import NotFound, ValidationFailed
from qarz.application.operations import operation, self_operation
from qarz.application.ports import Storage
from qarz.application.shops import require_member
from qarz.domain.access import Capability
from qarz.domain.cash import SWITCH as CASH_SWITCH
from qarz.domain.network import SWITCH as NETWORK_SWITCH
from qarz.domain.shared_catalog import SWITCH as CATALOG_SWITCH
from qarz.domain.stock import SWITCH as STOCK_SWITCH
from qarz.domain.territories import SWITCH as ADDRESS_SWITCH

LIST_MY_SHOPS = self_operation("me.shops.list")
SET_ACTIVE_SHOP = self_operation("me.active_shop.set")
READ_ACTIVITY = operation("activity.list", Capability.ADMINISTER_SHOP)

MAX_PAGE = 100


class AccountService:
    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    async def permissions_on(self) -> bool:
        """Whether the per-member permissions are on.

        Not part of the body of the caller's shops, which stays as it was: the route says it in a header,
        and only when it is on, so that a client asks a shop for the member's permissions
        (`permissions.mine`) only when there is such a route to ask.
        """
        async with self._storage.platform() as session:
            return await session.platform_setting(SWITCH) is True

    async def cash_book_on(self) -> bool:
        """Whether the cash book is on (expansion module H). Said in a header as well, and only when it
        is on: a client offers the cash book only then."""
        async with self._storage.platform() as session:
            return await session.platform_setting(CASH_SWITCH) is True

    async def stock_on(self) -> bool:
        """Whether the stock is on (expansion module I). Said in a header, and only when it is on."""
        async with self._storage.platform() as session:
            return await session.platform_setting(STOCK_SWITCH) is True

    async def network_on(self) -> bool:
        """Whether the network between shops is on (expansion module J), which needs the stock as well.
        Said in a header, and only when it is on."""
        async with self._storage.platform() as session:
            return (
                await session.platform_setting(NETWORK_SWITCH) is True
                and await session.platform_setting(STOCK_SWITCH) is True
            )

    async def catalog_on(self) -> bool:
        """Whether the shared product catalogue is on. Said in a header, and only when it is on."""
        async with self._storage.platform() as session:
            return await session.platform_setting(CATALOG_SWITCH) is True

    async def address_on(self) -> bool:
        """Whether a customer may have an address. Said in a header, and only when it is on."""
        async with self._storage.platform() as session:
            return await session.platform_setting(ADDRESS_SWITCH) is True

    async def my_shops(self, user_id: UUID) -> dict[str, Any]:
        async with self._storage.platform() as session:
            shops = await session.my_memberships(user_id)
            active = await session.active_shop(user_id)
        ids = {shop.shop_id for shop in shops}
        return {
            "items": [
                {
                    "shop_id": str(shop.shop_id),
                    "name": shop.name,
                    "role": shop.role.value,
                    # Lets a client tell which entries the caller wrote (`author_id` on an entry).
                    "membership_id": str(shop.membership_id),
                }
                for shop in shops
            ],
            # An active shop the user has since lost access to is not reported.
            "active_shop": str(active) if active in ids else None,
        }

    async def set_active_shop(self, user_id: UUID, shop_id: UUID) -> dict[str, Any]:
        async with self._storage.platform() as session:
            shops = await session.my_memberships(user_id)
            if shop_id not in {shop.shop_id for shop in shops}:
                # Not a member, or the shop does not exist: the same answer.
                raise NotFound()
            await session.set_active_shop(user_id, shop_id)
        return {"active_shop": str(shop_id)}


def _encode_cursor(at: datetime, activity_id: UUID) -> str:
    raw = json.dumps([at.isoformat(), str(activity_id)]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        at, activity_id = json.loads(raw)
        return datetime.fromisoformat(at), UUID(activity_id)
    except (ValueError, TypeError) as error:
        raise ValidationFailed({"cursor": "not a cursor returned by this API"}) from error


class ActivityService:
    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    async def list(
        self,
        user_id: UUID,
        shop_id: UUID,
        *,
        actor: UUID | None,
        action: str | None,
        subject: UUID | None,
        cursor: str | None,
        limit: int,
    ) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            await require_member(session, user_id, READ_ACTIVITY)
            if not 1 <= limit <= MAX_PAGE:
                raise ValidationFailed({"limit": f"must be between 1 and {MAX_PAGE}"})
            if action is not None and not (
                1 <= len(action) <= 60 and action.replace(".", "").replace("_", "").isalnum()
            ):
                raise ValidationFailed({"action": "letters, digits, dots and underscores only"})
            before = _decode_cursor(cursor) if cursor else None
            rows = await session.list_activity(
                actor=actor, action_prefix=action, subject=subject, before=before, limit=limit + 1
            )
            page, more = rows[:limit], len(rows) > limit
            return {
                "items": [
                    {
                        "id": str(row.activity_id),
                        "at": row.at.isoformat(),
                        "actor_kind": row.actor_kind,
                        "actor_id": None if row.actor_id is None else str(row.actor_id),
                        "action": row.action,
                        "subject_type": row.subject_type,
                        "subject_id": None if row.subject_id is None else str(row.subject_id),
                        # Present only for the actions that keep what changed (a change of permissions).
                        **({} if row.detail is None else {"detail": row.detail}),
                    }
                    for row in page
                ],
                "next_cursor": _encode_cursor(page[-1].at, page[-1].activity_id) if more else None,
            }

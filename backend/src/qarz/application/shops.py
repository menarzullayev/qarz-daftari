"""Shop operations available so far: read and change the shop's own settings."""

from dataclasses import asdict, dataclass
from typing import Any
from uuid import UUID

from qarz.application import idempotency
from qarz.application.errors import ForbiddenRole, NotFound, ValidationFailed
from qarz.application.operations import Operation, operation
from qarz.application.ports import Membership, ShopSettings, Storage, TenantSession
from qarz.domain.access import Capability, allows, lowest_role_with

READ_SHOP = operation("shop.read", Capability.READ_SHOP)
UPDATE_SHOP = operation("shop.update", Capability.ADMINISTER_SHOP)

LANGUAGES = ("uz", "ru")


async def require_member(session: TenantSession, user_id: UUID, op: Operation) -> Membership:
    """Authorize a staff operation inside an open tenant transaction.

    A caller who is not an active member of the shop gets NotFound, exactly as if the shop did not exist.
    A member whose role lacks the capability is told which role is needed.
    """
    membership = await session.active_membership(user_id)
    if membership is None:
        raise NotFound()
    if op.capability is None:
        raise ValueError(f"{op.name} is not a shop operation")
    if not allows(membership.role, op.capability):
        raise ForbiddenRole(lowest_role_with(op.capability))
    return membership


@dataclass(frozen=True)
class ShopUpdate:
    name: str | None = None
    lang: str | None = None
    default_promise_days: int | None = None

    def validate(self) -> None:
        fields: dict[str, str] = {}
        if self.name is not None and not 1 <= len(self.name.strip()) <= 80:
            fields["name"] = "length must be between 1 and 80"
        if self.lang is not None and self.lang not in LANGUAGES:
            fields["lang"] = "must be uz or ru"
        if self.default_promise_days is not None and not 1 <= self.default_promise_days <= 365:
            fields["default_promise_days"] = "must be between 1 and 365"
        if self.name is None and self.lang is None and self.default_promise_days is None:
            fields["_"] = "nothing to change"
        if fields:
            raise ValidationFailed(fields)


def _as_body(settings: ShopSettings) -> dict[str, Any]:
    return {
        "id": str(settings.shop_id),
        "name": settings.name,
        "lang": settings.lang,
        "default_promise_days": settings.default_promise_days,
    }


class ShopService:
    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    async def read(self, user_id: UUID, shop_id: UUID) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            await require_member(session, user_id, READ_SHOP)
            settings = await session.shop_settings()
            if settings is None:
                raise NotFound()
            return _as_body(settings)

    async def update(self, user_id: UUID, shop_id: UUID, change: ShopUpdate, request_key: str | None) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            # Authorize before validating, so a non-member learns nothing about what the shop accepts.
            membership = await require_member(session, user_id, UPDATE_SHOP)
            key = idempotency.validate_key(request_key)
            change.validate()

            async def apply() -> dict[str, Any]:
                settings = await session.update_shop_settings(
                    name=change.name.strip() if change.name is not None else None,
                    lang=change.lang,
                    default_promise_days=change.default_promise_days,
                )
                await session.record_activity(
                    membership_id=membership.membership_id,
                    action="shop.settings_changed",
                    subject_type="shop",
                    subject_id=shop_id,
                )
                return _as_body(settings)

            return await idempotency.run_once(
                session,
                key=key,
                operation=UPDATE_SHOP.name,
                user_id=user_id,
                request=asdict(change),
                action=apply,
            )

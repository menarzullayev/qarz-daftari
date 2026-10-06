"""The shop's credit settings: a default limit, and whether sellers may sell above a limit (REQ-044)."""

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from qarz.application import idempotency
from qarz.application.customers import require_viewable, require_writable
from qarz.application.errors import AppError, ValidationFailed
from qarz.application.operations import operation
from qarz.application.ports import CreditSettings, Storage
from qarz.application.shops import require_member
from qarz.domain.access import Capability
from qarz.domain.credit import MAX_LIMIT, MIN_LIMIT, valid_limit
from qarz.domain.promise import tashkent_date

READ_CREDIT_SETTINGS = operation("shop.credit.read", Capability.RECORD)
UPDATE_CREDIT_SETTINGS = operation("shop.credit.update", Capability.MANAGE)

UNSET: Any = object()


class LimitReached(AppError):
    """The sale would take the customer above their limit, and this shop does not let a seller proceed (BR-8)."""

    code = "LIMIT_REACHED"


def _body(settings: CreditSettings) -> dict[str, Any]:
    return {
        "default_credit_limit": settings.default_limit,
        "sellers_may_exceed": settings.sellers_may_exceed,
        "limit_bounds": [MIN_LIMIT, MAX_LIMIT],
    }


class CreditService:
    def __init__(self, storage: Storage, now: Callable[[], datetime] | None = None) -> None:
        self._storage = storage
        self._now = now or (lambda: datetime.now(UTC))

    def _today(self) -> date:
        return tashkent_date(self._now())

    async def settings(self, user_id: UUID, shop_id: UUID) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, READ_CREDIT_SETTINGS)
            await require_viewable(session, actor, self._today())
            return _body(await session.credit_settings())

    async def update(
        self,
        user_id: UUID,
        shop_id: UUID,
        *,
        default_credit_limit: Any,
        sellers_may_exceed: bool | None,
        request_key: str | None,
    ) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, UPDATE_CREDIT_SETTINGS)
            key = idempotency.validate_key(request_key)
            fields: dict[str, str] = {}
            if default_credit_limit is UNSET and sellers_may_exceed is None:
                fields["_"] = "nothing to change"
            if (
                default_credit_limit is not UNSET
                and default_credit_limit is not None
                and not valid_limit(default_credit_limit)
            ):
                fields["default_credit_limit"] = f"a whole amount between {MIN_LIMIT} and {MAX_LIMIT} UZS, or null"
            if fields:
                raise ValidationFailed(fields)
            await require_writable(session, self._today(), new_credit=False)

            async def apply() -> dict[str, Any]:
                await session.update_credit_settings(
                    set_default=default_credit_limit is not UNSET,
                    default_limit=None if default_credit_limit is UNSET else default_credit_limit,
                    sellers_may_exceed=sellers_may_exceed,
                )
                await session.record_activity(
                    membership_id=actor.membership_id,
                    action="shop.credit_settings_changed",
                    subject_type="shop",
                    subject_id=shop_id,
                )
                return _body(await session.credit_settings())

            return await idempotency.run_once(
                session,
                key=key,
                operation=UPDATE_CREDIT_SETTINGS.name,
                user_id=user_id,
                request={
                    "default_set": default_credit_limit is not UNSET,
                    "default": None if default_credit_limit is UNSET else default_credit_limit,
                    "sellers_may_exceed": sellers_may_exceed,
                },
                action=apply,
            )

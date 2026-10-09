"""The shop's credit settings: a default limit, and whether sellers may sell above a limit (REQ-044)."""

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from qarz.application import idempotency
from qarz.application.currencies import USD, dollars_on, limit_hint
from qarz.application.customers import require_viewable, require_writable
from qarz.application.errors import AppError, ForbiddenRole, ValidationFailed
from qarz.application.operations import operation
from qarz.application.ports import CreditSettings, Storage
from qarz.application.shops import require_member
from qarz.domain.access import Capability, Role
from qarz.domain.credit import MAX_LIMIT, MIN_LIMIT, valid_limit
from qarz.domain.money import RULES
from qarz.domain.promise import tashkent_date

READ_CREDIT_SETTINGS = operation("shop.credit.read", Capability.RECORD)
UPDATE_CREDIT_SETTINGS = operation("shop.credit.update", Capability.MANAGE)

UNSET: Any = object()


class LimitReached(AppError):
    """The sale would take the customer above their limit, and this shop does not let a seller proceed (BR-8)."""

    code = "LIMIT_REACHED"


class AdvancesStand(AppError):
    """The shop holds an advance of at least one customer: it cannot stop accepting advances until none stands."""

    code = "ADVANCES_STAND"


def _body(settings: CreditSettings, dollars: bool = False) -> dict[str, Any]:
    """The settings as the API gives them. The dollar limit is a setting of its own, in cents (BR-8)."""
    body: dict[str, Any] = {
        "default_credit_limit": settings.default_limit,
        "sellers_may_exceed": settings.sellers_may_exceed,
        "limit_bounds": [MIN_LIMIT, MAX_LIMIT],
        # Whether a customer may pay more than they owe, the rest staying as their advance (INV-3).
        "accept_advances": settings.accept_advances,
    }
    if dollars:
        body["usd"] = {
            "default_credit_limit": settings.default_limit_usd,
            "limit_bounds": [RULES[USD].min_limit, RULES[USD].max_limit],
        }
    return body


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
            return _body(await session.credit_settings(), await dollars_on(session))

    async def update(
        self,
        user_id: UUID,
        shop_id: UUID,
        *,
        default_credit_limit: Any,
        sellers_may_exceed: bool | None,
        request_key: str | None,
        default_credit_limit_usd: Any = UNSET,
        accept_advances: bool | None = None,
    ) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, UPDATE_CREDIT_SETTINGS)
            if accept_advances is not None and actor.role is not Role.OWNER:
                # Whether the shop holds its customers' money is the owner's to decide, whatever else of
                # these settings a manager may change.
                raise ForbiddenRole(Role.OWNER)
            key = idempotency.validate_key(request_key)
            fields: dict[str, str] = {}
            dollars = await dollars_on(session)
            if default_credit_limit_usd is not UNSET and not dollars:
                # As any field the request model does not know: the shop has no dollar limit to set.
                raise ValidationFailed({"default_credit_limit_usd": "unknown field"})
            if (
                default_credit_limit is UNSET
                and sellers_may_exceed is None
                and default_credit_limit_usd is UNSET
                and accept_advances is None
            ):
                fields["_"] = "nothing to change"
            if (
                default_credit_limit_usd is not UNSET
                and default_credit_limit_usd is not None
                and not valid_limit(default_credit_limit_usd, USD)
            ):
                fields["default_credit_limit_usd"] = limit_hint(USD)
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
                if accept_advances is not None and not await session.set_accept_advances(accept_advances):
                    raise AdvancesStand()
                await session.update_credit_settings(
                    set_default=default_credit_limit is not UNSET,
                    default_limit=None if default_credit_limit is UNSET else default_credit_limit,
                    sellers_may_exceed=sellers_may_exceed,
                    set_default_usd=default_credit_limit_usd is not UNSET,
                    default_limit_usd=None if default_credit_limit_usd is UNSET else default_credit_limit_usd,
                )
                await session.record_activity(
                    membership_id=actor.membership_id,
                    action="shop.credit_settings_changed",
                    subject_type="shop",
                    subject_id=shop_id,
                )
                return _body(await session.credit_settings(), dollars)

            return await idempotency.run_once(
                session,
                key=key,
                operation=UPDATE_CREDIT_SETTINGS.name,
                user_id=user_id,
                request={
                    "default_set": default_credit_limit is not UNSET,
                    "default": None if default_credit_limit is UNSET else default_credit_limit,
                    "sellers_may_exceed": sellers_may_exceed,
                    # Only a request that names the dollar limit carries the key: every other request
                    # keeps the fingerprint it had before dollars existed.
                    **({} if default_credit_limit_usd is UNSET else {"default_usd": default_credit_limit_usd}),
                    # Likewise only a request that names the switch.
                    **({} if accept_advances is None else {"accept_advances": accept_advances}),
                },
                action=apply,
            )

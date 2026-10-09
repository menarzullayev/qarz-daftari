"""Shop operations available so far: read and change the shop's own settings."""

from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5
from zoneinfo import ZoneInfo

from qarz.application import idempotency
from qarz.application.currencies import DollarBalanceOpen, platform_dollars
from qarz.application.errors import AppError, ForbiddenRole, NotFound, ValidationFailed
from qarz.application.operations import Operation, operation, self_operation
from qarz.application.ports import Membership, ShopSettings, Storage, TenantSession
from qarz.domain import platform_settings
from qarz.domain.access import Capability, Role, allows, lowest_role_with

READ_SHOP = operation("shop.read", Capability.READ_SHOP)
UPDATE_SHOP = operation("shop.update", Capability.ADMINISTER_SHOP)
CREATE_SHOP = self_operation("shop.create")

LANGUAGES = ("uz", "ru")
DEFAULT_TRIAL_DAYS = 30
TASHKENT = ZoneInfo("Asia/Tashkent")


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


class ShopSuspended(AppError):
    code = "SHOP_SUSPENDED"


async def refuse_suspended(session: TenantSession) -> None:
    """BR-30: in a suspended shop nothing is changed; only its owner may still look and export."""
    stored = await session.subscription()
    if stored is not None and stored[0] == "suspended":
        raise ShopSuspended()


@dataclass(frozen=True)
class ShopUpdate:
    name: str | None = None
    lang: str | None = None
    default_promise_days: int | None = None
    # "This shop also works in dollars." Only the owner changes the shop's settings, this one included.
    usd_on: bool | None = None

    def validate(self) -> None:
        fields: dict[str, str] = {}
        if self.name is not None and not 1 <= len(self.name.strip()) <= 80:
            fields["name"] = "length must be between 1 and 80"
        if self.lang is not None and self.lang not in LANGUAGES:
            fields["lang"] = "must be uz or ru"
        if self.default_promise_days is not None and not 1 <= self.default_promise_days <= 365:
            fields["default_promise_days"] = "must be between 1 and 365"
        if self.name is None and self.lang is None and self.default_promise_days is None and self.usd_on is None:
            fields["_"] = "nothing to change"
        if fields:
            raise ValidationFailed(fields)


def _as_body(settings: ShopSettings, dollars_offered: bool = False) -> dict[str, Any]:
    """The shop as the API gives it.

    `usd_on` is there only while the platform offers dollars (the switch `usd_on`): its presence tells a
    client that the choice exists, its value whether this shop has made it.
    """
    body: dict[str, Any] = {
        "id": str(settings.shop_id),
        "name": settings.name,
        "lang": settings.lang,
        "default_promise_days": settings.default_promise_days,
    }
    if dollars_offered:
        body["usd_on"] = settings.usd_on
    return body


async def set_dollars_in(session: TenantSession, on: bool) -> None:
    """Turn the shop's dollars on or off, inside a transaction the caller has opened and authorized.

    Off is refused while any customer owes dollars: the debts would stay, unseen and unpayable. The
    setting is written first, which waits for everyone who is recording a dollar amount, and what is
    owed is read after it; a refusal undoes the write with the transaction.
    """
    await session.set_dollars_setting(on)
    if not on and await session.dollars_owed():
        raise DollarBalanceOpen()


class ShopService:
    def __init__(self, storage: Storage, now: Callable[[], datetime] | None = None) -> None:
        self._storage = storage
        self._now = now or (lambda: datetime.now(UTC))

    async def create(self, user_id: UUID, name: str, lang: str, request_key: str | None) -> dict[str, Any]:
        """Create a shop owned by the caller and start its trial if trials are on (REQ-001, REQ-052)."""
        key = idempotency.validate_key(request_key)
        fields: dict[str, str] = {}
        if not 1 <= len(name.strip()) <= 80:
            fields["name"] = "length must be between 1 and 80"
        if lang not in LANGUAGES:
            fields["lang"] = "must be uz or ru"
        if fields:
            raise ValidationFailed(fields)

        async with self._storage.platform() as platform:
            # Read as the administrator's panel shows them: a stored value that is not valid does not apply.
            trial_on = platform_settings.effective("trial_on", await platform.platform_setting("trial_on"))
            trial_days = platform_settings.effective("trial_days", await platform.platform_setting("trial_days"))
        trial_on = True if trial_on is None else bool(trial_on)
        days = (
            trial_days
            if isinstance(trial_days, int) and not isinstance(trial_days, bool) and trial_days > 0
            else DEFAULT_TRIAL_DAYS
        )

        # The shop's identifier is derived from the caller and the key, so a repeated request lands in
        # the same tenant and finds its stored response instead of creating a second shop.
        shop_id = uuid5(NAMESPACE_URL, f"qarz-daftari:shop:{user_id}:{key}")
        async with self._storage.tenant(shop_id) as session:

            async def apply() -> dict[str, Any]:
                # A person may own any number of shops (DEC-065). One trial for a person, decided by the
                # database so that two requests at once cannot both take it: later shops start limited.
                claim = await session.claim_owned_shop(user_id, wants_trial=trial_on)
                settings = await session.create_shop(name=name.strip(), lang=lang)
                membership_id = await session.add_member(user_id=user_id, role=Role.OWNER)
                today = self._now().astimezone(TASHKENT).date()
                if claim == "trial":
                    await session.create_subscription(state="trial", trial_ends=today + timedelta(days=days))
                else:
                    await session.create_subscription(state="limited", trial_ends=None)
                await session.record_activity(
                    membership_id=membership_id, action="shop.created", subject_type="shop", subject_id=shop_id
                )
                started = "trial" if claim == "trial" else "limited"
                if started == "limited" and await session.platform_setting("free_plan_on") is True:
                    # Nothing else is stored: a shop without a period and without customers is free (BR-33).
                    started = "free"
                return {**_as_body(settings), "subscription_state": started}

            body = await idempotency.run_once(
                session,
                key=key,
                operation=CREATE_SHOP.name,
                user_id=user_id,
                request={"name": name, "lang": lang},
                action=apply,
            )
        async with self._storage.platform() as platform:
            await platform.set_active_shop(user_id, shop_id)
        return body

    async def read(self, user_id: UUID, shop_id: UUID) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            await require_member(session, user_id, READ_SHOP)
            settings = await session.shop_settings()
            if settings is None:
                raise NotFound()
            return _as_body(settings, await platform_dollars(session))

    async def update(self, user_id: UUID, shop_id: UUID, change: ShopUpdate, request_key: str | None) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            # Authorize before validating, so a non-member learns nothing about what the shop accepts.
            membership = await require_member(session, user_id, UPDATE_SHOP)
            await refuse_suspended(session)
            key = idempotency.validate_key(request_key)
            offered = await platform_dollars(session)
            if change.usd_on is not None and not offered:
                # As any field the request model does not know: there is no such setting to change.
                raise ValidationFailed({"usd_on": "unknown field"})
            change.validate()

            async def apply() -> dict[str, Any]:
                if change.usd_on is not None:
                    await set_dollars_in(session, change.usd_on)
                    await session.record_activity(
                        membership_id=membership.membership_id,
                        action="shop.dollars_on" if change.usd_on else "shop.dollars_off",
                        subject_type="shop",
                        subject_id=shop_id,
                    )
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
                return _as_body(settings, offered)

            # A request that does not name the dollar setting keeps the fingerprint it always had.
            request = asdict(change)
            if change.usd_on is None:
                del request["usd_on"]
            return await idempotency.run_once(
                session,
                key=key,
                operation=UPDATE_SHOP.name,
                user_id=user_id,
                request=request,
                action=apply,
            )

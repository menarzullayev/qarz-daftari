"""What an administrator does: shops, subscriptions, suspension, platform settings, and the audit
(REQ-052, REQ-053, REQ-058, REQ-N14; ADR-018).

An administrator sees a shop's name, its subscription, its owner's Telegram identifier and how many staff
and customers it has, and nothing of its customers or entries (REQ-059). Every change, and every look at
one shop's details, is written to an audit the application can add to and read but never alter.

Every method here assumes the caller already passed `AdminAccess.require_admin`.
"""

import base64
import json
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from qarz.application import idempotency
from qarz.application.admin_access import AdminAccess, AdminRequestKeys
from qarz.application.chat_texts import say
from qarz.application.errors import AppError, NotFound, Unauthenticated, ValidationFailed
from qarz.application.operations import admin_operation
from qarz.application.ports import AdminAuditRow, AdminShopRow, PlatformSession, Storage
from qarz.domain import platform_settings
from qarz.domain.admin_subscription import (
    ChangeRefused,
    Subscription,
    end_trial,
    set_paid_through,
    set_trial_end,
    suspend,
    unsuspend,
)
from qarz.domain.promise import tashkent_date
from qarz.domain.subscription import ACTIVE, FREE, LIMITED, SUSPENDED, TRIAL, with_free_plan

LIST_SHOPS = admin_operation("admin.shops.list")
READ_SHOP = admin_operation("admin.shops.read")
SET_TRIAL = admin_operation("admin.shops.trial.set")
END_TRIAL = admin_operation("admin.shops.trial.end")
SET_PAID_THROUGH = admin_operation("admin.shops.paid_through.set")
SUSPEND_SHOP = admin_operation("admin.shops.suspend")
UNSUSPEND_SHOP = admin_operation("admin.shops.unsuspend")
READ_SETTINGS = admin_operation("admin.settings.read")
UPDATE_SETTINGS = admin_operation("admin.settings.update")
LIST_AUDIT = admin_operation("admin.audit.list")

MAX_PAGE = 100
# How many shops one call of the search gives at most (migration 0016).
SEARCH_PAGE = MAX_PAGE + 1
STATES = (TRIAL, ACTIVE, LIMITED, SUSPENDED)
MIN_REASON, MAX_REASON = 3, 500
# What the shop page shows as the subscription's history: these audit actions on that shop.
HISTORY_PREFIX = "subscription."


class SubscriptionChangeRefused(AppError):
    """The change does not apply to the subscription as it stands; `fields.reason` says why."""

    code = "SUBSCRIPTION_CHANGE_REFUSED"


class _CodeRefused(Exception):
    """Carries a refused second-factor code out of the idempotent action without rolling back."""

    def __init__(self, refusal: AppError) -> None:
        super().__init__(refusal.code)
        self.refusal = refusal


def _encode_cursor(at: datetime, row_id: UUID) -> str:
    raw = json.dumps([at.isoformat(), str(row_id)]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        at, row_id = json.loads(raw)
        return datetime.fromisoformat(at), UUID(row_id)
    except (ValueError, TypeError) as error:
        raise ValidationFailed({"cursor": "not a cursor returned by this API"}) from error


def _iso(value: date | datetime | None) -> str | None:
    return None if value is None else value.isoformat()


FREE_PLAN_ON = "free_plan_on"
FREE_PLAN_CUSTOMERS = "free_plan_customers"


async def free_plan_held(session: PlatformSession) -> int | None:
    """How many customers the free plan holds; None while the platform switch is off (BR-33)."""
    switch = await session.platform_setting(FREE_PLAN_ON)
    if switch is not True:
        return None
    return platform_settings.free_plan_customers(switch, await session.platform_setting(FREE_PLAN_CUSTOMERS))


def shop_body(row: AdminShopRow, held: int | None = None, customers: int = 0) -> dict[str, Any]:
    """A shop as the administrator's side answers it. `held` is how many customers the free plan holds
    (None while it is switched off) and `customers` how many active ones the shop has: with the plan on
    the state is what applies to the shop itself, `free` included, and `plan` says how much of the plan
    is used. With it off the answer is what it always was."""
    body = _shop_body(row)
    if held is not None:
        body["subscription"]["state"] = with_free_plan(row.effective_state, held, customers)
        body["plan"] = {"free_customers": held, "customers": customers}
    return body


async def shop_bodies(session: PlatformSession, rows: Sequence[AdminShopRow]) -> list[dict[str, Any]]:
    """The answer for each of these shops, the free plan taken into account while it is on. The state
    the search gave is computed in SQL from the stored row alone; whether a shop without a period is
    free depends on its active customers, which are counted here. Nothing is counted with the plan off."""
    held = await free_plan_held(session)
    if held is None or not rows:
        return [shop_body(row) for row in rows]
    counts = await session.admin_active_customers([row.shop_id for row in rows])
    return [shop_body(row, held, counts[row.shop_id]) for row in rows]


def _shop_body(row: AdminShopRow) -> dict[str, Any]:
    return {
        "id": str(row.shop_id),
        "name": row.name,
        "status": row.status,
        "created_at": row.created_at.isoformat(),
        "subscription": {
            # What applies today; `stored_state` is the row as written, which a finished period outlives
            # until the daily review.
            "state": row.effective_state,
            "stored_state": row.state,
            "trial_ends": _iso(row.trial_ends),
            "paid_through": _iso(row.paid_through),
            "prior_state": row.prior_state,
        },
        "owner_tg_id": row.owner_tg,
        "staff_count": row.staff_count,
        "customer_count": row.customer_count,
    }


def audit_body(row: AdminAuditRow) -> dict[str, Any]:
    return {
        "id": str(row.audit_id),
        "at": row.at.isoformat(),
        # Who acted: an administrator, or, for a receipt decided from the review group by one of its
        # Telegram administrators (DEC-064), that person's Telegram identifier. Exactly one is set.
        "admin_id": None if row.admin_id is None else str(row.admin_id),
        "actor_tg_id": row.actor_tg,
        "action": row.action,
        "target_type": row.target_type,
        "target_id": row.target_id,
        "shop_id": None if row.shop_id is None else str(row.shop_id),
        "reason": row.reason,
        "detail": row.detail,
    }


def clean_reason(raw: str | None, *, required: bool) -> str | None:
    reason = " ".join((raw or "").split())
    if not reason and not required:
        return None
    if not MIN_REASON <= len(reason) <= MAX_REASON:
        raise ValidationFailed({"reason": f"length must be between {MIN_REASON} and {MAX_REASON}"})
    return reason


def _subscription_detail(sub: Subscription) -> dict[str, Any]:
    return {
        "state": sub.state,
        "trial_ends": _iso(sub.trial_ends),
        "paid_through": _iso(sub.paid_through),
        "prior_state": sub.prior_state,
    }


class AdminService:
    def __init__(self, storage: Storage, access: AdminAccess, now: Callable[[], datetime] | None = None) -> None:
        self._storage = storage
        self._access = access
        self._now = now or (lambda: datetime.now(UTC))

    def _today(self) -> date:
        return tashkent_date(self._now())

    # --- shops ------------------------------------------------------------------------------------------

    async def list_shops(
        self, admin_id: UUID, *, query: str | None, state: str | None, cursor: str | None, limit: int
    ) -> dict[str, Any]:
        """Shops, newest first. `state` filters by what applies today. With the free plan on that is
        what the list shows: `limited` leaves out the shops the plan holds, and `free` lists them."""
        today = self._today()
        async with self._storage.platform() as session:
            held = await free_plan_held(session)
            fields: dict[str, str] = {}
            if not 1 <= limit <= MAX_PAGE:
                fields["limit"] = f"must be between 1 and {MAX_PAGE}"
            if state is not None and state not in STATES and (held is None or state != FREE):
                fields["state"] = (
                    "must be trial, active, limited or suspended"
                    if held is None
                    else "must be trial, active, free, limited or suspended"
                )
            part = " ".join((query or "").split()) or None
            if part is not None and len(part) > 80:
                fields["q"] = "at most 80 characters"
            if fields:
                raise ValidationFailed(fields)
            after = _decode_cursor(cursor) if cursor else None
            # The search knows the stored row alone: to it a free shop is a limited one. With the plan
            # on, those two filters read the limited shops page by page and keep the ones that match.
            by_plan = held is not None and state in (LIMITED, FREE)
            found: list[tuple[AdminShopRow, dict[str, Any]]] = []
            while True:
                rows = await session.admin_shop_search(
                    admin_id,
                    today=today,
                    query=part,
                    state=LIMITED if by_plan else state,
                    shop_id=None,
                    after=after,
                    limit=limit + 1,
                )
                bodies = await shop_bodies(session, rows)
                found += [
                    (row, body)
                    for row, body in zip(rows, bodies, strict=True)
                    if not by_plan or body["subscription"]["state"] == state
                ]
                if not by_plan or len(found) > limit or len(rows) <= limit:
                    break
                after = (rows[-1].created_at, rows[-1].shop_id)
        page, more = found[:limit], len(found) > limit
        return {
            "items": [body for _, body in page],
            "next_cursor": _encode_cursor(page[-1][0].created_at, page[-1][0].shop_id) if more else None,
        }

    async def _shop(self, session: PlatformSession, admin_id: UUID, shop_id: UUID) -> AdminShopRow:
        rows = await session.admin_shop_search(
            admin_id, today=self._today(), query=None, state=None, shop_id=shop_id, after=None, limit=1
        )
        if not rows:
            raise NotFound()
        return rows[0]

    async def read_shop(self, admin_id: UUID, shop_id: UUID) -> dict[str, Any]:
        """One shop with its payment history and the changes administrators made. The look is audited."""
        now = self._now()
        async with self._storage.platform() as session:
            row = await self._shop(session, admin_id, shop_id)
            (body,) = await shop_bodies(session, [row])
            receipts = await session.admin_shop_receipts(admin_id, shop_id)
            changes = await session.list_admin_audit(
                shop_id=shop_id, action_prefix=HISTORY_PREFIX, before=None, limit=MAX_PAGE
            )
            await session.add_admin_audit(
                admin_id=admin_id,
                action="shop.viewed",
                target_type="shop",
                target_id=str(shop_id),
                shop_id=shop_id,
                reason=None,
                detail={},
                now=now,
            )
        return {
            **body,
            "lang": row.lang,
            "deletion_due": _iso(row.deletion_due),
            "receipts": [
                {
                    "id": str(receipt.receipt_id),
                    "stated_amount": receipt.stated_amount,
                    "status": receipt.status,
                    "months": receipt.months,
                    "reject_reason": receipt.reject_reason,
                    "created_at": receipt.created_at.isoformat(),
                    "decided_at": _iso(receipt.decided_at),
                }
                for receipt in receipts
            ],
            "changes": [audit_body(change) for change in changes],
        }

    async def _change_subscription(
        self,
        admin_id: UUID,
        shop_id: UUID,
        *,
        operation: str,
        action: str,
        request: dict[str, Any],
        reason: str | None,
        request_key: str | None,
        change: Callable[[Subscription], Subscription],
        notice: str | None = None,
    ) -> dict[str, Any]:
        """Lock the shop's subscription, apply one domain rule, store the result, audit it."""
        key = idempotency.validate_key(request_key)
        why = clean_reason(reason, required=True)
        now = self._now()
        async with self._storage.platform() as session:

            async def apply() -> dict[str, Any]:
                locked = await session.admin_lock_subscription(admin_id, shop_id)
                if locked is None:
                    raise NotFound()
                before = Subscription(locked.state, locked.trial_ends, locked.paid_through, locked.prior_state)
                try:
                    after = change(before)
                except ChangeRefused as refused:
                    raise SubscriptionChangeRefused({"reason": refused.why}) from refused
                stored = await session.admin_store_subscription(
                    admin_id,
                    shop_id,
                    state=after.state,
                    trial_ends=after.trial_ends,
                    paid_through=after.paid_through,
                    prior_state=after.prior_state,
                    now=now,
                )
                if not stored:
                    # The account was disabled, or the shop erased, between the lock and here.
                    raise NotFound()
                audit_id = await session.add_admin_audit(
                    admin_id=admin_id,
                    action=action,
                    target_type="shop",
                    target_id=str(shop_id),
                    shop_id=shop_id,
                    reason=why,
                    detail={"before": _subscription_detail(before), "after": _subscription_detail(after)},
                    now=now,
                )
                if notice is not None and locked.owner_tg is not None:
                    # Specification, events: a suspension is told to the owner, with the reason given.
                    text = say(locked.owner_lang or "uz", notice, shop=locked.shop_name, reason=why)
                    await session.enqueue(
                        channel="telegram",
                        recipient=str(locked.owner_tg),
                        payload={"text": text},
                        dedupe_key=f"admin:{audit_id}",
                        shop_id=shop_id,
                    )
                (body,) = await shop_bodies(session, [await self._shop(session, admin_id, shop_id)])
                return body

            return await idempotency.run_once(
                # The stored answer shows the shop; it is kept under the shop and erased with it.
                AdminRequestKeys(session, admin_id, about_shop=shop_id),
                key=key,
                operation=operation,
                user_id=admin_id,
                request={"shop": str(shop_id), "reason": why, **request},
                action=apply,
            )

    async def set_trial(
        self, admin_id: UUID, shop_id: UUID, trial_ends: date, reason: str | None, request_key: str | None
    ) -> dict[str, Any]:
        today = self._today()
        return await self._change_subscription(
            admin_id,
            shop_id,
            operation=SET_TRIAL.name,
            action="subscription.trial_set",
            request={"trial_ends": trial_ends.isoformat()},
            reason=reason,
            request_key=request_key,
            change=lambda sub: set_trial_end(sub, trial_ends, today),
        )

    async def end_trial(
        self, admin_id: UUID, shop_id: UUID, reason: str | None, request_key: str | None
    ) -> dict[str, Any]:
        today = self._today()
        return await self._change_subscription(
            admin_id,
            shop_id,
            operation=END_TRIAL.name,
            action="subscription.trial_ended",
            request={},
            reason=reason,
            request_key=request_key,
            change=lambda sub: end_trial(sub, today),
        )

    async def set_paid_through(
        self, admin_id: UUID, shop_id: UUID, paid_through: date, reason: str | None, request_key: str | None
    ) -> dict[str, Any]:
        today = self._today()
        return await self._change_subscription(
            admin_id,
            shop_id,
            operation=SET_PAID_THROUGH.name,
            action="subscription.paid_through_set",
            request={"paid_through": paid_through.isoformat()},
            reason=reason,
            request_key=request_key,
            change=lambda sub: set_paid_through(sub, paid_through, today),
        )

    async def suspend(
        self, admin_id: UUID, shop_id: UUID, reason: str | None, request_key: str | None
    ) -> dict[str, Any]:
        return await self._change_subscription(
            admin_id,
            shop_id,
            operation=SUSPEND_SHOP.name,
            action="subscription.suspended",
            request={},
            reason=reason,
            request_key=request_key,
            change=suspend,
            notice="shop_suspended",
        )

    async def unsuspend(
        self, admin_id: UUID, shop_id: UUID, reason: str | None, request_key: str | None
    ) -> dict[str, Any]:
        return await self._change_subscription(
            admin_id,
            shop_id,
            operation=UNSUSPEND_SHOP.name,
            action="subscription.unsuspended",
            request={},
            reason=reason,
            request_key=request_key,
            change=unsuspend,
            notice="shop_unsuspended",
        )

    # --- platform settings ------------------------------------------------------------------------------

    @staticmethod
    async def _settings_body(session: PlatformSession) -> dict[str, Any]:
        stored = await session.platform_settings()
        return {
            "settings": {
                key: platform_settings.effective(key, stored[key][0] if key in stored else None)
                for key in platform_settings.SETTINGS
            },
            "needs_code": sorted(key for key in platform_settings.SETTINGS if platform_settings.needs_code(key)),
            "changed": {
                key: {"by": stored[key][1], "at": stored[key][2].isoformat()}
                for key in platform_settings.SETTINGS
                if key in stored
            },
        }

    async def _leaving_plan(
        self, session: PlatformSession, admin_id: UUID, *, held: int, lowered_to: int | None
    ) -> list[tuple[AdminShopRow, int]]:
        """The shops the free plan holds today that it would not hold at `lowered_to` customers, each with
        its active customers: without a period, and with more than `lowered_to` and no more than `held`.
        A shop in a trial or paid period, and a suspended one, is not among them whatever it has.

        `lowered_to` None is the plan switched off: it then holds no shop, so every shop it holds today
        is among them, except one that is being deleted or was erased."""
        leaving: list[tuple[AdminShopRow, int]] = []
        after: tuple[datetime, UUID] | None = None
        while lowered_to is None:
            rows = await session.admin_shop_search(
                admin_id,
                today=self._today(),
                query=None,
                state=LIMITED,
                shop_id=None,
                after=after,
                limit=SEARCH_PAGE,
            )
            counts = await session.admin_active_customers([row.shop_id for row in rows])
            leaving += [
                (row, counts[row.shop_id]) for row in rows if row.status == "active" and counts[row.shop_id] <= held
            ]
            if len(rows) < SEARCH_PAGE:
                return leaving
            after = (rows[-1].created_at, rows[-1].shop_id)
        while lowered_to < held:
            rows = await session.admin_shop_search(
                admin_id,
                today=self._today(),
                query=None,
                state=LIMITED,
                shop_id=None,
                after=after,
                limit=SEARCH_PAGE,
            )
            counts = await session.admin_active_customers([row.shop_id for row in rows])
            leaving += [(row, counts[row.shop_id]) for row in rows if lowered_to < counts[row.shop_id] <= held]
            if len(rows) < SEARCH_PAGE:
                break
            after = (rows[-1].created_at, rows[-1].shop_id)
        return leaving

    async def read_settings(
        self, admin_id: UUID, free_plan_customers: str | None = None, free_plan_on: str | None = None
    ) -> dict[str, Any]:
        """The settings. With the free plan on, `free_plan_customers` asks what saving that number would
        do: `free_plan_preview` then says how many shops the plan holds today would become limited.
        `free_plan_on=false` asks what switching the plan off would do: `free_plan_off_preview` then says
        how many shops it holds today, all of which would become limited; the number is then not looked
        at, since a plan that is off holds nobody whatever number is stored with it. Nothing is changed
        and nobody is told. With the plan off neither question is looked at."""
        async with self._storage.platform() as session:
            body = await self._settings_body(session)
            held = await free_plan_held(session)
            if held is None:
                return body
            if free_plan_on is not None:
                if free_plan_on not in ("true", "false"):
                    raise ValidationFailed({"free_plan_on": "must be true or false"})
                if free_plan_on == "false":
                    held_today = await self._leaving_plan(session, admin_id, held=held, lowered_to=None)
                    body["free_plan_off_preview"] = {"shops_limited": len(held_today)}
                    return body
            if free_plan_customers is None:
                return body
            try:
                if not (free_plan_customers.isascii() and free_plan_customers.isdigit()):
                    raise platform_settings.InvalidSetting("must be a whole number")
                asked = platform_settings.validate("free_plan_customers", int(free_plan_customers))
            except platform_settings.InvalidSetting as invalid:
                raise ValidationFailed({"free_plan_customers": str(invalid)}) from invalid
            assert isinstance(asked, int)
            leaving = await self._leaving_plan(session, admin_id, held=held, lowered_to=asked)
            body["free_plan_preview"] = {"customers": asked, "shops_limited": len(leaving)}
            return body

    async def _tell_plan_lowered(
        self, session: PlatformSession, admin_id: UUID, shop_id: UUID, held: int, customers: int
    ) -> None:
        """Tell the owner of a shop the free plan no longer holds what happened and how to leave the
        limited mode. Once: the outbox keeps one message for a shop, a number and a day, so the same
        number saved again that day tells nobody twice. Nothing of the shop is changed or removed."""
        locked = await session.admin_lock_subscription(admin_id, shop_id)
        if locked is None or locked.owner_tg is None:
            return
        await session.enqueue(
            channel="telegram",
            recipient=str(locked.owner_tg),
            payload={
                "text": say(
                    locked.owner_lang or "uz", "free_plan_lowered", shop=locked.shop_name, limit=held, used=customers
                )
            },
            dedupe_key=f"free_plan:lowered:{shop_id}:{held}:{self._today().isoformat()}",
            shop_id=shop_id,
        )

    async def _tell_plan_off(self, session: PlatformSession, admin_id: UUID, shop_id: UUID) -> None:
        """Tell the owner of a shop the free plan held that the plan was switched off and how to leave
        the limited mode. Once: the outbox keeps one message for a shop and a day, so the plan switched
        on and off again that day tells nobody twice. Nothing of the shop is changed or removed."""
        locked = await session.admin_lock_subscription(admin_id, shop_id)
        if locked is None or locked.owner_tg is None:
            return
        await session.enqueue(
            channel="telegram",
            recipient=str(locked.owner_tg),
            payload={"text": say(locked.owner_lang or "uz", "free_plan_off", shop=locked.shop_name)},
            dedupe_key=f"free_plan:off:{shop_id}:{self._today().isoformat()}",
            shop_id=shop_id,
        )

    async def update_settings(
        self,
        admin_id: UUID,
        changes: dict[str, Any],
        *,
        code: str | None,
        reason: str | None,
        request_key: str | None,
    ) -> dict[str, Any]:
        """Change switches and prices. Sensitive ones need a fresh code, which is used up by the change.

        A change that lowers how many customers the free plan holds tells the owners of the shops that
        become limited by it, and the answer then carries `free_plan_lowered` with how many they are.
        A change that switches the free plan off tells the owners of every shop it held, and the answer
        then carries `free_plan_off` with how many they are; a number changed in the same request adds
        nothing to that."""
        key = idempotency.validate_key(request_key)
        fields: dict[str, str] = {}
        cleaned: dict[str, platform_settings.Value] = {}
        for name, value in changes.items():
            try:
                cleaned[name] = platform_settings.validate(name, value)
            except platform_settings.InvalidSetting as invalid:
                fields[f"changes.{name}"] = str(invalid)
        if not changes:
            fields["changes"] = "nothing to change"
        if fields:
            raise ValidationFailed(fields)
        why = clean_reason(reason, required=False)
        code_needed = any(platform_settings.needs_code(name) for name in cleaned)
        now = self._now()
        refusal: AppError | None = None
        body: dict[str, Any] = {}
        async with self._storage.platform() as session:

            async def apply() -> dict[str, Any]:
                if code_needed:
                    refused = await self._access.check_code(session, admin_id, code)
                    if refused is not None:
                        raise _CodeRefused(refused)
                stored = await session.platform_settings()
                # BR-33: lowering the number of customers the free plan holds, while it is on and stays
                # on, limits the shops that are now over it. They are found before the change is stored.
                plan = {name: stored[name][0] for name in (FREE_PLAN_ON, FREE_PLAN_CUSTOMERS) if name in stored}
                held = platform_settings.free_plan_customers(plan.get(FREE_PLAN_ON), plan.get(FREE_PLAN_CUSTOMERS))
                plan |= {name: cleaned[name] for name in (FREE_PLAN_ON, FREE_PLAN_CUSTOMERS) if name in cleaned}
                held_now = platform_settings.free_plan_customers(plan.get(FREE_PLAN_ON), plan.get(FREE_PLAN_CUSTOMERS))
                lowered_to = held_now if held is not None and held_now is not None and held_now < held else None
                # Switched off, the plan holds nobody: every shop it held is limited, whatever number is
                # stored with it. A plan that was off, or one that stays on, is not switched off.
                switched_off = held is not None and held_now is None
                leaving: list[tuple[AdminShopRow, int]] = []
                if held is not None and (lowered_to is not None or switched_off):
                    leaving = await self._leaving_plan(session, admin_id, held=held, lowered_to=lowered_to)
                for name in sorted(cleaned):
                    old = platform_settings.effective(name, stored[name][0] if name in stored else None)
                    # One call: the database stores the setting and its audit row together, and refuses
                    # both unless it finds the administrator itself (security review, finding 11).
                    stored_now = await session.set_platform_setting(
                        name,
                        cleaned[name],
                        admin_id=admin_id,
                        reason=why,
                        detail={
                            "before": platform_settings.masked(name, old),
                            "after": platform_settings.masked(name, cleaned[name]),
                        },
                        now=now,
                    )
                    if not stored_now:
                        raise Unauthenticated()
                body = await self._settings_body(session)
                if lowered_to is not None:
                    for row, customers in leaving:
                        await self._tell_plan_lowered(session, admin_id, row.shop_id, lowered_to, customers)
                    body["free_plan_lowered"] = {"customers": lowered_to, "shops_limited": len(leaving)}
                if switched_off:
                    for row, _ in leaving:
                        await self._tell_plan_off(session, admin_id, row.shop_id)
                    body["free_plan_off"] = {"shops_limited": len(leaving)}
                return body

            try:
                body = await idempotency.run_once(
                    AdminRequestKeys(session, admin_id),
                    key=key,
                    operation=UPDATE_SETTINGS.name,
                    user_id=admin_id,
                    # The code is not part of what is asked for: a retry with a newer code is the same request.
                    request={"changes": cleaned, "reason": why},
                    action=apply,
                )
            except _CodeRefused as stop:
                # Caught inside the transaction so that it commits: the wrong code has been counted, and
                # nothing else was written.
                refusal = stop.refusal
        if refusal is not None:
            raise refusal
        return body

    # --- audit ------------------------------------------------------------------------------------------

    async def list_audit(
        self,
        admin_id: UUID,
        *,
        shop_id: UUID | None,
        action: str | None,
        cursor: str | None,
        limit: int,
        by: UUID | None = None,
    ) -> dict[str, Any]:
        """The audit, newest first, optionally of one shop, one kind of action, or one administrator."""
        fields: dict[str, str] = {}
        if not 1 <= limit <= MAX_PAGE:
            fields["limit"] = f"must be between 1 and {MAX_PAGE}"
        if action is not None and not (1 <= len(action) <= 60 and action.replace(".", "").replace("_", "").isalnum()):
            fields["action"] = "letters, digits, dots and underscores only"
        if fields:
            raise ValidationFailed(fields)
        before = _decode_cursor(cursor) if cursor else None
        async with self._storage.platform() as session:
            rows = await session.list_admin_audit(
                shop_id=shop_id, action_prefix=action, before=before, limit=limit + 1, admin_id=by
            )
        page, more = rows[:limit], len(rows) > limit
        return {
            "items": [audit_body(row) for row in page],
            "next_cursor": _encode_cursor(page[-1].at, page[-1].audit_id) if more else None,
        }

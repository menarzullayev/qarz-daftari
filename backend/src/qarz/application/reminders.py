"""Reminders (REQ-022 to REQ-025, REQ-042, REQ-043; BR-13, BR-17 to BR-19; REQ-N10).

Off until the shop turns them on. One on the promised date, then at most one every seven days while the
debt stays overdue; a manager or owner may send one by hand, once a day per customer. A reminder states
the shop and the amount and nothing else, in a fixed polite wording the shop picks from.
"""

from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from qarz.application import idempotency
from qarz.application.chat_texts import both, money, say
from qarz.application.chat_texts import template as wording
from qarz.application.currencies import USD, UZS, dollars_on, shop_currencies
from qarz.application.customers import (
    effective_subscription,
    free_plan_customers,
    require_viewable,
    require_writable,
    stored_subscription,
)
from qarz.application.errors import AppError, NotFound, ValidationFailed
from qarz.application.operations import operation
from qarz.application.ports import ReminderCandidate, ReminderSettings, Storage, TenantSession
from qarz.application.shops import require_member
from qarz.domain import languages, ledger, platform_settings
from qarz.domain.access import Capability
from qarz.domain.promise import tashkent_date
from qarz.domain.reminders import (
    FIRST_HOUR,
    LAST_HOUR,
    TEMPLATES,
    Channel,
    ReminderPlan,
    choose_channel,
    plan_automatic,
    plan_manual,
)
from qarz.domain.subscription import sms_included

READ_REMINDER_SETTINGS = operation("reminders.settings.read", Capability.MANAGE)
UPDATE_REMINDER_SETTINGS = operation("reminders.settings.update", Capability.MANAGE)
SEND_REMINDER = operation("reminders.send", Capability.MANAGE)
LIST_UNREACHABLE = operation("reminders.unreachable", Capability.MANAGE)

BATCH = 200
SMS_ON = "sms_on"
SMS_QUOTA = "sms_monthly_quota"


class RemindersOff(AppError):
    code = "REMINDERS_OFF"


class ReminderNotDue(AppError):
    """Nothing is overdue or due today, so there is nothing to remind of (BR-17)."""

    code = "REMINDER_NOT_DUE"


class LimitReached(AppError):
    """A manual reminder was already sent to this customer today (REQ-025)."""

    code = "REMINDER_LIMIT_REACHED"


class CustomerUnreachable(AppError):
    code = "CUSTOMER_UNREACHABLE"


async def sms_allowance(session: TenantSession, today: date) -> tuple[bool, int, int]:
    """Whether the platform offers SMS, the month's quota of a shop, and how many this shop may still
    send this month. With the free plan on, a shop that is not in a paid period may send none (BR-35);
    what the platform offers is still told, so that its owner can be shown what paying adds."""
    on = await session.platform_setting(SMS_ON) is True
    quota = platform_settings.effective(SMS_QUOTA, await session.platform_setting(SMS_QUOTA))
    if not on or isinstance(quota, bool) or not isinstance(quota, int) or quota <= 0:
        return on, 0, 0
    free_plan_on = await free_plan_customers(session) is not None
    if free_plan_on and not sms_included(await stored_subscription(session, today), free_plan_on):
        return on, quota, 0
    return on, quota, max(0, quota - await session.sms_reminders_since(today.replace(day=1)))


def reminder_text(lang: str, template: int, plan: ReminderPlan, channel: Channel, *, shop: str, name: str) -> str:
    """The fixed wording (REQ-024). SMS uses its own short form, whatever template the shop chose."""
    if channel is Channel.SMS:
        # So'm only: see `qarz.domain.reminders.choose_channel`. Only the Uzbek and the Russian wording
        # are registered with the provider, so every other language is sent the Uzbek one, whole: the
        # amount's unit too.
        language = languages.sms_language(lang)
        return say(language, f"sms_{plan.kind.value}", shop=shop, name=name, amount=money(language, plan.amount))
    language = lang if languages.is_language(lang) else languages.DEFAULT
    return say(
        language,
        f"r{template}_{plan.kind.value}",
        shop=shop,
        name=name,
        amount=both(language, plan.amount, plan.amount_usd),
    )


def _by_sms(candidate: ReminderCandidate, plan: ReminderPlan) -> bool:
    """Whether an SMS could carry this reminder: there is a number, and a so'm amount to state."""
    return bool(candidate.phone) and plan.amount > 0


def _statuses(
    entries: Sequence[ledger.Entry], today: date, dollars: bool
) -> tuple[ledger.OverdueStatus, ledger.OverdueStatus | None]:
    """What is due in the so'm book and, for a shop that works in dollars, in the dollar book."""
    return (
        ledger.overdue(ledger.in_currency(entries, UZS), today),
        ledger.overdue(ledger.in_currency(entries, USD), today) if dollars else None,
    )


def _settings_body(settings: ReminderSettings) -> dict[str, Any]:
    return {
        "on": settings.on,
        "hour": settings.hour,
        "template": settings.template,
        "sms_on": settings.sms_on,
        "hours": [FIRST_HOUR, LAST_HOUR],
        "templates": [
            {
                "id": template,
                "due_today": {lang: wording(lang, f"r{template}_due_today") for lang in languages.LANGUAGES},
                "overdue": {lang: wording(lang, f"r{template}_overdue") for lang in languages.LANGUAGES},
            }
            for template in TEMPLATES
        ],
    }


class ReminderService:
    def __init__(self, storage: Storage, now: Callable[[], datetime] | None = None) -> None:
        self._storage = storage
        self._now = now or (lambda: datetime.now(UTC))

    def _today(self) -> date:
        return tashkent_date(self._now())

    # --- settings -------------------------------------------------------------------------------------

    async def settings(self, user_id: UUID, shop_id: UUID) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, READ_REMINDER_SETTINGS)
            await require_viewable(session, actor, self._today())
            settings = await session.reminder_settings()
            if settings is None:
                raise NotFound()
            return _settings_body(settings)

    async def update_settings(
        self,
        user_id: UUID,
        shop_id: UUID,
        *,
        on: bool | None,
        hour: int | None,
        template: int | None,
        sms_on: bool | None,
        request_key: str | None,
    ) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, UPDATE_REMINDER_SETTINGS)
            key = idempotency.validate_key(request_key)
            fields: dict[str, str] = {}
            if on is None and hour is None and template is None and sms_on is None:
                fields["_"] = "nothing to change"
            if hour is not None and (isinstance(hour, bool) or not FIRST_HOUR <= hour <= LAST_HOUR):
                fields["hour"] = f"a whole hour between {FIRST_HOUR} and {LAST_HOUR}"
            if template is not None and (isinstance(template, bool) or template not in TEMPLATES):
                fields["template"] = f"one of {list(TEMPLATES)}"
            if fields:
                raise ValidationFailed(fields)
            await require_writable(session, self._today(), new_credit=False)

            async def apply() -> dict[str, Any]:
                await session.update_reminder_settings(on=on, hour=hour, template=template, sms_on=sms_on)
                await session.record_activity(
                    membership_id=actor.membership_id,
                    action="reminders.settings_changed",
                    subject_type="shop",
                    subject_id=shop_id,
                )
                settings = await session.reminder_settings()
                if settings is None:
                    raise NotFound()
                return _settings_body(settings)

            return await idempotency.run_once(
                session,
                key=key,
                operation=UPDATE_REMINDER_SETTINGS.name,
                user_id=user_id,
                request={"on": on, "hour": hour, "template": template, "sms_on": sms_on},
                action=apply,
            )

    # --- sending --------------------------------------------------------------------------------------

    async def _sms_left(self, session: TenantSession, today: date) -> tuple[bool, int]:
        """Whether SMS is on for the platform, and how many the shop may still send this month."""
        on, _, left = await sms_allowance(session, today)
        return on, left

    async def _send(
        self,
        session: TenantSession,
        settings: ReminderSettings,
        candidate: ReminderCandidate,
        plan: ReminderPlan,
        *,
        kind: str,
        today: date,
        sms: tuple[bool, int],
    ) -> Channel | None:
        """Store the reminder and queue its message. None when the customer cannot be reached (BR-18)."""
        channel = choose_channel(
            telegram_reachable=candidate.tg_id is not None,
            has_phone=_by_sms(candidate, plan),
            sms_on_platform=sms[0],
            sms_on_shop=settings.sms_on,
            sms_quota_left=sms[1],
        )
        if channel is None:
            return None
        if not await session.add_reminder(
            customer_id=candidate.customer_id,
            kind=kind,
            channel=channel.value,
            amount=plan.amount,
            # What the message stated: an SMS states no dollars.
            amount_usd=0 if channel is Channel.SMS else plan.amount_usd,
            sent_on=today,
        ):
            raise LimitReached()
        # BR-19: the customer's language if known, else the shop's.
        lang = candidate.lang or settings.lang
        text = reminder_text(lang, settings.template, plan, channel, shop=settings.name, name=candidate.display_name)
        recipient = str(candidate.tg_id) if channel is Channel.TELEGRAM else str(candidate.phone)
        await session.enqueue(
            channel=channel.value,
            recipient=recipient,
            payload={"text": text},
            dedupe_key=f"reminder:{kind}:{candidate.customer_id}:{today.isoformat()}",
        )
        return channel

    async def send_manual(
        self, user_id: UUID, shop_id: UUID, customer_id: UUID, request_key: str | None
    ) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, SEND_REMINDER)
            key = idempotency.validate_key(request_key)
            today = self._today()
            await require_writable(session, today, new_credit=False)

            async def apply() -> dict[str, Any]:
                settings = await session.reminder_settings()
                candidate = await session.reminder_candidate(customer_id)
                if settings is None or candidate is None:
                    raise NotFound()
                # BR-17 holds for a reminder sent by hand as well.
                if not settings.on or candidate.reminders_off:
                    raise RemindersOff()
                dollars = await dollars_on(session)
                accounts = await session.entries_of_many([customer_id])
                plan = plan_manual(*_statuses(accounts[customer_id], today, dollars))
                if plan is None:
                    raise ReminderNotDue()
                channel = await self._send(
                    session,
                    settings,
                    candidate,
                    plan,
                    kind="manual",
                    today=today,
                    sms=await self._sms_left(session, today),
                )
                if channel is None:
                    raise CustomerUnreachable()
                await session.record_activity(
                    membership_id=actor.membership_id,
                    action="reminder.sent_manually",
                    subject_type="customer",
                    subject_id=customer_id,
                )
                body: dict[str, Any] = {"sent": True, "channel": channel.value, "amount": plan.amount}
                if dollars:
                    body["usd"] = {"amount": 0 if channel is Channel.SMS else plan.amount_usd}
                return body

            return await idempotency.run_once(
                session,
                key=key,
                operation=SEND_REMINDER.name,
                user_id=user_id,
                request={"customer": str(customer_id), "day": today},
                action=apply,
            )

    async def unreachable(self, user_id: UUID, shop_id: UUID) -> dict[str, Any]:
        """Customers who have something overdue or due today and no channel to be reminded through."""
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, LIST_UNREACHABLE)
            today = self._today()
            await require_viewable(session, actor, today)
            settings = await session.reminder_settings()
            if settings is None:
                raise NotFound()
            sms = await self._sms_left(session, today)
            dollars = await dollars_on(session)
            currencies = await shop_currencies(session)
            items: list[dict[str, Any]] = []
            after: UUID | None = None
            while True:
                batch = await session.reminder_candidates(after=after, limit=BATCH, currencies=currencies)
                if not batch:
                    break
                accounts = await session.entries_of_many([candidate.customer_id for candidate in batch])
                for candidate in batch:
                    plan = plan_manual(*_statuses(accounts[candidate.customer_id], today, dollars))
                    if plan is None:
                        continue
                    reachable = choose_channel(
                        telegram_reachable=candidate.tg_id is not None,
                        has_phone=_by_sms(candidate, plan),
                        sms_on_platform=sms[0],
                        sms_on_shop=settings.sms_on,
                        sms_quota_left=sms[1],
                    )
                    if reachable is None:
                        items.append(
                            {
                                "customer_id": str(candidate.customer_id),
                                "display_name": candidate.display_name,
                                "phone": candidate.phone,
                                "amount": plan.amount,
                                **({"usd": {"amount": plan.amount_usd}} if dollars else {}),
                            }
                        )
                after = batch[-1].customer_id
            return {"items": items}

    # --- the hourly job -------------------------------------------------------------------------------

    async def run_hour(self, hour: int) -> int:
        """Send the automatic reminders of every shop whose chosen hour this is. Safe to repeat."""
        async with self._storage.platform() as platform:
            shops = await platform.shops_due_for_reminders(hour)
        sent = 0
        for shop_id in shops:
            sent += await self._remind_shop(shop_id)
        return sent

    async def _remind_shop(self, shop_id: UUID) -> int:
        today = self._today()
        sent = 0
        async with self._storage.tenant(shop_id) as session:
            settings = await session.reminder_settings()
            # Read again inside the shop: the list of shops was only where to look.
            if settings is None or not settings.on or await effective_subscription(session, today) == "suspended":
                return 0
            sms_on, sms_left = await self._sms_left(session, today)
            dollars = await dollars_on(session)
            currencies = await shop_currencies(session)
            after: UUID | None = None
            while True:
                batch = await session.reminder_candidates(after=after, limit=BATCH, currencies=currencies)
                if not batch:
                    break
                ids = [candidate.customer_id for candidate in batch]
                accounts = await session.entries_of_many(ids)
                last = await session.last_automatic_reminders(ids)
                for candidate in batch:
                    if candidate.reminders_off:
                        continue
                    status, in_dollars = _statuses(accounts[candidate.customer_id], today, dollars)
                    plan = plan_automatic(status, last.get(candidate.customer_id), today, in_dollars)
                    if plan is None:
                        continue
                    channel = await self._send(
                        session, settings, candidate, plan, kind="auto", today=today, sms=(sms_on, sms_left)
                    )
                    if channel is Channel.SMS:
                        sms_left -= 1
                    if channel is not None:
                        sent += 1
                after = batch[-1].customer_id
        return sent

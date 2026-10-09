"""Reminders (REQ-022 to REQ-025, REQ-042, REQ-043; BR-13, BR-17 to BR-19; REQ-N10).

Off until the shop turns them on. One on the promised date, then at most one every seven days while the
debt stays overdue; a manager or owner may send one by hand, once a day per customer. A reminder states
the shop and the amount and nothing else, in a fixed polite wording the shop picks from.

**An SMS and dollars.** Every SMS wording is registered with the provider before it may be sent, and the
registered ones state one so'm amount. So an SMS is the reminder of the so'm book alone, planned as if
the shop had no dollars: its ground, its kind (due today or overdue) and its amount are the so'm
book's, and it is not sent when only dollars are due. It never states a dollar amount, never a figure
that includes one, and nothing in it says that the amount is all that is owed. What it leaves out the
staff are told: the answer to a reminder sent by hand names the dollars it did not state
(`usd.unstated`), the settings of a shop that works in dollars say that SMS carries so'm only
(`usd.sms`), and a customer whose dollar debt is due and who has no Telegram is on the list of those
who cannot be reached, with the reason (`reason`), whether or not an SMS tells them of their so'm.
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


class DollarsNeedTelegram(CustomerUnreachable):
    """Only dollars are due, and the customer has no Telegram: an SMS could not say what is owed."""

    wording = "CUSTOMER_UNREACHABLE_USD"


# Why a customer is on the list of those who cannot be reached, told only in a shop that works in
# dollars: nothing reaches them at all, or their dollar debt is due and only Telegram could carry it.
NO_CHANNEL = "no_channel"
USD_NEEDS_TELEGRAM = "usd_needs_telegram"


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
        # So'm only, and so the caller's plan is the so'm book's alone (see the module's docstring). Only
        # the Uzbek and the Russian wording are registered with the provider, so every other language
        # is sent the Uzbek one, whole: the amount's unit too.
        if plan.amount <= 0 or plan.amount_usd:
            raise ValueError("an SMS states a so'm amount and nothing else")
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


def _by_sms(candidate: ReminderCandidate, in_sum: ReminderPlan | None) -> bool:
    """Whether an SMS could carry a reminder: there is a number, and the so'm book has one to send."""
    return bool(candidate.phone) and in_sum is not None


def _statuses(
    entries: Sequence[ledger.Entry], today: date, dollars: bool
) -> tuple[ledger.OverdueStatus, ledger.OverdueStatus | None]:
    """What is due in the so'm book and, for a shop that works in dollars, in the dollar book."""
    return (
        ledger.overdue(ledger.in_currency(entries, UZS), today),
        ledger.overdue(ledger.in_currency(entries, USD), today) if dollars else None,
    )


def _settings_body(settings: ReminderSettings, dollars: bool = False) -> dict[str, Any]:
    """`dollars`: the shop works in dollars, and is told that an SMS does not carry them. The body of
    every other shop is what it always was."""
    return {
        **({"usd": {"sms": False}} if dollars else {}),
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
            return _settings_body(settings, await dollars_on(session))

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
                return _settings_body(settings, await dollars_on(session))

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
        in_sum: ReminderPlan | None,
        *,
        kind: str,
        today: date,
        sms: tuple[bool, int],
    ) -> tuple[Channel, ReminderPlan] | None:
        """Store the reminder and queue its message. None when the customer cannot be reached (BR-18).

        `plan` is the reminder of everything that is due; `in_sum` the reminder the so'm book alone
        would send, which is all an SMS can be. Returns the channel and what the message stated.
        """
        channel = choose_channel(
            telegram_reachable=candidate.tg_id is not None,
            has_phone=_by_sms(candidate, in_sum),
            sms_on_platform=sms[0],
            sms_on_shop=settings.sms_on,
            sms_quota_left=sms[1],
        )
        if channel is None:
            return None
        stated = plan
        if channel is Channel.SMS:
            assert in_sum is not None  # `_by_sms`
            stated = in_sum
        if not await session.add_reminder(
            customer_id=candidate.customer_id,
            kind=kind,
            channel=channel.value,
            # What the message stated: an SMS states no dollars.
            amount=stated.amount,
            amount_usd=stated.amount_usd,
            sent_on=today,
        ):
            raise LimitReached()
        # BR-19: the customer's language if known, else the shop's.
        lang = candidate.lang or settings.lang
        text = reminder_text(lang, settings.template, stated, channel, shop=settings.name, name=candidate.display_name)
        recipient = str(candidate.tg_id) if channel is Channel.TELEGRAM else str(candidate.phone)
        await session.enqueue(
            channel=channel.value,
            recipient=recipient,
            payload={"text": text},
            dedupe_key=f"reminder:{kind}:{candidate.customer_id}:{today.isoformat()}",
        )
        return channel, stated

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
                status, in_dollars = _statuses(accounts[customer_id], today, dollars)
                plan = plan_manual(status, in_dollars)
                if plan is None:
                    raise ReminderNotDue()
                in_sum = plan_manual(status)
                sent = await self._send(
                    session,
                    settings,
                    candidate,
                    plan,
                    in_sum,
                    kind="manual",
                    today=today,
                    sms=await self._sms_left(session, today),
                )
                if sent is None:
                    if in_sum is None:
                        # Only dollars are due: a number and SMS would not have helped, and the words say so.
                        raise DollarsNeedTelegram({"reason": USD_NEEDS_TELEGRAM})
                    raise CustomerUnreachable()
                channel, stated = sent
                await session.record_activity(
                    membership_id=actor.membership_id,
                    action="reminder.sent_manually",
                    subject_type="customer",
                    subject_id=customer_id,
                )
                body: dict[str, Any] = {"sent": True, "channel": channel.value, "amount": stated.amount}
                if dollars:
                    body["usd"] = {"amount": stated.amount_usd}
                    if plan.amount_usd > stated.amount_usd:
                        # Due in dollars and not said: the SMS stated so'm only, and whoever sent it is told.
                        body["usd"]["unstated"] = plan.amount_usd - stated.amount_usd
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
                    status, in_dollars = _statuses(accounts[candidate.customer_id], today, dollars)
                    plan = plan_manual(status, in_dollars)
                    if plan is None:
                        continue
                    reachable = choose_channel(
                        telegram_reachable=candidate.tg_id is not None,
                        has_phone=_by_sms(candidate, plan_manual(status)),
                        sms_on_platform=sms[0],
                        sms_on_shop=settings.sms_on,
                        sms_quota_left=sms[1],
                    )
                    # A dollar debt that is due reaches a customer through Telegram or not at all: one
                    # whom an SMS tells of their so'm is still listed for their dollars.
                    if reachable is None or (reachable is Channel.SMS and plan.amount_usd > 0):
                        item: dict[str, Any] = {
                            "customer_id": str(candidate.customer_id),
                            "display_name": candidate.display_name,
                            "phone": candidate.phone,
                            "amount": plan.amount,
                        }
                        if dollars:
                            item["usd"] = {"amount": plan.amount_usd}
                            item["reason"] = USD_NEEDS_TELEGRAM if plan.amount_usd > 0 else NO_CHANNEL
                        items.append(item)
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
                    last_sent = last.get(candidate.customer_id)
                    plan = plan_automatic(status, last_sent, today, in_dollars)
                    if plan is None:
                        continue
                    in_sum = plan_automatic(status, last_sent, today)
                    done = await self._send(
                        session, settings, candidate, plan, in_sum, kind="auto", today=today, sms=(sms_on, sms_left)
                    )
                    if done is not None and done[0] is Channel.SMS:
                        sms_left -= 1
                    if done is not None:
                        sent += 1
                after = batch[-1].customer_id
        return sent

"""The shop's subscription as its owner sees it, and the daily review (REQ-052, REQ-053, REQ-057).

Seven days and one day before a trial or paid period ends the owner is warned; the day after it ends the
shop becomes limited and the owner is told what still works and how to pay (BR-28, BR-29).
"""

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from qarz.application.chat_texts import day, money, say
from qarz.application.errors import NotFound
from qarz.application.operations import operation
from qarz.application.ports import Storage, TenantSession
from qarz.application.shops import require_member
from qarz.domain.access import Capability
from qarz.domain.promise import tashkent_date
from qarz.domain.subscription import (
    ACTIVE,
    DEFAULT_PRICE_UZS,
    LIMITED,
    TRIAL,
    effective_state,
    period_end,
    warning_days,
)

READ_SUBSCRIPTION = operation("shop.subscription.read", Capability.ADMINISTER_SHOP)

PRICE = "price_uzs"
CARD = "card_number"


async def subscription_body(session: TenantSession, today: date) -> dict[str, Any]:
    row = await session.subscription()
    state, trial_ends, paid_through = row if row is not None else (LIMITED, None, None)
    effective = effective_state(state, trial_ends, paid_through, today)
    end = period_end(state, trial_ends, paid_through) if effective in (TRIAL, ACTIVE) else None
    price = await session.platform_setting(PRICE)
    card = await session.platform_setting(CARD)
    return {
        "state": effective,
        "trial_ends": None if trial_ends is None else trial_ends.isoformat(),
        "paid_through": None if paid_through is None else paid_through.isoformat(),
        "ends_on": None if end is None else end.isoformat(),
        "days_left": None if end is None else (end - today).days,
        "price_uzs": price
        if isinstance(price, int) and not isinstance(price, bool) and price > 0
        else DEFAULT_PRICE_UZS,
        # Where to transfer the payment (REQ-054). Absent until the administrator has set it.
        "card_number": card if isinstance(card, str) and card.strip() else None,
    }


def subscription_text(lang: str, shop: str, body: dict[str, Any]) -> str:
    """What `/obuna` says."""
    lines = [say(lang, "sub_header", shop=shop)]
    if body["ends_on"] is not None:
        key = "sub_state_trial" if body["state"] == TRIAL else "sub_state_active"
        lines.append(say(lang, key, date=day(date.fromisoformat(body["ends_on"])), days=body["days_left"]))
    else:
        lines.append(say(lang, f"sub_state_{body['state']}"))
    lines.append(say(lang, "sub_price", price=money(lang, body["price_uzs"])))
    if body["card_number"] is None:
        lines.append(say(lang, "sub_no_card"))
    else:
        lines.append(say(lang, "sub_pay_to", card=body["card_number"]))
    return "\n".join(lines)


class SubscriptionService:
    def __init__(self, storage: Storage, now: Callable[[], datetime] | None = None) -> None:
        self._storage = storage
        self._now = now or (lambda: datetime.now(UTC))

    def _today(self) -> date:
        return tashkent_date(self._now())

    async def state(self, user_id: UUID, shop_id: UUID) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            # The owner may always see this, in every mode: it says how to leave the mode.
            await require_member(session, user_id, READ_SUBSCRIPTION)
            return await subscription_body(session, self._today())

    async def chat_text(self, user_id: UUID, shop_id: UUID, lang: str) -> str:
        async with self._storage.tenant(shop_id) as session:
            await require_member(session, user_id, READ_SUBSCRIPTION)
            settings = await session.shop_settings()
            if settings is None:
                raise NotFound()
            return subscription_text(lang, settings.name, await subscription_body(session, self._today()))

    async def run_daily(self) -> int:
        """Warn owners and move ended periods to limited. Safe to repeat on the same day."""
        today = self._today()
        async with self._storage.platform() as platform:
            rows = await platform.subscriptions_to_review(today)
        told = 0
        for row in rows:
            async with self._storage.tenant(row.shop_id) as session:
                stored = await session.subscription()
                if stored is None:
                    continue
                state, trial_ends, paid_through = stored
                end = period_end(state, trial_ends, paid_through)
                lang = row.owner_lang or "uz"
                text: str | None = None
                if state in (TRIAL, ACTIVE) and (end is None or end < today):
                    # Read and changed inside the shop: the list was only where to look.
                    await session.limit_subscription(self._now())
                    await session.record_system_activity(action="subscription.limited", subject_id=row.shop_id)
                    text, key = say(lang, "sub_limited", shop=row.shop_name), f"sub:limited:{row.shop_id}:{end}"
                elif end is not None and (days := warning_days(end, today)) is not None:
                    which = "sub_trial_ending" if state == TRIAL else "sub_paid_ending"
                    text = say(lang, which, shop=row.shop_name, days=days, date=day(end))
                    key = f"sub:warn:{row.shop_id}:{end}:{days}"
                if text is None or row.owner_tg is None:
                    continue
                if await session.enqueue(recipient=str(row.owner_tg), payload={"text": text}, dedupe_key=key):
                    told += 1
        return told

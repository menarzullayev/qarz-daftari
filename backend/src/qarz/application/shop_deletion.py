"""Deleting a shop (REQ-048, BR-25).

The owner asks, confirming with the shop's name. For 30 days the shop goes on as before and the owner can
export or cancel. Then the shop's data is erased: customer links end, staff lose access, and nothing of
the shop remains but a tombstone row.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from qarz.application import idempotency
from qarz.application.chat_texts import day, say
from qarz.application.errors import AppError, NotFound, ValidationFailed
from qarz.application.operations import operation
from qarz.application.ports import Storage, TenantSession
from qarz.application.shops import require_member
from qarz.domain.access import Capability
from qarz.domain.promise import tashkent_date
from qarz.domain.shop_deletion import erasure_due, name_confirms

REQUEST_DELETION = operation("shop.deletion.request", Capability.ADMINISTER_SHOP)
CANCEL_DELETION = operation("shop.deletion.cancel", Capability.ADMINISTER_SHOP)
READ_DELETION = operation("shop.deletion.read", Capability.ADMINISTER_SHOP)


class DeletionAlreadyRequested(AppError):
    code = "DELETION_ALREADY_REQUESTED"


class DeletionNotRequested(AppError):
    code = "DELETION_NOT_REQUESTED"


def _body(status: str, due: datetime | None) -> dict[str, Any]:
    return {"status": status, "deletion_due": None if due is None else due.isoformat()}


async def _tell_owner(session: TenantSession, shop_id: UUID, dedupe: str, key: str, **values: Any) -> None:
    for tg_id, lang in await session.staff_recipients(["owner"]):
        await session.enqueue(
            recipient=str(tg_id), payload={"text": say(lang, key, **values)}, dedupe_key=f"{dedupe}:{shop_id}:{tg_id}"
        )


class ShopDeletionService:
    def __init__(self, storage: Storage, now: Callable[[], datetime] | None = None) -> None:
        self._storage = storage
        self._now = now or (lambda: datetime.now(UTC))

    async def state(self, user_id: UUID, shop_id: UUID) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            await require_member(session, user_id, READ_DELETION)
            return _body(*await session.deletion_state())

    async def request(self, user_id: UUID, shop_id: UUID, confirm_name: str, request_key: str | None) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, REQUEST_DELETION)
            key = idempotency.validate_key(request_key)
            settings = await session.shop_settings()
            if settings is None:
                raise NotFound()
            if not name_confirms(confirm_name, settings.name):
                raise ValidationFailed({"confirm_name": "must be the shop's name"})

            async def apply() -> dict[str, Any]:
                status, _ = await session.deletion_state(for_update=True)
                if status != "active":
                    raise DeletionAlreadyRequested()
                due = erasure_due(self._now())
                await session.set_deletion(status="deletion_pending", due=due)
                await session.record_activity(
                    membership_id=actor.membership_id,
                    action="shop.deletion_requested",
                    subject_type="shop",
                    subject_id=shop_id,
                )
                await _tell_owner(
                    session,
                    shop_id,
                    f"shop:deletion:{due.isoformat()}",
                    "shop_deletion_requested",
                    shop=settings.name,
                    date=day(tashkent_date(due)),
                )
                return _body("deletion_pending", due)

            return await idempotency.run_once(
                session,
                key=key,
                operation=REQUEST_DELETION.name,
                user_id=user_id,
                request={"confirm": " ".join(confirm_name.split()).casefold()},
                action=apply,
            )

    async def cancel(self, user_id: UUID, shop_id: UUID, request_key: str | None) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, CANCEL_DELETION)
            key = idempotency.validate_key(request_key)

            async def apply() -> dict[str, Any]:
                status, due = await session.deletion_state(for_update=True)
                if status != "deletion_pending" or due is None:
                    raise DeletionNotRequested()
                await session.set_deletion(status="active", due=None)
                await session.record_activity(
                    membership_id=actor.membership_id,
                    action="shop.deletion_cancelled",
                    subject_type="shop",
                    subject_id=shop_id,
                )
                settings = await session.shop_settings()
                await _tell_owner(
                    session,
                    shop_id,
                    f"shop:deletion_cancelled:{due.isoformat()}",
                    "shop_deletion_cancelled",
                    shop="" if settings is None else settings.name,
                )
                return _body("active", None)

            return await idempotency.run_once(
                session,
                key=key,
                operation=CANCEL_DELETION.name,
                user_id=user_id,
                request={},
                action=apply,
            )

    async def erase_due(self) -> int:
        """Erase every shop whose waiting period is over. Safe to repeat; the database decides what is due."""
        async with self._storage.platform() as session:
            shops = await session.shops_to_erase()
        erased = 0
        for shop in shops:
            async with self._storage.platform() as session:
                if not await session.erase_shop(shop.shop_id):
                    continue
                erased += 1
                if shop.owner_tg is not None:
                    # Queued without the shop's identifier: the shop's own messages were erased with it.
                    await session.enqueue(
                        channel="telegram",
                        recipient=str(shop.owner_tg),
                        payload={"text": say(shop.owner_lang or "uz", "shop_erased", shop=shop.shop_name)},
                        dedupe_key=f"shop:erased:{shop.shop_id}",
                    )
        return erased

"""The staff side of connecting customers (REQ-013): personal links, the counter code, the waiting list.

What the customer does (opening the link, agreeing, disconnecting) is in `qarz.application.chat`; it runs
through database functions, because a customer is not a member of the shop.
"""

import secrets
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

from qarz.application import idempotency
from qarz.application.chat_texts import say
from qarz.application.customers import CustomerArchived, require_viewable, require_writable
from qarz.application.errors import AppError, NotFound
from qarz.application.operations import operation
from qarz.application.ports import Storage
from qarz.application.shops import require_member
from qarz.application.staff import token_hash
from qarz.domain.access import Capability
from qarz.domain.promise import tashkent_date

READ_LINK = operation("customers.link.read", Capability.RECORD)
CREATE_LINK = operation("customers.link.create", Capability.RECORD)
READ_COUNTER_CODE = operation("counter_code.read", Capability.RECORD)
ROTATE_COUNTER_CODE = operation("counter_code.rotate", Capability.MANAGE)
LIST_WAITING = operation("waiting.list", Capability.RECORD)
ATTACH_WAITING = operation("waiting.attach", Capability.RECORD)
DISMISS_WAITING = operation("waiting.dismiss", Capability.RECORD)

PERSONAL_LINK_LIFETIME = timedelta(days=7)
WAITING_LIFETIME = timedelta(hours=24)  # BR-16
# What follows "/start " in the deep link. Telegram allows 64 characters of A-Z a-z 0-9 _ -.
PERSONAL_PREFIX = "c_"
COUNTER_PREFIX = "k_"


class CustomerAlreadyLinked(AppError):
    code = "CUSTOMER_ALREADY_LINKED"


def _redact(body: dict[str, Any]) -> dict[str, Any]:
    return {**body, "token": None, "start": None}


class LinkService:
    def __init__(self, storage: Storage, now: Callable[[], datetime] | None = None) -> None:
        self._storage = storage
        self._now = now or (lambda: datetime.now(UTC))

    def _today(self) -> date:
        return tashkent_date(self._now())

    async def link_state(self, user_id: UUID, shop_id: UUID, customer_id: UUID) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, READ_LINK)
            await require_viewable(session, actor, self._today())
            customer = await session.get_customer(customer_id, for_update=False)
            if customer is None or customer.status == "anonymized":
                raise NotFound()
            state = await session.link_state(customer_id)
            return {
                "linked": state is not None,
                "status": None if state is None else state[0],
                "since": None if state is None else state[1].isoformat(),
            }

    async def create_link(
        self, user_id: UUID, shop_id: UUID, customer_id: UUID, request_key: str | None
    ) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, CREATE_LINK)
            key = idempotency.validate_key(request_key)
            await require_writable(session, self._today(), new_credit=False)

            async def apply() -> dict[str, Any]:
                customer = await session.get_customer(customer_id, for_update=True)
                if customer is None or customer.status == "anonymized":
                    raise NotFound()
                if customer.status == "archived":
                    raise CustomerArchived()
                if await session.link_state(customer_id) is not None:
                    raise CustomerAlreadyLinked()
                # The token is returned once, here; only its hash is stored, and the stored copy of this
                # response has it removed.
                token = secrets.token_urlsafe(32)
                expires_at = self._now() + PERSONAL_LINK_LIFETIME
                await session.issue_customer_link(token_hash(token), customer_id, expires_at)
                await session.record_activity(
                    membership_id=actor.membership_id,
                    action="customer.link_issued",
                    subject_type="customer",
                    subject_id=customer_id,
                )
                return {"token": token, "start": PERSONAL_PREFIX + token, "expires_at": expires_at.isoformat()}

            return await idempotency.run_once(
                session,
                key=key,
                operation=CREATE_LINK.name,
                user_id=user_id,
                request={"customer": str(customer_id)},
                action=apply,
                redact=_redact,
            )

    async def counter_code(self, user_id: UUID, shop_id: UUID) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, READ_COUNTER_CODE)
            await require_viewable(session, actor, self._today())
            since = await session.counter_code_since()
            return {"exists": since is not None, "since": None if since is None else since.isoformat()}

    async def rotate_counter_code(self, user_id: UUID, shop_id: UUID, request_key: str | None) -> dict[str, Any]:
        """Issue the shop's counter code, cancelling the one before it. The code is shown once, to be printed."""
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, ROTATE_COUNTER_CODE)
            key = idempotency.validate_key(request_key)
            await require_writable(session, self._today(), new_credit=False)

            async def apply() -> dict[str, Any]:
                token = secrets.token_urlsafe(32)
                await session.rotate_counter_code(token_hash(token))
                await session.record_activity(
                    membership_id=actor.membership_id,
                    action="counter_code.rotated",
                    subject_type="shop",
                    subject_id=shop_id,
                )
                return {"token": token, "start": COUNTER_PREFIX + token}

            return await idempotency.run_once(
                session,
                key=key,
                operation=ROTATE_COUNTER_CODE.name,
                user_id=user_id,
                request={},
                action=apply,
                redact=_redact,
            )

    async def waiting(self, user_id: UUID, shop_id: UUID) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, LIST_WAITING)
            await require_viewable(session, actor, self._today())
            rows = await session.waiting_links(self._now() - WAITING_LIFETIME)
            return {
                "items": [{"id": str(row.link_id), "name": row.name, "since": row.since.isoformat()} for row in rows]
            }

    async def attach(
        self, user_id: UUID, shop_id: UUID, link_id: UUID, customer_id: UUID, request_key: str | None
    ) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, ATTACH_WAITING)
            key = idempotency.validate_key(request_key)
            await require_writable(session, self._today(), new_credit=False)

            async def apply() -> dict[str, Any]:
                # The customer row is locked first, as in every write to an account, so that this and a
                # personal link opened at the same moment cannot both attach someone.
                customer = await session.get_customer(customer_id, for_update=True)
                if customer is None or customer.status == "anonymized":
                    raise NotFound()
                if customer.status == "archived":
                    raise CustomerArchived()
                if await session.link_state(customer_id) is not None:
                    raise CustomerAlreadyLinked()
                recipient = await session.waiting_recipient(link_id)
                if not await session.attach_waiting(link_id, customer_id, self._now() - WAITING_LIFETIME):
                    raise NotFound()
                await session.record_activity(
                    membership_id=actor.membership_id,
                    action="customer.linked",
                    subject_type="customer",
                    subject_id=customer_id,
                )
                if recipient is not None:
                    settings = await session.shop_settings()
                    tg_id, lang = recipient
                    await session.enqueue(
                        recipient=str(tg_id),
                        payload={"text": say(lang, "linked", shop="" if settings is None else settings.name)},
                        dedupe_key=f"link:{link_id}:attached",
                    )
                return {"customer_id": str(customer_id), "linked": True}

            return await idempotency.run_once(
                session,
                key=key,
                operation=ATTACH_WAITING.name,
                user_id=user_id,
                request={"link": str(link_id), "customer": str(customer_id)},
                action=apply,
            )

    async def dismiss(self, user_id: UUID, shop_id: UUID, link_id: UUID, request_key: str | None) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            await require_member(session, user_id, DISMISS_WAITING)
            key = idempotency.validate_key(request_key)
            await require_writable(session, self._today(), new_credit=False)

            async def apply() -> dict[str, Any]:
                if not await session.dismiss_waiting(link_id, self._now()):
                    raise NotFound()
                return {"dismissed": True}

            return await idempotency.run_once(
                session,
                key=key,
                operation=DISMISS_WAITING.name,
                user_id=user_id,
                request={"link": str(link_id)},
                action=apply,
            )

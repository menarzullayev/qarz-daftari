"""Ownership transfer (REQ-036, BR-22): the owner offers the shop to an active manager, who must accept."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from qarz.application import idempotency
from qarz.application.errors import AppError, NotFound
from qarz.application.operations import operation
from qarz.application.ports import Storage, TransferRecord
from qarz.application.shops import refuse_suspended, require_member
from qarz.domain.access import Capability, Role

START_TRANSFER = operation("ownership.transfer.start", Capability.ADMINISTER_SHOP)
CANCEL_TRANSFER = operation("ownership.transfer.cancel", Capability.ADMINISTER_SHOP)
READ_TRANSFER = operation("ownership.transfer.read", Capability.READ_SHOP)
ACCEPT_TRANSFER = operation("ownership.transfer.accept", Capability.MANAGE)
DECLINE_TRANSFER = operation("ownership.transfer.decline", Capability.MANAGE)

TRANSFER_LIFETIME = timedelta(hours=48)


class TransferPending(AppError):
    """A transfer is already waiting for an answer."""

    code = "TRANSFER_PENDING"


class TransferTargetInvalid(AppError):
    """Ownership can be offered only to an active manager of the same shop."""

    code = "TRANSFER_TARGET_INVALID"


class NotTransferTarget(AppError):
    """Only the manager the shop was offered to can answer."""

    code = "NOT_TRANSFER_TARGET"


def _body(transfer: TransferRecord) -> dict[str, Any]:
    return {
        "id": str(transfer.transfer_id),
        "from_membership": str(transfer.from_membership),
        "to_membership": str(transfer.to_membership),
        "status": transfer.status,
        "expires_at": transfer.expires_at.isoformat(),
    }


class OwnershipService:
    def __init__(self, storage: Storage, now: Callable[[], datetime] | None = None) -> None:
        self._storage = storage
        self._now = now or (lambda: datetime.now(UTC))

    async def start(self, user_id: UUID, shop_id: UUID, to_membership: UUID, request_key: str | None) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, START_TRANSFER)
            await refuse_suspended(session)
            key = idempotency.validate_key(request_key)

            async def apply() -> dict[str, Any]:
                now = self._now()
                await session.expire_transfers(now)
                if await session.pending_transfer() is not None:
                    raise TransferPending()
                target = await session.get_member(to_membership)
                if target is None or target.role is not Role.MANAGER or target.status != "active":
                    raise TransferTargetInvalid()
                transfer = await session.create_transfer(
                    transfer_id=uuid4(),
                    from_membership=actor.membership_id,
                    to_membership=to_membership,
                    now=now,
                    expires_at=now + TRANSFER_LIFETIME,
                )
                await session.record_activity(
                    membership_id=actor.membership_id,
                    action="ownership.transfer_started",
                    subject_type="membership",
                    subject_id=to_membership,
                )
                return _body(transfer)

            return await idempotency.run_once(
                session,
                key=key,
                operation=START_TRANSFER.name,
                user_id=user_id,
                request={"to": str(to_membership)},
                action=apply,
            )

    async def read(self, user_id: UUID, shop_id: UUID) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            await require_member(session, user_id, READ_TRANSFER)
            await session.expire_transfers(self._now())
            transfer = await session.pending_transfer()
            return {"pending": None if transfer is None else _body(transfer)}

    async def _decide(
        self, user_id: UUID, shop_id: UUID, request_key: str | None, *, accept: bool, by_owner: bool
    ) -> dict[str, Any]:
        op = CANCEL_TRANSFER if by_owner else (ACCEPT_TRANSFER if accept else DECLINE_TRANSFER)
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, op)
            await refuse_suspended(session)
            key = idempotency.validate_key(request_key)

            async def apply() -> dict[str, Any]:
                now = self._now()
                await session.expire_transfers(now)
                transfer = await session.pending_transfer()
                if transfer is None:
                    raise NotFound()
                if not by_owner and transfer.to_membership != actor.membership_id:
                    raise NotTransferTarget()

                if by_owner:
                    status, action = "cancelled", "ownership.transfer_cancelled"
                elif accept:
                    # Re-check both sides at the moment of acceptance: either may have changed since.
                    giver = await session.get_member(transfer.from_membership)
                    taker = await session.get_member(transfer.to_membership)
                    if (
                        giver is None
                        or giver.role is not Role.OWNER
                        or giver.status != "active"
                        or taker is None
                        or taker.role is not Role.MANAGER
                        or taker.status != "active"
                    ):
                        raise TransferTargetInvalid()
                    # Demote first: the database allows only one active owner per shop at any moment.
                    await session.update_member(transfer.from_membership, role=Role.MANAGER, status=None)
                    await session.update_member(transfer.to_membership, role=Role.OWNER, status=None)
                    status, action = "accepted", "ownership.transferred"
                else:
                    status, action = "declined", "ownership.transfer_declined"

                closed = await session.close_transfer(transfer.transfer_id, status=status, now=now)
                await session.record_activity(
                    membership_id=actor.membership_id,
                    action=action,
                    subject_type="membership",
                    subject_id=transfer.to_membership,
                )
                return _body(closed)

            return await idempotency.run_once(
                session, key=key, operation=op.name, user_id=user_id, request={}, action=apply
            )

    async def cancel(self, user_id: UUID, shop_id: UUID, request_key: str | None) -> dict[str, Any]:
        return await self._decide(user_id, shop_id, request_key, accept=False, by_owner=True)

    async def accept(self, user_id: UUID, shop_id: UUID, request_key: str | None) -> dict[str, Any]:
        return await self._decide(user_id, shop_id, request_key, accept=True, by_owner=False)

    async def decline(self, user_id: UUID, shop_id: UUID, request_key: str | None) -> dict[str, Any]:
        return await self._decide(user_id, shop_id, request_key, accept=False, by_owner=False)

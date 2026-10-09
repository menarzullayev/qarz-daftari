"""Staff of a shop: members, roles, invitations (REQ-031 to REQ-035).

The owner's by default. While the per-member permissions are on, the owner may give `staff.manage` to
another member; that member then manages sellers only, and never above their own rights (`_within_own`).
"""

import hashlib
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from qarz.application import idempotency
from qarz.application.authorization import effective_of
from qarz.application.errors import AppError, BeyondOwnPermissions, NotFound, ValidationFailed
from qarz.application.operations import operation, self_operation
from qarz.application.ports import MemberRecord, Membership, Storage
from qarz.application.shops import refuse_suspended, require_member
from qarz.domain import permissions
from qarz.domain.access import Capability, Role

LIST_STAFF = operation("staff.list", Capability.ADMINISTER_SHOP)
INVITE_STAFF = operation("staff.invite", Capability.ADMINISTER_SHOP)
LIST_INVITATIONS = operation("staff.invitations.list", Capability.ADMINISTER_SHOP)
CANCEL_INVITATION = operation("staff.invitations.cancel", Capability.ADMINISTER_SHOP)
UPDATE_STAFF = operation("staff.update", Capability.ADMINISTER_SHOP)
REMOVE_STAFF = operation("staff.remove", Capability.ADMINISTER_SHOP)
ACCEPT_INVITATION = self_operation("staff.invitations.accept")

INVITATION_LIFETIME = timedelta(days=7)
INVITABLE_ROLES = (Role.MANAGER, Role.SELLER)


class OwnerMembershipFixed(AppError):
    """The owner's membership changes only through an ownership transfer (REQ-036)."""

    code = "OWNER_MEMBERSHIP_FIXED"


def _within_own(
    actor: Membership,
    role: Role,
    granted: frozenset[str] = frozenset(),
    denied: frozenset[str] = frozenset(),
    *,
    target: UUID | None = None,
) -> None:
    """Refuse a member who manages staff without being the owner anything above their own rights.

    The owner is never refused here. Anyone else may act only on a seller, never on themselves, and only
    when everything that seller holds (or an invited seller would hold) is something they hold too: a
    member cannot hand out, or bring back into the shop, a permission they do not have. Roles and
    permissions themselves are changed by the owner alone.
    """
    if actor.role is Role.OWNER:
        return
    if role is not Role.SELLER or target == actor.membership_id:
        raise BeyondOwnPermissions()
    held = permissions.effective(role, granted, denied) if actor.permissions_on else permissions.effective(role)
    if not held <= effective_of(actor):
        raise BeyondOwnPermissions()


def token_hash(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def _member_body(member: MemberRecord) -> dict[str, Any]:
    return {
        "id": str(member.membership_id),
        "user_id": str(member.user_id),
        "role": member.role.value,
        "status": member.status,
    }


class StaffService:
    def __init__(self, storage: Storage, now: Callable[[], datetime] | None = None) -> None:
        self._storage = storage
        self._now = now or (lambda: datetime.now(UTC))

    async def list_members(self, user_id: UUID, shop_id: UUID) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            await require_member(session, user_id, LIST_STAFF)
            members = await session.list_members()
            return {"items": [_member_body(member) for member in members]}

    async def invite(self, user_id: UUID, shop_id: UUID, role: str, request_key: str | None) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, INVITE_STAFF)
            await refuse_suspended(session)
            key = idempotency.validate_key(request_key)
            if role not in {r.value for r in INVITABLE_ROLES}:
                raise ValidationFailed({"role": "must be manager or seller"})
            _within_own(actor, Role(role))

            async def apply() -> dict[str, Any]:
                # The token is returned once, here, and only its hash is stored. The stored response of
                # this idempotent request has the token removed, so a repeat does not return it again.
                token = secrets.token_urlsafe(32)
                expires_at = self._now() + INVITATION_LIFETIME
                await session.create_staff_invitation(token_hash(token), Role(role), expires_at)
                await session.record_activity(
                    membership_id=actor.membership_id,
                    action="staff.invited",
                    subject_type="invitation",
                    subject_id=shop_id,
                )
                return {
                    "id": token_hash(token).hex(),
                    "token": token,
                    "role": role,
                    "expires_at": expires_at.isoformat(),
                }

            return await idempotency.run_once(
                session,
                key=key,
                operation=INVITE_STAFF.name,
                user_id=user_id,
                request={"role": role},
                action=apply,
                redact=lambda body: {**body, "token": None},
            )

    async def list_invitations(self, user_id: UUID, shop_id: UUID) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            await require_member(session, user_id, LIST_INVITATIONS)
            invitations = await session.list_staff_invitations(self._now())
            return {
                "items": [
                    {"id": inv.token_hash.hex(), "role": inv.role.value, "expires_at": inv.expires_at.isoformat()}
                    for inv in invitations
                ]
            }

    async def cancel_invitation(
        self, user_id: UUID, shop_id: UUID, invitation_id: str, request_key: str | None
    ) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, CANCEL_INVITATION)
            await refuse_suspended(session)
            key = idempotency.validate_key(request_key)
            try:
                digest = bytes.fromhex(invitation_id)
            except ValueError as error:
                raise NotFound() from error
            if len(digest) != 32:
                raise NotFound()

            async def apply() -> dict[str, Any]:
                if actor.role is not Role.OWNER:
                    # Whom the owner invited as a manager is not this member's to turn away.
                    for invitation in await session.list_staff_invitations(self._now()):
                        if invitation.token_hash == digest:
                            _within_own(actor, invitation.role)
                if not await session.cancel_staff_invitation(digest):
                    raise NotFound()
                await session.record_activity(
                    membership_id=actor.membership_id,
                    action="staff.invitation_cancelled",
                    subject_type="invitation",
                    subject_id=shop_id,
                )
                return {"id": invitation_id, "status": "cancelled"}

            return await idempotency.run_once(
                session,
                key=key,
                operation=CANCEL_INVITATION.name,
                user_id=user_id,
                request={"invitation": invitation_id},
                action=apply,
            )

    async def update_member(
        self,
        user_id: UUID,
        shop_id: UUID,
        membership_id: UUID,
        *,
        role: str | None,
        status: str | None,
        request_key: str | None,
    ) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, UPDATE_STAFF)
            await refuse_suspended(session)
            key = idempotency.validate_key(request_key)
            fields: dict[str, str] = {}
            if role is None and status is None:
                fields["_"] = "nothing to change"
            if role is not None and role not in {r.value for r in INVITABLE_ROLES}:
                fields["role"] = "must be manager or seller"
            if status is not None and status not in ("active", "suspended"):
                fields["status"] = "must be active or suspended"
            if fields:
                raise ValidationFailed(fields)

            async def apply() -> dict[str, Any]:
                member = await session.get_member(membership_id)
                if member is None or member.status == "removed":
                    raise NotFound()
                if member.role is Role.OWNER:
                    raise OwnerMembershipFixed()
                _within_own(actor, member.role, member.granted, member.denied, target=membership_id)
                if role is not None and Role(role) is not member.role:
                    # A new role is a new set of rights: the owner's to give.
                    _within_own(actor, Role.OWNER)
                updated = await session.update_member(membership_id, role=Role(role) if role else None, status=status)
                await session.record_activity(
                    membership_id=actor.membership_id,
                    action="staff.updated",
                    subject_type="membership",
                    subject_id=membership_id,
                )
                return _member_body(updated)

            return await idempotency.run_once(
                session,
                key=key,
                operation=UPDATE_STAFF.name,
                user_id=user_id,
                request={"membership": str(membership_id), "role": role, "status": status},
                action=apply,
            )

    async def remove_member(
        self, user_id: UUID, shop_id: UUID, membership_id: UUID, request_key: str | None
    ) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, REMOVE_STAFF)
            await refuse_suspended(session)
            key = idempotency.validate_key(request_key)

            async def apply() -> dict[str, Any]:
                member = await session.get_member(membership_id)
                if member is None or member.status == "removed":
                    raise NotFound()
                if member.role is Role.OWNER:
                    raise OwnerMembershipFixed()
                _within_own(actor, member.role, member.granted, member.denied, target=membership_id)
                # The row is kept: entries the person recorded stay attributed to them (REQ-034).
                updated = await session.update_member(membership_id, role=None, status="removed")
                await session.record_activity(
                    membership_id=actor.membership_id,
                    action="staff.removed",
                    subject_type="membership",
                    subject_id=membership_id,
                )
                return _member_body(updated)

            return await idempotency.run_once(
                session,
                key=key,
                operation=REMOVE_STAFF.name,
                user_id=user_id,
                request={"membership": str(membership_id)},
                action=apply,
            )

    async def accept_invitation(self, user_id: UUID, token: str) -> dict[str, Any]:
        if not token.isascii() or not 20 <= len(token) <= 128:
            raise NotFound()
        async with self._storage.platform() as session:
            shop_id = await session.accept_staff_invitation(token_hash(token), user_id)
        if shop_id is None:
            # Unknown, used, cancelled and expired invitations are indistinguishable.
            raise NotFound()
        return {"shop_id": str(shop_id)}

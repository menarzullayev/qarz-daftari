"""The permission matrix: the owner reads the catalogue and reads and sets one member's permissions,
and every member reads their own.

The owner's three operations are the owner's alone (`permissions.manage`, a fixed permission: it cannot
be given to anyone). The fourth, `permissions.mine`, is what a client asks to learn what to offer the
signed-in member; the server still decides every call. All four exist only while the platform switch
`permissions_on` is on; with it off every one of them answers as a route that does not exist, to the
owner too, and a client falls back to the role.

Setting replaces the member's changes as a whole, so sending the same matrix twice changes nothing, and
"reset to the role's defaults" is sending two empty lists. What is stored is only the difference from
the role (`qarz.domain.permissions.clean_overrides`). Every change is written to the shop's activity log
with the member's changes before and after it.
"""

from typing import Any
from uuid import UUID

from qarz.application import idempotency, texts_en, texts_kaa, texts_tg
from qarz.application.authorization import SWITCH, effective_of
from qarz.application.errors import NotFound, ValidationFailed
from qarz.application.operations import operation
from qarz.application.ports import MemberRecord, Storage, TenantSession
from qarz.application.shops import refuse_suspended, require_member
from qarz.application.staff import OwnerMembershipFixed
from qarz.domain import languages, permissions
from qarz.domain.access import Capability, Role
from qarz.domain.uz_cyrillic import to_cyrillic

READ_CATALOGUE = operation("permissions.catalogue", Capability.ADMINISTER_SHOP)
READ_MEMBER_PERMISSIONS = operation("permissions.member.read", Capability.ADMINISTER_SHOP)
SET_MEMBER_PERMISSIONS = operation("permissions.member.set", Capability.ADMINISTER_SHOP)
# Every member, whatever was denied to them: it only tells them what they already cannot do.
READ_MY_PERMISSIONS = operation("permissions.mine", Capability.RECORD)

_ROLES = (Role.SELLER, Role.MANAGER, Role.OWNER)


async def _require_switch(session: TenantSession) -> None:
    if await session.platform_setting(SWITCH) is not True:
        raise NotFound()


# What Tajik, Karakalpak and English call the groups ("group.<key>") and the permissions ("<key>").
_NAMES = {"tg": texts_tg.PERMISSIONS, "kaa": texts_kaa.PERMISSIONS, "en": texts_en.PERMISSIONS}


def label(key: str, uz: str, ru: str) -> dict[str, str]:
    """A name in every language that has it. A reader whose language is absent is shown the Uzbek one."""
    names = {"uz": uz, languages.UZ_CYRILLIC: to_cyrillic(uz), "ru": ru}
    names.update({lang: own[key] for lang, own in _NAMES.items() if key in own})
    return names


def catalogue_body() -> dict[str, Any]:
    return {
        "groups": [
            {
                "key": group.key,
                "label": label(f"group.{group.key}", group.uz, group.ru),
                "permissions": [
                    {
                        "key": permission.key,
                        "label": label(permission.key, permission.uz, permission.ru),
                        "roles": [role.value for role in _ROLES if role in permission.roles],
                        "fixed": permission.fixed,
                    }
                    for permission in permissions.CATALOGUE
                    if permission.group == group.key
                ],
            }
            for group in permissions.GROUPS
        ]
    }


def _overrides(member: MemberRecord) -> dict[str, list[str]]:
    return {"granted": sorted(member.granted), "denied": sorted(member.denied)}


def member_body(member: MemberRecord) -> dict[str, Any]:
    """What the member may do while the permissions are on, and where each answer comes from."""
    held = permissions.effective(member.role, member.granted, member.denied)
    return {
        "membership_id": str(member.membership_id),
        "role": member.role.value,
        "status": member.status,
        **_overrides(member),
        "permissions": [
            {
                "key": permission.key,
                "allowed": permission.key in held,
                "source": permissions.source(member.role, member.granted, member.denied, permission.key),
                "default": member.role in permission.roles,
                "fixed": permission.fixed,
            }
            for permission in permissions.CATALOGUE
        ],
    }


class PermissionService:
    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    async def mine(self, user_id: UUID, shop_id: UUID) -> dict[str, Any]:
        """What the caller may do in the shop now: read with the membership, in this request."""
        async with self._storage.tenant(shop_id) as session:
            await _require_switch(session)
            member = await require_member(session, user_id, READ_MY_PERMISSIONS)
            return {
                "membership_id": str(member.membership_id),
                "role": member.role.value,
                "permissions": sorted(effective_of(member)),
            }

    async def catalogue(self, user_id: UUID, shop_id: UUID) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            await _require_switch(session)
            await require_member(session, user_id, READ_CATALOGUE)
            return catalogue_body()

    async def read_member(self, user_id: UUID, shop_id: UUID, membership_id: UUID) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            await _require_switch(session)
            await require_member(session, user_id, READ_MEMBER_PERMISSIONS)
            member = await session.get_member(membership_id)
            if member is None or member.status == "removed":
                raise NotFound()
            return member_body(member)

    async def set_member(
        self,
        user_id: UUID,
        shop_id: UUID,
        membership_id: UUID,
        *,
        granted: list[str],
        denied: list[str],
        request_key: str | None,
    ) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            await _require_switch(session)
            # Authorize before validating, so that only the owner learns what the shop accepts.
            actor = await require_member(session, user_id, SET_MEMBER_PERMISSIONS)
            await refuse_suspended(session)
            key = idempotency.validate_key(request_key)

            async def apply() -> dict[str, Any]:
                member = await session.get_member(membership_id)
                if member is None or member.status == "removed":
                    raise NotFound()
                if member.role is Role.OWNER:
                    # Nothing can reduce the owner's rights; the database refuses it as well.
                    raise OwnerMembershipFixed()
                try:
                    keep_granted, keep_denied = permissions.clean_overrides(member.role, granted, denied)
                except permissions.InvalidOverrides as error:
                    raise ValidationFailed(error.fields) from error
                if (keep_granted, keep_denied) == (member.granted, member.denied):
                    return member_body(member)
                updated = await session.set_member_permissions(membership_id, granted=keep_granted, denied=keep_denied)
                await session.record_activity(
                    membership_id=actor.membership_id,
                    action="staff.permissions_changed",
                    subject_type="membership",
                    subject_id=membership_id,
                    detail={"before": _overrides(member), "after": _overrides(updated)},
                )
                return member_body(updated)

            return await idempotency.run_once(
                session,
                key=key,
                operation=SET_MEMBER_PERMISSIONS.name,
                user_id=user_id,
                request={"membership": str(membership_id), "granted": sorted(granted), "denied": sorted(denied)},
                action=apply,
            )

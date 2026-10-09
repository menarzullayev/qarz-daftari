"""What a member of a shop may do: the one place the application decides it.

Every check of the application goes through `may`: the gate of an operation (`require_operation`, called
by `qarz.application.shops.require_member`), the finer checks inside a service (`require_permission`),
whom the bot tells about something to decide (`holders`), and what a client is told it may offer
(`effective_of`). `may` reads the member's role and, only while the platform switch `permissions_on` is
on, the owner's per-member changes, and asks the domain (`qarz.domain.permissions.effective`).

The membership is read from the database inside each request's own transaction, together with the switch,
so a change of permissions, of the role or of the switch applies to the member's next request. Nothing
here is cached.
"""

from qarz.application.errors import ForbiddenPermission, ForbiddenRole
from qarz.application.operations import Operation
from qarz.application.ports import Membership, StaffContact
from qarz.domain import permissions
from qarz.domain.access import lowest_role_with

# The platform setting that turns the per-member permissions on (off by default).
SWITCH = "permissions_on"


def effective_of(member: Membership) -> frozenset[str]:
    """Every permission the member holds now."""
    if not member.permissions_on:
        # Switch off: the role alone, whatever is stored for the member.
        return permissions.effective(member.role)
    return permissions.effective(member.role, member.granted, member.denied)


def may(member: Membership, permission: str) -> bool:
    if permission not in permissions.ALL_KEYS:
        raise LookupError(f"{permission!r} is not a permission of the catalogue")
    return permission in effective_of(member)


def refusal(member: Membership, permission: str) -> ForbiddenRole:
    """The error for a member who lacks the permission.

    With the switch off it is the refusal by role the API has always given; with it on the role no longer
    says what is missing, so the permission is named.
    """
    needed = permissions.lowest_role_holding(permission)
    return ForbiddenPermission(permission, needed) if member.permissions_on else ForbiddenRole(needed)


def require_permission(member: Membership, permission: str) -> None:
    if not may(member, permission):
        raise refusal(member, permission)


def require_operation(member: Membership, op: Operation) -> None:
    """Refuse unless the member holds a permission that opens the operation.

    An operation that no permission of the catalogue names is refused to everyone: a new operation cannot
    be reached before it is given a permission (tests/test_permissions.py fails for it first).
    """
    opening = permissions.permissions_of_operation(op.name)
    if not opening:
        raise LookupError(f"operation {op.name!r} has no permission in the catalogue")
    if any(may(member, permission.key) for permission in opening):
        return
    if op.capability is not None and not member.permissions_on:
        raise ForbiddenRole(lowest_role_with(op.capability))
    raise refusal(member, opening[0].key)


def holders(contacts: list[StaffContact], permission: str) -> list[tuple[int, str]]:
    """Telegram chat and language of each active member who holds the permission."""
    return [(contact.tg_id, contact.lang) for contact in contacts if may(contact.member, permission)]

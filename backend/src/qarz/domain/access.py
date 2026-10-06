"""Roles and what each may do (REQ-033; technical specification, authorization table).

This module is the single definition of staff permissions. Every operation names one capability, and the
authorization suite checks every operation against this table.
"""

from enum import StrEnum


class Role(StrEnum):
    SELLER = "seller"
    MANAGER = "manager"
    OWNER = "owner"


class Capability(StrEnum):
    # Record credit sales and payments, add customers, add goods lines to own entries, view customers.
    RECORD = "record"
    # Accept or decline a customer's payment notice.
    DECIDE_PAYMENT_NOTICE = "decide_payment_notice"
    # Reverse entries, change promises, decide disputes and date requests, edit customers and limits,
    # catalog, reminders, reports, exports, import.
    MANAGE = "manage"
    # Read the shop's own settings.
    READ_SHOP = "read_shop"
    # Staff, ownership, shop settings, subscription, activity log, deletion.
    ADMINISTER_SHOP = "administer_shop"


_SELLER = frozenset({Capability.RECORD, Capability.DECIDE_PAYMENT_NOTICE})
_MANAGER = _SELLER | {Capability.MANAGE, Capability.READ_SHOP}
_OWNER = _MANAGER | {Capability.ADMINISTER_SHOP}

ROLE_CAPABILITIES: dict[Role, frozenset[Capability]] = {
    Role.SELLER: _SELLER,
    Role.MANAGER: _MANAGER,
    Role.OWNER: _OWNER,
}

# Lowest first: used to name the role a refused caller would need.
_RANK = (Role.SELLER, Role.MANAGER, Role.OWNER)


def allows(role: Role, capability: Capability) -> bool:
    return capability in ROLE_CAPABILITIES[role]


def lowest_role_with(capability: Capability) -> Role:
    for role in _RANK:
        if allows(role, capability):
            return role
    raise LookupError(f"no role has capability {capability}")

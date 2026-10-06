"""Registry of API operations.

Every operation the API exposes is declared here once, with who may call it. The HTTP layer may only expose
registered operations, and the authorization suite iterates this registry, so an operation cannot be added
without being checked.

Scopes:
- "shop": acts on one shop; the caller must be an active member whose role has the capability.
- "self": acts only on the signed-in caller's own account.
- "public": callable without signing in (the sign-in endpoints themselves).
- "admin": the platform administrator's side; the caller must be on the allow-list, have an active
  administrator account, and hold an admin session obtained by passing the second factor (ADR-017).
- "admin_entry": the door to that side, where the second factor is enrolled and passed; the caller must
  be on the allow-list, and a disabled administrator account is refused. Nothing more can be asked of
  someone who has not passed the factor yet.
"""

from dataclasses import dataclass
from typing import Literal

from qarz.domain.access import Capability

Scope = Literal["shop", "self", "public", "admin", "admin_entry"]


@dataclass(frozen=True)
class Operation:
    name: str
    scope: Scope
    capability: Capability | None = None

    def __post_init__(self) -> None:
        if (self.scope == "shop") != (self.capability is not None):
            raise ValueError("a shop operation needs a capability, and only a shop operation has one")


_REGISTRY: dict[str, Operation] = {}


def _register(op: Operation) -> Operation:
    if op.name in _REGISTRY:
        raise ValueError(f"operation {op.name!r} is already registered")
    _REGISTRY[op.name] = op
    return op


def operation(name: str, capability: Capability) -> Operation:
    """A staff operation on one shop."""
    return _register(Operation(name, "shop", capability))


def self_operation(name: str) -> Operation:
    """An operation on the caller's own account."""
    return _register(Operation(name, "self"))


def public_operation(name: str) -> Operation:
    """An operation that needs no sign-in."""
    return _register(Operation(name, "public"))


def admin_operation(name: str) -> Operation:
    """An operation of the platform administrator, behind the second factor."""
    return _register(Operation(name, "admin"))


def admin_entry_operation(name: str) -> Operation:
    """An operation an allow-listed person needs before they hold an admin session."""
    return _register(Operation(name, "admin_entry"))


def all_operations() -> tuple[Operation, ...]:
    return tuple(_REGISTRY.values())


def get_operation(name: str) -> Operation:
    return _REGISTRY[name]

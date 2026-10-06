"""Registry of API operations.

Every operation the API exposes is declared here once, with who may call it. The HTTP layer may only expose
registered operations, and the authorization suite iterates this registry, so an operation cannot be added
without being checked.

Scopes:
- "shop": acts on one shop; the caller must be an active member whose role has the capability.
- "self": acts only on the signed-in caller's own account.
- "public": callable without signing in (the sign-in endpoints themselves).
"""

from dataclasses import dataclass
from typing import Literal

from qarz.domain.access import Capability

Scope = Literal["shop", "self", "public"]


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


def all_operations() -> tuple[Operation, ...]:
    return tuple(_REGISTRY.values())


def get_operation(name: str) -> Operation:
    return _REGISTRY[name]

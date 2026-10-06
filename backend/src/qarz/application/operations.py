"""Registry of staff operations.

Every operation a staff member can perform is declared here once, with the capability it needs. The HTTP
layer may only expose registered operations, and the authorization suite iterates this registry, so an
operation cannot be added without being checked as every role and from another shop.
"""

from dataclasses import dataclass

from qarz.domain.access import Capability


@dataclass(frozen=True)
class Operation:
    name: str
    capability: Capability


_REGISTRY: dict[str, Operation] = {}


def operation(name: str, capability: Capability) -> Operation:
    if name in _REGISTRY:
        raise ValueError(f"operation {name!r} is already registered")
    op = Operation(name, capability)
    _REGISTRY[name] = op
    return op


def all_operations() -> tuple[Operation, ...]:
    return tuple(_REGISTRY.values())


def get_operation(name: str) -> Operation:
    return _REGISTRY[name]

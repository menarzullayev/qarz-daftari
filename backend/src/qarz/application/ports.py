"""What the application needs from storage. Implemented in the infrastructure layer."""

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from qarz.domain.access import Role


@dataclass(frozen=True)
class Membership:
    membership_id: UUID
    role: Role


@dataclass(frozen=True)
class ShopSettings:
    shop_id: UUID
    name: str
    lang: str
    default_promise_days: int


class TenantSession(Protocol):
    """One database transaction scoped to one shop. Nothing outside that shop is visible through it."""

    async def active_membership(self, user_id: UUID) -> Membership | None: ...

    async def shop_settings(self) -> ShopSettings | None: ...

    async def update_shop_settings(
        self, *, name: str | None, lang: str | None, default_promise_days: int | None
    ) -> ShopSettings: ...

    async def record_activity(
        self, *, membership_id: UUID, action: str, subject_type: str, subject_id: UUID
    ) -> None: ...


class Storage(Protocol):
    def tenant(self, shop_id: UUID) -> AbstractAsyncContextManager[TenantSession]:
        """Open a transaction for one shop. Commits on normal exit, rolls back on an exception."""
        ...

    async def user_language(self, user_id: UUID) -> str | None: ...

"""What the application needs from storage and from the outside world. Implemented in the infrastructure layer."""

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
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


@dataclass(frozen=True)
class OutboxMessage:
    message_id: UUID
    channel: str
    recipient: str
    payload: dict[str, Any]
    attempts: int
    created_at: datetime


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

    async def lock_request_key(self, key: str) -> None:
        """Serialize concurrent requests that carry the same idempotency key, until the transaction ends."""
        ...

    async def stored_response(self, key: str) -> dict[str, Any] | None: ...

    async def store_response(self, key: str, response: dict[str, Any]) -> None: ...


class PlatformSession(Protocol):
    """One database transaction with no tenant. Tenant tables show no rows through it."""

    async def claim_update(self, update_id: int) -> bool:
        """Record a Telegram update identifier. False means it was already processed."""
        ...

    async def language_of_telegram_user(self, tg_id: int) -> str | None: ...

    async def enqueue(
        self, *, channel: str, recipient: str, payload: dict[str, Any], dedupe_key: str, shop_id: UUID | None = None
    ) -> bool:
        """Queue an outbound message in this transaction. False means the dedupe key was already queued."""
        ...

    async def claim_due_messages(self, *, now: datetime, lease_seconds: int, limit: int) -> list[OutboxMessage]:
        """Take pending messages that are due and push their next attempt past the lease."""
        ...

    async def mark_sent(self, message_id: UUID, *, now: datetime) -> None: ...

    async def reschedule(self, message_id: UUID, *, next_try_at: datetime, count_attempt: bool) -> None: ...

    async def mark_failed(self, message_id: UUID) -> None: ...

    async def fail_pending_for(self, *, channel: str, recipient: str) -> int: ...

    async def mark_recipient_unreachable(self, tg_id: int) -> int:
        """Mark every active customer link of this Telegram account as unreachable, across shops."""
        ...


class Storage(Protocol):
    def tenant(self, shop_id: UUID) -> AbstractAsyncContextManager[TenantSession]:
        """Open a transaction for one shop. Commits on normal exit, rolls back on an exception."""
        ...

    def platform(self) -> AbstractAsyncContextManager[PlatformSession]:
        """Open a transaction with no tenant. Commits on normal exit, rolls back on an exception."""
        ...

    async def user_language(self, user_id: UUID) -> str | None: ...


class RetryLater(Exception):
    """The channel asked us to wait (Telegram error 429)."""

    def __init__(self, seconds: float) -> None:
        super().__init__(f"retry after {seconds}s")
        self.seconds = seconds


class RecipientBlocked(Exception):
    """The recipient cannot be reached and will not be until they act (Telegram error 403)."""


class SendFailed(Exception):
    """Any other delivery failure; worth retrying with backoff."""


class Sender(Protocol):
    async def send(self, channel: str, recipient: str, payload: dict[str, Any]) -> None: ...

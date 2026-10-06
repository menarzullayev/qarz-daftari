"""What the application needs from storage and from the outside world. Implemented in the infrastructure layer."""

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol
from uuid import UUID

from qarz.domain.access import Role
from qarz.domain.ledger import Entry


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
class MemberRecord:
    membership_id: UUID
    user_id: UUID
    role: Role
    status: str


@dataclass(frozen=True)
class StaffInvitation:
    token_hash: bytes
    role: Role
    expires_at: datetime


@dataclass(frozen=True)
class TransferRecord:
    transfer_id: UUID
    from_membership: UUID
    to_membership: UUID
    status: str
    expires_at: datetime


@dataclass(frozen=True)
class MyShop:
    shop_id: UUID
    name: str
    role: Role
    membership_id: UUID


@dataclass(frozen=True)
class CustomerRecord:
    customer_id: UUID
    display_name: str
    phone: str | None
    status: str
    reminders_off: bool


@dataclass(frozen=True)
class CatalogItemRecord:
    item_id: UUID
    name: str
    name_norm: str
    unit: str
    price: int
    learned: bool
    status: str
    # Set on a hidden alias: the item a manager merged this spelling into.
    merged_into: UUID | None


@dataclass(frozen=True)
class EntryRow:
    """A ledger entry as the domain reads it, with the fields only the API shows."""

    entry: Entry
    note: str | None
    author_id: UUID


@dataclass(frozen=True)
class DebtFigures:
    """What one customer owes, worked out by the database with the oldest-first allocation (BR-3, BR-4)."""

    balance: int
    overdue_amount: int
    overdue_since: date | None
    due_today_amount: int


@dataclass(frozen=True)
class ShopTotals:
    outstanding: int
    debtors: int
    overdue_amount: int
    overdue_customers: int
    due_today_amount: int


@dataclass(frozen=True)
class ActivityRow:
    activity_id: UUID
    at: datetime
    actor_kind: str
    actor_id: UUID | None
    action: str
    subject_type: str
    subject_id: UUID | None


@dataclass(frozen=True)
class SessionInfo:
    user_id: UUID
    kind: str
    csrf_hash: bytes | None


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

    async def create_shop(self, *, name: str, lang: str) -> ShopSettings: ...

    async def add_member(self, *, user_id: UUID, role: Role) -> UUID: ...

    async def create_subscription(self, *, state: str, trial_ends: date | None) -> None: ...

    async def list_members(self) -> list[MemberRecord]: ...

    async def get_member(self, membership_id: UUID) -> MemberRecord | None: ...

    async def update_member(self, membership_id: UUID, *, role: Role | None, status: str | None) -> MemberRecord: ...

    async def create_staff_invitation(self, token_hash: bytes, role: Role, expires_at: datetime) -> None: ...

    async def list_staff_invitations(self, now: datetime) -> list[StaffInvitation]: ...

    async def cancel_staff_invitation(self, token_hash: bytes) -> bool: ...

    async def expire_transfers(self, now: datetime) -> None: ...

    async def pending_transfer(self) -> TransferRecord | None: ...

    async def create_transfer(
        self, *, transfer_id: UUID, from_membership: UUID, to_membership: UUID, now: datetime, expires_at: datetime
    ) -> TransferRecord: ...

    async def close_transfer(self, transfer_id: UUID, *, status: str, now: datetime) -> TransferRecord: ...

    async def list_activity(
        self,
        *,
        actor: UUID | None,
        action_prefix: str | None,
        subject: UUID | None,
        before: tuple[datetime, UUID] | None,
        limit: int,
    ) -> list[ActivityRow]: ...

    async def subscription(self) -> tuple[str, date | None, date | None] | None:
        """The stored state, the trial end and the paid-through date; None when the shop has no subscription."""
        ...

    async def create_customer(
        self, *, customer_id: UUID, display_name: str, name_norm: str, phone: str | None
    ) -> CustomerRecord: ...

    async def get_customer(self, customer_id: UUID, *, for_update: bool) -> CustomerRecord | None:
        """With `for_update` the row stays locked until the transaction ends: one writer per account."""
        ...

    async def update_customer(
        self,
        customer_id: UUID,
        *,
        display_name: str | None,
        name_norm: str | None,
        set_phone: bool,
        phone: str | None,
        reminders_off: bool | None,
    ) -> CustomerRecord: ...

    async def set_customer_status(self, customer_id: UUID, status: str) -> CustomerRecord: ...

    async def search_customers(
        self,
        *,
        name_part: str | None,
        phone_digits: str | None,
        status: str,
        after: tuple[str, UUID] | None,
        limit: int,
    ) -> list[tuple[CustomerRecord, int, str]]:
        """Customers in name order with their balance and normalized name (the paging position)."""
        ...

    async def customers_named(self, name_norm: str) -> list[tuple[CustomerRecord, int]]:
        """Active customers whose normalized name is exactly this, with their balances."""
        ...

    async def balances(self, customer_ids: list[UUID]) -> dict[UUID, int]: ...

    async def entries_of(self, customer_id: UUID) -> list[EntryRow]: ...

    async def customer_of_entry(self, entry_id: UUID) -> UUID | None: ...

    async def entry_created_at(self, entry_id: UUID) -> datetime | None: ...

    async def promise_actors(self, entry_id: UUID) -> list[str]:
        """Who set each promise of the entry, oldest first."""
        ...

    async def append_entry(
        self,
        *,
        entry_id: UUID,
        customer_id: UUID,
        seq: int,
        kind: str,
        amount: int,
        note: str | None,
        reverses_id: UUID | None,
        author_id: UUID,
        created_at: datetime,
    ) -> None: ...

    async def add_promise(self, *, entry_id: UUID, promised_date: date, actor: str, created_at: datetime) -> None: ...

    async def record_measure(self, *, kind: str, entry_ref: UUID, amount: int, promised: date | None) -> None:
        """One row for product measurement. Carries no name, phone, or Telegram identity."""
        ...

    async def shop_totals(self, today: date) -> ShopTotals: ...

    async def debtors_page(
        self, *, today: date, only_overdue: bool, before: tuple[int, UUID] | None, limit: int
    ) -> list[tuple[CustomerRecord, DebtFigures]]:
        """Customers who owe something, largest balance first."""
        ...

    async def insert_catalog_item(
        self, *, item_id: UUID, name: str, name_norm: str, unit: str, price: int, learned: bool
    ) -> CatalogItemRecord | None:
        """None when the shop already has an item with this normalized name; nothing is stored then."""
        ...

    async def get_catalog_item(self, item_id: UUID, *, for_update: bool) -> CatalogItemRecord | None: ...

    async def catalog_item_by_norm(self, name_norm: str) -> CatalogItemRecord | None: ...

    async def update_catalog_item(
        self, item_id: UUID, *, name: str | None, name_norm: str | None, unit: str | None, price: int | None
    ) -> CatalogItemRecord | None:
        """Change the given parts. None when the new name belongs to another item; nothing is changed then."""
        ...

    async def set_catalog_item_state(
        self, item_id: UUID, *, status: str, learned: bool, merged_into: UUID | None
    ) -> CatalogItemRecord: ...

    async def search_catalog(
        self,
        *,
        name_part: str | None,
        status: str,
        learned: bool | None,
        after: tuple[str, UUID] | None,
        limit: int,
    ) -> list[CatalogItemRecord]:
        """Items in name order; `after` is the normalized name and identifier of the last item already seen."""
        ...

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

    async def update_seen(self, update_id: int) -> bool: ...

    async def language_of_telegram_user(self, tg_id: int) -> str | None: ...

    async def ensure_user(self, tg_id: int, lang: str) -> UUID:
        """Return the user for this Telegram account, creating it with the given language if it is new."""
        ...

    async def user_language(self, user_id: UUID) -> str | None: ...

    async def set_user_language(self, user_id: UUID, lang: str) -> None: ...

    async def create_session(
        self,
        *,
        token_hash: bytes,
        user_id: UUID,
        kind: str,
        csrf_hash: bytes | None,
        now: datetime,
        expires_at: datetime,
    ) -> None: ...

    async def find_session(self, token_hash: bytes, now: datetime) -> "SessionInfo | None":
        """The session for this token hash if it exists, is not revoked, and has not expired."""
        ...

    async def revoke_session(self, token_hash: bytes, now: datetime) -> None: ...

    async def platform_setting(self, key: str) -> Any | None: ...

    async def set_active_shop(self, user_id: UUID, shop_id: UUID) -> None: ...

    async def active_shop(self, user_id: UUID) -> UUID | None: ...

    async def my_memberships(self, user_id: UUID) -> list[MyShop]: ...

    async def put_pending(
        self,
        *,
        pending_id: UUID,
        user_id: UUID,
        kind: str,
        payload: dict[str, Any],
        now: datetime,
        expires_at: datetime,
    ) -> None:
        """Remember what a chat question was about until its button is pressed. Clears the user's expired ones."""
        ...

    async def take_pending(self, pending_id: UUID, user_id: UUID, kind: str, now: datetime) -> dict[str, Any] | None:
        """Use up one pending question of this user. None when it is gone, expired, or someone else's."""
        ...

    async def current_pending(self, user_id: UUID, kind: str, now: datetime) -> tuple[UUID, dict[str, Any]] | None: ...

    async def drop_pending(self, user_id: UUID, kind: str) -> None: ...

    async def accept_staff_invitation(self, token_hash: bytes, user_id: UUID) -> UUID | None:
        """Join the shop the invitation belongs to. None when the invitation cannot be used."""
        ...

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

"""What the application needs from storage and from the outside world. Implemented in the infrastructure layer."""

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
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
    credit_limit: int | None = None


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
class GoodsLineRecord:
    """One good of a credit sale. It keeps its own name, unit and price (INV-17)."""

    line_no: int
    catalog_item_id: UUID | None
    name: str
    qty: Decimal
    unit: str
    unit_price: int
    line_total: int


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
class PeriodTotals:
    """A shop's figures for one period (REQ-046).

    Reversed entries and their reversals are left out of everything except the count and sum of the
    reversals recorded.
    """

    outstanding_start: int
    outstanding_end: int
    credit_amount: int
    credit_count: int
    credit_customers: int
    payment_amount: int
    payment_count: int
    payment_customers: int
    opening_amount: int
    opening_count: int
    reversal_amount: int
    reversal_count: int
    new_customers: int
    disputes_opened: int


@dataclass(frozen=True)
class DayFigures:
    day: date  # a Tashkent calendar day
    credit: int
    payments: int


@dataclass(frozen=True)
class StaffFigures:
    """What one staff member recorded in a period."""

    membership_id: UUID
    role: Role
    credit_amount: int
    credit_count: int
    payment_amount: int
    payment_count: int


@dataclass(frozen=True)
class UncoveredDebt:
    """What one customer still owes on debt promised for one day, after the oldest-first allocation (BR-3)."""

    customer_id: UUID
    promised_date: date
    remaining: int


@dataclass(frozen=True)
class DisputeRecord:
    dispute_id: UUID
    entry_id: UUID
    customer_id: UUID
    amount: int  # of the disputed entry
    reason: str
    status: str
    decline_reason: str | None
    created_at: datetime


@dataclass(frozen=True)
class ReminderSettings:
    name: str  # of the shop; a reminder states it
    lang: str
    on: bool
    hour: int
    template: int
    sms_on: bool


@dataclass(frozen=True)
class ReminderCandidate:
    """A customer who owes something, and how they could be reached."""

    customer_id: UUID
    display_name: str
    phone: str | None
    lang: str | None  # the linked person's language if linked, else the one recorded for the customer
    reminders_off: bool
    tg_id: int | None  # set only when the customer has an active Telegram link


@dataclass(frozen=True)
class CreditSettings:
    default_limit: int | None
    sellers_may_exceed: bool


@dataclass(frozen=True)
class SubscriptionToReview:
    shop_id: UUID
    shop_name: str
    state: str
    ends_on: date | None
    owner_tg: int | None
    owner_lang: str | None


@dataclass(frozen=True)
class DateRequestRecord:
    """A customer's request to move the promised date of one entry (DOM-015)."""

    request_id: UUID
    entry_id: UUID
    customer_id: UUID
    customer_name: str
    amount: int  # of the entry
    promised_date: date | None  # the entry's promised date now, whatever became of the request
    requested_date: date
    reason: str | None
    status: str
    decline_reason: str | None
    created_at: datetime
    closed_at: datetime | None


@dataclass(frozen=True)
class PromiseRecord:
    """One row of an entry's promise history (INV-9). The newest row is the current promised date."""

    promised_date: date
    actor: str
    reason: str | None
    created_at: datetime


@dataclass(frozen=True)
class OnlinePayment:
    """An order to pay the subscription online, and what the provider has done with it."""

    id: UUID
    prepare_id: int
    months: int
    amount: int
    state: str
    provider: str | None
    provider_txn: str | None
    provider_time: int | None
    cancel_reason: int | None
    started_at: datetime | None
    paid_at: datetime | None
    cancelled_at: datetime | None


@dataclass(frozen=True)
class ShopToErase:
    shop_id: UUID
    shop_name: str
    owner_tg: int | None
    owner_lang: str | None


@dataclass(frozen=True)
class WaitingLink:
    """Someone who started the bot from the counter code and agreed, not yet attached to a record."""

    link_id: UUID
    name: str | None
    since: datetime


@dataclass(frozen=True)
class CustomerAccount:
    """One shop a person is linked to as a customer, and what they owe there."""

    link_id: UUID
    shop_id: UUID
    shop_name: str
    customer_id: UUID
    display_name: str
    balance: int


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
        set_limit: bool = False,
        credit_limit: int | None = None,
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

    async def add_promise(
        self, *, entry_id: UUID, promised_date: date, actor: str, created_at: datetime, reason: str | None = None
    ) -> None: ...

    async def promises_of(self, entry_ids: list[UUID]) -> dict[UUID, list[PromiseRecord]]:
        """The promise history of the given entries, oldest first. An entry without a promise is absent."""
        ...

    async def record_measure(
        self, *, kind: str, entry_ref: UUID, amount: int, promised: date | None, handle_ms: int | None = None
    ) -> None:
        """One row for product measurement. Carries no name, phone, or Telegram identity."""
        ...

    async def add_goods_lines(self, entry_id: UUID, lines: list[GoodsLineRecord]) -> None:
        """Store all the goods lines of one entry as its single batch (INV-8)."""
        ...

    async def goods_lines_of(self, entry_ids: list[UUID]) -> dict[UUID, list[GoodsLineRecord]]:
        """The goods lines of the given entries in line order. An entry without lines is absent."""
        ...

    async def shop_totals(self, today: date) -> ShopTotals: ...

    async def debtors_page(
        self, *, today: date, only_overdue: bool, before: tuple[int, UUID] | None, limit: int
    ) -> list[tuple[CustomerRecord, DebtFigures]]:
        """Customers who owe something, largest balance first."""
        ...

    async def period_totals(self, start: datetime, end: datetime) -> PeriodTotals:
        """Figures of what was recorded from `start` up to but not including `end`, and the balances at both."""
        ...

    async def period_days(self, start: datetime, end: datetime) -> list[DayFigures]:
        """Credit given and payments received per Tashkent day; a day with neither is absent."""
        ...

    async def period_staff(self, start: datetime, end: datetime) -> list[StaffFigures]: ...

    async def debtors_as_of(self, end: datetime, limit: int) -> list[tuple[UUID, str, int]]:
        """Customer, name and balance of those who owed the most just before `end`, largest first."""
        ...

    async def fell_due(self, first: date, before: date) -> tuple[int, int]:
        """Of the debt whose current promised date is from `first` up to but not including `before`:
        the part covered by payments made on or before the promised date, and the whole (BR-9)."""
        ...

    async def uncovered_debts(self) -> list[UncoveredDebt]:
        """Per customer and promised date, what payments have not covered. Fully covered debt is absent."""
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

    async def issue_customer_link(self, token_hash: bytes, customer_id: UUID, expires_at: datetime) -> None: ...

    async def rotate_counter_code(self, token_hash: bytes) -> None: ...

    async def counter_code_since(self) -> datetime | None: ...

    async def link_state(self, customer_id: UUID) -> tuple[str, datetime] | None:
        """Status and start of the customer's live link, if there is one."""
        ...

    async def waiting_links(self, since: datetime) -> list[WaitingLink]: ...

    async def attach_waiting(self, link_id: UUID, customer_id: UUID, since: datetime) -> bool: ...

    async def dismiss_waiting(self, link_id: UUID, now: datetime) -> bool: ...

    async def waiting_recipient(self, link_id: UUID) -> tuple[int, str] | None: ...

    async def customer_recipient(self, customer_id: UUID) -> tuple[int, str] | None:
        """Telegram chat and language of the customer's active link; None when nobody is to be notified."""
        ...

    async def enqueue(
        self, *, recipient: str, payload: dict[str, Any], dedupe_key: str, channel: str = "telegram"
    ) -> bool:
        """Queue a message in this shop's transaction. False when the key was already queued."""
        ...

    async def record_customer_activity(self, *, action: str, subject_id: UUID) -> None:
        """Something the customer did about their own record. No staff member is the actor."""
        ...

    async def removal_waiting(self, customer_id: UUID) -> bool: ...

    async def open_removal_request(self, customer_id: UUID, now: datetime) -> None: ...

    async def close_removal_request(self, customer_id: UUID, now: datetime) -> None: ...

    async def anonymize_customer(self, customer_id: UUID, *, label: str, name_norm: str, now: datetime) -> None:
        """Replace the name, erase phone and links, and forget the person if nothing else refers to them."""
        ...

    async def dispute_of_entry(self, entry_id: UUID) -> DisputeRecord | None:
        """The dispute ever opened on the entry, open or not: there is at most one (BR-11)."""
        ...

    async def get_dispute(self, dispute_id: UUID) -> DisputeRecord | None: ...

    async def disputes_of_customer(self, customer_id: UUID) -> dict[UUID, DisputeRecord]:
        """By entry."""
        ...

    async def open_dispute(self, *, dispute_id: UUID, entry_id: UUID, reason: str, now: datetime) -> DisputeRecord: ...

    async def close_dispute(
        self, dispute_id: UUID, *, status: str, decline_reason: str | None, decided_by: UUID | None, now: datetime
    ) -> DisputeRecord: ...

    async def open_disputes(self) -> list[tuple[DisputeRecord, str]]:
        """Open disputes of the shop, oldest first, each with the customer's name."""
        ...

    async def get_date_request(self, request_id: UUID) -> DateRequestRecord | None: ...

    async def date_requests_of_customer(self, customer_id: UUID) -> list[DateRequestRecord]:
        """Every date request ever made on the customer's entries, oldest first."""
        ...

    async def open_date_request(
        self, *, request_id: UUID, entry_id: UUID, requested_date: date, reason: str | None, now: datetime
    ) -> DateRequestRecord: ...

    async def close_date_request(
        self, request_id: UUID, *, status: str, decline_reason: str | None, decided_by: UUID | None, now: datetime
    ) -> DateRequestRecord: ...

    async def open_date_requests(self) -> list[DateRequestRecord]:
        """Open date requests of the shop, oldest first."""
        ...

    async def staff_recipients(self, roles: list[str]) -> list[tuple[int, str]]:
        """Telegram chat and language of each active member holding one of the roles."""
        ...

    async def reminder_settings(self) -> ReminderSettings | None: ...

    async def update_reminder_settings(
        self, *, on: bool | None, hour: int | None, template: int | None, sms_on: bool | None
    ) -> None: ...

    async def reminder_candidates(self, *, after: UUID | None, limit: int) -> list[ReminderCandidate]:
        """Active customers who owe something, by identifier, a batch at a time."""
        ...

    async def reminder_candidate(self, customer_id: UUID) -> ReminderCandidate | None: ...

    async def entries_of_many(self, customer_ids: list[UUID]) -> dict[UUID, list[Entry]]: ...

    async def last_automatic_reminders(self, customer_ids: list[UUID]) -> dict[UUID, date]: ...

    async def add_reminder(self, *, customer_id: UUID, kind: str, channel: str, amount: int, sent_on: date) -> bool:
        """False when this customer already has a reminder of this kind on this day."""
        ...

    async def sms_reminders_since(self, first_day: date) -> int: ...

    async def platform_setting(self, key: str) -> Any | None: ...

    async def credit_settings(self) -> CreditSettings: ...

    async def update_credit_settings(
        self, *, set_default: bool, default_limit: int | None, sellers_may_exceed: bool | None
    ) -> None: ...

    async def limit_subscription(self, now: datetime) -> None:
        """Store that the period has ended, remembering the state it ended from."""
        ...

    async def record_system_activity(self, *, action: str, subject_id: UUID) -> None: ...

    async def subscription_locked(self) -> tuple[str, date | None, date | None] | None:
        """As `subscription`, holding the row until the transaction ends."""
        ...

    async def pay_subscription(self, paid_through: date, now: datetime) -> None:
        """Set the paid-through date. The shop becomes active unless an administrator suspended it."""
        ...

    async def create_online_payment(self, *, order_id: UUID, months: int, amount: int) -> OnlinePayment: ...

    async def online_payment(self, order_id: UUID, *, for_update: bool = False) -> OnlinePayment | None: ...

    async def online_payment_by_txn(self, provider: str, txn: str) -> OnlinePayment | None:
        """The order a provider's transaction belongs to, locked until the transaction ends."""
        ...

    async def start_online_payment(
        self, order_id: UUID, *, provider: str, txn: str, provider_time: int | None, now: datetime
    ) -> None: ...

    async def finish_online_payment(self, order_id: UUID, now: datetime) -> None: ...

    async def cancel_online_payment(self, order_id: UUID, *, reason: int | None, now: datetime) -> None: ...

    async def deletion_state(self, *, for_update: bool = False) -> tuple[str, datetime | None]:
        """The shop's status (active or deletion_pending) and when it is due to be erased."""
        ...

    async def set_deletion(self, *, status: str, due: datetime | None) -> None: ...

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

    async def shops_due_for_reminders(self, hour: int) -> list[UUID]: ...

    async def measure_between(self, start: datetime, end: datetime) -> dict[str, float | None]:
        """Identity-free totals of the measurement events in [start, end)."""
        ...

    async def store_week(self, week_start: date, metrics: dict[str, float | None]) -> None: ...

    async def stored_weeks(self, limit: int) -> list[tuple[date, str, float | None]]: ...

    async def shops_to_erase(self) -> list[ShopToErase]: ...

    async def erase_shop(self, shop_id: UUID) -> bool:
        """Erase a shop whose waiting period is over by the database's clock. False when it is not due."""
        ...

    async def subscriptions_to_review(self, today: date) -> list[SubscriptionToReview]: ...

    async def online_payment_shop(self, order_id: UUID) -> UUID | None:
        """The shop an order belongs to; nothing else about it."""
        ...

    async def online_payment_shop_by_txn(self, provider: str, txn: str) -> UUID | None: ...

    async def payme_statement(self, from_ms: int, to_ms: int) -> list[OnlinePayment]:
        """Payme's transactions whose time, as Payme gave it, lies in the period."""
        ...

    async def job_done(self, job: str, period: str) -> bool: ...

    async def finish_job(self, job: str, period: str) -> None: ...

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

    async def customer_token_info(self, token_hash: bytes) -> tuple[str, UUID, str] | None:
        """Kind (counter or customer), shop and shop name a usable code leads to."""
        ...

    async def link_customer(
        self, token_hash: bytes, user_id: UUID, consent_version: int, name: str | None
    ) -> tuple[str, UUID | None, UUID | None]:
        """Outcome (linked, waiting, already, taken, invalid), shop, and customer when linked."""
        ...

    async def my_accounts(self, user_id: UUID) -> list[CustomerAccount]: ...

    async def my_link(self, user_id: UUID, link_id: UUID) -> tuple[UUID, UUID] | None:
        """Shop and customer behind a live link of this user; None when it is not theirs."""
        ...

    async def end_my_link(self, user_id: UUID, shop_id: UUID) -> bool: ...

    async def mark_recipient_reachable(self, user_id: UUID) -> int: ...

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

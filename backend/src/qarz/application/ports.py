"""What the application needs from storage and from the outside world. Implemented in the infrastructure layer."""

from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

from qarz.domain.access import Role
from qarz.domain.ledger import Entry
from qarz.domain.ops_alerts import Alert, DatabaseFigures


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
    # Set on an opening balance that came from an import (REQ-063).
    import_batch_id: UUID | None = None


@dataclass(frozen=True)
class ImportBatchRecord:
    """One spreadsheet import (DOM-017). The summary holds counts, codes and identifiers, never a row's content."""

    batch_id: UUID
    status: str
    file_id: UUID | None
    summary: dict[str, Any]
    author_id: UUID
    created_at: datetime
    applied_at: datetime | None
    plan: str | None  # the fingerprint of the preview the worker made; applying must name it
    step_by: UUID | None  # who asked for the step the batch waits for, or for the last one done
    queued_at: datetime | None


@dataclass(frozen=True)
class NewReversal:
    """The reversal of one entry of an import, written in bulk when the import is undone."""

    reversal_id: UUID
    entry_id: UUID
    customer_id: UUID
    seq: int
    amount: int


@dataclass(frozen=True)
class ImportCandidate:
    """A customer of the shop that a row of an import could mean."""

    customer_id: UUID
    display_name: str
    name_norm: str
    phone: str | None
    status: str


@dataclass(frozen=True)
class NewImportCustomer:
    customer_id: UUID
    display_name: str
    name_norm: str
    phone: str | None


@dataclass(frozen=True)
class NewImportEntry:
    """One opening balance of an import, with the promise it starts with."""

    entry_id: UUID
    customer_id: UUID
    seq: int
    amount: int
    note: str | None
    promised_date: date
    promise_actor: str


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
class StoredFileRecord:
    """A file the service keeps for a shop. The key is random and names nobody."""

    file_id: UUID
    purpose: str
    object_key: str
    sha256: bytes
    size_bytes: int
    mime: str
    delete_after: datetime | None


@dataclass(frozen=True)
class PaymentNoticeRecord:
    notice_id: UUID
    customer_id: UUID
    amount: int  # what the customer says they paid
    file_id: UUID | None
    status: str
    payment_entry: UUID | None
    recorded_amount: int | None  # of the payment an accepted notice produced
    decline_reason: str | None
    created_at: datetime
    closed_at: datetime | None
    # An earlier file of the same shop has the same content. For staff only.
    receipt_seen_before: bool = False


@dataclass(frozen=True)
class ExportJobRecord:
    job_id: UUID
    requested_by: UUID
    status: str
    file_id: UUID | None
    error: str | None
    attempts: int
    row_count: int | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    file_delete_after: datetime | None  # when the workbook is deleted; None when there is none


@dataclass(frozen=True)
class ExportEntry:
    """One ledger entry as the export writes it."""

    entry_id: UUID
    customer_id: UUID
    customer_name: str
    seq: int
    kind: str
    amount: int
    note: str | None
    reverses_id: UUID | None
    reversed_kind: str | None  # the kind of the entry a reversal reverses
    is_reversed: bool
    promised_date: date | None
    author_id: UUID
    author_role: str
    created_at: datetime


@dataclass(frozen=True)
class ExportPromise:
    entry_id: UUID
    promised_date: date
    reason: str | None
    actor: str
    created_at: datetime


@dataclass(frozen=True)
class ExportCustomer:
    customer_id: UUID
    display_name: str
    name_norm: str
    phone: str | None
    status: str
    credit_limit: int | None
    created_at: datetime


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
class AdminAccount:
    """An administrator's account as stored. `secret` is the second factor's secret, still encrypted."""

    status: str
    secret: bytes
    confirmed: bool
    failures: int
    locked_until: datetime | None
    last_step: int | None


@dataclass(frozen=True)
class AdminShopRow:
    """A shop as an administrator may see it: no customer, entry or amount owed (REQ-059)."""

    shop_id: UUID
    name: str
    lang: str
    status: str
    created_at: datetime
    deletion_due: datetime | None
    state: str | None  # as stored; None when the shop has no subscription row
    effective_state: str
    trial_ends: date | None
    paid_through: date | None
    prior_state: str | None
    owner_tg: int | None
    staff_count: int
    customer_count: int


@dataclass(frozen=True)
class AdminReceiptRow:
    receipt_id: UUID
    stated_amount: int
    status: str
    months: int | None
    reject_reason: str | None
    created_at: datetime
    decided_at: datetime | None


@dataclass(frozen=True)
class SubscriptionReceiptRecord:
    """A subscription receipt as its own shop sees it (DOM-019)."""

    receipt_id: UUID
    stated_amount: int
    stated_months: int | None
    status: str
    months: int | None  # what the administrator recorded on approval
    reject_reason: str | None
    created_at: datetime
    decided_at: datetime | None
    file_id: UUID | None
    # The card the owner chose to pay to: its label and the last four digits of its number, never the
    # number. None for a receipt sent before cards could be chosen, and when the owner did not say.
    paid_to_card: str | None = None


@dataclass(frozen=True)
class AdminReceipt:
    """A subscription receipt as an administrator sees it, with its shop's name. `copies` is how many
    other receipts, of any shop, carry a file with the same content."""

    receipt_id: UUID
    shop_id: UUID
    shop_name: str
    stated_amount: int
    stated_months: int | None
    status: str
    months: int | None
    reject_reason: str | None
    created_at: datetime
    decided_at: datetime | None
    decided_by: UUID | None
    has_file: bool
    copies: int = 0
    # The Telegram identifier of the review-group administrator who decided, when it was not an
    # administrator of the platform (DEC-064). Then `decided_by` is empty.
    decided_by_tg: int | None = None
    file: StoredFileRecord | None = None  # filled only when one receipt is read
    paid_to_card: str | None = None  # as in SubscriptionReceiptRecord


@dataclass(frozen=True)
class ReceiptCopy:
    """Another receipt whose file has the same content."""

    receipt_id: UUID
    shop_id: UUID
    shop_name: str
    stated_amount: int
    status: str
    created_at: datetime


@dataclass(frozen=True)
class GroupReceipt:
    """A subscription receipt as the review group decides it (DEC-064): what is needed to approve or
    reject it and to tell the owner, and nothing of its file."""

    receipt_id: UUID
    shop_id: UUID
    shop_name: str
    stated_amount: int
    stated_months: int | None
    status: str
    state: str | None  # the subscription's; None when the shop has no subscription row
    trial_ends: date | None
    paid_through: date | None
    owner_tg: int | None
    owner_lang: str | None


@dataclass(frozen=True)
class OwnerReassignment:
    """What `admin_reassign_owner` answered. Everything but `outcome` is set only as far as it got."""

    outcome: str
    audit_id: UUID | None
    shop_name: str | None
    shop_lang: str | None
    deletion_due: datetime | None
    previous_owner: UUID | None
    previous_owner_tg: int | None
    previous_owner_lang: str | None
    new_owner: UUID | None
    new_owner_lang: str | None
    transfer_cancelled: bool


@dataclass(frozen=True)
class LockedSubscription:
    """A shop's subscription row, locked for the transaction, and where to reach its owner."""

    state: str
    trial_ends: date | None
    paid_through: date | None
    prior_state: str | None
    shop_name: str
    owner_tg: int | None
    owner_lang: str | None


@dataclass(frozen=True)
class SupportAccessRow:
    """One support access (BR-31). `shop_name` is given only where the reader sees more than one shop."""

    access_id: UUID
    shop_id: UUID
    admin_id: UUID
    reason: str
    starts_at: datetime
    ends_at: datetime
    closed_at: datetime | None
    closed_by: str | None
    shop_name: str | None = None


@dataclass(frozen=True)
class SupportChange:
    """What opening or closing a support access found: the access, and where to reach the shop's owner."""

    access_id: UUID
    shop_name: str
    owner_tg: int | None
    owner_lang: str | None
    already_open: bool = False


@dataclass(frozen=True)
class AdminAuditRow:
    """One row of the admin audit. Who acted is `admin_id`, or, for a subscription receipt decided from
    the review group by one of the group's Telegram administrators (DEC-064), `actor_tg`: exactly one."""

    audit_id: UUID
    at: datetime
    admin_id: UUID | None
    action: str
    target_type: str
    target_id: str | None
    shop_id: UUID | None
    reason: str | None
    detail: dict[str, Any]
    actor_tg: int | None = None


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

    async def claim_owned_shop(self, user_id: UUID, *, wants_trial: bool) -> str:
        """Use up the person's one trial if it is asked for and still unused: 'trial' or 'limited'.
        A person may own any number of shops (DEC-065); only the first gets a trial."""
        ...

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

    async def add_stored_file(
        self,
        *,
        file_id: UUID,
        purpose: str,
        object_key: str,
        sha256: bytes,
        size_bytes: int,
        mime: str,
        now: datetime,
        delete_after: datetime | None,
    ) -> None: ...

    async def get_stored_file(self, file_id: UUID) -> StoredFileRecord | None: ...

    async def shorten_file_retention(self, file_id: UUID, delete_after: datetime) -> None:
        """Bring the day a file is deleted forward. It is never moved later."""
        ...

    async def due_receipt_files(self, now: datetime, limit: int) -> list[StoredFileRecord]:
        """Receipts of payment notices and of the subscription whose retention has run out, oldest first."""
        ...

    async def remove_stored_file(self, file_id: UUID) -> None:
        """Forget a receipt file whose object has been deleted. Whatever pointed to it has no file now."""
        ...

    async def add_subscription_receipt(
        self,
        *,
        receipt_id: UUID,
        stated_amount: int,
        stated_months: int,
        file_id: UUID,
        paid_to_card: str | None,
        now: datetime,
    ) -> SubscriptionReceiptRecord: ...

    async def subscription_receipts(self, limit: int) -> list[SubscriptionReceiptRecord]:
        """The shop's own receipts, newest first."""
        ...

    async def count_waiting_receipts(self) -> int: ...

    async def subscription_receipt_copies(self, file_id: UUID) -> int:
        """How many other subscription receipts, of any shop, have a file with the same content."""
        ...

    async def admin_recipients(self) -> list[tuple[int, str]]:
        """Telegram chat and language of every administrator whose account is active and confirmed."""
        ...

    async def create_import_batch(
        self,
        *,
        batch_id: UUID,
        file_id: UUID,
        summary: dict[str, Any],
        author_id: UUID,
        now: datetime,
    ) -> None:
        """A batch that waits for the worker to check its file."""
        ...

    async def get_import_batch(self, batch_id: UUID, *, for_update: bool) -> ImportBatchRecord | None:
        """With `for_update` the batch stays locked until the transaction ends: a step is done once."""
        ...

    async def set_import_batch(
        self,
        batch_id: UUID,
        *,
        status: str,
        summary: dict[str, Any],
        plan: str | None,
        applied_at: datetime | None = None,
        queued: tuple[UUID, datetime] | None = None,
    ) -> None:
        """Move the batch on. Any move ends the worker's claim on it.

        `applied_at` is set only when given and never cleared. `queued` (who asked, and when) is given
        when the new state is one the worker is to take.
        """
        ...

    async def set_import_preview(self, batch_id: UUID, preview: dict[str, Any] | None) -> None: ...

    async def import_preview(self, batch_id: UUID) -> dict[str, Any] | None: ...

    async def list_import_batches(self, limit: int) -> list[ImportBatchRecord]:
        """The shop's imports, newest first."""
        ...

    async def import_candidates(self, name_norms: list[str], phones: list[str]) -> list[ImportCandidate]:
        """Customers, archived ones included, with one of these normalized names or phones. Oldest first."""
        ...

    async def lock_customers(self, customer_ids: list[UUID]) -> None:
        """Lock these customer rows, always in the same order, until the transaction ends."""
        ...

    async def last_seqs(self, customer_ids: list[UUID]) -> dict[UUID, int]:
        """The highest entry number of each account that has entries."""
        ...

    async def add_import_customers(self, customers: list[NewImportCustomer]) -> None: ...

    async def add_import_entries(
        self, batch_id: UUID, author_id: UUID, now: datetime, entries: list[NewImportEntry]
    ) -> None:
        """Store the opening balances of an import with their promises and one measurement row each."""
        ...

    async def customers_of_import(self, batch_id: UUID) -> list[UUID]:
        """The customers that have an entry of the import, in identifier order."""
        ...

    async def standing_entries_of_import(self, batch_id: UUID) -> list[tuple[UUID, UUID, int]]:
        """Entry, customer and amount of the import's entries that are not reversed, by customer and number."""
        ...

    async def add_reversals(self, author_id: UUID, now: datetime, reversals: list[NewReversal]) -> None:
        """Store reversals in bulk, each with its activity row and its measurement row, as one by one."""
        ...

    async def close_disputes_of(self, entry_ids: list[UUID], decided_by: UUID, now: datetime) -> None:
        """Reversing a disputed entry ends its open dispute as reversed (BR-12)."""
        ...

    async def customers_with_open_date_requests(self, customer_ids: list[UUID]) -> list[UUID]: ...

    async def customers_waiting_removal(self, customer_ids: list[UUID]) -> list[UUID]: ...

    async def linked_customers(self, customer_ids: list[UUID]) -> list[UUID]:
        """Those of the customers who have an active link, and so are told of what happens on their account."""
        ...

    async def archive_customers(self, customer_ids: list[UUID]) -> int:
        """Archive those of the customers that are active. Returns how many were."""
        ...

    async def stored_object_keys(self) -> list[str]:
        """The key of every file kept for the shop, whatever its purpose."""
        ...

    async def add_payment_notice(
        self, *, notice_id: UUID, customer_id: UUID, amount: int, file_id: UUID | None, now: datetime
    ) -> PaymentNoticeRecord: ...

    async def get_payment_notice(self, notice_id: UUID) -> PaymentNoticeRecord | None: ...

    async def notices_of_customer(self, customer_id: UUID, limit: int) -> list[PaymentNoticeRecord]:
        """Newest first."""
        ...

    async def count_open_notices(self, customer_id: UUID) -> int:
        """Notices of the customer marked as waiting for the shop. Stale ones count until they are marked expired."""
        ...

    async def open_payment_notices(self, since: datetime) -> list[tuple[PaymentNoticeRecord, str]]:
        """Notices waiting for the shop and sent no earlier than `since`, oldest first, with the customer's name."""
        ...

    async def close_payment_notice(
        self,
        notice_id: UUID,
        *,
        status: str,
        payment_entry: UUID | None,
        decline_reason: str | None,
        decided_by: UUID | None,
        now: datetime,
    ) -> PaymentNoticeRecord: ...

    async def expire_payment_notices(
        self, *, before: datetime, now: datetime, customer_id: UUID | None, files_delete_after: datetime
    ) -> int:
        """Mark as expired the waiting notices sent before `before`; their receipts get the given deadline."""
        ...

    async def lock_exports(self) -> None:
        """Let one request at a time decide whether the shop may start an export, until the transaction ends."""
        ...

    async def add_export_job(self, *, job_id: UUID, requested_by: UUID, now: datetime) -> ExportJobRecord: ...

    async def get_export_job(self, job_id: UUID) -> ExportJobRecord | None: ...

    async def export_jobs(self, limit: int) -> list[ExportJobRecord]:
        """Newest first."""
        ...

    async def export_in_progress(self) -> bool: ...

    async def exports_since(self, start: datetime) -> int:
        """Exports of the shop asked for at or after `start`, failed ones left out."""
        ...

    async def finish_export_job(
        self,
        job_id: UUID,
        *,
        status: str,
        file_id: UUID | None,
        error: str | None,
        row_count: int | None,
        now: datetime,
    ) -> bool:
        """Close a job that is running. False when it is not running any more; nothing is changed then."""
        ...

    async def member_recipient(self, membership_id: UUID) -> tuple[int, str] | None:
        """Telegram chat and language of an active member; None when there is nobody to tell."""
        ...

    async def export_entries(
        self, *, until: datetime, after: tuple[datetime, UUID] | None, limit: int
    ) -> list[ExportEntry]:
        """Ledger entries recorded up to `until`, oldest first, after the given position."""
        ...

    async def export_promises(self, entry_ids: list[UUID]) -> list[ExportPromise]:
        """Every promise ever set on the given entries, by entry and then oldest first."""
        ...

    async def export_customers(self, *, after: UUID | None, limit: int) -> list[ExportCustomer]:
        """Customers of the shop in identifier order, after the given one."""
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

    async def record_admin_activity(self, *, admin_id: UUID, action: str, subject_type: str, subject_id: UUID) -> None:
        """What an administrator did in this shop under a support access, for the owner's activity log."""
        ...

    async def support_accesses(self, *, before: tuple[datetime, UUID] | None, limit: int) -> list[SupportAccessRow]:
        """This shop's support accesses, newest first."""
        ...

    async def support_access(self, access_id: UUID, *, for_update: bool) -> SupportAccessRow | None: ...

    async def end_support_access(self, access_id: UUID, now: datetime) -> None:
        """Marks it closed by the owner."""
        ...

    async def subscription_locked(self) -> tuple[str, date | None, date | None] | None:
        """As `subscription`, holding the row until the transaction ends."""
        ...

    async def pay_subscription(self, *, state: str, paid_through: date, prior_state: str | None, now: datetime) -> None:
        """Store what `qarz.domain.subscription.after_payment` worked out."""
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

    async def claim_import_batch(self, now: datetime, stale_before: datetime) -> tuple[UUID, UUID, str, int] | None:
        """Take one import batch that waits for the worker: the batch, its shop, the state it waits in,
        and how many times that step has now been started."""
        ...

    async def claim_export_job(self, now: datetime, stale_before: datetime) -> tuple[UUID, UUID, int] | None:
        """Take one export job to write: the job, its shop, and how many times it has now been started."""
        ...

    async def shops_with_receipt_work(self, stale_before: datetime, now: datetime) -> list[UUID]:
        """Shops with a waiting notice sent before `stale_before` or a receipt due for deletion at `now`."""
        ...

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

    async def health_figures(self) -> dict[str, dict[str, float]]:
        """Numbers for monitoring: how long the oldest due message of each channel has waited, in
        seconds, how long ago each scheduled job last finished, and how long the oldest subscription
        receipt has awaited a decision; and how many SMS of the last 24 hours were sent, failed, or wait
        after an attempt that did not succeed. No identifiers."""
        ...

    async def online_payment_shop(self, order_id: UUID) -> UUID | None:
        """The shop an order belongs to; nothing else about it."""
        ...

    async def online_payment_shop_by_txn(self, provider: str, txn: str) -> UUID | None: ...

    async def payme_statement(self, from_ms: int, to_ms: int) -> list[OnlinePayment]:
        """Payme's transactions whose time, as Payme gave it, lies in the period."""
        ...

    async def job_done(self, job: str, period: str) -> bool: ...

    async def finish_job(self, job: str, period: str) -> None: ...

    # --- the operations watch (DEC-078) ------------------------------------------------------------------

    async def ops_alerts(self) -> list[Alert]:
        """Every condition that holds, or stopped and has not been said yet."""
        ...

    async def store_ops_alert(self, alert: Alert) -> None: ...

    async def delete_ops_alert(self, key: str) -> None: ...

    async def ops_database_figures(self, now: datetime) -> DatabaseFigures:
        """Ages and counts of the outbox, the scheduled jobs, SMS, receipts and the ledger check."""
        ...

    async def add_ops_samples(self, taken_at: datetime, values: Mapping[str, float]) -> None: ...

    async def ops_samples(self, since: datetime) -> dict[str, list[tuple[datetime, float]]]: ...

    async def prune_ops_samples(self, before: datetime) -> None:
        """Delete samples older than `before`, keeping the newest of every series."""
        ...

    async def ledger_mismatch_count(self) -> int:
        """In how many places the stored open debts differ from the ledger, over every shop."""
        ...

    async def use_signed_data(self, payload_hash: bytes, expires_at: datetime) -> bool:
        """Remember that this signed sign-in payload was accepted. False when it already had been."""
        ...

    async def purge_expired_sign_ins(self) -> int:
        """Delete sign-in records past their expiry, sessions and administrator sessions that expired
        or were revoked, and old administrator request keys. Returns how many rows went."""
        ...

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

    async def revoke_user_sessions(self, user_id: UUID, now: datetime, *, kind: str | None = None) -> int:
        """End every session of this person that is still open: of both kinds, or of the one named.
        Returns how many it ended."""
        ...

    async def platform_setting(self, key: str) -> Any | None: ...

    # --- the administrator's side (ADR-017, ADR-018) ---------------------------------------------------

    async def telegram_id(self, user_id: UUID) -> int | None: ...

    async def admin_account(self, user_id: UUID, *, for_update: bool) -> AdminAccount | None: ...

    async def enrol_admin(self, user_id: UUID, secret: bytes) -> bool:
        """Store a new encrypted secret. False when the account is already confirmed or is disabled."""
        ...

    async def save_factor_state(
        self,
        user_id: UUID,
        *,
        failures: int,
        locked_until: datetime | None,
        last_step: int | None,
        confirmed_at: datetime | None,
    ) -> None:
        """`confirmed_at` is stored only if the account was not confirmed before."""
        ...

    async def open_admin_session(
        self, *, token_hash: bytes, user_id: UUID, now: datetime, expires_at: datetime
    ) -> None:
        """Revokes the administrator's other admin sessions: one elevation at a time."""
        ...

    async def admin_session_expiry(self, token_hash: bytes, user_id: UUID, now: datetime) -> datetime | None:
        """When the admin session with this hash ends, if it is this user's, not revoked and not over."""
        ...

    async def revoke_admin_sessions(self, user_id: UUID, now: datetime) -> int:
        """End the administrator sessions of this person that are still open. Returns how many it ended."""
        ...

    async def add_admin_audit(
        self,
        *,
        admin_id: UUID,
        action: str,
        target_type: str,
        target_id: str | None,
        shop_id: UUID | None,
        reason: str | None,
        detail: dict[str, Any],
        now: datetime,
    ) -> UUID: ...

    async def list_admin_audit(
        self,
        *,
        shop_id: UUID | None,
        action_prefix: str | None,
        before: tuple[datetime, UUID] | None,
        limit: int,
        admin_id: UUID | None = None,
    ) -> list[AdminAuditRow]: ...

    async def admin_open_shop(self, admin_id: UUID, shop_id: UUID, now: datetime) -> tuple[UUID, datetime] | None:
        """The administrator's current support access for the shop (its identifier and end), if any."""
        ...

    async def admin_support_open(
        self, admin_id: UUID, shop_id: UUID, *, access_id: UUID, reason: str, now: datetime, ends_at: datetime
    ) -> SupportChange | None:
        """None when there is no such shop for an administrator. Also writes the shop's activity log."""
        ...

    async def admin_support_close(self, admin_id: UUID, shop_id: UUID, now: datetime) -> SupportChange | None:
        """None when the administrator has no open support access for the shop."""
        ...

    async def admin_support_list(
        self,
        admin_id: UUID,
        *,
        shop_id: UUID | None,
        open_only: bool,
        now: datetime,
        after: tuple[datetime, UUID] | None,
        limit: int,
    ) -> list[SupportAccessRow]: ...

    async def lock_admin_request_key(self, admin_id: UUID, key: str) -> None: ...

    async def admin_stored_response(self, admin_id: UUID, key: str) -> dict[str, Any] | None: ...

    async def store_admin_response(
        self, admin_id: UUID, key: str, response: dict[str, Any], about_shop: UUID | None
    ) -> None:
        """`about_shop` names the shop the request was about; the row is erased with that shop."""
        ...

    async def admin_shop_search(
        self,
        admin_id: UUID,
        *,
        today: date,
        query: str | None,
        state: str | None,
        shop_id: UUID | None,
        after: tuple[datetime, UUID] | None,
        limit: int,
    ) -> list[AdminShopRow]: ...

    async def admin_shop_receipts(self, admin_id: UUID, shop_id: UUID) -> list[AdminReceiptRow]: ...

    async def admin_has_live_session(self, user_id: UUID, now: datetime) -> bool:
        """Whether the user holds an admin session that is neither revoked nor expired: the proof that
        they passed the second factor within its lifetime. No token is involved."""
        ...

    async def admin_receipts(
        self, admin_id: UUID, *, status: str, after: tuple[datetime, UUID] | None, limit: int
    ) -> list[AdminReceipt]:
        """Subscription receipts of one status across shops, oldest first."""
        ...

    async def admin_receipt(self, admin_id: UUID, receipt_id: UUID, *, lock: bool) -> AdminReceipt | None:
        """One receipt with its file's record. With `lock` it is held until the transaction ends."""
        ...

    async def admin_receipt_copies(self, admin_id: UUID, receipt_id: UUID) -> list[ReceiptCopy]: ...

    async def admin_decide_receipt(
        self, admin_id: UUID, receipt_id: UUID, *, status: str, months: int | None, reason: str | None, now: datetime
    ) -> bool:
        """Approve or reject a receipt that is still waiting. False when it was not waiting any more."""
        ...

    async def admin_shop_activity(self, admin_id: UUID, shop_id: UUID, *, action: str, subject_id: UUID) -> bool:
        """A line in the shop's activity for what an administrator did to it."""
        ...

    async def review_group_receipt(self, group_id: int, receipt_id: UUID, *, lock: bool) -> GroupReceipt | None:
        """One receipt for a decision made in the review group (DEC-064). None unless `group_id` is the
        configured review group. With `lock` the receipt and its shop's subscription are held until the
        transaction ends."""
        ...

    async def review_group_decide_receipt(
        self,
        group_id: int,
        decider_tg: int,
        receipt_id: UUID,
        *,
        status: str,
        months: int | None,
        reason: str | None,
        state: str | None,
        paid_through: date | None,
        prior_state: str | None,
        detail: dict[str, Any],
        now: datetime,
    ) -> bool:
        """Write the decision of a Telegram administrator of the review group: the receipt, recorded with
        their Telegram identifier and no administrator; the subscription, when it is an approval; the
        audit row; and the line in the shop's activity. All or nothing. False, and nothing written, when
        the receipt was not waiting any more or `group_id` is not the configured review group."""
        ...

    async def record_shop_measure(self, shop_id: UUID, *, kind: str, entry_ref: UUID, amount: int) -> None:
        """As a shop's `record_measure`, from outside the shop: no name, phone or identity."""
        ...

    async def admin_lock_subscription(self, admin_id: UUID, shop_id: UUID) -> LockedSubscription | None: ...

    async def admin_reassign_owner(
        self, admin_id: UUID, shop_id: UUID, *, new_owner_tg: int, reason: str, now: datetime
    ) -> OwnerReassignment:
        """Give the shop to the person with this Telegram identifier, with the audit row and the line in
        the shop's activity, all or nothing. The database checks the administrator itself."""
        ...

    async def admin_secrets(self) -> list[tuple[UUID, bytes]]:
        """Every administrator's stored second-factor secret, still encrypted, each row locked until
        the transaction ends."""
        ...

    async def replace_admin_secret(self, user_id: UUID, *, old: bytes, new: bytes) -> bool:
        """Store `new` where `old` is stored. False, and nothing changed, when it is not."""
        ...

    async def admin_store_subscription(
        self,
        admin_id: UUID,
        shop_id: UUID,
        *,
        state: str,
        trial_ends: date | None,
        paid_through: date | None,
        prior_state: str | None,
        now: datetime,
    ) -> bool: ...

    async def platform_settings(self) -> dict[str, tuple[Any, str, datetime]]:
        """Every stored setting: key -> (value, who changed it last, when)."""
        ...

    async def set_platform_setting(
        self, key: str, value: Any, *, admin_id: UUID, reason: str | None, detail: dict[str, Any], now: datetime
    ) -> bool:
        """Store a setting and its audit row together. False, and nothing written, when the database
        does not find an active, confirmed administrator with an open session."""
        ...

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
        """Outcome (linked, waiting, already, taken, full, invalid), shop, and customer when linked."""
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


class SecretUnreadable(Exception):
    """A stored secret could not be decrypted: the key changed, or the stored bytes are not ours."""


class SecretCipher(Protocol):
    """Encrypts the secrets the application keeps in the database (the second factor's secret).

    `context` binds a ciphertext to the row it belongs to, so one account's secret copied into another
    account's row does not decrypt.
    """

    def encrypt(self, plaintext: bytes, context: bytes) -> bytes: ...

    def decrypt(self, ciphertext: bytes, context: bytes) -> bytes:
        """Raises SecretUnreadable."""
        ...


class RotatingCipher(Protocol):
    """A cipher that knows the server secret in use and the one before it (runbook 4)."""

    def reseal(self, ciphertext: bytes, context: bytes) -> bytes | None:
        """The same secret under the current key; None when the current key already reads it. Raises
        SecretUnreadable when neither key does."""
        ...


class RetryLater(Exception):
    """The channel asked us to wait (Telegram error 429)."""

    def __init__(self, seconds: float) -> None:
        super().__init__(f"retry after {seconds}s")
        self.seconds = seconds


class RecipientBlocked(Exception):
    """The recipient cannot be reached and will not be until they act (Telegram error 403)."""


class SendFailed(Exception):
    """Any other delivery failure; worth retrying with backoff."""


class SendRejected(Exception):
    """The channel refused this one message for good: the number cannot receive it, the text is not
    accepted, or the account cannot pay for it. Sending it again cannot help, so it is failed at once;
    other messages to the same recipient are left alone."""


class Sender(Protocol):
    async def send(self, channel: str, recipient: str, payload: dict[str, Any]) -> None: ...


class FileMissing(Exception):
    """The file store holds nothing under the key."""


class FileStoreError(Exception):
    """The file store could not be reached or refused the request. Never carries file content."""


class FileStore(Protocol):
    """Where file contents live (ADR-020). Keys are opaque; see `qarz.domain.files.is_safe_key`."""

    async def put(self, key: str, data: bytes, mime: str) -> None: ...

    async def get(self, key: str) -> bytes:
        """Raises FileMissing when there is no such object."""
        ...

    async def delete(self, key: str) -> None:
        """Deleting what is not there is not an error."""
        ...


class TelegramChatMembers(Protocol):
    """What a person is in a Telegram chat (Bot API `getChatMember`)."""

    async def status(self, chat_id: int, user_id: int) -> str | None:
        """The person's status in the chat as Telegram gives it now (creator, administrator, member,
        restricted, left, kicked), or None when Telegram could not be asked or did not answer."""
        ...


class TelegramFiles(Protocol):
    """Files people send to the bot (Bot API `getFile`)."""

    async def fetch(self, file_id: str, max_bytes: int) -> bytes | None:
        """The content, or None when it is larger than `max_bytes` or cannot be had."""
        ...

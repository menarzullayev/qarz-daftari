"""What the network between shops asks of storage (module J of the expansion).

Every read here is of the shop's own side, under the row-level security every tenant table has. Every
write of something two shops share goes to one of the database's functions of migration 0045, which
writes both sides and verifies the link first; a refusal comes back as `NetworkRefused` with the
function's code. `network_peer` is the one way a transaction acts as a second shop: the supplier of a
delivery note this shop, the buyer, is confirming.
"""

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID

if TYPE_CHECKING:
    from qarz.application.ports import StaffContact, TenantSession


class NetworkRefused(Exception):
    """A function of the network refused. `code` is all it says."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class InviteRecord:
    invite_id: UUID
    as_role: str
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class LinkRecord:
    link_id: UUID
    peer_shop_id: UUID
    role: str
    state: str
    invited: bool
    peer_name: str | None
    peer_phone: str | None
    peer_removed: bool
    supplier_id: UUID | None
    customer_id: UUID | None
    requested_at: datetime
    decided_at: datetime | None
    ended_at: datetime | None
    ended_by_peer: bool

    @property
    def counterpart_id(self) -> UUID | None:
        return self.supplier_id or self.customer_id


@dataclass(frozen=True)
class DraftRecord:
    draft_id: UUID
    link_id: UUID
    note: str | None
    wanted_date: date | None
    lines: list[dict[str, Any]]
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    peer_name: str | None = None


@dataclass(frozen=True)
class OrderRecord:
    order_id: UUID
    link_id: UUID
    peer_shop_id: UUID
    role: str
    number: int
    status: str
    note: str | None
    wanted_date: date | None
    currency: str | None
    total: int | None
    sent_at: datetime
    updated_at: datetime
    closed_reason: str | None
    peer_name: str | None


@dataclass(frozen=True)
class OrderLine:
    line_no: int
    name: str
    unit: str
    qty: Decimal
    item_id: UUID | None
    accepted_qty: Decimal | None
    unit_price: int | None


@dataclass(frozen=True)
class NoteRecord:
    note_id: UUID
    link_id: UUID
    peer_shop_id: UUID
    order_id: UUID
    role: str
    number: int
    status: str
    currency: str
    total: int
    paid: int
    supersedes_id: UUID | None
    supersede_reason: str | None
    issued_at: datetime
    issued_by: UUID | None
    customer_id: UUID | None
    decided_at: datetime | None
    reject_reason: str | None
    document_id: UUID | None
    ledger_entry_id: UUID | None
    order_number: int
    peer_name: str | None


@dataclass(frozen=True)
class NoteLine:
    line_no: int
    name: str
    unit: str
    qty: Decimal
    unit_price: int
    line_total: int
    item_id: UUID | None
    received_qty: Decimal | None


@dataclass(frozen=True)
class PaymentRecord:
    payment_id: UUID
    link_id: UUID
    peer_shop_id: UUID
    role: str
    recorded_by_own: bool
    status: str
    amount: int
    currency: str
    note: str | None
    recorded_at: datetime
    decided_at: datetime | None
    decline_reason: str | None
    supplier_entry_id: UUID | None
    ledger_entry_id: UUID | None
    peer_name: str | None
    # Whether this side's own entry for it still stands in its books; None when it wrote none.
    own_entry_stands: bool | None

    @property
    def own_entry_id(self) -> UUID | None:
        return self.supplier_entry_id or self.ledger_entry_id


@dataclass(frozen=True)
class EventRecord:
    kind: str
    by_peer: bool
    member_id: UUID | None
    at: datetime
    detail: dict[str, Any] | None


@dataclass(frozen=True)
class Agreed:
    """What both sides have confirmed over one link, in one currency."""

    currency: str
    delivered: int  # the totals of the notes that were received
    paid_on_delivery: int
    paid: int  # the payments both confirmed


class NetworkSession(Protocol):
    # --- invitations and links ---------------------------------------------------------------------

    async def network_invites(self, now: datetime) -> list[InviteRecord]: ...

    async def insert_network_invite(
        self,
        *,
        invite_id: UUID,
        code_hash: bytes,
        as_role: str,
        created_by: UUID,
        created_at: datetime,
        expires_at: datetime,
    ) -> None: ...

    async def revoke_network_invite(self, invite_id: UUID, now: datetime) -> bool: ...

    async def network_redeem(self, *, code_hash: bytes, role: str, member_id: UUID, now: datetime) -> UUID: ...

    async def network_links(self) -> list[LinkRecord]: ...

    async def network_link(self, link_id: UUID) -> LinkRecord | None: ...

    async def network_lock(self, peer_shop_id: UUID, link_id: UUID) -> str:
        """Take the link's locks before the caller touches its own books; returns the link's state."""
        ...

    async def network_decide_link(
        self, peer_shop_id: UUID, link_id: UUID, *, accept: bool, member_id: UUID, now: datetime
    ) -> None: ...

    async def network_end_link(self, peer_shop_id: UUID, link_id: UUID, *, member_id: UUID, now: datetime) -> None: ...

    async def network_attach(self, link_id: UUID, counterpart_id: UUID, *, made: bool, member_id: UUID) -> None: ...

    async def network_recipients(self, peer_shop_id: UUID, link_id: UUID) -> "list[StaffContact]":
        """The partner's active members who can be written to, with what decides whether to tell them."""
        ...

    async def network_events(self, subject_id: UUID) -> list[EventRecord]: ...

    async def network_waiting(self) -> dict[str, int]:
        """How many things wait for this shop's own step, by kind."""
        ...

    async def network_agreed(self, link_id: UUID) -> list[Agreed]: ...

    # --- orders ------------------------------------------------------------------------------------

    async def network_drafts(self, limit: int) -> list[DraftRecord]: ...

    async def network_draft(self, draft_id: UUID, *, for_update: bool) -> DraftRecord | None: ...

    async def insert_network_draft(
        self,
        *,
        draft_id: UUID,
        link_id: UUID,
        note: str | None,
        wanted_date: date | None,
        lines: list[dict[str, Any]],
        created_by: UUID,
        now: datetime,
    ) -> None: ...

    async def update_network_draft(
        self, draft_id: UUID, *, note: str | None, wanted_date: date | None, lines: list[dict[str, Any]], now: datetime
    ) -> None: ...

    async def delete_network_draft(self, draft_id: UUID) -> None: ...

    async def network_send_order(
        self,
        peer_shop_id: UUID,
        link_id: UUID,
        order_id: UUID,
        *,
        member_id: UUID,
        note: str | None,
        wanted_date: date | None,
        lines: list[dict[str, Any]],
        now: datetime,
    ) -> int: ...

    async def network_orders(
        self,
        *,
        role: str | None,
        status: str | None,
        link_id: UUID | None,
        before: tuple[datetime, UUID] | None,
        limit: int,
    ) -> list[OrderRecord]: ...

    async def network_order(self, order_id: UUID) -> OrderRecord | None: ...

    async def network_order_lines(self, order_id: UUID) -> list[OrderLine]: ...

    async def network_accept_order(
        self,
        peer_shop_id: UUID,
        order_id: UUID,
        *,
        member_id: UUID,
        currency: str,
        lines: list[dict[str, Any]],
        now: datetime,
    ) -> int: ...

    async def network_close_order(
        self, peer_shop_id: UUID, order_id: UUID, *, member_id: UUID, reason: str, now: datetime
    ) -> str: ...

    # --- delivery notes ------------------------------------------------------------------------------

    async def network_issue_note(
        self,
        peer_shop_id: UUID,
        order_id: UUID,
        note_id: UUID,
        *,
        member_id: UUID,
        customer_id: UUID,
        paid: int,
        reason: str | None,
        lines: list[dict[str, Any]] | None,
        now: datetime,
    ) -> int: ...

    async def network_notes(
        self,
        *,
        role: str | None,
        status: str | None,
        link_id: UUID | None,
        before: tuple[datetime, UUID] | None,
        limit: int,
    ) -> list[NoteRecord]: ...

    async def network_note(self, note_id: UUID) -> NoteRecord | None: ...

    async def network_current_note(self, order_id: UUID) -> NoteRecord | None:
        """The note of an order that counts now: issued, rejected or received."""
        ...

    async def network_note_lines(self, note_id: UUID) -> list[NoteLine]: ...

    async def network_reject_note(
        self,
        peer_shop_id: UUID,
        note_id: UUID,
        *,
        member_id: UUID,
        reason: str,
        lines: list[dict[str, Any]] | None,
        now: datetime,
    ) -> None: ...

    def network_peer(self, peer_shop_id: UUID, note_id: UUID) -> "AbstractAsyncContextManager[TenantSession]":
        """Act as the supplier of a note this shop is confirming, inside this transaction.

        The database moves the tenant setting only for that (`network_enter_peer`) and moves it back when
        the block ends. This session must not be used inside the block.
        """
        ...

    async def network_finish_receipt(
        self,
        peer_shop_id: UUID,
        note_id: UUID,
        *,
        member_id: UUID,
        document_id: UUID,
        entry_id: UUID,
        paid_entry_id: UUID | None,
        now: datetime,
    ) -> None: ...

    # --- payments ----------------------------------------------------------------------------------

    async def network_record_payment(
        self,
        peer_shop_id: UUID,
        link_id: UUID,
        payment_id: UUID,
        *,
        member_id: UUID,
        amount: int,
        currency: str,
        note: str | None,
        entry_id: UUID,
        now: datetime,
    ) -> None: ...

    async def network_decide_payment(
        self,
        peer_shop_id: UUID,
        payment_id: UUID,
        *,
        member_id: UUID,
        confirm: bool,
        reason: str | None,
        entry_id: UUID | None,
        now: datetime,
    ) -> None: ...

    async def network_withdraw_payment(
        self, peer_shop_id: UUID, payment_id: UUID, *, member_id: UUID, now: datetime
    ) -> None: ...

    async def network_payments(
        self, *, status: str | None, link_id: UUID | None, before: tuple[datetime, UUID] | None, limit: int
    ) -> list[PaymentRecord]: ...

    async def network_payment(self, payment_id: UUID) -> PaymentRecord | None: ...

    async def ledger_entry_customer(self, entry_id: UUID) -> UUID | None:
        """Whose account a ledger entry of this shop is on."""
        ...

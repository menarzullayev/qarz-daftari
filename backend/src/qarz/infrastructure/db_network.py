"""PostgreSQL storage of the network between shops (module J of the expansion).

`PgTenantSession` inherits `NetworkQueries`, so the reads here run in the tenant transaction of the
request, under the same row-level security as everything else: a shop reads its own side. Nothing here
writes a table two shops share. Each such change is one call of a function of migration 0045, which
verifies the link and writes both sides; `_call` turns its refusal into `NetworkRefused`.

`network_peer` is the one place in the application where a transaction is given a second tenant, and it
does not set it: it asks the database (`network_enter_peer`), which moves the setting only to the
supplier of a delivery note this shop is confirming.

SQL here is composed only from the module-level constants below, with every value bound as a parameter
(tests/test_sql_composition.py).
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Any, Self
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from qarz.application.network_ports import (
    Agreed,
    DraftRecord,
    EventRecord,
    InviteRecord,
    LinkRecord,
    NetworkRefused,
    NoteLine,
    NoteRecord,
    OrderLine,
    OrderRecord,
    PaymentRecord,
)
from qarz.application.ports import Membership, StaffContact
from qarz.domain.access import Role

# The SQLSTATE of `network_refuse` (migration 0045): the message is the code and nothing else.
_REFUSED = "QN000"
_KNOWN_CODES = frozenset(
    {
        "NETWORK_NOT_FOUND",
        "NETWORK_STATE",
        "NETWORK_INVALID",
        "NETWORK_INVITE_INVALID",
        "NETWORK_LINK_EXISTS",
        "NETWORK_PARTNER_UNAVAILABLE",
        "NETWORK_COUNTERPART_INVALID",
        "NETWORK_CURRENCY",
        "NETWORK_BOOKS_MISMATCH",
    }
)

_LINK_COLUMNS = (
    "l.id, l.peer_shop_id, l.role, l.state, l.invited, l.peer_name, l.peer_phone, l.peer_removed, l.supplier_id, "
    "l.customer_id, l.requested_at, l.decided_at, l.ended_at, l.ended_by_peer"
)
_LINKS = f"SELECT {_LINK_COLUMNS} FROM network_link l ORDER BY l.requested_at DESC, l.id LIMIT 200"
_LINK_BY_ID = f"SELECT {_LINK_COLUMNS} FROM network_link l WHERE l.id = :id"

_INVITES = (
    "SELECT i.id, i.as_role, i.created_at, i.expires_at FROM network_invite i "
    "WHERE i.used_at IS NULL AND i.revoked_at IS NULL AND i.expires_at > :now ORDER BY i.created_at DESC, i.id"
)

_DRAFT_COLUMNS = (
    "d.id, d.link_id, d.note, d.wanted_date, d.lines, d.created_by, d.created_at, d.updated_at, l.peer_name"
)
_DRAFT_FROM = "FROM network_order_draft d JOIN network_link l ON l.shop_id = d.shop_id AND l.id = d.link_id"
_DRAFTS = f"SELECT {_DRAFT_COLUMNS} {_DRAFT_FROM} ORDER BY d.updated_at DESC, d.id LIMIT :limit"
_DRAFT_BY_ID = f"SELECT {_DRAFT_COLUMNS} {_DRAFT_FROM} WHERE d.id = :id"
_DRAFT_LOCKED = f"{_DRAFT_BY_ID} FOR UPDATE OF d"

_ORDER_COLUMNS = (
    "o.id, o.link_id, l.peer_shop_id, o.role, o.number, o.status, o.note, o.wanted_date, o.currency, o.total, "
    "o.sent_at, o.updated_at, o.closed_reason, l.peer_name"
)
_ORDER_FROM = "FROM network_order o JOIN network_link l ON l.shop_id = o.shop_id AND l.id = o.link_id"
_ORDER_BY_ID = f"SELECT {_ORDER_COLUMNS} {_ORDER_FROM} WHERE o.id = :id"
_ORDERS = (
    f"SELECT {_ORDER_COLUMNS} {_ORDER_FROM} "
    "WHERE (CAST(:role AS text) IS NULL OR o.role = CAST(:role AS text)) "
    "AND (CAST(:status AS text) IS NULL OR o.status = CAST(:status AS text)) "
    "AND (CAST(:link AS uuid) IS NULL OR o.link_id = CAST(:link AS uuid)) "
    "AND (CAST(:before_at AS timestamptz) IS NULL "
    "     OR (o.sent_at, o.id) < (CAST(:before_at AS timestamptz), CAST(:before_id AS uuid))) "
    "ORDER BY o.sent_at DESC, o.id DESC LIMIT :limit"
)
_ORDER_LINES = (
    "SELECT n.line_no, n.name, n.unit, n.qty, n.item_id, n.accepted_qty, n.unit_price "
    "FROM network_order_line n WHERE n.order_id = :id ORDER BY n.line_no"
)

_NOTE_COLUMNS = (
    "n.id, n.link_id, l.peer_shop_id, n.order_id, n.role, n.number, n.status, n.currency, n.total, n.paid, "
    "n.supersedes_id, n.supersede_reason, n.issued_at, n.issued_by, n.customer_id, n.decided_at, n.reject_reason, "
    "n.document_id, n.ledger_entry_id, o.number AS order_number, l.peer_name"
)
_NOTE_FROM = (
    "FROM network_note n JOIN network_link l ON l.shop_id = n.shop_id AND l.id = n.link_id "
    "JOIN network_order o ON o.shop_id = n.shop_id AND o.id = n.order_id"
)
_NOTE_BY_ID = f"SELECT {_NOTE_COLUMNS} {_NOTE_FROM} WHERE n.id = :id"
_NOTE_CURRENT = (
    f"SELECT {_NOTE_COLUMNS} {_NOTE_FROM} WHERE n.order_id = :order AND n.status IN ('issued', 'received', 'rejected')"
)
_NOTES = (
    f"SELECT {_NOTE_COLUMNS} {_NOTE_FROM} "
    "WHERE (CAST(:role AS text) IS NULL OR n.role = CAST(:role AS text)) "
    "AND (CAST(:status AS text) IS NULL OR n.status = CAST(:status AS text)) "
    "AND (CAST(:link AS uuid) IS NULL OR n.link_id = CAST(:link AS uuid)) "
    "AND (CAST(:before_at AS timestamptz) IS NULL "
    "     OR (n.issued_at, n.id) < (CAST(:before_at AS timestamptz), CAST(:before_id AS uuid))) "
    "ORDER BY n.issued_at DESC, n.id DESC LIMIT :limit"
)
_NOTE_LINES = (
    "SELECT n.line_no, n.name, n.unit, n.qty, n.unit_price, n.line_total, n.item_id, n.received_qty "
    "FROM network_note_line n WHERE n.note_id = :id ORDER BY n.line_no"
)

_PAYMENT_COLUMNS = (
    "p.id, p.link_id, l.peer_shop_id, p.role, p.recorded_by_own, p.status, p.amount, p.currency, p.note, "
    "p.recorded_at, p.decided_at, p.decline_reason, p.supplier_entry_id, p.ledger_entry_id, l.peer_name, "
    "CASE WHEN p.supplier_entry_id IS NOT NULL THEN "
    "       NOT EXISTS (SELECT 1 FROM supplier_entry r WHERE r.reverses_id = p.supplier_entry_id) "
    "     WHEN p.ledger_entry_id IS NOT NULL THEN "
    "       NOT EXISTS (SELECT 1 FROM ledger_entry r WHERE r.reverses_id = p.ledger_entry_id) "
    "END AS own_entry_stands"
)
_PAYMENT_FROM = "FROM network_payment p JOIN network_link l ON l.shop_id = p.shop_id AND l.id = p.link_id"
_PAYMENT_BY_ID = f"SELECT {_PAYMENT_COLUMNS} {_PAYMENT_FROM} WHERE p.id = :id"
_PAYMENTS = (
    f"SELECT {_PAYMENT_COLUMNS} {_PAYMENT_FROM} "
    "WHERE (CAST(:status AS text) IS NULL OR p.status = CAST(:status AS text)) "
    "AND (CAST(:link AS uuid) IS NULL OR p.link_id = CAST(:link AS uuid)) "
    "AND (CAST(:before_at AS timestamptz) IS NULL "
    "     OR (p.recorded_at, p.id) < (CAST(:before_at AS timestamptz), CAST(:before_id AS uuid))) "
    "ORDER BY p.recorded_at DESC, p.id DESC LIMIT :limit"
)

_EVENTS = (
    "SELECT e.kind, e.by_peer, e.member_id, e.at, e.detail FROM network_event e "
    "WHERE e.subject_id = :id ORDER BY e.at, e.id LIMIT 200"
)
# What waits for this shop's own step: a request to answer, an order to accept, a note to answer, a
# rejected note to correct, a payment to answer.
_WAITING = (
    "SELECT "
    "(SELECT count(*) FROM network_link l WHERE l.state = 'requested' AND l.invited) AS links, "
    "(SELECT count(*) FROM network_order o WHERE o.role = 'supplier' AND o.status = 'sent') AS orders, "
    "(SELECT count(*) FROM network_order o WHERE o.role = 'supplier' AND o.status = 'accepted') AS deliveries, "
    "(SELECT count(*) FROM network_note n WHERE n.role = 'buyer' AND n.status = 'issued') AS notes, "
    "(SELECT count(*) FROM network_note n WHERE n.role = 'supplier' AND n.status = 'rejected') AS rejected_notes, "
    "(SELECT count(*) FROM network_payment p WHERE p.status = 'awaiting' AND NOT p.recorded_by_own) AS payments"
)
_AGREED = (
    "SELECT currency, sum(delivered)::bigint AS delivered, sum(paid_on_delivery)::bigint AS paid_on_delivery, "
    "       sum(paid)::bigint AS paid "
    "FROM (SELECT n.currency, n.total AS delivered, n.paid AS paid_on_delivery, 0 AS paid FROM network_note n "
    "       WHERE n.link_id = :link AND n.status = 'received' "
    "      UNION ALL "
    "      SELECT p.currency, 0, 0, p.amount FROM network_payment p "
    "       WHERE p.link_id = :link AND p.status = 'confirmed') agreed "
    "GROUP BY currency ORDER BY currency"
)


def _link(row: Any) -> LinkRecord:
    return LinkRecord(
        row.id, row.peer_shop_id, row.role, row.state, bool(row.invited), row.peer_name, row.peer_phone,
        bool(row.peer_removed), row.supplier_id, row.customer_id, row.requested_at, row.decided_at, row.ended_at,
        bool(row.ended_by_peer),
    )  # fmt: skip


def _draft(row: Any) -> DraftRecord:
    return DraftRecord(
        row.id, row.link_id, row.note, row.wanted_date, list(row.lines), row.created_by, row.created_at,
        row.updated_at, row.peer_name,
    )  # fmt: skip


def _order(row: Any) -> OrderRecord:
    return OrderRecord(
        row.id, row.link_id, row.peer_shop_id, row.role, int(row.number), row.status, row.note, row.wanted_date,
        row.currency, None if row.total is None else int(row.total), row.sent_at, row.updated_at, row.closed_reason,
        row.peer_name,
    )  # fmt: skip


def _note(row: Any) -> NoteRecord:
    return NoteRecord(
        row.id, row.link_id, row.peer_shop_id, row.order_id, row.role, int(row.number), row.status, row.currency,
        int(row.total), int(row.paid), row.supersedes_id, row.supersede_reason, row.issued_at, row.issued_by,
        row.customer_id, row.decided_at, row.reject_reason, row.document_id, row.ledger_entry_id,
        int(row.order_number), row.peer_name,
    )  # fmt: skip


def _payment(row: Any) -> PaymentRecord:
    return PaymentRecord(
        row.id, row.link_id, row.peer_shop_id, row.role, bool(row.recorded_by_own), row.status, int(row.amount),
        row.currency, row.note, row.recorded_at, row.decided_at, row.decline_reason, row.supplier_entry_id,
        row.ledger_entry_id, row.peer_name, None if row.own_entry_stands is None else bool(row.own_entry_stands),
    )  # fmt: skip


def _json(value: Any) -> str | None:
    return None if value is None else json.dumps(value, sort_keys=True)


def refusal_of(error: DBAPIError) -> NetworkRefused | None:
    """The refusal a function of the network raised, or None for any other database error."""
    if getattr(error.orig, "sqlstate", None) != _REFUSED:
        return None
    raw = str(getattr(getattr(error.orig, "__cause__", None), "message", "") or error.orig)
    for code in sorted(_KNOWN_CODES, key=len, reverse=True):
        if code in raw:
            return NetworkRefused(code)
    return NetworkRefused("NETWORK_NOT_FOUND")


class NetworkQueries:
    _conn: AsyncConnection
    _shop_id: UUID

    def __init__(self, conn: AsyncConnection, shop_id: UUID) -> None:
        # The session's own constructor has this shape; it is repeated so `network_peer` can make one.
        self._conn = conn
        self._shop_id = shop_id

    async def _call(self, statement: str, parameters: dict[str, Any]) -> Any:
        """One function of the network. Its refusal is `NetworkRefused`; anything else is an error."""
        try:
            return (await self._conn.execute(text(statement), {"shop": self._shop_id, **parameters})).scalar()
        except DBAPIError as error:
            refused = refusal_of(error)
            if refused is None:
                raise
            raise refused from error

    # --- invitations and links ---------------------------------------------------------------------

    async def network_invites(self, now: datetime) -> list[InviteRecord]:
        rows = (await self._conn.execute(text(_INVITES), {"now": now})).all()
        return [InviteRecord(row.id, row.as_role, row.created_at, row.expires_at) for row in rows]

    async def insert_network_invite(
        self,
        *,
        invite_id: UUID,
        code_hash: bytes,
        as_role: str,
        created_by: UUID,
        created_at: datetime,
        expires_at: datetime,
    ) -> None:
        await self._conn.execute(
            text(
                "INSERT INTO network_invite (id, shop_id, code_hash, as_role, created_by, created_at, expires_at) "
                "VALUES (:id, :shop_id, :code_hash, :as_role, :created_by, :created_at, :expires_at)"
            ),
            {
                "id": invite_id,
                "shop_id": self._shop_id,
                "code_hash": code_hash,
                "as_role": as_role,
                "created_by": created_by,
                "created_at": created_at,
                "expires_at": expires_at,
            },
        )

    async def revoke_network_invite(self, invite_id: UUID, now: datetime) -> bool:
        row = (
            await self._conn.execute(
                text(
                    "UPDATE network_invite SET revoked_at = :now "
                    "WHERE id = :id AND used_at IS NULL AND revoked_at IS NULL RETURNING id"
                ),
                {"id": invite_id, "now": now},
            )
        ).first()
        return row is not None

    async def network_redeem(self, *, code_hash: bytes, role: str, member_id: UUID, now: datetime) -> UUID:
        try:
            link = await self._call(
                "SELECT network_invite_redeem(:shop, :code_hash, :role, :member, :now)",
                {"code_hash": code_hash, "role": role, "member": member_id, "now": now},
            )
        except IntegrityError as error:
            # Two requests for the same pair at the same moment: the index let one through.
            raise NetworkRefused("NETWORK_LINK_EXISTS") from error
        return UUID(str(link))

    async def network_links(self) -> list[LinkRecord]:
        return [_link(row) for row in (await self._conn.execute(text(_LINKS))).all()]

    async def network_link(self, link_id: UUID) -> LinkRecord | None:
        row = (await self._conn.execute(text(_LINK_BY_ID), {"id": link_id})).first()
        return None if row is None else _link(row)

    async def network_lock(self, peer_shop_id: UUID, link_id: UUID) -> str:
        return str(
            await self._call("SELECT network_lock(:shop, :peer, :link)", {"peer": peer_shop_id, "link": link_id})
        )

    async def network_decide_link(
        self, peer_shop_id: UUID, link_id: UUID, *, accept: bool, member_id: UUID, now: datetime
    ) -> None:
        await self._call(
            "SELECT network_link_decide(:shop, :peer, :link, :accept, :member, :now)",
            {"peer": peer_shop_id, "link": link_id, "accept": accept, "member": member_id, "now": now},
        )

    async def network_end_link(self, peer_shop_id: UUID, link_id: UUID, *, member_id: UUID, now: datetime) -> None:
        await self._call(
            "SELECT network_link_end(:shop, :peer, :link, :member, :now)",
            {"peer": peer_shop_id, "link": link_id, "member": member_id, "now": now},
        )

    async def network_attach(self, link_id: UUID, counterpart_id: UUID, *, made: bool, member_id: UUID) -> None:
        await self._call(
            "SELECT network_link_attach(:shop, :link, :counterpart, :made, :member)",
            {"link": link_id, "counterpart": counterpart_id, "made": made, "member": member_id},
        )

    async def network_recipients(self, peer_shop_id: UUID, link_id: UUID) -> list[StaffContact]:
        try:
            rows = (
                await self._conn.execute(
                    text(
                        "SELECT r.tg_id, r.lang, r.role, r.granted, r.denied, "
                        "coalesce((SELECT p.value = 'true'::jsonb FROM platform_setting p "
                        "          WHERE p.key = 'permissions_on'), false) AS permissions_on "
                        "FROM network_notice_recipients(:shop, :peer, :link) r"
                    ),
                    {"shop": self._shop_id, "peer": peer_shop_id, "link": link_id},
                )
            ).all()
        except DBAPIError as error:
            refused = refusal_of(error)
            if refused is None:
                raise
            raise refused from error
        return [
            StaffContact(
                int(row.tg_id),
                str(row.lang),
                # The partner's member is never identified here: the notice needs only what they may see.
                Membership(
                    UUID(int=0),
                    Role(row.role),
                    permissions_on=bool(row.permissions_on),
                    granted=frozenset(row.granted),
                    denied=frozenset(row.denied),
                ),
            )
            for row in rows
        ]

    async def network_events(self, subject_id: UUID) -> list[EventRecord]:
        rows = (await self._conn.execute(text(_EVENTS), {"id": subject_id})).all()
        return [EventRecord(row.kind, bool(row.by_peer), row.member_id, row.at, row.detail) for row in rows]

    async def network_waiting(self) -> dict[str, int]:
        row = (await self._conn.execute(text(_WAITING))).one()
        return {name: int(getattr(row, name)) for name in row._fields}

    async def network_agreed(self, link_id: UUID) -> list[Agreed]:
        rows = (await self._conn.execute(text(_AGREED), {"link": link_id})).all()
        return [Agreed(row.currency, int(row.delivered), int(row.paid_on_delivery), int(row.paid)) for row in rows]

    # --- orders ------------------------------------------------------------------------------------

    async def network_drafts(self, limit: int) -> list[DraftRecord]:
        return [_draft(row) for row in (await self._conn.execute(text(_DRAFTS), {"limit": limit})).all()]

    async def network_draft(self, draft_id: UUID, *, for_update: bool) -> DraftRecord | None:
        row = (await self._conn.execute(text(_DRAFT_LOCKED if for_update else _DRAFT_BY_ID), {"id": draft_id})).first()
        return None if row is None else _draft(row)

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
    ) -> None:
        await self._conn.execute(
            text(
                "INSERT INTO network_order_draft (id, shop_id, link_id, note, wanted_date, lines, created_by, "
                "  created_at, updated_at) "
                "VALUES (:id, :shop_id, :link, :note, :wanted, CAST(:lines AS jsonb), :created_by, :now, :now)"
            ),
            {
                "id": draft_id,
                "shop_id": self._shop_id,
                "link": link_id,
                "note": note,
                "wanted": wanted_date,
                "lines": _json(lines),
                "created_by": created_by,
                "now": now,
            },
        )

    async def update_network_draft(
        self, draft_id: UUID, *, note: str | None, wanted_date: date | None, lines: list[dict[str, Any]], now: datetime
    ) -> None:
        await self._conn.execute(
            text(
                "UPDATE network_order_draft SET note = :note, wanted_date = :wanted, lines = CAST(:lines AS jsonb), "
                "  updated_at = :now WHERE id = :id"
            ),
            {"id": draft_id, "note": note, "wanted": wanted_date, "lines": _json(lines), "now": now},
        )

    async def delete_network_draft(self, draft_id: UUID) -> None:
        await self._conn.execute(text("DELETE FROM network_order_draft WHERE id = :id"), {"id": draft_id})

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
    ) -> int:
        number = await self._call(
            "SELECT network_order_send(:shop, :peer, :link, :order, :member, :note, :wanted, "
            "  CAST(:lines AS jsonb), :now)",
            {
                "peer": peer_shop_id,
                "link": link_id,
                "order": order_id,
                "member": member_id,
                "note": note,
                "wanted": wanted_date,
                "lines": _json(lines),
                "now": now,
            },
        )
        return int(number)

    async def network_orders(
        self,
        *,
        role: str | None,
        status: str | None,
        link_id: UUID | None,
        before: tuple[datetime, UUID] | None,
        limit: int,
    ) -> list[OrderRecord]:
        rows = (
            await self._conn.execute(
                text(_ORDERS),
                {
                    "role": role,
                    "status": status,
                    "link": link_id,
                    "before_at": None if before is None else before[0],
                    "before_id": None if before is None else before[1],
                    "limit": limit,
                },
            )
        ).all()
        return [_order(row) for row in rows]

    async def network_order(self, order_id: UUID) -> OrderRecord | None:
        row = (await self._conn.execute(text(_ORDER_BY_ID), {"id": order_id})).first()
        return None if row is None else _order(row)

    async def network_order_lines(self, order_id: UUID) -> list[OrderLine]:
        rows = (await self._conn.execute(text(_ORDER_LINES), {"id": order_id})).all()
        return [
            OrderLine(
                int(row.line_no), row.name, row.unit, row.qty, row.item_id, row.accepted_qty,
                None if row.unit_price is None else int(row.unit_price),
            )
            for row in rows
        ]  # fmt: skip

    async def network_accept_order(
        self,
        peer_shop_id: UUID,
        order_id: UUID,
        *,
        member_id: UUID,
        currency: str,
        lines: list[dict[str, Any]],
        now: datetime,
    ) -> int:
        total = await self._call(
            "SELECT network_order_accept(:shop, :peer, :order, :member, :currency, CAST(:lines AS jsonb), :now)",
            {
                "peer": peer_shop_id,
                "order": order_id,
                "member": member_id,
                "currency": currency,
                "lines": _json(lines),
                "now": now,
            },
        )
        return int(total)

    async def network_close_order(
        self, peer_shop_id: UUID, order_id: UUID, *, member_id: UUID, reason: str, now: datetime
    ) -> str:
        return str(
            await self._call(
                "SELECT network_order_close(:shop, :peer, :order, :member, :reason, :now)",
                {"peer": peer_shop_id, "order": order_id, "member": member_id, "reason": reason, "now": now},
            )
        )

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
    ) -> int:
        number = await self._call(
            "SELECT network_note_issue(:shop, :peer, :order, :note, :member, :customer, :paid, :reason, "
            "  CAST(:lines AS jsonb), :now)",
            {
                "peer": peer_shop_id,
                "order": order_id,
                "note": note_id,
                "member": member_id,
                "customer": customer_id,
                "paid": paid,
                "reason": reason,
                "lines": _json(lines),
                "now": now,
            },
        )
        return int(number)

    async def network_notes(
        self,
        *,
        role: str | None,
        status: str | None,
        link_id: UUID | None,
        before: tuple[datetime, UUID] | None,
        limit: int,
    ) -> list[NoteRecord]:
        rows = (
            await self._conn.execute(
                text(_NOTES),
                {
                    "role": role,
                    "status": status,
                    "link": link_id,
                    "before_at": None if before is None else before[0],
                    "before_id": None if before is None else before[1],
                    "limit": limit,
                },
            )
        ).all()
        return [_note(row) for row in rows]

    async def network_note(self, note_id: UUID) -> NoteRecord | None:
        row = (await self._conn.execute(text(_NOTE_BY_ID), {"id": note_id})).first()
        return None if row is None else _note(row)

    async def network_current_note(self, order_id: UUID) -> NoteRecord | None:
        row = (await self._conn.execute(text(_NOTE_CURRENT), {"order": order_id})).first()
        return None if row is None else _note(row)

    async def network_note_lines(self, note_id: UUID) -> list[NoteLine]:
        rows = (await self._conn.execute(text(_NOTE_LINES), {"id": note_id})).all()
        return [
            NoteLine(
                int(row.line_no), row.name, row.unit, row.qty, int(row.unit_price), int(row.line_total), row.item_id,
                row.received_qty,
            )
            for row in rows
        ]  # fmt: skip

    async def network_note_poster(self, issuer_id: UUID) -> tuple[Membership | None, UUID | None]:
        rows = (
            await self._conn.execute(
                # The shop is named as well as held by row-level security, as for `active_membership`.
                # The switch and the member's own changes are read here, in the confirming transaction.
                text(
                    "SELECT m.id, m.role, m.permissions_granted, m.permissions_denied, "
                    "coalesce((SELECT p.value = 'true'::jsonb FROM platform_setting p "
                    "          WHERE p.key = 'permissions_on'), false) AS permissions_on "
                    "FROM membership m "
                    "WHERE m.shop_id = :shop AND m.status = 'active' AND (m.id = :issuer OR m.role = 'owner')"
                ),
                {"shop": self._shop_id, "issuer": issuer_id},
            )
        ).all()
        issuer: Membership | None = None
        owner_id: UUID | None = None
        for row in rows:
            if row.id == issuer_id:
                issuer = Membership(
                    row.id,
                    Role(row.role),
                    permissions_on=bool(row.permissions_on),
                    granted=frozenset(row.permissions_granted),
                    denied=frozenset(row.permissions_denied),
                )
            if row.role == Role.OWNER.value:
                owner_id = row.id
        return issuer, owner_id

    async def network_reject_note(
        self,
        peer_shop_id: UUID,
        note_id: UUID,
        *,
        member_id: UUID,
        reason: str,
        lines: list[dict[str, Any]] | None,
        now: datetime,
    ) -> None:
        await self._call(
            "SELECT network_note_reject(:shop, :peer, :note, :member, :reason, CAST(:lines AS jsonb), :now)",
            {
                "peer": peer_shop_id,
                "note": note_id,
                "member": member_id,
                "reason": reason,
                "lines": _json(lines),
                "now": now,
            },
        )

    @asynccontextmanager
    async def network_peer(self, peer_shop_id: UUID, note_id: UUID) -> AsyncIterator[Self]:
        # The database decides whether the tenant may move, and moves it (migration 0045). If the block
        # raises, the transaction is over and the setting with it; nothing is run after an error.
        await self._call("SELECT network_enter_peer(:shop, :peer, :note)", {"peer": peer_shop_id, "note": note_id})
        yield type(self)(self._conn, peer_shop_id)
        await self._call("SELECT network_leave_peer(:shop, :peer, :note)", {"peer": peer_shop_id, "note": note_id})

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
    ) -> None:
        await self._call(
            "SELECT network_receipt_finish(:shop, :peer, :note, :member, :document, :entry, :paid_entry, :now)",
            {
                "peer": peer_shop_id,
                "note": note_id,
                "member": member_id,
                "document": document_id,
                "entry": entry_id,
                "paid_entry": paid_entry_id,
                "now": now,
            },
        )

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
    ) -> None:
        await self._call(
            "SELECT network_payment_record(:shop, :peer, :link, :payment, :member, :amount, :currency, :note, "
            "  :entry, :now)",
            {
                "peer": peer_shop_id,
                "link": link_id,
                "payment": payment_id,
                "member": member_id,
                "amount": amount,
                "currency": currency,
                "note": note,
                "entry": entry_id,
                "now": now,
            },
        )

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
    ) -> None:
        await self._call(
            "SELECT network_payment_decide(:shop, :peer, :payment, :member, :confirm, :reason, :entry, :now)",
            {
                "peer": peer_shop_id,
                "payment": payment_id,
                "member": member_id,
                "confirm": confirm,
                "reason": reason,
                "entry": entry_id,
                "now": now,
            },
        )

    async def network_withdraw_payment(
        self, peer_shop_id: UUID, payment_id: UUID, *, member_id: UUID, now: datetime
    ) -> None:
        await self._call(
            "SELECT network_payment_withdraw(:shop, :peer, :payment, :member, :now)",
            {"peer": peer_shop_id, "payment": payment_id, "member": member_id, "now": now},
        )

    async def network_payments(
        self, *, status: str | None, link_id: UUID | None, before: tuple[datetime, UUID] | None, limit: int
    ) -> list[PaymentRecord]:
        rows = (
            await self._conn.execute(
                text(_PAYMENTS),
                {
                    "status": status,
                    "link": link_id,
                    "before_at": None if before is None else before[0],
                    "before_id": None if before is None else before[1],
                    "limit": limit,
                },
            )
        ).all()
        return [_payment(row) for row in rows]

    async def network_payment(self, payment_id: UUID) -> PaymentRecord | None:
        row = (await self._conn.execute(text(_PAYMENT_BY_ID), {"id": payment_id})).first()
        return None if row is None else _payment(row)

    async def ledger_entry_customer(self, entry_id: UUID) -> UUID | None:
        row = (
            await self._conn.execute(
                text("SELECT e.customer_id FROM ledger_entry e WHERE e.id = :id"), {"id": entry_id}
            )
        ).first()
        return None if row is None else row.customer_id

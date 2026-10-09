"""Payment notices (REQ-060, REQ-061; domain rule BR-14, invariant INV-15).

A linked customer tells the shop they paid an amount, optionally with a receipt. The notice changes
nothing by itself: any staff member accepts it, which records a payment through the ledger like any
other, or declines it with a reason. A notice nobody decides within fourteen days expires.
"""

import hashlib
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

from qarz.application import idempotency
from qarz.application.authorization import holders
from qarz.application.chat_texts import money, say
from qarz.application.currencies import USD, UZS, amount_hint, dollars_on, require_currency, tag
from qarz.application.customer_account import resolve_link
from qarz.application.customers import require_viewable, require_writable
from qarz.application.errors import AppError, NotFound, ValidationFailed
from qarz.application.files import PURGE_BATCH, FileService, FileStoreUnavailable, StagedFile
from qarz.application.ledger_service import LedgerRefused, append_entry_in, clean_entry
from qarz.application.notice_view import notice_body, staff_notice_body
from qarz.application.operations import operation, self_operation
from qarz.application.ports import CustomerRecord, Membership, PaymentNoticeRecord, Storage, TenantSession
from qarz.application.shops import require_member
from qarz.domain import ledger, permissions
from qarz.domain.access import Capability
from qarz.domain.disputes import clean_reason
from qarz.domain.files import receipt_delete_after
from qarz.domain.ledger import EntryKind
from qarz.domain.money import Currency
from qarz.domain.payment_notices import (
    ACCEPTED,
    DECLINED,
    NOTICE_LIFETIME,
    SENT,
    NoticeRefusal,
    effective_status,
    may_send,
    receipt_bound,
    valid_amount,
)
from qarz.domain.promise import tashkent_date

SEND_NOTICE = self_operation("me.accounts.payment_notices.send")
LIST_NOTICES = operation("payment_notices.list", Capability.DECIDE_PAYMENT_NOTICE)
ACCEPT_NOTICE = operation("payment_notices.accept", Capability.DECIDE_PAYMENT_NOTICE)
DECLINE_NOTICE = operation("payment_notices.decline", Capability.DECIDE_PAYMENT_NOTICE)
READ_RECEIPT = operation("payment_notices.receipt", Capability.DECIDE_PAYMENT_NOTICE)

# Every staff member handles payment notices (specification, resources table).
FILE_PURPOSE = "payment_notice"
# Where a signed link points; served outside the API, to whoever holds a valid link.
FILE_LINK_PATH = "/files"


class PaymentNoticeNotAllowed(AppError):
    """The customer may not send another notice now. The field says why."""

    code = "PAYMENT_NOTICE_NOT_ALLOWED"


class PaymentNoticeNotOpen(AppError):
    """The notice was already accepted or declined, or it expired."""

    code = "PAYMENT_NOTICE_NOT_OPEN"


def callback_data(action: str, identifier: UUID) -> str:
    # The same format as qarz.application.chat.callback; kept here so that this module does not import the chat.
    return f"v2:{action}:{identifier.hex}"


async def _tell_staff(session: TenantSession, record: PaymentNoticeRecord, name: str, balance: int) -> None:
    """Every staff member is told, each in their own language, with the two decisions as buttons."""
    settings = await session.shop_settings()
    shop = "" if settings is None else settings.name
    key = "s_notice" if record.file_id is None else "s_notice_receipt"
    for tg_id, lang in holders(await session.staff_contacts(), permissions.PAYMENT_NOTICES_DECIDE):
        # Both in the notice's currency: what the customer says they paid, and what they owe in it.
        text = say(
            lang,
            key,
            shop=shop,
            name=name,
            amount=money(lang, record.amount, record.currency),
            balance=money(lang, balance, record.currency),
        )
        if record.receipt_seen_before:
            # The same file was sent to this shop before: staff are told, the customer is not.
            text += "\n" + say(lang, "s_receipt_seen_before")
        payload = {
            "text": text,
            "reply_markup": {
                "inline_keyboard": [
                    [
                        {
                            "text": say(lang, "notice_accept_button"),
                            "callback_data": callback_data("pna", record.notice_id),
                        },
                        {"text": say(lang, "decline_button"), "callback_data": callback_data("pnd", record.notice_id)},
                    ]
                ]
            },
        }
        await session.enqueue(
            recipient=str(tg_id), payload=payload, dedupe_key=f"notice:{record.notice_id}:sent:{tg_id}"
        )


def _require_open(record: PaymentNoticeRecord, now: datetime) -> None:
    status = effective_status(record.status, record.created_at, now)
    if status != SENT:
        raise PaymentNoticeNotOpen({"reason": status})


async def _locked_open_notice(session: TenantSession, notice_id: UUID, now: datetime) -> PaymentNoticeRecord:
    """The notice, read again after its customer's row is locked: two decisions cannot both find it open."""
    found = await session.get_payment_notice(notice_id)
    if found is None:
        raise NotFound()
    await session.get_customer(found.customer_id, for_update=True)
    record = await session.get_payment_notice(notice_id)
    if record is None or (record.currency is USD and not await dollars_on(session)):
        # A notice in dollars waits, unseen, while the shop does not show dollars.
        raise NotFound()
    _require_open(record, now)
    return record


async def accept_in(
    session: TenantSession, actor: Membership, notice_id: UUID, amount: int | None, now: datetime
) -> dict[str, Any]:
    """Accept an open notice inside a tenant transaction the caller has opened and authorized (BR-14).

    The payment is recorded for the stated amount, or for `amount` when the staff member corrects it.
    Whatever the ledger refuses (a payment above the balance, an archived customer) is raised unchanged,
    and the transaction's rollback leaves the notice open.
    """
    record = await _locked_open_notice(session, notice_id, now)
    if amount is not None:
        # A corrected amount is in the notice's currency, and inside that currency's range.
        clean_entry(EntryKind.PAYMENT.value, amount, None, None, record.currency)
    paid = record.amount if amount is None else amount
    # Looked up before the payment: the payment that settles a debt may carry out a removal request,
    # after which there is nobody left to tell.
    recipient = await session.customer_recipient(record.customer_id)
    body = await append_entry_in(
        session,
        actor,
        record.customer_id,
        kind=EntryKind.PAYMENT,
        amount=paid,
        note=None,
        promised_date=None,
        now=now,
        currency=record.currency,
    )
    closed = await session.close_payment_notice(
        notice_id,
        status=ACCEPTED,
        payment_entry=UUID(body["entry"]["id"]),
        decline_reason=None,
        decided_by=actor.membership_id,
        now=now,
    )
    if record.file_id is not None:
        await session.shorten_file_retention(record.file_id, receipt_delete_after(now))
    await session.record_activity(
        membership_id=actor.membership_id,
        action="payment_notice.accepted",
        subject_type="customer",
        subject_id=record.customer_id,
    )
    await session.record_measure(
        kind="payment_notice_accepted", entry_ref=notice_id, amount=paid, promised=None, currency=record.currency
    )
    if recipient is not None:
        tg_id, lang = recipient
        settings = await session.shop_settings()
        shop = "" if settings is None else settings.name
        text = (
            say(lang, "n_notice_accepted", shop=shop, amount=money(lang, record.amount, record.currency))
            if paid == record.amount
            else say(
                lang,
                "n_notice_corrected",
                shop=shop,
                amount=money(lang, record.amount, record.currency),
                recorded=money(lang, paid, record.currency),
            )
        )
        await session.enqueue(recipient=str(tg_id), payload={"text": text}, dedupe_key=f"notice:{notice_id}:accepted")
    return {"notice": notice_body(closed, now), "entry": body["entry"], "customer": body["customer"]}


async def decline_in(
    session: TenantSession, actor: Membership, notice_id: UUID, reason: str, now: datetime
) -> dict[str, Any]:
    """Decline an open notice inside a tenant transaction the caller has opened and authorized."""
    record = await _locked_open_notice(session, notice_id, now)
    closed = await session.close_payment_notice(
        notice_id,
        status=DECLINED,
        payment_entry=None,
        decline_reason=reason,
        decided_by=actor.membership_id,
        now=now,
    )
    if record.file_id is not None:
        await session.shorten_file_retention(record.file_id, receipt_delete_after(now))
    await session.record_activity(
        membership_id=actor.membership_id,
        action="payment_notice.declined",
        subject_type="customer",
        subject_id=record.customer_id,
    )
    await session.record_measure(
        kind="payment_notice_declined",
        entry_ref=notice_id,
        amount=record.amount,
        promised=None,
        currency=record.currency,
    )
    recipient = await session.customer_recipient(record.customer_id)
    if recipient is not None:
        tg_id, lang = recipient
        settings = await session.shop_settings()
        await session.enqueue(
            recipient=str(tg_id),
            payload={
                "text": say(
                    lang,
                    "n_notice_declined",
                    shop="" if settings is None else settings.name,
                    amount=money(lang, record.amount, record.currency),
                    reason=reason,
                )
            },
            dedupe_key=f"notice:{notice_id}:declined",
        )
    return notice_body(closed, now)


class PaymentNoticeService:
    def __init__(self, storage: Storage, files: FileService, now: Callable[[], datetime] | None = None) -> None:
        self._storage = storage
        self._files = files
        self._now = now or (lambda: datetime.now(UTC))

    def _today(self) -> date:
        return tashkent_date(self._now())

    # --- the customer ---------------------------------------------------------------------------------

    async def require_link(self, user_id: UUID, link_id: UUID) -> None:
        """Refuse a caller whose link this is not, before anything they sent is even read."""
        await resolve_link(self._storage, user_id, link_id)

    async def send(
        self,
        user_id: UUID,
        link_id: UUID,
        amount: Any,
        receipt: bytes | None,
        request_key: str | None = None,
        *,
        update_key: str | None = None,
        currency: str | None = None,
    ) -> dict[str, Any]:
        """Send a notice for one of the caller's own accounts.

        `amount` is in `currency` (so'm when none is named) and is compared with what the customer owes
        in that currency.

        `request_key` is optional for a customer: with it, a repeat returns the first notice and stores
        nothing new. The chat passes `update_key` instead, the key it derives from the Telegram update,
        which has a form no API caller can send.
        """
        shop_id, customer_id = await resolve_link(self._storage, user_id, link_id)
        key = update_key if request_key is None else idempotency.validate_key(request_key)
        paid_in = UZS
        if currency not in (None, UZS.value):
            # Whether the shop works in dollars is the shop's to say; asked again where the notice is stored.
            async with self._storage.tenant(shop_id) as session:
                paid_in = await require_currency(session, currency)
        if not valid_amount(amount, paid_in):
            raise ValidationFailed({"amount": amount_hint(paid_in)})
        checked = None if receipt is None else self._files.check(receipt)
        staged = None
        if checked is not None:
            # A notice that would be refused anyway is refused before its receipt is uploaded (security
            # review, P36-3). The same is checked again under the lock, where it counts.
            async with self._storage.tenant(shop_id) as session:
                if key is None or await session.stored_response(key) is None:
                    await self._require_sendable(session, customer_id, int(amount), paid_in, lock=False)
            # Stored before the writing transaction opens: no network call is made while the customer's
            # row is locked. It is removed again unless that transaction records it.
            staged = await self._files.stage(checked)
        recorded = False

        async def write(session: TenantSession) -> dict[str, Any]:
            nonlocal recorded
            body = await self._send_in(session, customer_id, int(amount), staged, paid_in)
            recorded = True
            return body

        try:
            async with self._storage.tenant(shop_id) as session:
                if key is None:
                    body = await write(session)
                else:

                    async def apply() -> dict[str, Any]:
                        return await write(session)

                    body = await idempotency.run_once(
                        session,
                        key=key,
                        operation=SEND_NOTICE.name,
                        user_id=user_id,
                        # Only a notice in dollars carries its currency: a so'm one keeps its fingerprint.
                        request=tag(
                            {
                                "link": str(link_id),
                                "amount": amount,
                                "receipt": None if receipt is None else hashlib.sha256(receipt).hexdigest(),
                            },
                            paid_in,
                        ),
                        action=apply,
                    )
        except BaseException:
            if staged is not None:
                await self._files.discard(staged)
            raise
        if staged is not None and not recorded:
            # A repeat of a request already carried out: the first copy of the receipt is the one kept.
            await self._files.discard(staged)
        return body

    async def _require_sendable(
        self, session: TenantSession, customer_id: UUID, amount: int, currency: Currency = UZS, *, lock: bool
    ) -> tuple[CustomerRecord, int]:
        """The customer and what they owe in the currency, when they may send a notice for this amount."""
        now = self._now()
        if currency is USD and not await dollars_on(session, lock=lock):
            raise ValidationFailed({"currency": "must be UZS"})
        customer = await session.get_customer(customer_id, for_update=lock)
        link = await session.link_state(customer_id)
        if customer is None or link is None:
            # The link ended between being resolved and the account being locked: nothing is there for them.
            raise NotFound()
        # Their own stale notices are marked expired first, so that what is counted below is what waits.
        await session.expire_payment_notices(
            before=now - NOTICE_LIFETIME, now=now, customer_id=customer_id, files_delete_after=receipt_delete_after(now)
        )
        book = ledger.in_currency([row.entry for row in await session.entries_of(customer_id)], currency)
        balance = ledger.balance(book)
        refusal = may_send(
            amount=amount,
            balance=balance,
            open_notices=await session.count_open_notices(customer_id),
            currency=currency,
        )
        if refusal is NoticeRefusal.EXCEEDS_BALANCE:
            raise LedgerRefused("EXCEEDS_BALANCE")
        if refusal is not None:
            raise PaymentNoticeNotAllowed({"reason": refusal.value})
        return customer, balance

    async def _send_in(
        self,
        session: TenantSession,
        customer_id: UUID,
        amount: int,
        staged: StagedFile | None,
        currency: Currency = UZS,
    ) -> dict[str, Any]:
        now = self._now()
        # The customer row is locked, as in every write to the account, so the balance and the number of
        # open notices cannot change between being checked and the notice being stored.
        customer, balance = await self._require_sendable(session, customer_id, amount, currency, lock=True)
        file_id = (
            None
            if staged is None
            else await self._files.record_in(
                session, staged, purpose=FILE_PURPOSE, now=now, delete_after=receipt_bound(now)
            )
        )
        record = await session.add_payment_notice(
            notice_id=uuid4(), customer_id=customer_id, amount=amount, file_id=file_id, now=now, currency=currency
        )
        await session.record_customer_activity(action="payment_notice.sent", subject_id=customer_id)
        await session.record_measure(
            kind="payment_notice_sent", entry_ref=record.notice_id, amount=amount, promised=None, currency=currency
        )
        await _tell_staff(session, record, customer.display_name, balance)
        return notice_body(record, now)

    # --- the shop -------------------------------------------------------------------------------------

    async def list_open(self, user_id: UUID, shop_id: UUID) -> dict[str, Any]:
        now = self._now()
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, LIST_NOTICES)
            await require_viewable(session, actor, self._today())
            dollars = await dollars_on(session)
            # A notice in dollars waits, unseen, while the shop does not show dollars.
            rows = [
                (record, name)
                for record, name in await session.open_payment_notices(now - NOTICE_LIFETIME)
                if dollars or record.currency is UZS
            ]
            balances = {
                currency: await session.balances(
                    list({record.customer_id for record, _ in rows if record.currency is currency}), currency
                )
                for currency in {record.currency for record, _ in rows}
            }
            return {
                "items": [
                    {
                        **staff_notice_body(record, now),
                        "customer_id": str(record.customer_id),
                        "customer_name": name,
                        # What the customer owes in the notice's own currency.
                        "customer_balance": balances[record.currency].get(record.customer_id, 0),
                    }
                    for record, name in rows
                ]
            }

    async def accept(
        self, user_id: UUID, shop_id: UUID, notice_id: UUID, amount: int | None, request_key: str | None
    ) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, ACCEPT_NOTICE)
            key = idempotency.validate_key(request_key)
            if amount is not None and not (await dollars_on(session) and valid_amount(amount, USD)):
                # Checked before the notice is read, as it always was. In a shop with dollars an amount
                # that only a dollar notice may carry is checked once the notice's currency is known.
                clean_entry(EntryKind.PAYMENT.value, amount, None, None)
            await require_writable(session, self._today(), new_credit=False)

            async def apply() -> dict[str, Any]:
                return await accept_in(session, actor, notice_id, amount, self._now())

            return await idempotency.run_once(
                session,
                key=key,
                operation=ACCEPT_NOTICE.name,
                user_id=user_id,
                request={"notice": str(notice_id), "amount": amount},
                action=apply,
            )

    async def decline(
        self, user_id: UUID, shop_id: UUID, notice_id: UUID, reason: str, request_key: str | None
    ) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, DECLINE_NOTICE)
            key = idempotency.validate_key(request_key)
            text = clean_reason(reason)
            if text is None:
                raise ValidationFailed({"reason": "between 3 and 300 characters"})
            await require_writable(session, self._today(), new_credit=False)

            async def apply() -> dict[str, Any]:
                return await decline_in(session, actor, notice_id, text, self._now())

            return await idempotency.run_once(
                session,
                key=key,
                operation=DECLINE_NOTICE.name,
                user_id=user_id,
                request={"notice": str(notice_id), "reason": text},
                action=apply,
            )

    async def receipt(self, user_id: UUID, shop_id: UUID, notice_id: UUID) -> dict[str, Any]:
        """A link to a notice's receipt, valid five minutes, for a staff member of the shop (ADR-020).

        The file itself is served by the link, not here: nothing is read from the file store.
        """
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, READ_RECEIPT)
            await require_viewable(session, actor, self._today())
            record = await session.get_payment_notice(notice_id)
            if record is None or record.file_id is None:
                # (The second test only narrows the type: no identifier would find no file below.)
                raise NotFound()
            stored = await self._files.record_of(session, record.file_id)
        token, expires_at = self._files.link(shop_id, stored, self._now())
        return {"url": f"{FILE_LINK_PATH}/{token}", "expires_at": expires_at.isoformat()}

    # --- the worker -----------------------------------------------------------------------------------

    async def run_hourly(self) -> tuple[int, int]:
        """The hourly job: mark stale notices as expired and delete receipts whose retention has run out,
        in every shop that has either. Returns how many of each. Safe to repeat."""
        now = self._now()
        async with self._storage.platform() as platform:
            shops = await platform.shops_with_receipt_work(now - NOTICE_LIFETIME, now)
        expired = deleted = 0
        for shop_id in shops:
            expired += await self.expire_due(shop_id)
            try:
                while True:
                    removed = await self._files.purge_due_receipts(shop_id, now)
                    deleted += removed
                    if removed < PURGE_BATCH:
                        break
            except FileStoreUnavailable:
                # Nothing can be deleted without the store; the rows stay due and the next hour tries again.
                continue
        return expired, deleted

    async def expire_due(self, shop_id: UUID) -> int:
        """Mark the shop's notices that nobody decided in time as expired. For the hourly job."""
        now = self._now()
        async with self._storage.tenant(shop_id) as session:
            return await session.expire_payment_notices(
                before=now - NOTICE_LIFETIME, now=now, customer_id=None, files_delete_after=receipt_delete_after(now)
            )

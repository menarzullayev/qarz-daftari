"""A customer's secret read-only link (the expansion of 2026-10-09, decision 12).

A customer without Telegram had no way to see what they owe. A manager or the owner can now make a link
for one customer and hand it over, as text or as a QR code; whoever opens it sees that one account,
read-only, without signing in.

Three things follow from "whoever opens it":

- the token is 256 random bits and only its hash is stored, so the link is shown once, when it is made.
  A link that was lost is replaced, not shown again, and replacing it ends the old one at once;
- the page says less than staff see, and less than the connected customer sees in the bot: the first
  word of the name, the balance, the entries with their dates and goods. No note, no author, no
  identifier of anything, nothing that can be acted on;
- every answer to someone who does not hold a live link is the same "not found": unknown, ended,
  expired, the customer's data removed, the shop being deleted, the switch off.

Sellers are not offered any of this. A seller already sees a customer's balance while they work in the
shop; a link is a way to keep seeing it afterwards, and nothing a seller does at the counter needs one.

Everything is behind the platform switch `customer_links_on`. While it is off these operations answer
exactly as a route that does not exist, to everyone, before the caller is even asked who they are.
"""

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

from qarz.application import idempotency
from qarz.application.currencies import USD, UZS, dollars_on, tag
from qarz.application.customers import CustomerArchived, clean_phone, require_viewable, require_writable
from qarz.application.errors import NotFound
from qarz.application.ledger_service import HISTORY_PAGE
from qarz.application.operations import link_operation, operation
from qarz.application.ports import ShareRecord, Storage
from qarz.application.shops import require_member
from qarz.domain import customer_share as rules
from qarz.domain import languages, ledger
from qarz.domain.access import Capability
from qarz.domain.goods import format_qty
from qarz.domain.promise import tashkent_date

READ_SHARE = operation("customers.share.read", Capability.MANAGE)
CREATE_SHARE = operation("customers.share.create", Capability.MANAGE)
REVOKE_SHARE = operation("customers.share.revoke", Capability.MANAGE)
READ_SHARE_CONTACT = operation("shop.share_contact.read", Capability.MANAGE)
SET_SHARE_CONTACT = operation("shop.share_contact.update", Capability.ADMINISTER_SHOP)
VIEW_SHARED_ACCOUNT = link_operation("customer_share.view")


def _redact(body: dict[str, Any]) -> dict[str, Any]:
    return {**body, "token": None}


def _state(record: ShareRecord | None, now: datetime) -> dict[str, Any]:
    """What staff are told about a customer's link. Never the link itself: it is not kept."""
    if record is None:
        return {"exists": False, "expired": False, "created_at": None, "expires_at": None, "last_opened_at": None}
    return {
        "exists": True,
        "expired": record.expires_at <= now,
        "created_at": record.created_at.isoformat(),
        "expires_at": record.expires_at.isoformat(),
        "last_opened_at": None if record.last_opened_at is None else record.last_opened_at.isoformat(),
    }


class CustomerShareService:
    def __init__(self, storage: Storage, now: Callable[[], datetime] | None = None) -> None:
        self._storage = storage
        self._now = now or (lambda: datetime.now(UTC))

    def _today(self) -> date:
        return tashkent_date(self._now())

    async def switched_on(self) -> bool:
        async with self._storage.platform() as session:
            # Only the JSON value `true` turns it on: absent, null, "true" and 1 all mean off.
            return await session.platform_setting(rules.SWITCH) is True

    async def require_on(self) -> None:
        """Called for every route of this module before anything else: off is "no such route"."""
        if not await self.switched_on():
            raise NotFound()

    # --- staff ----------------------------------------------------------------------------------

    async def state(self, user_id: UUID, shop_id: UUID, customer_id: UUID) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, READ_SHARE)
            await require_viewable(session, actor, self._today())
            customer = await session.get_customer(customer_id, for_update=False)
            if customer is None or customer.status == "anonymized":
                raise NotFound()
            return _state(await session.live_share(customer_id), self._now())

    async def create(self, user_id: UUID, shop_id: UUID, customer_id: UUID, request_key: str | None) -> dict[str, Any]:
        """Make the customer's link, ending the one before it. The token is in this answer and nowhere else."""
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, CREATE_SHARE)
            key = idempotency.validate_key(request_key)
            await require_writable(session, self._today(), new_credit=False)

            async def apply() -> dict[str, Any]:
                # The customer row is locked, as in every write to an account: two people making a link
                # for the same customer at once end up with one live link, the later one.
                customer = await session.get_customer(customer_id, for_update=True)
                if customer is None or customer.status == "anonymized":
                    raise NotFound()
                if customer.status == "archived":
                    raise CustomerArchived()
                now = self._now()
                replaced = await session.end_shares(customer_id, now)
                token = rules.new_token()
                expires_at = now + rules.SHARE_LIFETIME
                await session.issue_share(
                    share_id=uuid4(),
                    token_hash=rules.token_hash(token),
                    customer_id=customer_id,
                    membership_id=actor.membership_id,
                    now=now,
                    expires_at=expires_at,
                )
                await session.record_activity(
                    membership_id=actor.membership_id,
                    action="customer.share_replaced" if replaced else "customer.share_created",
                    subject_type="customer",
                    subject_id=customer_id,
                )
                return {"token": token, "created_at": now.isoformat(), "expires_at": expires_at.isoformat()}

            return await idempotency.run_once(
                session,
                key=key,
                operation=CREATE_SHARE.name,
                user_id=user_id,
                request={"customer": str(customer_id)},
                action=apply,
                redact=_redact,
            )

    async def revoke(self, user_id: UUID, shop_id: UUID, customer_id: UUID, request_key: str | None) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, REVOKE_SHARE)
            key = idempotency.validate_key(request_key)
            # Ending a link takes something away from a stranger and adds nothing to the shop's records,
            # so it is allowed in a suspended shop too: an owner who stopped paying can still close it.

            async def apply() -> dict[str, Any]:
                customer = await session.get_customer(customer_id, for_update=True)
                if customer is None or customer.status == "anonymized":
                    raise NotFound()
                if not await session.end_shares(customer_id, self._now()):
                    raise NotFound()
                await session.record_activity(
                    membership_id=actor.membership_id,
                    action="customer.share_revoked",
                    subject_type="customer",
                    subject_id=customer_id,
                )
                return {"revoked": True}

            return await idempotency.run_once(
                session,
                key=key,
                operation=REVOKE_SHARE.name,
                user_id=user_id,
                request={"customer": str(customer_id)},
                action=apply,
            )

    async def contact(self, user_id: UUID, shop_id: UUID) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, READ_SHARE_CONTACT)
            await require_viewable(session, actor, self._today())
            return {"phone": await session.share_phone()}

    async def set_contact(
        self, user_id: UUID, shop_id: UUID, phone: str | None, request_key: str | None
    ) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, SET_SHARE_CONTACT)
            key = idempotency.validate_key(request_key)
            await require_writable(session, self._today(), new_credit=False)
            cleaned = clean_phone(phone)

            async def apply() -> dict[str, Any]:
                await session.set_share_phone(cleaned)
                await session.record_activity(
                    membership_id=actor.membership_id,
                    action="shop.share_contact_changed",
                    subject_type="shop",
                    subject_id=shop_id,
                )
                return {"phone": cleaned}

            return await idempotency.run_once(
                session,
                key=key,
                operation=SET_SHARE_CONTACT.name,
                user_id=user_id,
                request={"phone": cleaned},
                action=apply,
            )

    # --- whoever holds the link --------------------------------------------------------------------

    async def view(self, token: object) -> dict[str, Any]:
        """The account behind a live link. Everything else is NotFound, in the same words."""
        if not rules.is_token(token):
            raise NotFound()
        assert isinstance(token, str)
        presented = rules.token_hash(token)
        now = self._now()
        async with self._storage.platform() as platform:
            found = await platform.customer_share_lookup(presented, now)
        # The database found the row by the hash; the hashes are compared once more here in constant
        # time, so nothing about a stored hash can be learned from how long the answer took.
        if found is None or not rules.same_hash(found[3], presented):
            raise NotFound()
        share_id, shop_id, customer_id, _ = found
        async with self._storage.tenant(shop_id) as session:
            customer = await session.get_customer(customer_id, for_update=False)
            settings = await session.shop_settings()
            if customer is None or settings is None or customer.status == "anonymized":
                raise NotFound()
            record = await session.live_share(customer_id)
            if record is None or record.share_id != share_id or record.expires_at <= now:
                # Ended or replaced between the lookup and this transaction.
                raise NotFound()
            # Each currency is a book of its own (qarz.domain.money): the so'm figures come from the so'm
            # entries alone, and a shop that works in dollars gets the dollar figures beside them. A shop
            # that does not shows its so'm book and nothing else, as before dollars existed.
            dollars = await dollars_on(session)
            account = [row for row in await session.entries_of(customer_id) if dollars or row.entry.currency is UZS]
            entries = ledger.in_currency([row.entry for row in account], UZS)
            in_dollars = ledger.in_currency([row.entry for row in account], USD)
            today = self._today()
            status = ledger.overdue(entries, today)
            usd_status = ledger.overdue(in_dollars, today)
            reversed_ids = {row.entry.reverses_id for row in account if row.entry.reverses_id is not None}
            shown = sorted(account, key=lambda row: row.entry.seq, reverse=True)[:HISTORY_PAGE]
            lines = await session.goods_lines_of([row.entry.id for row in shown])
            language = await session.customer_language(customer_id) or settings.lang
            # At most one line of the activity log a day for each link, however often the page is
            # opened: the log is the shop's, and a stranger must not be able to fill it.
            if await session.share_opened(share_id, now, today):
                await session.record_customer_activity(action="customer.share_opened", subject_id=customer_id)
            return {
                "shop_name": settings.name,
                "shop_phone": await session.share_phone(),
                "first_name": rules.first_name(customer.display_name),
                "lang": language if languages.is_language(language) else languages.DEFAULT,
                "balance": ledger.balance(entries),
                "overdue": {"amount": status.overdue_amount, "due_today": status.due_today_amount},
                # Whole cents, beside the so'm figures and never added to them.
                **(
                    {
                        "usd": {
                            "balance": ledger.balance(in_dollars),
                            "overdue": {"amount": usd_status.overdue_amount, "due_today": usd_status.due_today_amount},
                        }
                    }
                    if dollars
                    else {}
                ),
                "expires_at": record.expires_at.isoformat(),
                # No identifier, no note and no author: the page can only be read.
                "entries": [
                    tag(
                        {
                            "kind": row.entry.kind.value,
                            "amount": row.entry.amount,
                            "created_at": row.entry.created_at.isoformat(),
                            "promised_date": None
                            if row.entry.promised_date is None
                            else row.entry.promised_date.isoformat(),
                            "reversed": row.entry.id in reversed_ids,
                            "lines": [
                                {
                                    "name": line.name,
                                    "qty": format_qty(line.qty),
                                    "unit": line.unit,
                                    "unit_price": line.unit_price,
                                    "line_total": line.line_total,
                                }
                                for line in lines.get(row.entry.id, [])
                            ],
                        },
                        row.entry.currency,
                    )
                    for row in shown
                ],
                "entries_total": len(account),
            }

"""The network between shops: links, invitations and what every part of it shares (module J).

Two shops connect as buyer and supplier. Everything here acts for ONE shop, the caller's, in that shop's
own tenant transaction: it reads the shop's own copy of what the two share, and asks the database to
change both copies through the functions of migration 0045, which verify the link (see the trust model
in the technical specification). Nothing in this module names another shop to a client: a partner is
its name and its contact phone, and a link is known by its own identifier.

Orders and delivery notes are in `network_orders`, payments in `network_payments`.

What a shop may still do when it is not in good standing (BR-94): a suspended shop does nothing here but
let its owner look (BR-30); a limited shop reads, and answers what was already sent to it (confirm or
reject a note, answer a payment, cancel, end a link), but starts nothing: no invitation, no link, no
order, no acceptance and no delivery note, since a delivery is a new credit sale (BR-29). A shop on the
free plan does everything; the partner's row among its customers counts as one of them (BR-33).
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

from qarz.application import idempotency
from qarz.application.authorization import holders, may
from qarz.application.chat_texts import money, say
from qarz.application.customers import create_customer_in, require_viewable, require_writable
from qarz.application.errors import AppError, NotFound, ValidationFailed
from qarz.application.network_ports import LinkRecord, NetworkRefused
from qarz.application.operations import operation
from qarz.application.ports import Membership, Storage, TenantSession
from qarz.application.shops import require_member
from qarz.domain import network, permissions, stock
from qarz.domain.access import Capability
from qarz.domain.money import Currency
from qarz.domain.names import normalize_name
from qarz.domain.promise import tashkent_date

OVERVIEW = operation("network.overview", Capability.MANAGE)
READ_LINK = operation("network.links.read", Capability.MANAGE)
CREATE_INVITE = operation("network.invites.create", Capability.ADMINISTER_SHOP)
REVOKE_INVITE = operation("network.invites.revoke", Capability.ADMINISTER_SHOP)
REQUEST_LINK = operation("network.links.request", Capability.ADMINISTER_SHOP)
ACCEPT_LINK = operation("network.links.accept", Capability.ADMINISTER_SHOP)
DECLINE_LINK = operation("network.links.decline", Capability.ADMINISTER_SHOP)
END_LINK = operation("network.links.end", Capability.ADMINISTER_SHOP)
ATTACH_LINK = operation("network.links.attach", Capability.ADMINISTER_SHOP)


class NetworkState(AppError):
    """The step does not fit the state the thing is in, or is the other side's to take."""

    code = "NETWORK_STATE"


class InviteInvalid(AppError):
    """The code does not work: wrong, used, withdrawn, too old, one's own, or for the other role. The
    answer is the same for each, so a code cannot be probed."""

    code = "NETWORK_INVITE_INVALID"


class LinkExists(AppError):
    code = "NETWORK_LINK_EXISTS"


class TooManyInvites(AppError):
    code = "NETWORK_TOO_MANY_INVITES"


class PartnerUnavailable(AppError):
    code = "NETWORK_PARTNER_UNAVAILABLE"


class CounterpartInvalid(AppError):
    code = "NETWORK_COUNTERPART_INVALID"


class CurrencyUnavailable(AppError):
    """One of the two shops does not work in the currency."""

    code = "NETWORK_CURRENCY"


class BooksMismatch(AppError):
    """What was written to the books is not what the network record says: nothing is marked."""

    code = "NETWORK_BOOKS_MISMATCH"


class PartnerRefused(AppError):
    """The partner's books could not take the entry. Why is the partner's own business and is not said."""

    code = "NETWORK_PARTNER_REFUSED"


_REFUSALS: dict[str, type[AppError]] = {
    "NETWORK_STATE": NetworkState,
    "NETWORK_INVITE_INVALID": InviteInvalid,
    "NETWORK_LINK_EXISTS": LinkExists,
    "NETWORK_PARTNER_UNAVAILABLE": PartnerUnavailable,
    "NETWORK_COUNTERPART_INVALID": CounterpartInvalid,
    "NETWORK_CURRENCY": CurrencyUnavailable,
    "NETWORK_BOOKS_MISMATCH": BooksMismatch,
}


@contextmanager
def refusals() -> Iterator[None]:
    """Turn a refusal of the database's functions into the application's error. A shop that is no party
    to the thing is answered exactly as for a thing that does not exist."""
    try:
        yield
    except NetworkRefused as refused:
        if refused.code == "NETWORK_INVALID":
            raise ValidationFailed({"lines": "not what the order allows"}) from refused
        raise _REFUSALS.get(refused.code, NotFound)() from refused


async def switched_on(session: TenantSession) -> bool:
    """The network needs its own switch and the stock's: a delivery is a stock receipt."""
    return (
        await session.platform_setting(network.SWITCH) is True and await session.platform_setting(stock.SWITCH) is True
    )


async def require_on(session: TenantSession) -> None:
    """Off is "no such route", to everyone, before the caller is asked who they are."""
    if not await switched_on(session):
        raise NotFound()


def iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def link_body(link: LinkRecord) -> dict[str, Any]:
    """A link as its own side sees it. The partner is a name and a contact phone, nothing else; once the
    partner is erased it is nobody (`removed`)."""
    counterpart: dict[str, Any] | None = None
    if link.supplier_id is not None:
        counterpart = {"kind": "supplier", "id": str(link.supplier_id)}
    elif link.customer_id is not None:
        counterpart = {"kind": "customer", "id": str(link.customer_id)}
    ended_by = None
    if link.state == network.ENDED:
        ended_by = "partner" if link.ended_by_peer else "own"
    return {
        "id": str(link.link_id),
        "role": link.role,
        "state": link.state,
        "invited": link.invited,
        "partner": {"name": link.peer_name, "phone": link.peer_phone, "removed": link.peer_removed},
        "counterpart": counterpart,
        "requested_at": link.requested_at.isoformat(),
        "decided_at": iso(link.decided_at),
        "ended_at": iso(link.ended_at),
        "ended_by": ended_by,
    }


async def events_body(session: TenantSession, subject_id: UUID) -> list[dict[str, Any]]:
    """The history of one thing as this side may know it: a step of the partner's says `partner` and
    never who; a step of this shop's names its own member."""
    return [
        {
            "kind": event.kind,
            "by": "partner" if event.by_peer else "own",
            "member_id": None if event.member_id is None else str(event.member_id),
            "at": event.at.isoformat(),
            "detail": event.detail,
        }
        for event in await session.network_events(subject_id)
    ]


async def locked_link(session: TenantSession, link: LinkRecord, *, active: bool = True) -> None:
    """Take the link's locks, before anything of the shop's own books is locked: every step of one link
    takes its turn here, in the same order (lower shop first), so no two can wait for each other."""
    with refusals():
        state = await session.network_lock(link.peer_shop_id, link.link_id)
    if active and state != network.ACTIVE:
        raise NetworkState()


async def own_link(session: TenantSession, link_id: UUID) -> LinkRecord:
    link = await session.network_link(link_id)
    if link is None:
        raise NotFound()
    return link


async def ensure_counterpart_in(session: TenantSession, actor: Membership, link: LinkRecord, *, today: date) -> UUID:
    """The row of this shop's own books that the partner is: its supplier (for the buyer) or its customer
    (for the supplier). If the shop has not chosen one, one is made from the partner's name and phone.

    A customer made here is an ordinary customer of the shop: it counts toward the free plan (BR-34) and
    is refused when the plan is full.
    """
    if link.counterpart_id is not None:
        return link.counterpart_id
    name = (link.peer_name or "Hamkor")[:80]
    made: UUID | None = None
    if link.role == network.BUYER:
        # A supplier's name is unique in a shop: if the partner's is taken, say which shop it is.
        for candidate in (name, f"{name[:68]} (hamkor)", f"{name[:60]} (hamkor {str(link.link_id)[:6]})"):
            supplier = await session.insert_supplier(
                supplier_id=uuid4(),
                name=candidate,
                name_norm=normalize_name(candidate),
                phone=link.peer_phone,
                note=None,
            )
            if supplier is not None:
                made = supplier.supplier_id
                await session.record_activity(
                    membership_id=actor.membership_id,
                    action="supplier.created",
                    subject_type="supplier",
                    subject_id=made,
                )
                break
    else:
        made = (await create_customer_in(session, actor, name, link.peer_phone, today)).customer_id
    if made is None:
        raise CounterpartInvalid()
    with refusals():
        await session.network_attach(link.link_id, made, made=True, member_id=actor.membership_id)
    return made


async def tell_partner(
    session: TenantSession,
    link: LinkRecord,
    permission: str,
    key: str,
    subject_id: UUID,
    *,
    amount: tuple[int, str] | None = None,
    **values: Any,
) -> None:
    """Tell the partner's members who hold `permission`, in Telegram, through the outbox, once each.

    Who they are is asked of the database for this link only; the message says this shop's name and what
    both sides already hold (a number, a total, a reason), in the reader's language.
    """
    with refusals():
        contacts = await session.network_recipients(link.peer_shop_id, link.link_id)
    settings = await session.shop_settings()
    shop = "" if settings is None else settings.name
    for tg_id, lang in holders(contacts, permission):
        shown = dict(values)
        if amount is not None:
            shown["amount"] = money(lang, amount[0], Currency(amount[1]))
        await session.enqueue(
            recipient=str(tg_id),
            payload={"text": say(lang, key, shop=shop, **shown)},
            dedupe_key=f"net:{key}:{subject_id}:{tg_id}",
        )


async def reconciliation_in(session: TenantSession, actor: Membership, link: LinkRecord) -> list[dict[str, Any]]:
    """Per currency: what this shop's own books say about the partner, beside what both sides have
    confirmed through the network, and what still waits. The two are never made equal here: a difference
    is shown as a difference. The partner's books are never read.

    `own_balance` is, for the buyer, what it owes the supplier; for the supplier, what the customer owes.
    It is given only to a member who may see that account.
    """
    agreed = {row.currency: row for row in await session.network_agreed(link.link_id)}
    own: dict[str, int] | None = None
    if link.supplier_id is not None and may(actor, permissions.SUPPLIERS_VIEW):
        own = (await session.supplier_balances([link.supplier_id])).get(link.supplier_id, {})
    elif link.customer_id is not None and may(actor, permissions.LEDGER_VIEW):
        own = {}
        for currency in Currency:
            balance = (await session.balances([link.customer_id], currency)).get(link.customer_id, 0)
            if balance:
                own[currency.value] = balance
    notes = await session.network_notes(role=None, status=network.ISSUED, link_id=link.link_id, before=None, limit=100)
    payments = await session.network_payments(status=network.AWAITING, link_id=link.link_id, before=None, limit=100)
    declined = await session.network_payments(status=network.DECLINED, link_id=link.link_id, before=None, limit=100)
    currencies = sorted(
        set(agreed) | set(own or {}) | {n.currency for n in notes} | {p.currency for p in (*payments, *declined)}
    )
    rows: list[dict[str, Any]] = []
    for code in currencies:
        both = agreed.get(code)
        agreed_balance = 0 if both is None else both.delivered - both.paid_on_delivery - both.paid
        row: dict[str, Any] = {
            "currency": code,
            "agreed": {
                "delivered": 0 if both is None else both.delivered,
                "paid": 0 if both is None else both.paid_on_delivery + both.paid,
                "balance": agreed_balance,
            },
            "awaiting": {
                # Delivered and not yet confirmed: in nobody's books.
                "notes": sum(n.total - n.paid for n in notes if n.currency == code),
                # Recorded by this shop and in its books; the partner has not confirmed.
                "payments_own": sum(p.amount for p in payments if p.currency == code and p.recorded_by_own),
                # Recorded by the partner; not in this shop's books until it confirms.
                "payments_partner": sum(p.amount for p in payments if p.currency == code and not p.recorded_by_own),
                # Declined by the partner and still standing in this shop's books.
                "payments_declined": sum(
                    p.amount for p in declined if p.currency == code and p.recorded_by_own and p.own_entry_stands
                ),
            },
        }
        if own is not None:
            row["own_balance"] = own.get(code, 0)
            row["difference"] = own.get(code, 0) - agreed_balance
        rows.append(row)
    return rows


class NetworkService:
    def __init__(self, storage: Storage, now: Callable[[], datetime] | None = None) -> None:
        self._storage = storage
        self._now = now or (lambda: datetime.now(UTC))

    def _today(self) -> date:
        return tashkent_date(self._now())

    async def switched_on(self) -> bool:
        async with self._storage.platform() as session:
            return (
                await session.platform_setting(network.SWITCH) is True
                and await session.platform_setting(stock.SWITCH) is True
            )

    async def require_on(self) -> None:
        """Called for every route of the network before anything else."""
        if not await self.switched_on():
            raise NotFound()

    async def overview(self, user_id: UUID, shop_id: UUID) -> dict[str, Any]:
        """The shop's links, what waits for its own step, and (for who manages links) its open invitations."""
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, OVERVIEW)
            await require_viewable(session, actor, self._today())
            body: dict[str, Any] = {
                "links": [link_body(link) for link in await session.network_links()],
                "waiting": await session.network_waiting(),
            }
            if may(actor, permissions.NETWORK_MANAGE):
                body["invites"] = [
                    {"id": str(invite.invite_id), "as": invite.as_role, "expires_at": invite.expires_at.isoformat()}
                    for invite in await session.network_invites(self._now())
                ]
            return body

    async def link(self, user_id: UUID, shop_id: UUID, link_id: UUID) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, READ_LINK)
            await require_viewable(session, actor, self._today())
            link = await own_link(session, link_id)
            return {
                "link": link_body(link),
                "reconciliation": await reconciliation_in(session, actor, link),
                "events": await events_body(session, link_id),
            }

    async def create_invite(
        self, user_id: UUID, shop_id: UUID, *, as_role: str, request_key: str | None
    ) -> dict[str, Any]:
        """Make a code to hand to another shop. `as_role` is what THIS shop will be in the link. The code
        is shown once: only its hash is stored, and a repeated request does not show it again."""
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, CREATE_INVITE)
            key = idempotency.validate_key(request_key)
            if as_role not in network.ROLES:
                raise ValidationFailed({"as": "must be buyer or supplier"})
            await require_writable(session, self._today(), new_credit=True)

            async def apply() -> dict[str, Any]:
                now = self._now()
                if len(await session.network_invites(now)) >= network.MAX_OPEN_INVITES:
                    raise TooManyInvites({"limit": str(network.MAX_OPEN_INVITES)})
                code, invite_id = network.new_code(), uuid4()
                expires = now + network.INVITE_LIFETIME
                await session.insert_network_invite(
                    invite_id=invite_id,
                    code_hash=network.code_hash(code),
                    as_role=as_role,
                    created_by=actor.membership_id,
                    created_at=now,
                    expires_at=expires,
                )
                await session.record_activity(
                    membership_id=actor.membership_id,
                    action="network.invite_created",
                    subject_type="network_invite",
                    subject_id=invite_id,
                    detail={"as": as_role},
                )
                return {"id": str(invite_id), "as": as_role, "expires_at": expires.isoformat(), "code": code}

            return await idempotency.run_once(
                session,
                key=key,
                operation=CREATE_INVITE.name,
                user_id=user_id,
                request={"as": as_role},
                action=apply,
                redact=lambda body: {**body, "code": None},
            )

    async def revoke_invite(
        self, user_id: UUID, shop_id: UUID, invite_id: UUID, *, request_key: str | None
    ) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, REVOKE_INVITE)
            key = idempotency.validate_key(request_key)
            await require_writable(session, self._today(), new_credit=False)

            async def apply() -> dict[str, Any]:
                if not await session.revoke_network_invite(invite_id, self._now()):
                    raise NotFound()
                await session.record_activity(
                    membership_id=actor.membership_id,
                    action="network.invite_revoked",
                    subject_type="network_invite",
                    subject_id=invite_id,
                )
                return {"id": str(invite_id), "revoked": True}

            return await idempotency.run_once(
                session,
                key=key,
                operation=REVOKE_INVITE.name,
                user_id=user_id,
                request={"invite": str(invite_id)},
                action=apply,
            )

    async def request_link(
        self, user_id: UUID, shop_id: UUID, *, code: str, as_role: str, request_key: str | None
    ) -> dict[str, Any]:
        """Present a code another shop handed over. `as_role` is what THIS shop wants to be."""
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, REQUEST_LINK)
            key = idempotency.validate_key(request_key)
            if as_role not in network.ROLES:
                raise ValidationFailed({"as": "must be buyer or supplier"})
            await require_writable(session, self._today(), new_credit=True)
            digest = network.code_hash(code)

            async def apply() -> dict[str, Any]:
                if not network.plausible_code(code):
                    raise InviteInvalid()
                with refusals():
                    link_id = await session.network_redeem(
                        code_hash=digest, role=as_role, member_id=actor.membership_id, now=self._now()
                    )
                link = await own_link(session, link_id)
                await tell_partner(session, link, permissions.NETWORK_MANAGE, "net_link_requested", link_id)
                return {"link": link_body(link)}

            return await idempotency.run_once(
                session,
                key=key,
                operation=REQUEST_LINK.name,
                user_id=user_id,
                # The code itself is not kept with the key: its hash tells a repeat from another request.
                request={"code": digest.hex(), "as": as_role},
                action=apply,
            )

    async def decide(
        self,
        user_id: UUID,
        shop_id: UUID,
        link_id: UUID,
        *,
        accept: bool,
        counterpart_id: UUID | None = None,
        request_key: str | None,
    ) -> dict[str, Any]:
        """Accept or decline a request. Accepting may name the row of the shop's own books the partner
        is (one of its suppliers or customers); without one, a row is made."""
        op = ACCEPT_LINK if accept else DECLINE_LINK
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, op)
            key = idempotency.validate_key(request_key)
            await require_writable(session, self._today(), new_credit=accept)

            async def apply() -> dict[str, Any]:
                link = await own_link(session, link_id)
                if not network.may_decide_link(link.state, invited=link.invited):
                    raise NetworkState()
                await locked_link(session, link, active=False)
                with refusals():
                    await session.network_decide_link(
                        link.peer_shop_id, link_id, accept=accept, member_id=actor.membership_id, now=self._now()
                    )
                link = await own_link(session, link_id)
                if accept:
                    await self._attach(session, actor, link, counterpart_id)
                    link = await own_link(session, link_id)
                await tell_partner(
                    session,
                    link,
                    permissions.NETWORK_MANAGE,
                    "net_link_accepted" if accept else "net_link_declined",
                    link_id,
                )
                return {"link": link_body(link)}

            return await idempotency.run_once(
                session,
                key=key,
                operation=op.name,
                user_id=user_id,
                request={"link": str(link_id), "counterpart": None if counterpart_id is None else str(counterpart_id)},
                action=apply,
            )

    async def attach(
        self, user_id: UUID, shop_id: UUID, link_id: UUID, *, counterpart_id: UUID, request_key: str | None
    ) -> dict[str, Any]:
        """Say which existing supplier (the buyer) or customer (the supplier) the partner is, while the
        link has none yet."""
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, ATTACH_LINK)
            key = idempotency.validate_key(request_key)
            await require_writable(session, self._today(), new_credit=False)

            async def apply() -> dict[str, Any]:
                link = await own_link(session, link_id)
                if link.state != network.ACTIVE or link.counterpart_id is not None:
                    raise NetworkState()
                await locked_link(session, link)
                await self._attach(session, actor, link, counterpart_id)
                return {"link": link_body(await own_link(session, link_id))}

            return await idempotency.run_once(
                session,
                key=key,
                operation=ATTACH_LINK.name,
                user_id=user_id,
                request={"link": str(link_id), "counterpart": str(counterpart_id)},
                action=apply,
            )

    async def _attach(
        self, session: TenantSession, actor: Membership, link: LinkRecord, counterpart_id: UUID | None
    ) -> None:
        if counterpart_id is None:
            await ensure_counterpart_in(session, actor, link, today=self._today())
            return
        if link.role == network.BUYER:
            found = await session.get_supplier(counterpart_id, for_update=False) is not None
        else:
            found = await session.get_customer(counterpart_id, for_update=False) is not None
        if not found:
            raise ValidationFailed({"counterpart_id": "not a supplier or customer of this shop"})
        with refusals():
            await session.network_attach(link.link_id, counterpart_id, made=False, member_id=actor.membership_id)

    async def end(self, user_id: UUID, shop_id: UUID, link_id: UUID, *, request_key: str | None) -> dict[str, Any]:
        """End a link, or take back a request. What still waited is closed; what was posted and the
        history stay on each side."""
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, END_LINK)
            key = idempotency.validate_key(request_key)
            await require_writable(session, self._today(), new_credit=False)

            async def apply() -> dict[str, Any]:
                link = await own_link(session, link_id)
                if not network.may_end_link(link.state):
                    raise NetworkState()
                with refusals():
                    await session.network_end_link(
                        link.peer_shop_id, link_id, member_id=actor.membership_id, now=self._now()
                    )
                await tell_partner(session, link, permissions.NETWORK_MANAGE, "net_link_ended", link_id)
                return {"link": link_body(await own_link(session, link_id))}

            return await idempotency.run_once(
                session,
                key=key,
                operation=END_LINK.name,
                user_id=user_id,
                request={"link": str(link_id)},
                action=apply,
            )

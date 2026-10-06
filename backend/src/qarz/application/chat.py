"""The staff chat (technical specification, "Chat contract"): fast entry, date choices, shop switching.

Everything here runs inside the platform transaction that claimed the Telegram update. Replies are queued
in that transaction and sent by the worker. A write to a shop happens in a tenant transaction of its own,
made idempotent by a key derived from the update, so a redelivered update cannot write twice even if the
platform transaction failed after the shop's transaction had committed.

Every action is authorized again when a button is pressed: callback data is only an identifier.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid5

from qarz.application import idempotency
from qarz.application.chat_texts import CONSENT_VERSION, LANGUAGE_NAMES, day, money, say
from qarz.application.customer_account import CustomerAccountService
from qarz.application.customers import CREATE_CUSTOMER, create_customer_in, require_writable
from qarz.application.date_requests import (
    ACCEPT_ACTION,
    ACCEPT_DATE_REQUEST,
    DECLINE_ACTION,
    DECLINE_DATE_REQUEST,
    DateRequestService,
)
from qarz.application.date_requests import accept_in as accept_date_request_in
from qarz.application.date_requests import decline_in as decline_date_request_in
from qarz.application.disputes import DECLINE_DISPUTE, DisputeService, decline_in
from qarz.application.errors import AlreadyMember, AppError, ForbiddenRole, NotFound, ValidationFailed
from qarz.application.ledger_service import (
    CHOOSE_PROMISE,
    RECORD_ENTRY,
    REVERSE_ENTRY,
    append_entry_in,
    choose_promise_in,
    clean_entry,
    reverse_entry_in,
)
from qarz.application.links import COUNTER_PREFIX, PERSONAL_PREFIX
from qarz.application.ports import Membership, MyShop, PlatformSession, Storage, TenantSession
from qarz.application.shops import ShopService, require_member
from qarz.application.staff import StaffService, token_hash
from qarz.application.subscription import SubscriptionService
from qarz.domain.access import Capability, allows
from qarz.domain.chat_entry import ParsedEntry, ParseError, ParseErrorCode, parse_entry
from qarz.domain.disputes import clean_reason
from qarz.domain.ledger import EntryKind
from qarz.domain.promise import QuickChoice, parse_day_month, quick_choice_date, tashkent_date

CALLBACK_VERSION = "v2"
PENDING_LIFETIME = timedelta(minutes=15)
MAX_CANDIDATES = 6
STAFF_INVITATION_PREFIX = "s_"
_NAMESPACE = UUID("3d0c2a51-6c1e-5b0e-8a3e-9f5b6a7c8d90")

_QUICK = {
    "t": QuickChoice.TOMORROW,
    "w": QuickChoice.END_OF_WEEK,
    "2": QuickChoice.IN_TWO_WEEKS,
    "m": QuickChoice.IN_A_MONTH,
}
_PARSE_TEXTS = {
    ParseErrorCode.AMOUNT_NOT_WHOLE: "parse_amount_not_whole",
    ParseErrorCode.AMBIGUOUS: "parse_ambiguous",
    ParseErrorCode.TOO_LONG: "parse_too_long",
    ParseErrorCode.NOTE_TOO_LONG: "parse_too_long",
    ParseErrorCode.AMOUNT_TOO_SMALL: "amount_range",
    ParseErrorCode.AMOUNT_TOO_LARGE: "amount_range",
}
_LATER_COMMANDS = frozenset({"/ilova", "/toladim"})
MOVE_DATE_ACTION = "dmv"
# Why a customer's request to move a date was refused, in words of its own where there are any.
_DATE_REQUEST_TEXTS = {
    "not_later": "date_not_later",
    "fully_paid": "date_fully_paid",
    "reversed": "date_fully_paid",
    "declined_recently": "date_declined_recently",
    "not_open": "date_request_closed",
}

Keyboard = list[list[tuple[str, str]]]


def callback(action: str, *parts: object) -> str:
    data = ":".join([CALLBACK_VERSION, action, *(str(part) for part in parts)])
    if len(data.encode()) > 64:
        raise ValueError("callback data is limited to 64 bytes by Telegram")
    return data


def _markup(keyboard: Keyboard | None) -> dict[str, Any]:
    rows = [[{"text": label, "callback_data": data} for label, data in row] for row in keyboard or []]
    return {"inline_keyboard": rows}


def _uuid(hex_text: str) -> UUID | None:
    try:
        return UUID(hex=hex_text) if len(hex_text) == 32 else None
    except ValueError:
        return None


@dataclass(frozen=True)
class Incoming:
    update_id: int
    chat_id: int
    user_id: UUID
    lang: str
    message_id: int | None = None  # the message a pressed button belongs to
    profile_name: str | None = None  # the name in the person's Telegram profile
    received: float | None = None  # time.perf_counter() when the update arrived; for the handling time

    @property
    def key(self) -> str:
        return f"tg-update-{self.update_id}"


class Replies:
    """Queues what the bot says in answer to one update."""

    def __init__(self, session: PlatformSession, incoming: Incoming) -> None:
        self._session = session
        self._incoming = incoming
        self._count = 0

    async def _queue(self, payload: dict[str, Any]) -> None:
        suffix = "" if self._count == 0 else f":{self._count + 1}"
        self._count += 1
        await self._session.enqueue(
            channel="telegram",
            recipient=str(self._incoming.chat_id),
            payload=payload,
            dedupe_key=f"update:{self._incoming.update_id}:reply{suffix}",
        )

    async def send(self, text: str, keyboard: Keyboard | None = None) -> None:
        payload: dict[str, Any] = {"text": text}
        if keyboard:
            payload["reply_markup"] = _markup(keyboard)
        await self._queue(payload)

    async def show(self, text: str, keyboard: Keyboard | None = None) -> None:
        """Replace the message whose button was pressed; send a new one when there is none."""
        if self._incoming.message_id is None:
            await self.send(text, keyboard)
            return
        await self._queue(
            {
                "method": "editMessageText",
                "message_id": self._incoming.message_id,
                "text": text,
                "reply_markup": _markup(keyboard),
            }
        )

    async def buttons(self, keyboard: Keyboard | None) -> None:
        if self._incoming.message_id is not None:
            await self._queue(
                {
                    "method": "editMessageReplyMarkup",
                    "message_id": self._incoming.message_id,
                    "reply_markup": _markup(keyboard),
                }
            )


class ChatService:
    def __init__(
        self,
        storage: Storage,
        shops: ShopService,
        staff: StaffService,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._storage = storage
        self._shops = shops
        self._staff = staff
        self._accounts_service = CustomerAccountService(storage, now)
        self._disputes = DisputeService(storage, now)
        self._subscriptions = SubscriptionService(storage, now)
        self._date_requests = DateRequestService(storage, now)
        self._now = now or (lambda: datetime.now(UTC))

    def _today(self) -> date:
        return tashkent_date(self._now())

    # --- what a person may type ----------------------------------------------------------------------

    async def handle_text(self, session: PlatformSession, incoming: Incoming, text: str) -> None:
        replies = Replies(session, incoming)
        text = text.strip()
        if text.startswith("/"):
            await self._command(session, incoming, replies, text)
            return

        if await session.current_pending(incoming.user_id, "shop_name", self._now()) is not None:
            await self._create_shop(session, incoming, replies, text)
            return

        # A reason that was asked for is taken before anything else is read into the message.
        asked = await session.current_pending(incoming.user_id, "dispute", self._now())
        if asked is not None:
            await self._dispute_reason(session, incoming, replies, asked[1], text)
            return
        asked = await session.current_pending(incoming.user_id, "decline", self._now())
        if asked is not None:
            await self._decline_reason(session, incoming, replies, asked[1], text)
            return
        asked = await session.current_pending(incoming.user_id, "date_request", self._now())
        if asked is not None:
            await self._requested_date(session, incoming, replies, asked[1], text)
            return

        shops = await session.my_memberships(incoming.user_id)
        if not shops:
            await self._home_without_shop(session, incoming, replies)
            return

        awaiting_date = await session.current_pending(incoming.user_id, "promise_date", self._now())
        if awaiting_date is not None:
            chosen = parse_day_month(text, self._today())
            if chosen is not None:
                await session.drop_pending(incoming.user_id, "promise_date")
                entry_id = _uuid(str(awaiting_date[1].get("entry", "")))
                if entry_id is not None:
                    await self._choose_promise(session, incoming, replies, shops, entry_id, chosen)
                return

        shop = await self._active_shop(session, incoming.user_id, shops)
        if shop is None:
            await replies.send(say(incoming.lang, "choose_shop"), self._shop_buttons(shops))
            return

        parsed = parse_entry(text)
        if isinstance(parsed, ParseError):
            await replies.send(say(incoming.lang, _PARSE_TEXTS.get(parsed.code, "parse_hint")))
            return
        await self._entry(session, incoming, replies, shop, parsed)

    async def _command(self, session: PlatformSession, incoming: Incoming, replies: Replies, text: str) -> None:
        head, _, argument = text.partition(" ")
        command = head.split("@", 1)[0].lower()
        lang = incoming.lang
        if command == "/start":
            argument = argument.strip()
            if argument.startswith(STAFF_INVITATION_PREFIX):
                await self._join(session, incoming, replies, argument[len(STAFF_INVITATION_PREFIX) :])
                return
            if argument.startswith((PERSONAL_PREFIX, COUNTER_PREFIX)):
                await self._ask_consent(session, incoming, replies, argument[len(PERSONAL_PREFIX) :])
                return
            # Someone who had blocked the bot and comes back can be notified again.
            await session.mark_recipient_reachable(incoming.user_id)
            shops = await session.my_memberships(incoming.user_id)
            shop = await self._active_shop(session, incoming.user_id, shops)
            if not shops:
                await self._home_without_shop(session, incoming, replies)
            elif shop is None:
                await replies.send(say(lang, "choose_shop"), self._shop_buttons(shops))
            else:
                await replies.send(say(lang, "welcome_staff", shop=shop.name))
        elif command == "/til":
            await replies.send(
                say(lang, "lang_prompt"), [[(name, callback("lang", code)) for code, name in LANGUAGE_NAMES.items()]]
            )
        elif command == "/dokon":
            shops = await session.my_memberships(incoming.user_id)
            if shops:
                await replies.send(say(lang, "choose_shop"), self._shop_buttons(shops))
            else:
                await replies.send(say(lang, "no_shops"), self._open_shop(lang))
        elif command == "/qarzim":
            await session.mark_recipient_reachable(incoming.user_id)
            await self._accounts(session, incoming, replies)
        elif command == "/uzish":
            accounts = await session.my_accounts(incoming.user_id)
            if not accounts:
                await replies.send(say(lang, "no_accounts"))
            else:
                await replies.send(
                    say(lang, "unlink_choose"),
                    [
                        [(say(lang, "unlink_button", shop=account.shop_name), callback("unl", account.shop_id.hex))]
                        for account in accounts[:20]
                    ],
                )
        elif command == "/ochirish":
            accounts = await session.my_accounts(incoming.user_id)
            if not accounts:
                await replies.send(say(lang, "no_accounts"))
            else:
                await replies.send(
                    say(lang, "removal_choose"),
                    [
                        [(say(lang, "removal_button", shop=account.shop_name), callback("del", account.link_id.hex))]
                        for account in accounts[:20]
                    ],
                )
        elif command == "/obuna":
            shops = await session.my_memberships(incoming.user_id)
            shop = await self._active_shop(session, incoming.user_id, shops)
            if not shops:
                await replies.send(say(lang, "no_shops"), self._open_shop(lang))
            elif shop is None:
                await replies.send(say(lang, "choose_shop"), self._shop_buttons(shops))
            else:
                try:
                    await replies.send(await self._subscriptions.chat_text(incoming.user_id, shop.shop_id, lang))
                except AppError as error:
                    await replies.send(self._error_text(lang, error))
        elif command == "/yordam":
            await replies.send(say(lang, "help"))
        elif command in _LATER_COMMANDS:
            await replies.send(say(lang, "soon"))
        else:
            await replies.send(say(lang, "help"))

    # --- what a person may press ---------------------------------------------------------------------

    async def handle_callback(self, session: PlatformSession, incoming: Incoming, data: str) -> None:
        replies = Replies(session, incoming)
        parts = data.split(":")
        if len(parts) < 2 or parts[0] != CALLBACK_VERSION:
            await replies.buttons(None)
            return
        action, arguments = parts[1], parts[2:]
        lang = incoming.lang

        if action == "lang" and arguments and arguments[0] in LANGUAGE_NAMES:
            await session.set_user_language(incoming.user_id, arguments[0])
            await replies.show(say(arguments[0], "lang_set"))
        elif action == "newshop":
            await session.put_pending(
                pending_id=self._pending_id(incoming),
                user_id=incoming.user_id,
                kind="shop_name",
                payload={},
                now=self._now(),
                expires_at=self._now() + PENDING_LIFETIME,
            )
            await replies.show(say(lang, "ask_shop_name"))
        elif action == "shop" and arguments:
            shop_id = _uuid(arguments[0])
            mine = {shop.shop_id: shop for shop in await session.my_memberships(incoming.user_id)}
            if shop_id is None or shop_id not in mine:
                await replies.show(say(lang, "expired"))
                return
            await session.set_active_shop(incoming.user_id, shop_id)
            await replies.show(say(lang, "shop_switched", shop=mine[shop_id].name))
        elif action in ("ok", "no") and arguments:
            await self._consent_answer(session, incoming, replies, action, arguments[0])
        elif action in ("dsp", "dcl") and arguments:
            target = _uuid(arguments[0])
            if target is None:
                await replies.buttons(None)
                return
            kind, field, question = (
                ("dispute", "entry", "ask_dispute_reason")
                if action == "dsp"
                else (
                    "decline",
                    "dispute",
                    "ask_decline_reason",
                )
            )
            await session.drop_pending(incoming.user_id, kind)
            await session.put_pending(
                pending_id=self._pending_id(incoming),
                user_id=incoming.user_id,
                kind=kind,
                payload={field: target.hex},
                now=self._now(),
                expires_at=self._now() + PENDING_LIFETIME,
            )
            await replies.send(say(lang, question))
        elif action == MOVE_DATE_ACTION and arguments:
            entry_id = _uuid(arguments[0])
            if entry_id is None:
                await replies.buttons(None)
                return
            # The newest question is the one answered, so pressing another entry's button replaces this one.
            await session.put_pending(
                pending_id=self._pending_id(incoming),
                user_id=incoming.user_id,
                kind="date_request",
                payload={"entry": entry_id.hex},
                now=self._now(),
                expires_at=self._now() + PENDING_LIFETIME,
            )
            await replies.send(say(lang, "ask_move_date"))
        elif action in (ACCEPT_ACTION, DECLINE_ACTION) and arguments:
            request_id = _uuid(arguments[0])
            if request_id is None:
                await replies.buttons(None)
                return
            await self._decide_date_request(session, incoming, replies, request_id, accept=action == ACCEPT_ACTION)
        elif action in ("del", "delok", "delno") and arguments:
            await self._removal_answer(session, incoming, replies, action, arguments[0])
        elif action == "unl" and arguments:
            shop_id = _uuid(arguments[0])
            accounts = {account.shop_id: account for account in await session.my_accounts(incoming.user_id)}
            if shop_id is None or shop_id not in accounts or not await session.end_my_link(incoming.user_id, shop_id):
                await replies.show(say(lang, "expired"))
                return
            await replies.show(say(lang, "unlinked", shop=accounts[shop_id].shop_name))
        elif action in ("nc", "pk", "x") and arguments:
            await self._pending_entry(session, incoming, replies, action, arguments)
        elif action in ("pd", "rv", "rvok", "keep") and arguments:
            entry_id = _uuid(arguments[0])
            if entry_id is None:
                await replies.buttons(None)
                return
            shops = await session.my_memberships(incoming.user_id)
            if action == "keep":
                await replies.buttons(None)
            elif action == "rv":
                await replies.buttons(
                    [
                        [
                            (say(lang, "reverse_yes"), callback("rvok", entry_id.hex)),
                            (say(lang, "reverse_no"), callback("keep", entry_id.hex)),
                        ]
                    ]
                )
            elif action == "rvok":
                await self._reverse(incoming, replies, shops, entry_id)
            elif len(arguments) == 2 and arguments[1] == "p":
                await session.put_pending(
                    pending_id=self._pending_id(incoming),
                    user_id=incoming.user_id,
                    kind="promise_date",
                    payload={"entry": entry_id.hex},
                    now=self._now(),
                    expires_at=self._now() + PENDING_LIFETIME,
                )
                await replies.send(say(lang, "ask_date"))
            elif len(arguments) == 2 and arguments[1] in _QUICK:
                await self._choose_promise(session, incoming, replies, shops, entry_id, _QUICK[arguments[1]])
            else:
                await replies.buttons(None)
        else:
            await replies.buttons(None)

    # --- customers -----------------------------------------------------------------------------------

    async def _home_without_shop(self, session: PlatformSession, incoming: Incoming, replies: Replies) -> None:
        """Someone who works in no shop: a customer sees what they owe, anyone else how to begin."""
        if await session.my_accounts(incoming.user_id):
            await self._accounts(session, incoming, replies)
        else:
            await replies.send(say(incoming.lang, "welcome_new"), self._open_shop(incoming.lang))

    async def _accounts(self, session: PlatformSession, incoming: Incoming, replies: Replies) -> None:
        lang = incoming.lang
        accounts = await session.my_accounts(incoming.user_id)
        if not accounts:
            await replies.send(say(lang, "no_accounts"))
            return
        lines = [say(lang, "accounts_header")]
        lines += [
            say(lang, "account_line", shop=account.shop_name, balance=money(lang, account.balance))
            for account in accounts
        ]
        await replies.send("\n".join(lines))

    async def _ask_consent(self, session: PlatformSession, incoming: Incoming, replies: Replies, token: str) -> None:
        """Show what will be stored, by whom and why. Nothing is stored about the person until they agree."""
        lang = incoming.lang
        usable = token.isascii() and 20 <= len(token) <= 128
        info = await session.customer_token_info(token_hash(token)) if usable else None
        if info is None:
            await replies.send(say(lang, "link_invalid"))
            return
        _, _, shop_name = info
        pending_id = self._pending_id(incoming)
        await session.put_pending(
            pending_id=pending_id,
            user_id=incoming.user_id,
            kind="consent",
            # Only the hash: the code itself is never stored anywhere.
            payload={"code": token_hash(token).hex(), "shop": shop_name},
            now=self._now(),
            expires_at=self._now() + PENDING_LIFETIME,
        )
        await replies.send(
            say(lang, "consent_v2", shop=shop_name),
            [
                [
                    (say(lang, "consent_yes"), callback("ok", pending_id.hex)),
                    (say(lang, "consent_no"), callback("no", pending_id.hex)),
                ]
            ],
        )

    async def _consent_answer(
        self, session: PlatformSession, incoming: Incoming, replies: Replies, action: str, pending_hex: str
    ) -> None:
        lang = incoming.lang
        pending_id = _uuid(pending_hex)
        payload = (
            None
            if pending_id is None
            else await session.take_pending(pending_id, incoming.user_id, "consent", self._now())
        )
        if payload is None:
            await replies.show(say(lang, "expired"))
            return
        if action == "no":
            await replies.show(say(lang, "consent_declined"))
            return
        try:
            code = bytes.fromhex(str(payload.get("code", "")))
        except ValueError:
            await replies.show(say(lang, "expired"))
            return
        outcome, _, _ = await session.link_customer(code, incoming.user_id, CONSENT_VERSION, incoming.profile_name)
        shop_name = str(payload.get("shop", ""))
        texts = {
            "linked": "linked",
            "waiting": "waiting_ok",
            "already": "link_already",
            "taken": "link_taken",
        }
        await replies.show(say(lang, texts.get(outcome, "link_invalid"), shop=shop_name))

    async def _removal_answer(
        self, session: PlatformSession, incoming: Incoming, replies: Replies, action: str, link_hex: str
    ) -> None:
        """/ochirish: choose the shop, confirm, then the same removal as the API (REQ-029)."""
        lang = incoming.lang
        link_id = _uuid(link_hex)
        accounts = {account.link_id: account for account in await session.my_accounts(incoming.user_id)}
        if link_id is None or link_id not in accounts:
            await replies.show(say(lang, "expired"))
            return
        account = accounts[link_id]
        if action == "del":
            await replies.show(
                say(lang, "removal_confirm", shop=account.shop_name),
                [
                    [
                        (say(lang, "removal_yes"), callback("delok", link_id.hex)),
                        (say(lang, "reverse_no"), callback("delno", link_id.hex)),
                    ]
                ],
            )
            return
        if action == "delno":
            await replies.show(say(lang, "cancelled"))
            return
        try:
            result = await self._accounts_service.request_removal(incoming.user_id, link_id)
        except NotFound:
            await replies.show(say(lang, "expired"))
            return
        if result["removed"]:
            await replies.show(say(lang, "removal_done", shop=account.shop_name))
        else:
            await replies.show(
                say(
                    lang,
                    "removal_waiting",
                    shop=account.shop_name,
                    balance=money(lang, int(result["waiting_for_balance"])),
                )
            )

    # --- disputes ------------------------------------------------------------------------------------

    async def _dispute_reason(
        self, session: PlatformSession, incoming: Incoming, replies: Replies, payload: dict[str, Any], text: str
    ) -> None:
        """The customer's next message after pressing "dispute" is the reason (REQ-016)."""
        lang = incoming.lang
        entry_id = _uuid(str(payload.get("entry", "")))
        await session.drop_pending(incoming.user_id, "dispute")
        if entry_id is None:
            await replies.send(say(lang, "expired"))
            return
        for account in await session.my_accounts(incoming.user_id):
            try:
                await self._disputes.open(incoming.user_id, account.link_id, entry_id, text)
            except NotFound:
                continue  # the entry is not on this account; perhaps on another
            except ValidationFailed:
                await replies.send(say(lang, "reason_invalid"))
                return
            except AppError as error:
                await replies.send(self._error_text(lang, error))
                return
            await replies.send(say(lang, "dispute_sent", shop=account.shop_name))
            return
        await replies.send(say(lang, "not_found"))

    async def _decline_reason(
        self, session: PlatformSession, incoming: Incoming, replies: Replies, payload: dict[str, Any], text: str
    ) -> None:
        """A manager's next message after pressing "decline" is the reason given to the customer (BR-12)."""
        lang = incoming.lang
        dispute_id = _uuid(str(payload.get("dispute", "")))
        await session.drop_pending(incoming.user_id, "decline")
        reason = clean_reason(text)
        if dispute_id is None:
            await replies.send(say(lang, "expired"))
            return
        if reason is None:
            await replies.send(say(lang, "reason_invalid"))
            return
        for shop in await session.my_memberships(incoming.user_id):
            try:
                async with self._storage.tenant(shop.shop_id) as tenant:
                    if await tenant.get_dispute(dispute_id) is None:
                        continue
                    actor = await require_member(tenant, incoming.user_id, DECLINE_DISPUTE)
                    await require_writable(tenant, self._today(), new_credit=False)

                    async def apply(tenant: TenantSession = tenant, actor: Membership = actor) -> dict[str, Any]:
                        return await decline_in(tenant, actor, dispute_id, reason, self._now())

                    await idempotency.run_once(
                        tenant,
                        key=incoming.key,
                        operation="chat.dispute.decline",
                        user_id=incoming.user_id,
                        request={"dispute": str(dispute_id), "reason": reason},
                        action=apply,
                    )
            except AppError as error:
                await replies.send(self._error_text(lang, error))
                return
            await replies.send(say(lang, "dispute_declined_staff"))
            return
        await replies.send(say(lang, "not_found"))

    # --- date change requests ------------------------------------------------------------------------

    def _date_request_error(self, lang: str, error: AppError) -> str:
        if isinstance(error, ValidationFailed):
            return say(lang, error.fields.get("requested_date", "error"))
        key = _DATE_REQUEST_TEXTS.get(error.fields.get("reason", ""))
        return say(lang, key) if key is not None else self._error_text(lang, error)

    async def _requested_date(
        self, session: PlatformSession, incoming: Incoming, replies: Replies, payload: dict[str, Any], text: str
    ) -> None:
        """The customer's next message after pressing "move the date" is the date they ask for (REQ-066).

        No reason is asked for in the chat: it is optional, and the customer page has a field for it.
        """
        lang = incoming.lang
        requested = parse_day_month(text, self._today())
        if requested is None:
            # The question stays open, so a mistyped date can simply be written again.
            await replies.send(say(lang, "move_date_invalid"))
            return
        entry_id = _uuid(str(payload.get("entry", "")))
        await session.drop_pending(incoming.user_id, "date_request")
        if entry_id is None:
            await replies.send(say(lang, "expired"))
            return
        for account in await session.my_accounts(incoming.user_id):
            try:
                await self._date_requests.open(incoming.user_id, account.link_id, entry_id, requested, None)
            except NotFound:
                continue  # the entry is not on this account; perhaps on another
            except AppError as error:
                await replies.send(self._date_request_error(lang, error))
                return
            await replies.send(say(lang, "date_request_sent", shop=account.shop_name, date=day(requested)))
            return
        await replies.send(say(lang, "not_found"))

    async def _decide_date_request(
        self, session: PlatformSession, incoming: Incoming, replies: Replies, request_id: UUID, *, accept: bool
    ) -> None:
        """A manager or owner presses accept or decline under the notice of a request (REQ-067).

        One press decides. Declining from the chat carries no reason; the Mini App can give one.
        """
        lang = incoming.lang
        for shop in await session.my_memberships(incoming.user_id):
            try:
                async with self._storage.tenant(shop.shop_id) as tenant:
                    if await tenant.get_date_request(request_id) is None:
                        continue
                    operation = ACCEPT_DATE_REQUEST if accept else DECLINE_DATE_REQUEST
                    actor = await require_member(tenant, incoming.user_id, operation)
                    await require_writable(tenant, self._today(), new_credit=False)

                    async def apply(tenant: TenantSession = tenant, actor: Membership = actor) -> dict[str, Any]:
                        if accept:
                            return await accept_date_request_in(tenant, actor, request_id, self._now())
                        return await decline_date_request_in(tenant, actor, request_id, None, self._now())

                    body = await idempotency.run_once(
                        tenant,
                        key=incoming.key,
                        operation=f"chat.{operation.name}",
                        user_id=incoming.user_id,
                        request={"request": str(request_id)},
                        action=apply,
                    )
            except AppError as error:
                await replies.send(self._date_request_error(lang, error))
                if error.fields.get("reason") == "not_open":
                    await replies.buttons(None)
                return
            await replies.show(
                say(
                    lang,
                    "date_accepted_staff" if accept else "date_declined_staff",
                    name=body["customer_name"],
                    amount=money(lang, body["amount"]),
                    date=day(date.fromisoformat(body["requested_date"])),
                )
            )
            return
        await replies.send(say(lang, "not_found"))

    # --- shops ---------------------------------------------------------------------------------------

    @staticmethod
    def _pending_id(incoming: Incoming) -> UUID:
        return uuid5(_NAMESPACE, f"pending:{incoming.update_id}")

    @staticmethod
    def _open_shop(lang: str) -> Keyboard:
        return [[(say(lang, "open_shop"), callback("newshop"))]]

    @staticmethod
    def _shop_buttons(shops: list[MyShop]) -> Keyboard:
        return [[(shop.name, callback("shop", shop.shop_id.hex))] for shop in shops[:20]]

    @staticmethod
    async def _active_shop(session: PlatformSession, user_id: UUID, shops: list[MyShop]) -> MyShop | None:
        """The shop chat entry applies to (REQ-064): the chosen one, or the only one."""
        if len(shops) == 1:
            return shops[0]
        chosen = await session.active_shop(user_id)
        return next((shop for shop in shops if shop.shop_id == chosen), None)

    async def _create_shop(self, session: PlatformSession, incoming: Incoming, replies: Replies, text: str) -> None:
        name = " ".join(text.split())
        try:
            body = await self._shops.create(incoming.user_id, name, incoming.lang, incoming.key)
        except ValidationFailed:
            await replies.send(say(incoming.lang, "shop_name_invalid"))
            return
        await session.drop_pending(incoming.user_id, "shop_name")
        await session.set_active_shop(incoming.user_id, UUID(body["id"]))
        await replies.send(say(incoming.lang, "shop_created", shop=body["name"]))

    async def _join(self, session: PlatformSession, incoming: Incoming, replies: Replies, token: str) -> None:
        try:
            joined = await self._staff.accept_invitation(incoming.user_id, token)
        except NotFound:
            await replies.send(say(incoming.lang, "invitation_invalid"))
            return
        except AlreadyMember:
            await replies.send(say(incoming.lang, "already_member"))
            return
        shop_id = UUID(joined["shop_id"])
        await session.set_active_shop(incoming.user_id, shop_id)
        shops = await session.my_memberships(incoming.user_id)
        name = next((shop.name for shop in shops if shop.shop_id == shop_id), "")
        await replies.send(say(incoming.lang, "joined_shop", shop=name))

    # --- entries -------------------------------------------------------------------------------------

    def _error_text(self, lang: str, error: AppError) -> str:
        if isinstance(error, ForbiddenRole):
            return say(lang, "forbidden")
        if isinstance(error, NotFound):
            return say(lang, "not_found")
        if isinstance(error, ValidationFailed):
            reason = error.fields.get("promised_date")
            if reason in ("PROMISE_BEFORE_SALE", "PROMISE_TOO_FAR"):
                return say(lang, reason)
            return say(lang, "amount_range" if "amount" in error.fields else "parse_hint")
        try:
            return say(lang, error.code)
        except KeyError:
            return say(lang, "error")

    def _saved(self, lang: str, shop: MyShop, body: dict[str, Any]) -> tuple[str, Keyboard]:
        entry, customer = body["entry"], body["customer"]
        entry_hex = UUID(entry["id"]).hex
        values = {
            "shop": shop.name,
            "name": customer["display_name"],
            "amount": money(lang, entry["amount"]),
            "balance": money(lang, customer["balance"]),
        }
        keyboard: Keyboard = []
        if entry["kind"] == "credit":
            text = say(lang, "credit_saved", date=day(date.fromisoformat(entry["promised_date"])), **values)
            keyboard.append(
                [(say(lang, choice.value), callback("pd", entry_hex, code)) for code, choice in _QUICK.items()]
            )
            keyboard.append([(say(lang, "other_date"), callback("pd", entry_hex, "p"))])
            warning = body.get("limit_warning")
            if warning:
                limit, owed = money(lang, warning["limit"]), money(lang, warning["balance"])
                text = "\n".join([text, say(lang, "limit_warning", limit=limit, balance=owed)])
        else:
            text = say(lang, "payment_saved", **values)
        if allows(shop.role, Capability.MANAGE):
            keyboard.append([(say(lang, "reverse"), callback("rv", entry_hex))])
        return text, keyboard

    async def _write_entry(
        self,
        session: TenantSession,
        incoming: Incoming,
        *,
        customer_id: UUID | None,
        new_name: str | None,
        kind: EntryKind,
        amount: int,
        note: str | None,
    ) -> dict[str, Any]:
        """Record the entry, creating the customer first when asked to, as one idempotent write."""
        actor = await require_member(session, incoming.user_id, RECORD_ENTRY)
        if new_name is not None:
            await require_member(session, incoming.user_id, CREATE_CUSTOMER)
        clean_entry(kind.value, amount, note, None)
        await require_writable(session, self._today(), new_credit=kind is EntryKind.CREDIT)

        async def apply() -> dict[str, Any]:
            target = customer_id
            if new_name is not None:
                target = (await create_customer_in(session, actor, new_name, None)).customer_id
            if target is None:
                raise NotFound()
            return await append_entry_in(
                session,
                actor,
                target,
                kind=kind,
                amount=amount,
                note=note,
                promised_date=None,
                now=self._now(),
                started=incoming.received,
            )

        return await idempotency.run_once(
            session,
            key=incoming.key,
            operation="chat.entry",
            user_id=incoming.user_id,
            request={
                "customer": str(customer_id),
                "new_name": new_name,
                "kind": kind.value,
                "amount": amount,
                "note": note,
            },
            action=apply,
        )

    async def _entry(
        self, session: PlatformSession, incoming: Incoming, replies: Replies, shop: MyShop, parsed: ParsedEntry
    ) -> None:
        lang = incoming.lang
        kind = EntryKind(parsed.kind.value)
        amount_text = money(lang, parsed.amount)
        saved: dict[str, Any] | None = None
        candidates: list[tuple[UUID, str, int]] = []
        try:
            async with self._storage.tenant(shop.shop_id) as tenant:
                await require_member(tenant, incoming.user_id, RECORD_ENTRY)
                exact = await tenant.customers_named(parsed.normalized_name)
                if len(exact) == 1:
                    saved = await self._write_entry(
                        tenant,
                        incoming,
                        customer_id=exact[0][0].customer_id,
                        new_name=None,
                        kind=kind,
                        amount=parsed.amount,
                        note=parsed.note,
                    )
                else:
                    # Check now what would be refused anyway, before asking the seller anything.
                    clean_entry(kind.value, parsed.amount, parsed.note, None)
                    await require_writable(tenant, self._today(), new_credit=kind is EntryKind.CREDIT)
                    found = exact or [
                        (customer, balance)
                        for customer, balance, _ in await tenant.search_customers(
                            name_part=parsed.normalized_name,
                            phone_digits=None,
                            status="active",
                            after=None,
                            limit=MAX_CANDIDATES,
                        )
                    ]
                    candidates = [(c.customer_id, c.display_name, balance) for c, balance in found[:MAX_CANDIDATES]]
        except AppError as error:
            await replies.send(self._error_text(lang, error))
            return

        if saved is not None:
            text, keyboard = self._saved(lang, shop, saved)
            await replies.send(text, keyboard)
            return

        # A payment needs someone who owes: a customer who is not in the book cannot have paid.
        if kind is EntryKind.PAYMENT:
            candidates = [candidate for candidate in candidates if candidate[2] > 0]
            if not candidates:
                await replies.send(say(lang, "unknown_customer_payment", shop=shop.name, name=parsed.name))
                return

        pending_id = self._pending_id(incoming)
        await session.put_pending(
            pending_id=pending_id,
            user_id=incoming.user_id,
            kind="entry",
            payload={
                "shop": shop.shop_id.hex,
                "name": parsed.name,
                "kind": kind.value,
                "amount": parsed.amount,
                "note": parsed.note,
                "candidates": [customer_id.hex for customer_id, _, _ in candidates],
            },
            now=self._now(),
            expires_at=self._now() + PENDING_LIFETIME,
        )
        cancel = (say(lang, "cancel"), callback("x", pending_id.hex))
        if not candidates:
            await replies.send(
                say(lang, "unknown_customer_credit", shop=shop.name, name=parsed.name, amount=amount_text),
                [[(say(lang, "add_and_record"), callback("nc", pending_id.hex))], [cancel]],
            )
            return
        choices: Keyboard = [
            [(f"{name} · {money(lang, balance)}", callback("pk", pending_id.hex, index))]
            for index, (_, name, balance) in enumerate(candidates)
        ]
        if kind is EntryKind.CREDIT:
            choices.append([(say(lang, "new_customer_option", name=parsed.name), callback("nc", pending_id.hex))])
        choices.append([cancel])
        await replies.send(say(lang, "pick_customer", shop=shop.name, name=parsed.name, amount=amount_text), choices)

    async def _pending_entry(
        self, session: PlatformSession, incoming: Incoming, replies: Replies, action: str, arguments: list[str]
    ) -> None:
        lang = incoming.lang
        pending_id = _uuid(arguments[0])
        payload = (
            None
            if pending_id is None
            else await session.take_pending(pending_id, incoming.user_id, "entry", self._now())
        )
        if payload is None:
            await replies.show(say(lang, "expired"))
            return
        if action == "x":
            await replies.show(say(lang, "cancelled"))
            return

        shop_id = _uuid(str(payload.get("shop", "")))
        shops = {shop.shop_id: shop for shop in await session.my_memberships(incoming.user_id)}
        if shop_id is None or shop_id not in shops:
            await replies.show(say(lang, "expired"))
            return
        kind = EntryKind(payload["kind"])
        customer_id: UUID | None = None
        new_name: str | None = None
        if action == "nc" and kind is EntryKind.CREDIT:
            new_name = str(payload["name"])
        elif action == "pk" and len(arguments) == 2 and arguments[1].isdigit():
            listed = list(payload.get("candidates", []))
            index = int(arguments[1])
            # Only a customer that was offered may be picked; the button carries no identifier.
            customer_id = _uuid(str(listed[index])) if index < len(listed) else None
        if customer_id is None and new_name is None:
            await replies.show(say(lang, "expired"))
            return

        try:
            async with self._storage.tenant(shop_id) as tenant:
                saved = await self._write_entry(
                    tenant,
                    incoming,
                    customer_id=customer_id,
                    new_name=new_name,
                    kind=kind,
                    amount=int(payload["amount"]),
                    note=payload.get("note"),
                )
        except AppError as error:
            await replies.show(self._error_text(lang, error))
            return
        text, keyboard = self._saved(lang, shops[shop_id], saved)
        await replies.show(text, keyboard)

    async def _shop_of_entry(self, user_id: UUID, shops: list[MyShop], entry_id: UUID) -> MyShop | None:
        """Which of the caller's shops holds the entry. Another shop's entry is simply not found.

        Only a lookup: row-level security limits it to each shop, and the action that follows is
        authorized on its own.
        """
        for shop in shops:
            async with self._storage.tenant(shop.shop_id) as tenant:
                if await tenant.customer_of_entry(entry_id) is not None:
                    return shop
        return None

    async def _choose_promise(
        self,
        session: PlatformSession,
        incoming: Incoming,
        replies: Replies,
        shops: list[MyShop],
        entry_id: UUID,
        choice: QuickChoice | date,
    ) -> None:
        lang = incoming.lang
        shop = await self._shop_of_entry(incoming.user_id, shops, entry_id)
        if shop is None:
            await replies.show(say(lang, "not_found"))
            return
        try:
            async with self._storage.tenant(shop.shop_id) as tenant:
                actor = await require_member(tenant, incoming.user_id, CHOOSE_PROMISE)
                await require_writable(tenant, self._today(), new_credit=False)
                if isinstance(choice, QuickChoice):
                    sold_at = await tenant.entry_created_at(entry_id)
                    if sold_at is None:
                        raise NotFound()
                    chosen = quick_choice_date(choice, tashkent_date(sold_at))
                else:
                    chosen = choice

                async def apply() -> dict[str, Any]:
                    return await choose_promise_in(tenant, actor, entry_id, chosen, now=self._now())

                body = await idempotency.run_once(
                    tenant,
                    key=incoming.key,
                    operation="chat.promise",
                    user_id=incoming.user_id,
                    request={"entry": str(entry_id), "date": chosen},
                    action=apply,
                )
        except AppError as error:
            text = say(lang, "promise_closed") if error.code == "PROMISE_ALREADY_SET" else self._error_text(lang, error)
            await replies.send(text)
            if error.code == "PROMISE_ALREADY_SET":
                await replies.buttons(self._reverse_only(lang, shop, entry_id))
            return
        text = say(
            lang,
            "credit_saved",
            shop=shop.name,
            name=body["customer"]["display_name"],
            amount=money(lang, body["entry"]["amount"]),
            balance=money(lang, body["customer"]["balance"]),
            date=day(chosen),
        )
        await replies.show(text, self._reverse_only(lang, shop, entry_id))

    @staticmethod
    def _reverse_only(lang: str, shop: MyShop, entry_id: UUID) -> Keyboard:
        if not allows(shop.role, Capability.MANAGE):
            return []
        return [[(say(lang, "reverse"), callback("rv", entry_id.hex))]]

    async def _reverse(self, incoming: Incoming, replies: Replies, shops: list[MyShop], entry_id: UUID) -> None:
        lang = incoming.lang
        shop = await self._shop_of_entry(incoming.user_id, shops, entry_id)
        if shop is None:
            await replies.show(say(lang, "not_found"))
            return
        try:
            async with self._storage.tenant(shop.shop_id) as tenant:
                actor = await require_member(tenant, incoming.user_id, REVERSE_ENTRY)
                await require_writable(tenant, self._today(), new_credit=False)

                async def apply() -> dict[str, Any]:
                    return await reverse_entry_in(tenant, actor, entry_id, now=self._now())

                body = await idempotency.run_once(
                    tenant,
                    key=incoming.key,
                    operation="chat.reverse",
                    user_id=incoming.user_id,
                    request={"entry": str(entry_id)},
                    action=apply,
                )
        except AppError as error:
            await replies.send(self._error_text(lang, error))
            await replies.buttons(None)
            return
        await replies.show(
            say(
                lang,
                "reversed",
                shop=shop.name,
                name=body["customer"]["display_name"],
                amount=money(lang, body["entry"]["amount"]),
                balance=money(lang, body["customer"]["balance"]),
            )
        )

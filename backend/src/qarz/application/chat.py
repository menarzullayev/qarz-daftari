"""The staff chat (technical specification, "Chat contract"): fast entry, date choices, shop switching.

Everything here runs inside the platform transaction that claimed the Telegram update. Replies are queued
in that transaction and sent by the worker. A write to a shop happens in a tenant transaction of its own,
made idempotent by a key derived from the update, so a redelivered update cannot write twice even if the
platform transaction failed after the shop's transaction had committed.

Every action is authorized again when a button is pressed: callback data is only an identifier.
"""

from collections.abc import Callable, Container
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid5

from qarz.application import idempotency
from qarz.application.admin_receipts import CHAT as DECIDED_IN_CHAT
from qarz.application.admin_receipts import AdminReceiptService, ReceiptAlreadyDecided
from qarz.application.authorization import may
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
from qarz.application.errors import (
    AlreadyMember,
    AppError,
    ForbiddenPermission,
    ForbiddenRole,
    NotFound,
    ValidationFailed,
)
from qarz.application.files import FileService
from qarz.application.group_receipts import GroupReceiptService
from qarz.application.ledger_service import (
    CHOOSE_PROMISE,
    RECORD_ENTRY,
    REVERSE_ENTRY,
    append_entry_in,
    choose_promise_in,
    clean_entry,
    require_kind,
    reverse_entry_in,
)
from qarz.application.links import COUNTER_PREFIX, PERSONAL_PREFIX
from qarz.application.payment_notices import ACCEPT_NOTICE, DECLINE_NOTICE, PaymentNoticeService
from qarz.application.payment_notices import accept_in as accept_notice_in
from qarz.application.payment_notices import decline_in as decline_notice_in
from qarz.application.ports import CustomerAccount, Membership, MyShop, PlatformSession, Storage, TenantSession
from qarz.application.shops import ShopService, require_member
from qarz.application.staff import StaffService, token_hash
from qarz.application.subscription import SubscriptionService
from qarz.application.subscription_receipts import REVIEW_GROUP, SubscriptionReceiptService
from qarz.domain import permissions, platform_settings
from qarz.domain.chat_entry import ParsedEntry, ParseError, ParseErrorCode, parse_amount, parse_entry
from qarz.domain.disputes import clean_reason
from qarz.domain.ledger import EntryKind
from qarz.domain.promise import QuickChoice, parse_day_month, quick_choice_date, tashkent_date
from qarz.domain.subscription_receipts import OFFERED_MONTHS, expected_amount
from qarz.domain.subscription_receipts import clean_reason as clean_receipt_reason

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
_LATER_COMMANDS = frozenset({"/ilova"})
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


GROUP_LANG = "uz"  # the review group is answered in the language its announcement was written in
CALLBACK_NOTICE_MAX = 200  # Bot API: the text of answerCallbackQuery


@dataclass(frozen=True)
class Incoming:
    update_id: int
    chat_id: int
    user_id: UUID
    lang: str
    message_id: int | None = None  # the message a pressed button belongs to
    profile_name: str | None = None  # the name in the person's Telegram profile
    received: float | None = None  # time.perf_counter() when the update arrived; for the handling time
    # Set when a button of the receipt announcement was pressed in the review group: that chat and
    # the announcement's message. `chat_id` is then the presser's own private chat, where they are
    # answered; the group only sees the announcement change once a decision is made.
    group: tuple[int, int] | None = None
    # The chat of which Telegram said, while this update was being handled, that the person is its
    # creator or one of its administrators. Only ever asked about the review group (DEC-064).
    administers: int | None = None

    @property
    def key(self) -> str:
        # With colons, which an API request key may not contain: nobody can take this key first.
        return f"tg:update:{self.update_id}"

    @property
    def new_shop_key(self) -> str:
        """The key for opening a shop, which goes through the same operation as the API and so must have
        an API key's form. A new shop has no colleagues yet for anyone to take the key from."""
        return f"tg-update-{self.update_id}"


class Replies:
    """Queues what the bot says in answer to one update."""

    def __init__(self, session: PlatformSession, incoming: Incoming) -> None:
        self._session = session
        self._incoming = incoming
        self._count = 0
        # Words to show over the pressed button itself (`answerCallbackQuery`), for someone the bot may
        # not be able to write to: a person in the review group who never started the bot.
        self.notice: str | None = None

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

    def alert(self, text: str) -> None:
        """Say it over the pressed button. Telegram shows at most 200 characters there."""
        self.notice = text[:CALLBACK_NOTICE_MAX]

    async def strip(self, message_id: int) -> None:
        """Take the buttons off an earlier message of this chat."""
        await self._queue({"method": "editMessageReplyMarkup", "message_id": message_id, "reply_markup": _markup(None)})

    async def close_in_group(self, chat_id: int, message_id: int, text: str) -> None:
        """Replace the announcement in the review group: it says what was decided and has no buttons."""
        await self._session.enqueue(
            channel="telegram",
            recipient=str(chat_id),
            payload={
                "method": "editMessageText",
                "message_id": message_id,
                "text": text,
                "reply_markup": _markup(None),
            },
            dedupe_key=f"update:{self._incoming.update_id}:group",
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
        files: FileService | None = None,
        admin_tg_ids: Container[int] = (),
        admin_storage: Storage | None = None,
    ) -> None:
        self._storage = storage
        self._shops = shops
        self._staff = staff
        self._accounts_service = CustomerAccountService(storage, now)
        self._disputes = DisputeService(storage, now)
        self._subscriptions = SubscriptionService(storage, now)
        # Without a file service a notice can still be sent; only a receipt is refused.
        self._notices = PaymentNoticeService(storage, files or FileService(storage, None), now)
        self._receipts = SubscriptionReceiptService(
            storage, files or FileService(storage, None), now, admin_tg_ids=admin_tg_ids
        )
        # The allow-list of the administrator's side, and the same decisions the panel makes. They are
        # made through the administrators' own database role, like the panel's: the ordinary role this
        # chat otherwise works with cannot read an administrator's account or decide a receipt. Without
        # that storage nobody is an administrator here, so the fallback below is never used.
        self._reviewers = admin_tg_ids
        self._admin_storage = admin_storage
        self._admin_receipts = AdminReceiptService(admin_storage or storage, files or FileService(storage, None), now)
        # The decisions of the review group's own Telegram administrators (DEC-064).
        self._group_receipts = GroupReceiptService(now)
        self._date_requests = DateRequestService(storage, now)
        self._now = now or (lambda: datetime.now(UTC))

    def _today(self) -> date:
        return tashkent_date(self._now())

    # --- what a person may type ----------------------------------------------------------------------

    async def handle_text(self, session: PlatformSession, incoming: Incoming, text: str) -> None:
        replies = Replies(session, incoming)
        text = text.strip()
        if text.startswith("/"):
            # A command ends an unfinished payment notice: what is typed next is not its amount. It ends
            # a subscription receipt that was being sent as well.
            await session.drop_pending(incoming.user_id, "notice")
            await session.drop_pending(incoming.user_id, "sub_receipt")
            await session.drop_pending(incoming.user_id, "receipt_reject")
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
        asked = await session.current_pending(incoming.user_id, "notice_decline", self._now())
        if asked is not None:
            await self._notice_decline_reason(session, incoming, replies, asked[1], text)
            return
        asked = await session.current_pending(incoming.user_id, "receipt_reject", self._now())
        if asked is not None:
            await self._receipt_reject_reason(session, incoming, replies, asked[1], text)
            return
        asked = await session.current_pending(incoming.user_id, "notice", self._now())
        if asked is not None:
            await self._notice_text(session, incoming, replies, asked[1], text)
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
                # A person may have any number of shops (DEC-065): this list is also where the next one
                # is opened, since the offer to open one is otherwise made only to a person with none.
                await replies.send(
                    say(lang, "choose_shop"),
                    [*self._shop_buttons(shops), [(say(lang, "new_shop"), callback("newshop"))]],
                )
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
                await self._subscription_offer(incoming, replies, shop)
        elif command == "/toladim":
            await self._notice_start(session, incoming, replies)
        elif command == "/yordam":
            await replies.send(say(lang, "help"))
        elif command in _LATER_COMMANDS:
            await replies.send(say(lang, "soon"))
        else:
            await replies.send(say(lang, "help"))

    # --- what a person may press ---------------------------------------------------------------------

    async def handle_callback(
        self, session: PlatformSession, incoming: Incoming, data: str, replies: Replies | None = None
    ) -> None:
        replies = replies or Replies(session, incoming)
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
        elif action == "pn" and arguments:
            await self._notice_shop(session, incoming, replies, arguments[0])
        elif action in ("pns", "pnx"):
            await self._notice_finish(session, incoming, replies, action)
        elif action == "srm" and len(arguments) in (2, 4):
            # Two parts: a button sent before a card could be chosen. It still works, for no card.
            await self._sub_receipt_start(session, incoming, replies, arguments[0], arguments[1], arguments[2:])
        elif action in ("sro", "src") and len(arguments) == 3:
            await self._sub_card_pressed(session, incoming, replies, action, arguments[0], arguments[1:])
        elif action in ("sra", "srj") and len(arguments) == 1:
            await self._receipt_pressed(session, incoming, replies, action, arguments[0])
        elif action == "srn":
            await session.drop_pending(incoming.user_id, "receipt_reject")
            await replies.show(say(lang, "cancelled"))
        elif action == "srx":
            await session.drop_pending(incoming.user_id, "sub_receipt")
            await replies.show(say(lang, "cancelled"))
        elif action in ("pna", "pnd") and arguments:
            notice_id = _uuid(arguments[0])
            if notice_id is None:
                await replies.buttons(None)
            elif action == "pna":
                await self._notice_accept(session, incoming, replies, notice_id)
            else:
                await session.drop_pending(incoming.user_id, "notice_decline")
                await session.put_pending(
                    pending_id=self._pending_id(incoming),
                    user_id=incoming.user_id,
                    kind="notice_decline",
                    payload={"notice": notice_id.hex},
                    now=self._now(),
                    expires_at=self._now() + PENDING_LIFETIME,
                )
                await replies.send(say(lang, "ask_decline_reason"))
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
            "full": "waiting_full",
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

    # --- payment notices ----------------------------------------------------------------------------

    @staticmethod
    def _receipt_buttons(lang: str) -> Keyboard:
        return [[(say(lang, "notice_without_receipt"), callback("pns"))], [(say(lang, "cancel"), callback("pnx"))]]

    async def _notice_start(self, session: PlatformSession, incoming: Incoming, replies: Replies) -> None:
        """/toladim: choose the shop when linked to several, then the amount, then the receipt (REQ-060)."""
        lang = incoming.lang
        accounts = await session.my_accounts(incoming.user_id)
        if not accounts:
            await replies.send(say(lang, "no_accounts"))
        elif len(accounts) == 1:
            await self._notice_ask_amount(session, incoming, replies, accounts[0])
        else:
            await replies.send(
                say(lang, "notice_choose_shop"),
                [
                    [
                        (
                            say(
                                lang, "notice_shop_button", shop=account.shop_name, balance=money(lang, account.balance)
                            ),
                            callback("pn", account.link_id.hex),
                        )
                    ]
                    for account in accounts[:20]
                ],
            )

    async def _notice_shop(self, session: PlatformSession, incoming: Incoming, replies: Replies, link_hex: str) -> None:
        link_id = _uuid(link_hex)
        accounts = {account.link_id: account for account in await session.my_accounts(incoming.user_id)}
        if link_id is None or link_id not in accounts:
            await replies.show(say(incoming.lang, "expired"))
            return
        await self._notice_ask_amount(session, incoming, replies, accounts[link_id])

    async def _notice_ask_amount(
        self, session: PlatformSession, incoming: Incoming, replies: Replies, account: CustomerAccount
    ) -> None:
        lang = incoming.lang
        await session.drop_pending(incoming.user_id, "notice")
        if account.balance <= 0:
            await replies.show(say(lang, "notice_nothing_owed", shop=account.shop_name))
            return
        await session.put_pending(
            pending_id=self._pending_id(incoming),
            user_id=incoming.user_id,
            kind="notice",
            payload={"link": account.link_id.hex},
            now=self._now(),
            expires_at=self._now() + PENDING_LIFETIME,
        )
        await replies.show(
            say(lang, "ask_notice_amount", shop=account.shop_name, balance=money(lang, account.balance)),
            [[(say(lang, "cancel"), callback("pnx"))]],
        )

    @staticmethod
    async def _notice_account(
        session: PlatformSession, incoming: Incoming, payload: dict[str, Any]
    ) -> CustomerAccount | None:
        """The caller's own account the question was about; a link that has ended since is gone."""
        link_id = _uuid(str(payload.get("link", "")))
        accounts = await session.my_accounts(incoming.user_id)
        return next((account for account in accounts if account.link_id == link_id), None)

    async def _notice_text(
        self, session: PlatformSession, incoming: Incoming, replies: Replies, payload: dict[str, Any], text: str
    ) -> None:
        """What a customer types after /toladim: the amount, and nothing but the amount."""
        lang = incoming.lang
        account = await self._notice_account(session, incoming, payload)
        if account is None:
            await session.drop_pending(incoming.user_id, "notice")
            await replies.send(say(lang, "expired"))
            return
        if "amount" in payload:
            await replies.send(say(lang, "notice_receipt_hint"), self._receipt_buttons(lang))
            return
        amount = parse_amount(text)
        if isinstance(amount, ParseError):
            await replies.send(say(lang, "notice_amount_invalid"))
            return
        if amount > account.balance:
            await replies.send(say(lang, "notice_amount_exceeds", balance=money(lang, account.balance)))
            return
        await session.drop_pending(incoming.user_id, "notice")
        await session.put_pending(
            pending_id=self._pending_id(incoming),
            user_id=incoming.user_id,
            kind="notice",
            payload={"link": account.link_id.hex, "amount": amount},
            now=self._now(),
            expires_at=self._now() + PENDING_LIFETIME,
        )
        await replies.send(say(lang, "ask_notice_receipt", amount=money(lang, amount)), self._receipt_buttons(lang))

    async def awaits_receipt(self, session: PlatformSession, user_id: UUID) -> bool:
        """Whether a file from this person would be the receipt of a payment notice they are sending."""
        asked = await session.current_pending(user_id, "notice", self._now())
        if asked is not None and "amount" in asked[1]:
            return True
        # Or the receipt of a subscription payment, after a period was chosen under /obuna.
        return await session.current_pending(user_id, "sub_receipt", self._now()) is not None

    async def handle_file(self, session: PlatformSession, incoming: Incoming, content: bytes | None) -> None:
        """A photo or a document. `content` is None when it could not be had or is too large.

        Only a payment notice waiting for its receipt has any use for a file.
        """
        replies = Replies(session, incoming)
        lang = incoming.lang
        asked = await session.current_pending(incoming.user_id, "notice", self._now())
        paying = await session.current_pending(incoming.user_id, "sub_receipt", self._now())
        if (asked is None or "amount" not in asked[1]) and paying is not None:
            await self._sub_receipt_send(session, incoming, replies, paying[1], content)
        elif asked is None or "amount" not in asked[1]:
            await replies.send(say(lang, "only_text"))
        elif content is None:
            await replies.send(say(lang, "notice_receipt_invalid"), self._receipt_buttons(lang))
        else:
            await self._notice_send(session, incoming, replies, asked[1], content)

    async def _notice_finish(self, session: PlatformSession, incoming: Incoming, replies: Replies, action: str) -> None:
        lang = incoming.lang
        asked = await session.current_pending(incoming.user_id, "notice", self._now())
        if asked is None or (action == "pns" and "amount" not in asked[1]):
            await replies.show(say(lang, "expired"))
        elif action == "pnx":
            await session.drop_pending(incoming.user_id, "notice")
            await replies.show(say(lang, "cancelled"))
        else:
            await self._notice_send(session, incoming, replies, asked[1], None)

    async def _notice_send(
        self,
        session: PlatformSession,
        incoming: Incoming,
        replies: Replies,
        payload: dict[str, Any],
        receipt: bytes | None,
    ) -> None:
        lang = incoming.lang
        account = await self._notice_account(session, incoming, payload)
        amount = payload.get("amount")
        if account is None or not isinstance(amount, int):
            await session.drop_pending(incoming.user_id, "notice")
            await replies.show(say(lang, "expired"))
            return
        try:
            await self._notices.send(incoming.user_id, account.link_id, amount, receipt, update_key=incoming.key)
        except AppError as error:
            if isinstance(error, ValidationFailed) and "receipt" in error.fields:
                # The question stays open: another photo may follow.
                await replies.show(say(lang, "notice_receipt_invalid"), self._receipt_buttons(lang))
                return
            if error.code != "FILE_STORE_UNAVAILABLE":
                await session.drop_pending(incoming.user_id, "notice")
            await replies.show(self._error_text(lang, error))
            return
        await session.drop_pending(incoming.user_id, "notice")
        await replies.show(say(lang, "notice_sent", shop=account.shop_name, amount=money(lang, amount)))

    # --- paying the subscription by card transfer (REQ-054) -------------------------------------------

    @staticmethod
    def _card_mark(cards: list[dict[str, str]], place: int) -> tuple[int, str]:
        """How a button names a card: its place in the list and the last four digits of its number.

        The number itself does not fit beside a shop in Telegram's 64 bytes and has no business in a
        button. The digits are the check: when the list has changed, the place holds another card.
        """
        return place, cards[place]["number"][-4:]

    @staticmethod
    def _card_at(cards: list[dict[str, str]], mark: list[str]) -> int | None:
        """The place of the card a button names, if the list still has that card there."""
        place_text, last4 = mark
        if not (place_text.isascii() and place_text.isdigit() and len(place_text) <= 2):
            return None
        place = int(place_text)
        return place if place < len(cards) and cards[place]["number"][-4:] == last4 else None

    async def _subscription_offer(
        self, incoming: Incoming, replies: Replies, shop: MyShop, *, card: int = 0, edit: bool = False
    ) -> None:
        """Show `/obuna`: the card to pay to, the periods to pay for, and the way to the other cards.

        `card` is the place of the card shown; one the list does not have is the primary. With `edit`
        the message whose button was pressed is replaced.
        """
        lang = incoming.lang
        try:
            text, _, price, cards = await self._subscriptions.chat_offer(incoming.user_id, shop.shop_id, lang, card)
        except AppError as error:
            await replies.show(self._error_text(lang, error))
            return
        out = replies.show if edit else replies.send
        if price is None:
            await out(text)
            return
        mark = self._card_mark(cards, card if 0 <= card < len(cards) else 0)
        keyboard: Keyboard = [
            [
                (
                    say(lang, "sub_months_button", months=count, amount=money(lang, expected_amount(price, count))),
                    callback("srm", shop.shop_id.hex, count, *mark),
                )
            ]
            for count in OFFERED_MONTHS
        ]
        if len(cards) > 1:
            keyboard.append(
                [(say(lang, "sub_other_cards_button", count=len(cards) - 1), callback("sro", shop.shop_id.hex, *mark))]
            )
        await out(text + "\n" + say(lang, "sub_choose_months"), keyboard)

    async def _sub_card_pressed(
        self,
        session: PlatformSession,
        incoming: Incoming,
        replies: Replies,
        action: str,
        shop_hex: str,
        mark: list[str],
    ) -> None:
        """ "Other cards" under `/obuna` (`sro`): list them. A card of that list, or the way back (`src`):
        show `/obuna` again with that card as the one to pay to."""
        lang = incoming.lang
        shop_id = _uuid(shop_hex)
        mine = {shop.shop_id: shop for shop in await session.my_memberships(incoming.user_id)}
        if shop_id is None or shop_id not in mine:
            await replies.show(say(lang, "expired"))
            return
        try:
            _, _, _, cards = await self._subscriptions.chat_offer(incoming.user_id, shop_id, lang)
        except AppError as error:
            await replies.show(self._error_text(lang, error))
            return
        place = self._card_at(cards, mark)
        if place is None or action == "src" or len(cards) < 2:
            # A button from before the list changed names a card that is not there any more: what is
            # offered now is shown in its place, with the primary card.
            await self._subscription_offer(incoming, replies, mine[shop_id], card=place or 0, edit=True)
            return
        others: Keyboard = [
            [(platform_settings.card_tag(other), callback("src", shop_id.hex, *self._card_mark(cards, at)))]
            for at, other in enumerate(cards)
            if at != place
        ]
        back = [(say(lang, "sub_cards_back"), callback("src", shop_id.hex, *self._card_mark(cards, place)))]
        await replies.show(say(lang, "sub_choose_card"), [*others, back])

    async def _sub_receipt_start(
        self,
        session: PlatformSession,
        incoming: Incoming,
        replies: Replies,
        shop_hex: str,
        months_text: str,
        mark: list[str],
    ) -> None:
        """A period was chosen under `/obuna`: remember it, with the card that was shown, and ask for the
        receipt."""
        lang = incoming.lang
        shop_id = _uuid(shop_hex)
        mine = {shop.shop_id: shop for shop in await session.my_memberships(incoming.user_id)}
        months = int(months_text) if months_text.isascii() and months_text.isdigit() else 0
        if shop_id is None or shop_id not in mine or months not in OFFERED_MONTHS:
            await replies.show(say(lang, "expired"))
            return
        try:
            text, name, price, cards = await self._subscriptions.chat_offer(incoming.user_id, shop_id, lang)
        except AppError as error:
            await replies.show(self._error_text(lang, error))
            return
        if price is None:
            await replies.show(text)
            return
        paid_to: str | None = None
        if mark:
            place = self._card_at(cards, mark)
            if place is None:
                # The card the button was under is not in the list any more. Nothing is remembered: the
                # owner is shown the cards as they are now and chooses again.
                await self._subscription_offer(incoming, replies, mine[shop_id], edit=True)
                return
            paid_to = platform_settings.card_tag(cards[place])
        amount = expected_amount(price, months)
        for kind in ("notice", "sub_receipt"):
            await session.drop_pending(incoming.user_id, kind)
        await session.put_pending(
            pending_id=self._pending_id(incoming),
            user_id=incoming.user_id,
            kind="sub_receipt",
            # The card as the receipt will name it: its label and last four digits, not its number.
            payload={"shop": shop_id.hex, "months": months, "amount": amount, "card": paid_to},
            now=self._now(),
            expires_at=self._now() + PENDING_LIFETIME,
        )
        asked = say(lang, "ask_sub_receipt", shop=name, months=months, amount=money(lang, amount))
        if paid_to is not None:
            asked += "\n" + say(lang, "receipt_card", card=paid_to)
        await replies.show(asked, [[(say(lang, "cancel"), callback("srx"))]])

    async def _sub_receipt_send(
        self,
        session: PlatformSession,
        incoming: Incoming,
        replies: Replies,
        payload: dict[str, Any],
        content: bytes | None,
    ) -> None:
        lang = incoming.lang
        cancel: Keyboard = [[(say(lang, "cancel"), callback("srx"))]]
        shop_id = _uuid(str(payload.get("shop", "")))
        mine = {shop.shop_id: shop for shop in await session.my_memberships(incoming.user_id)}
        amount, months, card = payload.get("amount"), payload.get("months"), payload.get("card")
        if shop_id is None or shop_id not in mine or not isinstance(amount, int) or not isinstance(months, int):
            await session.drop_pending(incoming.user_id, "sub_receipt")
            await replies.send(say(lang, "expired"))
            return
        try:
            # A file that could not be had, or is too large, is refused like any other that is no receipt.
            await self._receipts.submit(
                incoming.user_id,
                shop_id,
                amount,
                months,
                content,
                update_key=incoming.key,
                # The card chosen under /obuna. A question asked before a card could be chosen has none.
                paid_to=card if isinstance(card, str) else None,
            )
        except AppError as error:
            if isinstance(error, ValidationFailed) and "receipt" in error.fields:
                await replies.send(say(lang, "sub_receipt_invalid"), cancel)
                return
            if error.code != "FILE_STORE_UNAVAILABLE":
                await session.drop_pending(incoming.user_id, "sub_receipt")
            await replies.send(self._error_text(lang, error))
            return
        await session.drop_pending(incoming.user_id, "sub_receipt")
        await replies.send(
            say(lang, "sub_receipt_sent", shop=mine[shop_id].name, months=months, amount=money(lang, amount))
        )

    # --- an administrator decides a subscription receipt from their private chat (REQ-055) -------------

    def on_allow_list(self, tg_id: int) -> bool:
        """Whether the person is on the platform administrators' allow-list."""
        return tg_id in self._reviewers

    @staticmethod
    async def is_review_group(session: PlatformSession, chat_id: int) -> bool:
        group = platform_settings.effective(REVIEW_GROUP, await session.platform_setting(REVIEW_GROUP))
        return isinstance(group, int) and not isinstance(group, bool) and group == chat_id

    async def _reviewer(self, incoming: Incoming) -> bool | None:
        """Whether the person may decide receipts here (ADR-017).

        True: on the allow-list, with an active and confirmed administrator account, and holding an admin
        session that is still valid, which is the proof that they passed the second factor within its
        lifetime. False: an administrator who lacks only that. None: anyone else, to whom these buttons
        are no buttons at all.

        The account and the session are read through the administrators' database role, and only for
        someone on the allow-list: the role the rest of the chat uses cannot read either table.
        """
        if self._admin_storage is None or incoming.chat_id not in self._reviewers:
            return None
        async with self._admin_storage.platform() as admin_session:
            account = await admin_session.admin_account(incoming.user_id, for_update=False)
            if account is None or account.status != "active":
                return None
            return account.confirmed and await admin_session.admin_has_live_session(incoming.user_id, self._now())

    async def awaited_group_reason(self, session: PlatformSession, user_id: UUID) -> int | None:
        """The review group in which this person pressed "reject" and has not yet written the reason.

        Their next message decides a receipt, so Telegram is asked again, then, whether they still
        administer that group. None when no such question is open or the group is no longer the review group.
        """
        asked = await session.current_pending(user_id, "receipt_reject", self._now())
        group = None if asked is None else self._group_of(asked[1])
        if group is None or not await self.is_review_group(session, group[0]):
            return None
        return group[0]

    @staticmethod
    def _group_of(payload: dict[str, Any]) -> tuple[int, int] | None:
        """The review group's chat and the announcement's message, as a rejection question remembers them."""
        group = payload.get("group")
        if (
            isinstance(group, list)
            and len(group) == 2
            and all(isinstance(part, int) and not isinstance(part, bool) for part in group)
        ):
            return group[0], group[1]
        return None

    async def _group_administrator(self, session: PlatformSession, incoming: Incoming, group_id: int) -> bool:
        """Whether the person decides here as a Telegram administrator of the review group (DEC-064).

        True only when Telegram said so while this update was being handled, about this very chat, and
        the chat is the configured review group. Nothing the button carries is believed.
        """
        return incoming.administers == group_id and await self.is_review_group(session, group_id)

    async def _receipt_pressed(
        self, session: PlatformSession, incoming: Incoming, replies: Replies, action: str, receipt_hex: str
    ) -> None:
        lang = incoming.lang
        receipt_id = _uuid(receipt_hex)
        allowed = await self._reviewer(incoming)
        if (
            receipt_id is not None
            and allowed is not True
            and incoming.group is not None
            and await self._group_administrator(session, incoming, incoming.group[0])
        ):
            # Not a platform administrator with a live session, but a Telegram administrator of the
            # review group pressing in that group: the one thing such a person may do.
            await self._group_receipt_pressed(session, incoming, replies, action, receipt_id)
            return
        if receipt_id is None or allowed is None:
            # Exactly what an unknown button gets; nothing about receipts is said.
            await replies.buttons(None)
            return
        if not allowed:
            await replies.send(say(lang, "a_sign_in_first"))
            return
        try:
            if action == "srj":
                await self._admin_receipts.require_waiting(incoming.user_id, receipt_id)
                await session.drop_pending(incoming.user_id, "receipt_reject")
                await session.put_pending(
                    pending_id=self._pending_id(incoming),
                    user_id=incoming.user_id,
                    kind="receipt_reject",
                    # The message the button was on: its buttons go once the receipt is rejected.
                    payload={
                        "receipt": receipt_id.hex,
                        "message": incoming.message_id,
                        "group": None if incoming.group is None else list(incoming.group),
                    },
                    now=self._now(),
                    expires_at=self._now() + PENDING_LIFETIME,
                )
                await replies.send(say(lang, "ask_receipt_reject_reason"), [[(say(lang, "cancel"), callback("srn"))]])
                return
            # The months the owner stated; correcting them is done in the panel.
            body = await self._admin_receipts.approve(
                incoming.user_id, receipt_id, None, None, None, update_key=incoming.key, via=DECIDED_IN_CHAT
            )
        except ReceiptAlreadyDecided:
            await replies.show(say(lang, "a_receipt_decided"))
            if incoming.group is not None:
                await replies.close_in_group(*incoming.group, say(GROUP_LANG, "a_receipt_decided"))
            return
        except ValidationFailed:
            await replies.send(say(lang, "a_receipt_use_panel"))
            return
        except AppError as error:
            await replies.show(self._error_text(lang, error))
            return
        # The message with the buttons is replaced: this administrator's copy has none any more.
        paid_through = day(date.fromisoformat(body["subscription"]["paid_through"]))
        await replies.show(
            say(lang, "a_receipt_approved", shop=body["shop_name"], months=body["months"], date=paid_through)
        )
        if incoming.group is not None:
            await replies.close_in_group(
                *incoming.group,
                say(GROUP_LANG, "a_receipt_approved", shop=body["shop_name"], months=body["months"], date=paid_through),
            )

    async def _group_receipt_pressed(
        self, session: PlatformSession, incoming: Incoming, replies: Replies, action: str, receipt_id: UUID
    ) -> None:
        """A Telegram administrator of the review group pressed approve or reject there (DEC-064).

        The group sees the outcome on the announcement. The person is answered over the button, because
        the bot cannot write to someone who never started it; only the reason for a rejection is asked
        for in their own chat with the bot, where their next message is read.
        """
        assert incoming.group is not None
        lang = incoming.lang
        group_id, announcement = incoming.group
        try:
            if action == "srj":
                await self._group_receipts.require_waiting(session, group_id, receipt_id)
                await session.drop_pending(incoming.user_id, "receipt_reject")
                await session.put_pending(
                    pending_id=self._pending_id(incoming),
                    user_id=incoming.user_id,
                    kind="receipt_reject",
                    payload={"receipt": receipt_id.hex, "message": None, "group": [group_id, announcement]},
                    now=self._now(),
                    expires_at=self._now() + PENDING_LIFETIME,
                )
                await replies.send(say(lang, "ask_receipt_reject_reason"), [[(say(lang, "cancel"), callback("srn"))]])
                replies.alert(say(lang, "g_reason_in_private"))
                return
            body = await self._group_receipts.approve(
                session, group_id=group_id, decider_tg=incoming.chat_id, receipt_id=receipt_id
            )
        except ReceiptAlreadyDecided:
            replies.alert(say(lang, "a_receipt_decided"))
            await replies.close_in_group(group_id, announcement, say(GROUP_LANG, "a_receipt_decided"))
            return
        except ValidationFailed:
            # The owner stated no months, and they cannot be entered from the group.
            replies.alert(say(lang, "g_receipt_needs_panel"))
            return
        except AppError as error:
            replies.alert(self._error_text(lang, error))
            return
        paid_through = day(date.fromisoformat(body["subscription"]["paid_through"]))
        await replies.close_in_group(
            group_id,
            announcement,
            say(GROUP_LANG, "a_receipt_approved", shop=body["shop_name"], months=body["months"], date=paid_through),
        )

    async def _receipt_reject_reason(
        self, session: PlatformSession, incoming: Incoming, replies: Replies, payload: dict[str, Any], text: str
    ) -> None:
        """An administrator's next message after pressing "reject" is the reason told to the owner."""
        lang = incoming.lang
        receipt_id = _uuid(str(payload.get("receipt", "")))
        allowed = await self._reviewer(incoming)
        pressed_in = self._group_of(payload)
        if (
            receipt_id is not None
            and allowed is not True
            and pressed_in is not None
            and await self._group_administrator(session, incoming, pressed_in[0])
        ):
            await self._group_reject_reason(session, incoming, replies, receipt_id, pressed_in, text)
            return
        if receipt_id is None or allowed is None:
            await session.drop_pending(incoming.user_id, "receipt_reject")
            await replies.send(say(lang, "expired"))
            return
        if not allowed:
            # The session ran out while the reason was being written. The question stays open.
            await replies.send(say(lang, "a_sign_in_first"))
            return
        reason = clean_receipt_reason(text)
        if reason is None:
            await replies.send(say(lang, "a_reason_invalid"), [[(say(lang, "cancel"), callback("srn"))]])
            return
        await session.drop_pending(incoming.user_id, "receipt_reject")
        try:
            body = await self._admin_receipts.reject(
                incoming.user_id, receipt_id, reason, None, update_key=incoming.key, via=DECIDED_IN_CHAT
            )
        except ReceiptAlreadyDecided:
            await replies.send(say(lang, "a_receipt_decided"))
            return
        except AppError as error:
            await replies.send(self._error_text(lang, error))
            return
        await replies.send(say(lang, "a_receipt_rejected", shop=body["shop_name"], reason=reason))
        group = payload.get("group")
        if isinstance(group, list) and len(group) == 2 and all(isinstance(part, int) for part in group):
            await replies.close_in_group(
                group[0], group[1], say(GROUP_LANG, "a_receipt_rejected", shop=body["shop_name"], reason=reason)
            )
        announced = payload.get("message")
        if isinstance(announced, int) and not isinstance(announced, bool):
            await replies.strip(announced)

    async def _group_reject_reason(
        self,
        session: PlatformSession,
        incoming: Incoming,
        replies: Replies,
        receipt_id: UUID,
        pressed_in: tuple[int, int],
        text: str,
    ) -> None:
        """The reason a Telegram administrator of the review group writes after pressing "reject" there.

        Telegram was asked again, for this message, whether they still administer the group.
        """
        lang = incoming.lang
        reason = clean_receipt_reason(text)
        if reason is None:
            await replies.send(say(lang, "a_reason_invalid"), [[(say(lang, "cancel"), callback("srn"))]])
            return
        await session.drop_pending(incoming.user_id, "receipt_reject")
        group_id, announcement = pressed_in
        try:
            body = await self._group_receipts.reject(
                session, group_id=group_id, decider_tg=incoming.chat_id, receipt_id=receipt_id, reason=reason
            )
        except ReceiptAlreadyDecided:
            await replies.send(say(lang, "a_receipt_decided"))
            return
        except AppError as error:
            await replies.send(self._error_text(lang, error))
            return
        await replies.send(say(lang, "a_receipt_rejected", shop=body["shop_name"], reason=reason))
        await replies.close_in_group(
            group_id, announcement, say(GROUP_LANG, "a_receipt_rejected", shop=body["shop_name"], reason=reason)
        )

    async def _shop_of_notice(self, shops: list[MyShop], notice_id: UUID) -> MyShop | None:
        """Which of the caller's shops holds the notice. Only a lookup, like `_shop_of_entry`."""
        for shop in shops:
            async with self._storage.tenant(shop.shop_id) as tenant:
                if await tenant.get_payment_notice(notice_id) is not None:
                    return shop
        return None

    async def _notice_accept(
        self, session: PlatformSession, incoming: Incoming, replies: Replies, notice_id: UUID
    ) -> None:
        """A staff member accepts a notice from its message: a payment of the stated amount (BR-14)."""
        lang = incoming.lang
        shop = await self._shop_of_notice(await session.my_memberships(incoming.user_id), notice_id)
        if shop is None:
            await replies.show(say(lang, "not_found"))
            return
        try:
            async with self._storage.tenant(shop.shop_id) as tenant:
                actor = await require_member(tenant, incoming.user_id, ACCEPT_NOTICE)
                await require_writable(tenant, self._today(), new_credit=False)

                async def apply() -> dict[str, Any]:
                    return await accept_notice_in(tenant, actor, notice_id, None, self._now())

                body = await idempotency.run_once(
                    tenant,
                    key=incoming.key,
                    operation="chat.payment_notice.accept",
                    user_id=incoming.user_id,
                    request={"notice": str(notice_id)},
                    action=apply,
                )
        except AppError as error:
            await replies.send(self._error_text(lang, error))
            if error.code == "PAYMENT_NOTICE_NOT_OPEN":
                # Nothing is left to decide. After any other refusal the notice can still be declined.
                await replies.buttons(None)
            return
        await replies.show(
            say(
                lang,
                "notice_accepted_staff",
                shop=shop.name,
                name=body["customer"]["display_name"],
                amount=money(lang, body["entry"]["amount"]),
                balance=money(lang, body["customer"]["balance"]),
            )
        )

    async def _notice_decline_reason(
        self, session: PlatformSession, incoming: Incoming, replies: Replies, payload: dict[str, Any], text: str
    ) -> None:
        """A staff member's next message after pressing "decline" is the reason given to the customer."""
        lang = incoming.lang
        notice_id = _uuid(str(payload.get("notice", "")))
        await session.drop_pending(incoming.user_id, "notice_decline")
        reason = clean_reason(text)
        if notice_id is None:
            await replies.send(say(lang, "expired"))
            return
        if reason is None:
            await replies.send(say(lang, "reason_invalid"))
            return
        shop = await self._shop_of_notice(await session.my_memberships(incoming.user_id), notice_id)
        if shop is None:
            await replies.send(say(lang, "not_found"))
            return
        try:
            async with self._storage.tenant(shop.shop_id) as tenant:
                actor = await require_member(tenant, incoming.user_id, DECLINE_NOTICE)
                await require_writable(tenant, self._today(), new_credit=False)

                async def apply() -> dict[str, Any]:
                    return await decline_notice_in(tenant, actor, notice_id, reason, self._now())

                await idempotency.run_once(
                    tenant,
                    key=incoming.key,
                    operation="chat.payment_notice.decline",
                    user_id=incoming.user_id,
                    request={"notice": str(notice_id), "reason": reason},
                    action=apply,
                )
        except AppError as error:
            await replies.send(self._error_text(lang, error))
            return
        await replies.send(say(lang, "notice_declined_staff"))

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
            body = await self._shops.create(incoming.user_id, name, incoming.lang, incoming.new_shop_key)
        except ValidationFailed:
            await replies.send(say(incoming.lang, "shop_name_invalid"))
            return
        await session.drop_pending(incoming.user_id, "shop_name")
        await session.set_active_shop(incoming.user_id, UUID(body["id"]))
        # A person's later shops start without a trial, and a shop without one cannot record credit yet:
        # saying "you can write debts now" would be untrue.
        opened = "shop_created_limited" if body.get("subscription_state") == "limited" else "shop_created"
        await replies.send(say(incoming.lang, opened, shop=body["name"]))

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
        if isinstance(error, ForbiddenPermission):
            return say(lang, "forbidden_permission")
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

    async def _may_cancel(self, shop: MyShop, user_id: UUID) -> bool:
        """Whether to offer the button that reverses an entry: asked of the shop now, like the press itself."""
        async with self._storage.tenant(shop.shop_id) as tenant:
            member = await tenant.active_membership(user_id)
            return member is not None and may(member, permissions.ENTRIES_CANCEL)

    def _saved(self, lang: str, shop: MyShop, body: dict[str, Any], may_cancel: bool) -> tuple[str, Keyboard]:
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
        if may_cancel:
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
        require_kind(actor, kind)
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
            text, keyboard = self._saved(lang, shop, saved, await self._may_cancel(shop, incoming.user_id))
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
        shop = shops[shop_id]
        text, keyboard = self._saved(lang, shop, saved, await self._may_cancel(shop, incoming.user_id))
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
                may_cancel = await self._may_cancel(shop, incoming.user_id)
                await replies.buttons(self._reverse_only(lang, may_cancel, entry_id))
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
        may_cancel = await self._may_cancel(shop, incoming.user_id)
        await replies.show(text, self._reverse_only(lang, may_cancel, entry_id))

    @staticmethod
    def _reverse_only(lang: str, may_cancel: bool, entry_id: UUID) -> Keyboard:
        if not may_cancel:
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

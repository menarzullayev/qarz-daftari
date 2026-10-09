"""Handling of incoming Telegram updates (ADR-006).

An update is processed at most once: its identifier is recorded in the same transaction as its replies, so
a redelivery after a crash is processed, and a redelivery after success is ignored. Replies are never sent
from here; they are queued in the outbox inside the same transaction (ADR-007). What the update means is
decided by `ChatService`.
"""

import time
from dataclasses import dataclass
from typing import Any

from qarz.application.chat import ChatService, Incoming, Replies
from qarz.application.chat_texts import say
from qarz.application.ports import Storage, TelegramChatMembers, TelegramFiles
from qarz.domain import languages
from qarz.domain.files import MAX_FILE_BYTES
from qarz.domain.subscription_receipts import decides_in_review_group


def _language(stored: str | None, telegram_code: str | None) -> str:
    """The person's own choice when there is one; otherwise what Telegram's interface language suggests."""
    if stored is not None and languages.is_language(stored):
        return stored
    return languages.from_telegram(telegram_code)


def _is_id(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _sent_file(message: dict[str, Any]) -> str | None:
    """Identifier of the photo or document in a message. Of a photo, the largest size the service accepts."""
    photo = message.get("photo")
    if isinstance(photo, list):
        sizes = [size for size in photo if isinstance(size, dict) and isinstance(size.get("file_id"), str)]
        fitting = [size for size in sizes if _is_id(size.get("file_size")) and size["file_size"] <= MAX_FILE_BYTES]
        chosen = max(fitting, key=lambda size: int(size["file_size"]), default=sizes[-1] if sizes else None)
        return None if chosen is None else str(chosen["file_id"])
    document = message.get("document")
    if isinstance(document, dict) and isinstance(document.get("file_id"), str):
        return str(document["file_id"])
    return None


@dataclass(frozen=True)
class Processed:
    fresh: bool  # False when the update was a duplicate and nothing was done
    # A Bot API call to return as the body of the webhook response; used to stop a button's spinner at once.
    webhook_reply: dict[str, Any] | None = None


# The buttons that may be pressed outside a private chat: the decisions on a subscription receipt.
GROUP_BUTTONS = ("v2:sra:", "v2:srj:")


class UpdateProcessor:
    def __init__(
        self,
        storage: Storage,
        chat: ChatService,
        files: TelegramFiles | None = None,
        members: TelegramChatMembers | None = None,
    ) -> None:
        self._storage = storage
        self._chat = chat
        self._files = files
        # Without it nobody is a review-group administrator: only platform administrators decide there.
        self._members = members

    async def _administers(self, group_id: int, person: int) -> bool:
        """Whether Telegram says, now, that the person is the creator or an administrator of the group.

        Asked outside any transaction. No way to ask, no answer, or any other answer: no.
        """
        if self._members is None:
            return False
        return decides_in_review_group(await self._members.status(group_id, person))

    async def process(self, update: dict[str, Any]) -> bool:
        """Process one update. Returns False when it was a duplicate and nothing was done."""
        return (await self.handle(update)).fresh

    async def handle(self, update: dict[str, Any]) -> Processed:
        received = time.perf_counter()
        update_id = update.get("update_id")
        if not _is_id(update_id):
            raise ValueError("update has no integer update_id")
        assert isinstance(update_id, int)

        pressed = update.get("callback_query")
        answer: dict[str, Any] | None = None
        if isinstance(pressed, dict) and isinstance(pressed.get("id"), str):
            # Answered even for a duplicate: it has no effect beyond the spinner.
            answer = {"method": "answerCallbackQuery", "callback_query_id": pressed["id"]}

        if isinstance(pressed, dict):
            message = pressed.get("message")
            data = pressed.get("data")
            sender = pressed.get("from") or {}
        else:
            message = update.get("message")
            data = None
            sender = (message.get("from") or {}) if isinstance(message, dict) else {}
        chat = (message.get("chat") or {}) if isinstance(message, dict) else {}
        sender_id = sender.get("id")
        # Only private chats are served, and in a private chat the chat is the person. Groups, channels
        # and anything else are recorded as seen and ignored.
        served = (
            isinstance(message, dict)
            and chat.get("type") == "private"
            and isinstance(sender_id, int)
            and not isinstance(sender_id, bool)
            and sender_id > 0
            and sender_id == chat.get("id")
        )

        # One exception: a button of the receipt announcement pressed in a group, by someone on the
        # administrators' allow-list or by a Telegram administrator of the review group (DEC-064).
        # Telegram is asked about the presser only once the group is known to be the review group, and
        # only about that group; nothing in the button's data is believed. Whether the person may
        # decide is settled when the press is handled. Nobody else is looked at, so pressing a button
        # in a group makes nobody else a user of the service.
        in_group = (
            not served
            and isinstance(pressed, dict)
            and isinstance(message, dict)
            and chat.get("type") in ("group", "supergroup")
            and isinstance(data, str)
            and data.startswith(GROUP_BUTTONS)
            and isinstance(sender_id, int)
            and not isinstance(sender_id, bool)
            and sender_id > 0
        )
        # The chat Telegram confirmed, for this update, that the person administers.
        administers: int | None = None
        group_press = False
        if in_group:
            assert isinstance(sender_id, int)
            pressed_in = chat.get("id")
            if self._members is not None and isinstance(pressed_in, int) and not isinstance(pressed_in, bool):
                async with self._storage.platform() as session:
                    if await session.update_seen(update_id):
                        return Processed(False, answer)
                    in_review_group = await self._chat.is_review_group(session, pressed_in)
                if in_review_group and await self._administers(pressed_in, sender_id):
                    administers = pressed_in
            group_press = administers is not None or self._chat.on_allow_list(sender_id)

        person: int | None = sender_id if (served or group_press) and isinstance(sender_id, int) else None

        sent_file = _sent_file(message) if isinstance(message, dict) and not isinstance(pressed, dict) else None
        wanted = False
        content: bytes | None = None
        user_id = None
        reason_for: int | None = None
        if person is not None:
            # Writing to the bot is how a person first becomes a user of the service (sign-in through
            # chat). This is committed on its own, before the update is claimed: the shop transactions
            # opened while handling the update refer to the user, and must not wait on an uncommitted row.
            async with self._storage.platform() as session:
                if await session.update_seen(update_id):
                    return Processed(False, answer)
                user_id = await session.ensure_user(person, _language(None, sender.get("language_code")))
                wanted = sent_file is not None and await self._chat.awaits_receipt(session, user_id)
                if served and not isinstance(pressed, dict) and self._members is not None:
                    # A message that may be the reason for rejecting a receipt from the review group.
                    reason_for = await self._chat.awaited_group_reason(session, user_id)
            if wanted and sent_file is not None and self._files is not None:
                # Fetched between the two transactions: none is open while Telegram is asked for the
                # file, and a file nobody asked for is never downloaded at all.
                content = await self._files.fetch(sent_file, MAX_FILE_BYTES)
            if reason_for is not None and await self._administers(reason_for, person):
                # Asked again for the message that decides, not remembered from the press.
                administers = reason_for

        async with self._storage.platform() as session:
            if not await session.claim_update(update_id):
                return Processed(False, answer)
            if user_id is None or person is None or not isinstance(message, dict):
                return Processed(True, answer)

            lang = _language(await session.user_language(user_id), sender.get("language_code"))
            names = [sender.get(part) for part in ("first_name", "last_name")]
            profile_name = " ".join(" ".join(str(n) for n in names if isinstance(n, str)).split())[:80] or None
            message_id = message.get("message_id")
            if isinstance(pressed, dict):
                group: tuple[int, int] | None = None
                if group_press:
                    group_id = chat.get("id")
                    if (
                        not isinstance(group_id, int)
                        or isinstance(group_id, bool)
                        or not _is_id(message_id)
                        or not await self._chat.is_review_group(session, group_id)
                    ):
                        return Processed(True, answer)
                    assert isinstance(message_id, int)
                    group = (group_id, message_id)
                incoming = Incoming(
                    update_id,
                    person,
                    user_id,
                    lang,
                    # In the group the presser is answered in their own chat, where that message is not.
                    message_id if group is None and _is_id(message_id) else None,
                    profile_name,
                    None,
                    group,
                    administers,
                    # An announcement sent with the receipt's file is edited as a caption.
                    media=bool(message.get("photo")) or isinstance(message.get("document"), dict),
                )
                if isinstance(data, str):
                    replies = Replies(session, incoming)
                    await self._chat.handle_callback(session, incoming, data, replies)
                    if replies.notice is not None and answer is not None:
                        # Said over the button itself, to someone the bot may be unable to write to.
                        answer = {**answer, "text": replies.notice, "show_alert": True}
                return Processed(True, answer)

            incoming = Incoming(update_id, person, user_id, lang, None, profile_name, received, None, administers)
            text = message.get("text")
            if isinstance(text, str) and text.strip():
                await self._chat.handle_text(session, incoming, text)
            elif sent_file is not None:
                await self._chat.handle_file(session, incoming, content)
            else:
                await Replies(session, incoming).send(say(lang, "only_text"))
            return Processed(True, answer)

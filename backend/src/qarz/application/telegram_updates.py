"""Handling of incoming Telegram updates (ADR-006).

An update is processed at most once: its identifier is recorded in the same transaction as its replies, so
a redelivery after a crash is processed, and a redelivery after success is ignored. Replies are never sent
from here; they are queued in the outbox inside the same transaction (ADR-007). What the update means is
decided by `ChatService`.
"""

from dataclasses import dataclass
from typing import Any

from qarz.application.chat import ChatService, Incoming, Replies
from qarz.application.chat_texts import CATALOGS, say
from qarz.application.ports import Storage


def _language(stored: str | None, telegram_code: str | None) -> str:
    if stored in CATALOGS:
        return stored
    return "ru" if (telegram_code or "").lower().startswith("ru") else "uz"


def _is_id(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


@dataclass(frozen=True)
class Processed:
    fresh: bool  # False when the update was a duplicate and nothing was done
    # A Bot API call to return as the body of the webhook response; used to stop a button's spinner at once.
    webhook_reply: dict[str, Any] | None = None


class UpdateProcessor:
    def __init__(self, storage: Storage, chat: ChatService) -> None:
        self._storage = storage
        self._chat = chat

    async def process(self, update: dict[str, Any]) -> bool:
        """Process one update. Returns False when it was a duplicate and nothing was done."""
        return (await self.handle(update)).fresh

    async def handle(self, update: dict[str, Any]) -> Processed:
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

        person: int | None = sender_id if served and isinstance(sender_id, int) else None

        user_id = None
        if person is not None:
            # Writing to the bot is how a person first becomes a user of the service (sign-in through
            # chat). This is committed on its own, before the update is claimed: the shop transactions
            # opened while handling the update refer to the user, and must not wait on an uncommitted row.
            async with self._storage.platform() as session:
                if await session.update_seen(update_id):
                    return Processed(False, answer)
                user_id = await session.ensure_user(person, _language(None, sender.get("language_code")))

        async with self._storage.platform() as session:
            if not await session.claim_update(update_id):
                return Processed(False, answer)
            if user_id is None or person is None or not isinstance(message, dict):
                return Processed(True, answer)

            lang = _language(await session.user_language(user_id), sender.get("language_code"))
            message_id = message.get("message_id")
            if isinstance(pressed, dict):
                incoming = Incoming(update_id, person, user_id, lang, message_id if _is_id(message_id) else None)
                if isinstance(data, str):
                    await self._chat.handle_callback(session, incoming, data)
                return Processed(True, answer)

            incoming = Incoming(update_id, person, user_id, lang)
            text = message.get("text")
            if isinstance(text, str) and text.strip():
                await self._chat.handle_text(session, incoming, text)
            else:
                await Replies(session, incoming).send(say(lang, "only_text"))
            return Processed(True, answer)

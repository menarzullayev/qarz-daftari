"""Handling of incoming Telegram updates (ADR-006).

An update is processed at most once: its identifier is recorded in the same transaction as its effects, so
a redelivery after a crash is processed, and a redelivery after success is ignored. Replies are never sent
from here; they are queued in the outbox inside the same transaction (ADR-007).
"""

from typing import Any

from qarz.application.ports import Storage

_HELP = {
    "uz": (
        "Qarz Daftari: do'kondagi nasiya hisobi.\n"
        "Xizmat hozircha ishlab chiqilmoqda. Tez orada shu yerda nasiya yozish va qarzni ko'rish mumkin bo'ladi."
    ),
    "ru": (
        "Qarz Daftari: учёт продаж в долг в магазине.\n"
        "Сервис пока в разработке. Скоро здесь можно будет записывать долги и видеть свой долг."
    ),
}


def _language(stored: str | None, telegram_code: str | None) -> str:
    if stored in _HELP:
        return stored
    return "ru" if (telegram_code or "").lower().startswith("ru") else "uz"


class UpdateProcessor:
    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    async def process(self, update: dict[str, Any]) -> bool:
        """Process one update. Returns False when it was a duplicate and nothing was done."""
        update_id = update.get("update_id")
        if not isinstance(update_id, int) or isinstance(update_id, bool):
            raise ValueError("update has no integer update_id")

        async with self._storage.platform() as session:
            if not await session.claim_update(update_id):
                return False

            message = update.get("message")
            if not isinstance(message, dict):
                return True
            chat = message.get("chat") or {}
            sender = message.get("from") or {}
            # Only private chats are served; groups and channels are ignored.
            if chat.get("type") != "private" or not isinstance(chat.get("id"), int):
                return True

            # Writing to the bot is how a person first becomes a user of the service (sign-in through chat).
            sender_id = sender.get("id")
            if isinstance(sender_id, int) and not isinstance(sender_id, bool) and sender_id > 0:
                user_id = await session.ensure_user(sender_id, _language(None, sender.get("language_code")))
                stored = await session.user_language(user_id)
            else:
                stored = None
            lang = _language(stored, sender.get("language_code"))
            await session.enqueue(
                channel="telegram",
                recipient=str(chat["id"]),
                payload={"text": _HELP[lang]},
                dedupe_key=f"update:{update_id}:reply",
            )
            return True

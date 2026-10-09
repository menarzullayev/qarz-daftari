"""The receipt's file beside its announcement (REQ-055): whoever decides sees what they approve.

The announcement of a waiting subscription receipt is queued as text with a reference to the file
(`ATTACHMENT`): the shop, the file and the chat it was written for. The worker turns that into a photo or
a document carrying the same words and the same buttons. The file never leaves any other way: there is no
link in the message, and the reference is honoured only

- for the chat named in it, which must be the chat the message is addressed to;
- when that chat is, at the moment of sending, the configured review group or a platform administrator
  on the allow-list;
- for a file of that shop kept as a subscription receipt whose retention has not run out.

Anything else, and any failure to read or to send the file, leaves today's text announcement, with a
line that says the file is not attached. An announcement is never held back by its file.
"""

import logging
from collections.abc import Callable, Container
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from qarz.application.errors import AppError
from qarz.application.files import FileService
from qarz.application.ports import Sender, SendFailed, SendRejected, Storage
from qarz.domain import platform_settings

log = logging.getLogger("qarz.receipt_attachment")

ATTACHMENT = "receipt_file"  # the key of the reference in an outbox payload
FILE_PURPOSE = "subscription_receipt"
REVIEW_GROUP = "review_group"
CAPTION_MAX = 1024  # Bot API: the words under a photo or a document; a text message may have 4096
SEND_PHOTO, SEND_DOCUMENT = "sendPhoto", "sendDocument"


def caption_fits(text: str) -> bool:
    """Whether Telegram takes the text as a caption. It counts UTF-16 code units."""
    return len(text.encode("utf-16-le")) // 2 <= CAPTION_MAX


def reference(shop_id: UUID, file_id: UUID, recipient: str, note: str) -> dict[str, str]:
    """What an announcement carries instead of the file. `note` is the line added, in the reader's
    language, when the file cannot be attached."""
    return {"shop": str(shop_id), "file": str(file_id), "to": recipient, "note": note}


def _chat(recipient: str) -> int | None:
    digits = recipient[1:] if recipient.startswith("-") else recipient
    return int(recipient) if digits.isascii() and digits.isdigit() else None


class ReceiptAttachingSender:
    """Sends an announcement that refers to a receipt's file as that file with the announcement under it.

    Every other message goes to the inner sender untouched.
    """

    def __init__(
        self,
        inner: Sender,
        storage: Storage,
        files: FileService,
        *,
        admin_tg_ids: Container[int] = (),
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._inner = inner
        self._storage = storage
        self._files = files
        self._admins = admin_tg_ids
        self._now = now or (lambda: datetime.now(UTC))

    async def _may_see(self, recipient: str) -> bool:
        """Whether the chat is, now, a platform administrator's or the review group."""
        chat = _chat(recipient)
        if chat is None:
            return False
        if chat in self._admins:
            return True
        async with self._storage.platform() as session:
            group = platform_settings.effective(REVIEW_GROUP, await session.platform_setting(REVIEW_GROUP))
        return isinstance(group, int) and not isinstance(group, bool) and group == chat

    async def _file(self, channel: str, recipient: str, ref: object) -> dict[str, Any] | None:
        """The file to attach, or None when it may not or cannot be attached."""
        if channel != "telegram" or not isinstance(ref, dict) or ref.get("to") != recipient:
            return None
        try:
            shop_id, file_id = UUID(str(ref.get("shop"))), UUID(str(ref.get("file")))
        except ValueError:
            return None
        try:
            if not await self._may_see(recipient):
                return None
            mime, name, content = await self._files.read(shop_id, file_id, purpose=FILE_PURPOSE, now=self._now())
        except AppError:
            # Deleted, past its retention, or the file store does not answer.
            return None
        except Exception as error:
            # Never the reference or the content: only what kind of failure it was.
            log.warning("receipt_attachment_failed", extra={"error": type(error).__name__})
            return None
        return {"name": name, "mime": mime, "content": content}

    async def _with_file(
        self, channel: str, recipient: str, plain: dict[str, Any], text: str, file: dict[str, Any]
    ) -> bool:
        """Send the file with the announcement. False when Telegram took the file in no form."""
        fits = caption_fits(text)
        rest = {key: value for key, value in plain.items() if key != "text"}
        # A picture Telegram does not take as a photo (its sides, its weight) may still go as a document.
        methods = (SEND_PHOTO, SEND_DOCUMENT) if str(file["mime"]).startswith("image/") else (SEND_DOCUMENT,)
        for method in methods:
            media: dict[str, Any] = {"method": method, "file": {"name": file["name"], "content": file["content"]}}
            try:
                # Words too long for a caption: the file goes alone and the announcement follows it as
                # the text message it is today, with the buttons.
                await self._inner.send(channel, recipient, {**rest, **media, "caption": text} if fits else media)
            except (SendFailed, SendRejected):
                continue
            if not fits:
                await self._inner.send(channel, recipient, plain)
            return True
        return False

    async def send(self, channel: str, recipient: str, payload: dict[str, Any]) -> None:
        if ATTACHMENT not in payload:
            await self._inner.send(channel, recipient, payload)
            return
        ref = payload[ATTACHMENT]
        plain = {key: value for key, value in payload.items() if key != ATTACHMENT}
        text = plain.get("text")
        if isinstance(text, str):
            file = await self._file(channel, recipient, ref)
            if file is not None and await self._with_file(channel, recipient, plain, text, file):
                return
            note = ref.get("note") if isinstance(ref, dict) else None
            if isinstance(note, str) and note:
                plain["text"] = f"{text}\n{note}"
        await self._inner.send(channel, recipient, plain)

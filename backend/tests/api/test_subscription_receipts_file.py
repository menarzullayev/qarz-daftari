"""The announcement of a waiting subscription receipt carries the receipt's file (REQ-055).

Whoever decides sees what they approve: the review group and each administrator on the allow-list get
the image or the PDF as a Telegram photo or document, with the announcement as its caption and the two
buttons under it. What must stay true:

- the file goes to those chats and to no other, whatever an outbox row says, and never as a link;
- when the file cannot be read or Telegram does not take it, the text announcement of before is sent,
  with a line that says the file is not attached, and it is never held back;
- a decision still closes the announcement: its words are a caption then, edited as one.

Telegram is never called: a fake stands where the Bot API sender is. The database is the real one, read
with the worker's own role, and the file store is the test directory the API wrote the receipt to.
"""

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from qarz.application.chat import Incoming, Replies, _replacing
from qarz.application.chat_texts import CATALOGS, day, say
from qarz.application.files import FileService
from qarz.application.ports import FileStoreError, RecipientBlocked, RetryLater, SendFailed, SendRejected
from qarz.application.receipt_attachment import ATTACHMENT, CAPTION_MAX, ReceiptAttachingSender, caption_fits
from qarz.domain.subscription import add_months
from qarz.infrastructure.db import Database
from qarz.infrastructure.file_store import FilesystemFileStore

from ..receipt_samples import PDF
from .conftest import AdminEnv, FakeChatMembers, World, elevate, make_admin, stored_objects
from .test_chat import SECRET, Chat, _update_ids
from .test_disputes import tg
from .test_subscription_receipts import rows, sent_ok, setting, subscription, unique_image
from .test_subscription_receipts_admin_chat import REASON, press_of, review_group

pytestmark = pytest.mark.db

ANNOUNCEMENT = 7  # the message that carries the file and the buttons
PHOTO = {"photo": [{"file_id": "AgAC-small", "file_size": 900}, {"file_id": "AgAC-large", "file_size": 90_000}]}
DOCUMENT = {"document": {"file_id": "BQAC-pdf", "file_size": 90_000}}


@dataclass
class Telegram:
    """Stands where the Bot API sender is. `refuse` names the methods it fails, and with what."""

    refuse: dict[str, Exception] = field(default_factory=dict)
    tried: list[str] = field(default_factory=list)
    sent: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def send(self, channel: str, recipient: str, payload: dict[str, Any]) -> None:
        assert channel == "telegram"
        method = str(payload.get("method", "sendMessage"))
        self.tried.append(method)
        if method in self.refuse:
            raise self.refuse[method]
        self.sent.append((recipient, payload))

    @property
    def files(self) -> list[tuple[str, bytes]]:
        """Every file that left, and for which chat."""
        return [(recipient, payload["file"]["content"]) for recipient, payload in self.sent if "file" in payload]


class BrokenStore:
    """A file store that does not answer."""

    async def put(self, key: str, data: bytes, mime: str) -> None:
        raise FileStoreError("down")

    async def get(self, key: str) -> bytes:
        raise FileStoreError("down")

    async def delete(self, key: str) -> None:
        raise FileStoreError("down")


@dataclass
class Delivery:
    """The worker's side of one message: its own database role and the store the API wrote to."""

    worker_database_url: str
    file_root: Path
    admin_env: AdminEnv

    def __call__(
        self,
        recipient: str,
        payload: dict[str, Any],
        telegram: Telegram | None = None,
        *,
        store: Any = None,
        now: datetime | None = None,
    ) -> Telegram:
        telegram = telegram or Telegram()

        async def scenario() -> None:
            database = Database(self.worker_database_url)
            try:
                sender = ReceiptAttachingSender(
                    telegram,
                    database,
                    FileService(database, store or FilesystemFileStore(self.file_root)),
                    admin_tg_ids=frozenset(self.admin_env.allowed),
                    now=(lambda: now) if now is not None else None,
                )
                await sender.send("telegram", recipient, payload)
            finally:
                await database.dispose()

        asyncio.run(scenario())
        return telegram


@pytest.fixture
def deliver(worker_database_url: str, file_root: Path, admin_env: AdminEnv) -> Delivery:
    return Delivery(worker_database_url, file_root, admin_env)


def queued(owner: psycopg.Connection, receipt: str) -> dict[str, dict[str, Any]]:
    """The announcements of a receipt as they wait in the outbox, by recipient."""
    return {
        str(recipient): payload
        for recipient, payload in owner.execute(
            "SELECT recipient, payload FROM outbox_message WHERE dedupe_key LIKE %s", (f"subreceipt:{receipt}:new:%",)
        ).fetchall()
    }


def file_of(owner: psycopg.Connection, receipt: str) -> str:
    row = owner.execute("SELECT file_id FROM subscription_receipt WHERE id = %s", (receipt,)).fetchone()
    assert row is not None
    return str(row[0])


@pytest.fixture
def group(owner: psycopg.Connection, world: World, admin_env: AdminEnv) -> int:
    return review_group(owner, world)


@pytest.fixture
def administrator(owner: psycopg.Connection, world: World, admin_env: AdminEnv) -> str:
    """A platform administrator on the allow-list; their private chat with the bot."""
    make_admin(owner, admin_env, world.admin)
    return tg(owner, world.admin)


# --- what is queued ----------------------------------------------------------------------------------------


def test_each_announcement_refers_to_the_file_for_its_own_chat_and_carries_no_link(
    client: TestClient, world: World, owner: psycopg.Connection, group: int, administrator: str
) -> None:
    owner.execute("UPDATE app_user SET lang = 'ru' WHERE id = %s", (world.admin,))
    receipt = sent_ok(client, world.owner_a, world.shop_a, 300_000, 3, unique_image()[0])
    waiting = queued(owner, receipt)
    assert set(waiting) == {administrator, str(group)}, "the shop's own people are sent nothing"
    file_id = file_of(owner, receipt)
    for recipient, lang in ((administrator, "ru"), (str(group), "uz")):
        payload = waiting[recipient]
        assert set(payload) == {"text", "reply_markup", ATTACHMENT}
        assert payload[ATTACHMENT] == {
            "shop": str(world.shop_a),
            "file": file_id,
            "to": recipient,
            "note": say(lang, "a_receipt_no_file"),
        }
        # Neither a link nor the place the file is kept under: only identifiers the worker resolves.
        assert "http" not in str(payload) and "/files" not in str(payload)
    stored = owner.execute("SELECT object_key FROM stored_file WHERE id = %s", (file_id,)).fetchone()
    assert stored is not None and stored[0] not in str(waiting)


@pytest.mark.parametrize("lang", ["uz", "ru", "tg", "kaa", "en"])
def test_the_note_is_written_in_every_language_and_is_one_line(lang: str) -> None:
    note = CATALOGS[lang]["a_receipt_no_file"]  # the catalog's own text, not the Uzbek one in its place
    assert note and "\n" not in note and "{" not in note
    assert say(lang, "a_receipt_no_file") == note
    # Uzbek in Cyrillic is made from the Uzbek text, not typed.
    assert say("uz-Cyrl", "a_receipt_no_file") not in ("", CATALOGS["uz"]["a_receipt_no_file"])


# --- the file leaves with the announcement -------------------------------------------------------------------


def test_the_group_and_an_administrator_get_the_image_with_the_words_and_the_buttons(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    file_root: Path,
    group: int,
    administrator: str,
    deliver: Delivery,
) -> None:
    receipt = sent_ok(client, world.owner_a, world.shop_a, 300_000, 3, unique_image()[0])
    (kept,) = stored_objects(file_root)
    for recipient, payload in queued(owner, receipt).items():
        telegram = deliver(recipient, payload)
        ((to, sent),) = telegram.sent
        assert to == recipient
        assert set(sent) == {"method", "file", "caption", "reply_markup"}, "one message: no text beside it"
        assert sent["method"] == "sendPhoto"
        assert sent["caption"] == payload["text"]
        assert sent["reply_markup"] == payload["reply_markup"]
        assert sent["file"]["content"] == kept.read_bytes()
        assert sent["file"]["name"] == f"receipt-{uuid.UUID(file_of(owner, receipt)).hex[:8]}.jpg"
        assert say("uz", "a_receipt_no_file") not in sent["caption"]


def test_a_pdf_goes_as_a_document(
    client: TestClient, world: World, owner: psycopg.Connection, group: int, deliver: Delivery
) -> None:
    receipt = sent_ok(client, world.owner_a, world.shop_a, 300_000, 3, PDF)
    telegram = deliver(str(group), queued(owner, receipt)[str(group)])
    assert telegram.tried == ["sendDocument"]
    assert telegram.sent[0][1]["file"]["name"].endswith(".pdf")


def test_the_warning_about_a_file_sent_before_stays_in_the_caption(
    client: TestClient, world: World, owner: psycopg.Connection, group: int, deliver: Delivery
) -> None:
    image = unique_image()[0]
    sent_ok(client, world.owner_a, world.shop_a, 300_000, 3, image)
    again = sent_ok(client, world.owner_b, world.shop_b, 300_000, 3, image)
    payload = queued(owner, again)[str(group)]
    warning = say("uz", "a_receipt_copies", count=1)
    assert payload["text"].endswith("\n" + warning)
    ((_, sent),) = deliver(str(group), payload).sent
    assert sent["method"] == "sendPhoto" and sent["caption"].endswith("\n" + warning)


def test_a_message_without_a_reference_is_passed_on_as_it_is(deliver: Delivery) -> None:
    payload = {"text": "Ali: +45 000 so'm", "reply_markup": {"inline_keyboard": []}}
    telegram = deliver("777", payload)
    assert telegram.sent == [("777", payload)] and telegram.sent[0][1] is payload


# --- to nobody else ----------------------------------------------------------------------------------------


def test_the_file_is_never_sent_to_a_chat_that_is_not_the_group_or_an_administrator(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    group: int,
    administrator: str,
    deliver: Delivery,
) -> None:
    """Whatever a row of the outbox says. Each of these gets the words and the note, and no file."""
    receipt = sent_ok(client, world.owner_a, world.shop_a, 300_000, 3, unique_image()[0])
    to_group = queued(owner, receipt)[str(group)]
    note = say("uz", "a_receipt_no_file")

    def readdressed(recipient: str) -> dict[str, Any]:
        return {**to_group, ATTACHMENT: {**to_group[ATTACHMENT], "to": recipient}}

    # The shop's owner, a seller of the shop, a stranger: a reference written for them is not honoured.
    for outsider in (world.owner_a, world.seller_a, world.stranger):
        chat = tg(owner, outsider)
        telegram = deliver(chat, readdressed(chat))
        assert telegram.files == []
        assert telegram.sent == [
            (chat, {"text": f"{to_group['text']}\n{note}", "reply_markup": to_group["reply_markup"]})
        ]

    # An administrator's account alone is not enough: off the allow-list, no file.
    off_the_list = world.owner_b
    make_admin(owner, admin_env, off_the_list)
    admin_env.allowed.discard(int(tg(owner, off_the_list)))
    assert deliver(tg(owner, off_the_list), readdressed(tg(owner, off_the_list))).files == []

    # A reference is good for the chat it names only: the group's row sent to another chat carries no
    # file, even when that chat is an administrator's.
    assert deliver(administrator, to_group).files == []
    assert deliver(tg(owner, world.owner_a), to_group).files == []

    # The group that was the review group when the receipt was sent, and is not any more.
    setting(owner, world.admin, "review_group", group - 1)
    assert deliver(str(group), to_group).files == []
    setting(owner, world.admin, "review_group", group)

    # The positive of all the above: the same row, to the chat it was written for.
    assert len(deliver(str(group), to_group).files) == 1
    assert len(deliver(administrator, readdressed(administrator)).files) == 1


def test_a_reference_reaches_only_a_subscription_receipt_of_the_shop_it_names(
    client: TestClient, world: World, owner: psycopg.Connection, group: int, deliver: Delivery
) -> None:
    receipt = sent_ok(client, world.owner_a, world.shop_a, 300_000, 3, unique_image()[0])
    payload = queued(owner, receipt)[str(group)]
    ref = payload[ATTACHMENT]

    def with_ref(**changed: Any) -> dict[str, Any]:
        return {**payload, ATTACHMENT: {**ref, **changed}}

    # The file under another shop's name does not exist (row-level security), nor does an unknown one.
    assert deliver(str(group), with_ref(shop=str(world.shop_b))).files == []
    assert deliver(str(group), with_ref(file=str(uuid.uuid4()))).files == []
    assert deliver(str(group), with_ref(file="../../etc/passwd")).files == []
    assert deliver(str(group), {**payload, ATTACHMENT: "not a reference"}).files == []
    # A file kept for anything else is not a receipt to show: an export, a customer's payment notice.
    for purpose in ("export", "payment_notice"):
        owner.execute("UPDATE stored_file SET purpose = %s WHERE id = %s", (purpose, ref["file"]))
        assert deliver(str(group), payload).files == []
    owner.execute("UPDATE stored_file SET purpose = 'subscription_receipt' WHERE id = %s", (ref["file"],))
    assert len(deliver(str(group), payload).files) == 1
    # Past its retention it is as good as deleted.
    assert deliver(str(group), payload, now=datetime.now(UTC) + timedelta(days=4 * 366)).files == []


# --- when the file cannot be attached ------------------------------------------------------------------------


def with_note(payload: dict[str, Any], lang: str = "uz") -> dict[str, Any]:
    """The announcement of before, saying that its file is not attached."""
    return {
        "text": f"{payload['text']}\n{say(lang, 'a_receipt_no_file')}",
        "reply_markup": payload["reply_markup"],
    }


def test_a_file_that_cannot_be_read_leaves_the_text_with_the_note(
    client: TestClient, world: World, owner: psycopg.Connection, file_root: Path, group: int, deliver: Delivery
) -> None:
    receipt = sent_ok(client, world.owner_a, world.shop_a, 300_000, 3, unique_image()[0])
    payload = queued(owner, receipt)[str(group)]

    # The file store does not answer.
    telegram = deliver(str(group), payload, store=BrokenStore())
    assert telegram.sent == [(str(group), with_note(payload))] and telegram.tried == ["sendMessage"]

    # The object is not what was recorded: it is not handed out.
    (kept,) = stored_objects(file_root)
    original = kept.read_bytes()
    kept.write_bytes(original + b"x")
    assert deliver(str(group), payload).sent == [(str(group), with_note(payload))]

    # The object is gone.
    kept.unlink()
    assert deliver(str(group), payload).sent == [(str(group), with_note(payload))]

    # And back: the same row carries the file again.
    kept.write_bytes(original)
    assert deliver(str(group), payload).files == [(str(group), original)]


def test_the_note_is_in_the_readers_language(
    client: TestClient, world: World, owner: psycopg.Connection, administrator: str, deliver: Delivery
) -> None:
    owner.execute("UPDATE app_user SET lang = 'ru' WHERE id = %s", (world.admin,))
    receipt = sent_ok(client, world.owner_a, world.shop_a, 300_000, 3, unique_image()[0])
    payload = queued(owner, receipt)[administrator]
    telegram = deliver(administrator, payload, store=BrokenStore())
    assert telegram.sent == [(administrator, with_note(payload, "ru"))]
    assert say("ru", "a_receipt_no_file") != say("uz", "a_receipt_no_file")


@pytest.mark.parametrize("failure", [SendRejected("too large"), SendFailed("TelegramBadRequest")])
def test_when_telegram_does_not_take_the_file_the_text_is_sent_with_the_note(
    client: TestClient, world: World, owner: psycopg.Connection, group: int, deliver: Delivery, failure: Exception
) -> None:
    receipt = sent_ok(client, world.owner_a, world.shop_a, 300_000, 3, unique_image()[0])
    payload = queued(owner, receipt)[str(group)]
    telegram = deliver(str(group), payload, Telegram(refuse={"sendPhoto": failure, "sendDocument": failure}))
    assert telegram.tried == ["sendPhoto", "sendDocument", "sendMessage"]
    assert telegram.sent == [(str(group), with_note(payload))]


def test_a_picture_telegram_refuses_as_a_photo_goes_as_a_document(
    client: TestClient, world: World, owner: psycopg.Connection, group: int, deliver: Delivery
) -> None:
    receipt = sent_ok(client, world.owner_a, world.shop_a, 300_000, 3, unique_image()[0])
    payload = queued(owner, receipt)[str(group)]
    telegram = deliver(str(group), payload, Telegram(refuse={"sendPhoto": SendRejected("PHOTO_INVALID_DIMENSIONS")}))
    ((_, sent),) = telegram.sent
    assert (sent["method"], sent["caption"]) == ("sendDocument", payload["text"])


@pytest.mark.parametrize("asked", [RetryLater(3.0), RecipientBlocked()])
def test_being_told_to_wait_or_being_blocked_is_not_answered_with_another_message(
    client: TestClient, world: World, owner: psycopg.Connection, group: int, deliver: Delivery, asked: Exception
) -> None:
    """The outbox handles these as for any message: the whole announcement is tried again, or failed."""
    receipt = sent_ok(client, world.owner_a, world.shop_a, 300_000, 3, unique_image()[0])
    telegram = Telegram(refuse={"sendPhoto": asked})
    with pytest.raises(type(asked)):
        deliver(str(group), queued(owner, receipt)[str(group)], telegram)
    assert telegram.tried == ["sendPhoto"] and telegram.sent == []


def test_when_nothing_can_be_sent_the_failure_is_the_messages_own(
    client: TestClient, world: World, owner: psycopg.Connection, group: int, deliver: Delivery
) -> None:
    receipt = sent_ok(client, world.owner_a, world.shop_a, 300_000, 3, unique_image()[0])
    down = SendFailed("TelegramNetworkError")
    telegram = Telegram(refuse={"sendPhoto": down, "sendDocument": down, "sendMessage": down})
    with pytest.raises(SendFailed):
        deliver(str(group), queued(owner, receipt)[str(group)], telegram)
    assert telegram.sent == []


def test_words_too_long_for_a_caption_follow_the_file_as_the_text_message_of_before(
    client: TestClient, world: World, owner: psycopg.Connection, group: int, deliver: Delivery
) -> None:
    receipt = sent_ok(client, world.owner_a, world.shop_a, 300_000, 3, unique_image()[0])
    payload = queued(owner, receipt)[str(group)]
    long = {**payload, "text": payload["text"] + "\n" + "я" * CAPTION_MAX}
    assert caption_fits(payload["text"]) and not caption_fits(long["text"])
    telegram = deliver(str(group), long)
    (_, file), (_, words) = telegram.sent
    assert set(file) == {"method", "file"}, "the file alone: no cut caption, no buttons"
    assert words == {"text": long["text"], "reply_markup": payload["reply_markup"]}


def test_a_caption_is_measured_as_telegram_measures_it() -> None:
    assert caption_fits("a" * CAPTION_MAX) and not caption_fits("a" * (CAPTION_MAX + 1))
    # A character outside the basic plane counts twice.
    assert caption_fits("✅" * CAPTION_MAX) and not caption_fits("😀" * (CAPTION_MAX // 2 + 1))


# --- the decision still closes the announcement -------------------------------------------------------------


def press(
    client: TestClient, owner: psycopg.Connection, tg_id: int, chat: dict[str, Any], data: str, carried: dict[str, Any]
) -> list[tuple[str, dict[str, Any]]]:
    """A press under the announcement, which is a photo or a document. What the bot then sends."""
    update_id = next(_update_ids)
    response = client.post(
        "/tg/webhook",
        json={
            "update_id": update_id,
            "callback_query": {
                "id": f"cb-{uuid.uuid4().hex}",
                "from": {"id": tg_id, "language_code": "uz"},
                "message": {"message_id": ANNOUNCEMENT, "chat": chat, "caption": "…", **carried},
                "data": data,
            },
        },
        headers=SECRET,
    )
    assert response.status_code == 200, response.text
    sent = owner.execute(
        "SELECT recipient, payload FROM outbox_message WHERE dedupe_key LIKE %s ORDER BY dedupe_key",
        (f"update:{update_id}:%",),
    ).fetchall()
    return [(str(recipient), payload) for recipient, payload in sent]


def caption_edit(chat: int | str, text: str) -> tuple[str, dict[str, Any]]:
    """The announcement's words replaced under its file, without buttons."""
    return (
        str(chat),
        {
            "method": "editMessageCaption",
            "message_id": ANNOUNCEMENT,
            "caption": text,
            "reply_markup": {"inline_keyboard": []},
        },
    )


@pytest.mark.parametrize("carried", [PHOTO, DOCUMENT])
def test_an_approval_from_the_group_edits_the_caption_of_the_announcement(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    telegram_members: FakeChatMembers,
    group: int,
    carried: dict[str, Any],
) -> None:
    receipt = sent_ok(client, world.owner_a, world.shop_a, 300_000, 3, unique_image()[0])
    presser = 7_300_000_000 + uuid.uuid4().int % 10**9
    telegram_members.statuses[(group, presser)] = "administrator"
    sent = press(client, owner, presser, {"id": group, "type": "supergroup"}, press_of(receipt, "sra"), carried)
    until = add_months(subscription(owner, world.shop_a)[1], 3)
    assert sent == [caption_edit(group, say("uz", "a_receipt_approved", shop="Shop A", months=3, date=day(until)))]
    assert rows(owner, world.shop_a) == [(300_000, 3, "approved", 3, None, None, True)]

    # A second press finds it decided and says so on the same caption.
    again = press(client, owner, presser, {"id": group, "type": "supergroup"}, press_of(receipt, "sra"), carried)
    assert again == [caption_edit(group, say("uz", "a_receipt_decided"))]


def test_a_rejection_from_the_group_edits_the_caption_when_the_reason_arrives(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    telegram_members: FakeChatMembers,
    group: int,
) -> None:
    """The reason is another update, in another chat: that the announcement is a file is remembered."""
    receipt = sent_ok(client, world.owner_a, world.shop_a, 300_000, 3, unique_image()[0])
    presser = 7_300_000_000 + uuid.uuid4().int % 10**9
    telegram_members.statuses[(group, presser)] = "administrator"
    press(client, owner, presser, {"id": group, "type": "supergroup"}, press_of(receipt, "srj"), PHOTO)
    assert Chat(client, owner, presser).say(REASON).text == say(
        "uz", "a_receipt_rejected", shop="Shop A", reason=REASON
    )
    in_group = owner.execute(
        "SELECT recipient, payload FROM outbox_message WHERE recipient = %s AND payload ? 'method'", (str(group),)
    ).fetchall()
    assert [(str(recipient), payload) for recipient, payload in in_group] == [
        caption_edit(group, say("uz", "a_receipt_rejected", shop="Shop A", reason=REASON))
    ]
    assert rows(owner, world.shop_a) == [(300_000, 3, "rejected", None, REASON, None, True)]


def test_an_administrator_deciding_in_their_own_chat_and_in_the_group_edits_captions(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    group: int,
) -> None:
    elevate(client, admin_env, world.admin, make_admin(owner, admin_env, world.admin))
    admin = int(tg(owner, world.admin))
    private = {"id": admin, "type": "private"}

    first = sent_ok(client, world.owner_a, world.shop_a, 300_000, 3, unique_image()[0])
    sent = press(client, owner, admin, private, press_of(first, "sra"), PHOTO)
    until = add_months(subscription(owner, world.shop_a)[1], 3)
    approved = say("uz", "a_receipt_approved", shop="Shop A", months=3, date=day(until))
    assert sent == [caption_edit(admin, approved)]

    # Rejected from the group by a platform administrator: the reason is written in their own chat, the
    # group's copy is closed as a caption.
    second = sent_ok(client, world.owner_b, world.shop_b, 200_000, 2, unique_image()[0])
    press(client, owner, admin, {"id": group, "type": "supergroup"}, press_of(second, "srj"), PHOTO)
    Chat(client, owner, admin).say(REASON)
    in_group = owner.execute(
        "SELECT recipient, payload FROM outbox_message WHERE recipient = %s AND payload ? 'method'", (str(group),)
    ).fetchall()
    assert [(str(recipient), payload) for recipient, payload in in_group] == [
        caption_edit(group, say("uz", "a_receipt_rejected", shop="Shop B", reason=REASON))
    ]


def test_an_announcement_without_a_file_is_still_edited_as_text(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    telegram_members: FakeChatMembers,
    group: int,
) -> None:
    """The fallback announcement, and every one sent before this change: `editMessageCaption` would fail."""
    receipt = sent_ok(client, world.owner_a, world.shop_a, 300_000, 3, unique_image()[0])
    presser = 7_300_000_000 + uuid.uuid4().int % 10**9
    telegram_members.statuses[(group, presser)] = "administrator"
    ((_, payload),) = press(client, owner, presser, {"id": group, "type": "supergroup"}, press_of(receipt, "sra"), {})
    assert payload["method"] == "editMessageText" and "caption" not in payload


def test_words_too_long_for_a_caption_are_not_cut_when_a_message_is_replaced() -> None:
    short, long = "✅ «Shop A»", "я" * (CAPTION_MAX + 1)
    keyboard = [[("Bekor", "v2:srn")]]
    assert _replacing(7, long, None, media=False) == [
        {"method": "editMessageText", "message_id": 7, "text": long, "reply_markup": {"inline_keyboard": []}}
    ]
    assert _replacing(7, short, None, media=True) == [
        {"method": "editMessageCaption", "message_id": 7, "caption": short, "reply_markup": {"inline_keyboard": []}}
    ]
    # The buttons leave the file, and the words follow in reply to it, whole.
    assert _replacing(7, long, keyboard, media=True) == [
        {"method": "editMessageReplyMarkup", "message_id": 7, "reply_markup": {"inline_keyboard": []}},
        {
            "text": long,
            "reply_parameters": {"message_id": 7, "allow_sending_without_reply": True},
            "reply_markup": {"inline_keyboard": [[{"text": "Bekor", "callback_data": "v2:srn"}]]},
        },
    ]


def test_closing_a_group_announcement_with_long_words_queues_both_messages() -> None:
    @dataclass
    class Session:
        queued: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)

        async def enqueue(self, *, channel: str, recipient: str, payload: dict[str, Any], dedupe_key: str) -> bool:
            self.queued.append((recipient, dedupe_key, payload))
            return True

    session = Session()
    replies = Replies(session, Incoming(41, 1, uuid.uuid4(), "uz"))  # type: ignore[arg-type]
    asyncio.run(replies.close_in_group(-100, 7, "я" * (CAPTION_MAX + 1), media=True))
    assert [(recipient, key, payload.get("method")) for recipient, key, payload in session.queued] == [
        ("-100", "update:41:group", "editMessageReplyMarkup"),
        ("-100", "update:41:group:2", None),
    ]

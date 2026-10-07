"""Payment notices in the chat: /toladim for the customer, accept and decline buttons for staff.

Updates go through the real webhook. Telegram's file download is replaced by a fake behind its port; the
real Bot API is never called.
"""

import uuid
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from qarz.application.chat_texts import money, say
from qarz.domain.files import MAX_FILE_BYTES

from ..receipt_samples import EXIF, HTML, JPEG, PDF, jpeg
from .conftest import FakeTelegramFiles, World, as_user, stored_objects
from .test_chat import Chat, Said, _message_ids, _update_ids, chat_of
from .test_customer_account import ME, link_of
from .test_customers_ledger import _subscription, record, shop, today
from .test_payment_notices import files, notices, payments, seed_notice, sent_to_staff, told

pytestmark = pytest.mark.db

NO_RECEIPT, CANCEL = "v2:pns", "v2:pnx"


def send_file(chat: Chat, *, update_id: int | None = None, **content: Any) -> Said:
    """A message that carries a photo or a document instead of text."""
    return chat._post(
        {
            "update_id": update_id or next(_update_ids),
            "message": {
                "message_id": next(_message_ids),
                "chat": {"id": chat.tg_id, "type": "private"},
                "from": chat._from(),
                **content,
            },
        }
    )


def photo(file_id: str, size: int) -> dict[str, Any]:
    return {"photo": [{"file_id": f"{file_id}-thumb", "file_size": 1}, {"file_id": file_id, "file_size": size}]}


def notice_button(owner: psycopg.Connection, world: World, action: str) -> str:
    """The accept or decline button of the one notice the staff of shop A were told about."""
    data = {
        button["callback_data"]
        for _, payload in sent_to_staff(owner, world.shop_a)
        for button in payload["reply_markup"]["inline_keyboard"][0]
        if button["callback_data"].startswith(f"v2:{action}:")
    }
    assert len(data) == 1, data
    return str(data.pop())


# --- the customer --------------------------------------------------------------------------------------


def test_a_customer_reports_a_payment_with_a_photo_and_a_seller_accepts_it(
    client: TestClient, world: World, owner: psycopg.Connection, file_root: Path, telegram_files: FakeTelegramFiles
) -> None:
    customer, seller = chat_of(client, owner, world.customer_of_a), chat_of(client, owner, world.seller_a)
    telegram_files.files["tg-photo-1"] = JPEG

    asked = customer.say("/toladim")
    assert asked.text == say("uz", "ask_notice_amount", shop="Shop A", balance=money("uz", 50000))
    assert asked.text == (
        "«Shop A» do'konidagi qarzingiz: 50\xa0000\xa0so'm\nQancha to'ladingiz? Faqat summani yozing, masalan: 50000"
    )
    assert asked.buttons == {"Bekor": CANCEL}

    asked = customer.say("20 000")
    assert asked.text == say("uz", "ask_notice_receipt", amount=money("uz", 20000))
    assert asked.buttons == {"Cheksiz yuborish": NO_RECEIPT, "Bekor": CANCEL}
    assert notices(owner, world.shop_a) == [], "nothing is stored until the notice is complete"
    assert telegram_files.asked == []

    sent = send_file(customer, **photo("tg-photo-1", len(JPEG)))
    assert sent.text == say("uz", "notice_sent", shop="Shop A", amount=money("uz", 20000))
    assert telegram_files.asked == ["tg-photo-1"], "the largest size that fits, once"
    assert notices(owner, world.shop_a) == [(world.customer_a, 20000, "sent", True, None, None, None, False)]
    assert [row[5] for row in files(owner, world.shop_a)] == ["image/jpeg"]
    assert [path.read_bytes() for path in stored_objects(file_root)] == [JPEG]
    assert payments(owner, world.customer_a) == []
    # The question is answered: what the customer writes next is not another notice.
    assert customer.say("30000").text.startswith(say("uz", "accounts_header"))
    assert len(notices(owner, world.shop_a)) == 1

    accepted = seller.press(notice_button(owner, world, "pna"))
    assert accepted.text == say(
        "uz", "notice_accepted_staff", shop="Shop A", name="Ali", amount=money("uz", 20000), balance=money("uz", 30000)
    )
    assert accepted.payloads[0]["method"] == "editMessageText", "the message with the buttons is replaced"
    paid = payments(owner, world.customer_a)
    assert [(amount, author) for _, amount, author in paid] == [(20000, world.seller_a_membership)]
    assert notices(owner, world.shop_a) == [
        (world.customer_a, 20000, "accepted", True, paid[0][0], None, world.seller_a_membership, True)
    ]
    notice_id = owner.execute("SELECT id FROM payment_notice WHERE shop_id = %s", (world.shop_a,)).fetchone()
    assert notice_id is not None
    assert told(owner, f"notice:{notice_id[0]}:accepted")[0][1]["text"] == say(
        "uz", "n_notice_accepted", shop="Shop A", amount=money("uz", 20000)
    )

    # Another staff member who presses the same button later is told it is decided, and nothing is recorded.
    late = chat_of(client, owner, world.manager_a).press(notice_button(owner, world, "pna"))
    assert late.text == say("uz", "PAYMENT_NOTICE_NOT_OPEN")
    assert len(payments(owner, world.customer_a)) == 1


def test_a_notice_can_be_sent_without_a_receipt(
    client: TestClient, world: World, owner: psycopg.Connection, file_root: Path
) -> None:
    customer = chat_of(client, owner, world.customer_of_a)
    customer.say("/toladim")
    customer.say("50k")
    sent = customer.press(NO_RECEIPT)
    assert sent.text == say("uz", "notice_sent", shop="Shop A", amount=money("uz", 50000))
    assert notices(owner, world.shop_a) == [(world.customer_a, 50000, "sent", False, None, None, None, False)]
    assert stored_objects(file_root) == []
    # The button belongs to a question that is over.
    assert customer.press(NO_RECEIPT).text == say("uz", "expired")
    assert len(notices(owner, world.shop_a)) == 1


def test_a_pdf_sent_as_a_document_is_a_receipt(
    client: TestClient, world: World, owner: psycopg.Connection, telegram_files: FakeTelegramFiles
) -> None:
    customer = chat_of(client, owner, world.customer_of_a)
    telegram_files.files["tg-doc-1"] = PDF
    customer.say("/toladim")
    customer.say("20000")
    document = {"file_id": "tg-doc-1", "file_name": "chek.pdf", "mime_type": "image/gif", "file_size": len(PDF)}
    assert send_file(customer, document=document).text.startswith("✅")
    assert [row[5] for row in files(owner, world.shop_a)] == ["application/pdf"]


@pytest.mark.parametrize(
    "typed",
    ["Ali 20000", "-20000", "20000 berdi", "20000 non uchun", "yigirma ming", "20000.5", "20,5", "99", "100000001", ""],
)
def test_anything_that_is_not_a_plain_amount_is_refused_and_can_be_typed_again(
    client: TestClient, world: World, owner: psycopg.Connection, typed: str
) -> None:
    customer = chat_of(client, owner, world.customer_of_a)
    customer.say("/toladim")
    if typed:
        assert customer.say(typed).text == say("uz", "notice_amount_invalid")
    assert notices(owner, world.shop_a) == [] and payments(owner, world.customer_a) == []
    assert customer.say("20000").text == say("uz", "ask_notice_receipt", amount=money("uz", 20000))


def test_an_amount_above_the_debt_is_refused_at_once(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    customer = chat_of(client, owner, world.customer_of_a)
    customer.say("/toladim")
    assert customer.say("50001").text == say("uz", "notice_amount_exceeds", balance=money("uz", 50000))
    assert customer.say("50000").text == say("uz", "ask_notice_receipt", amount=money("uz", 50000))
    # The debt shrinks before the notice is sent: the shop's own check has the last word.
    assert record(client, world, world.customer_a, "payment", 10000).status_code == 201
    assert customer.press(NO_RECEIPT).text == say("uz", "EXCEEDS_BALANCE")
    assert notices(owner, world.shop_a) == []


def test_text_instead_of_a_receipt_is_answered_with_a_hint_and_the_question_stays(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    customer = chat_of(client, owner, world.customer_of_a)
    customer.say("/toladim")
    customer.say("20000")
    hint = customer.say("chekim yo'q")
    assert hint.text == say("uz", "notice_receipt_hint")
    assert hint.buttons == {"Cheksiz yuborish": NO_RECEIPT, "Bekor": CANCEL}
    assert customer.press(NO_RECEIPT).text.startswith("✅")
    assert [row[1] for row in notices(owner, world.shop_a)] == [20000]


def test_a_notice_can_be_cancelled_at_either_step(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    customer = chat_of(client, owner, world.customer_of_a)
    customer.say("/toladim")
    assert customer.press(CANCEL).text == say("uz", "cancelled")
    assert customer.say("20000").text.startswith(say("uz", "accounts_header")), "no amount is being asked for now"

    customer.say("/toladim")
    customer.say("20000")
    assert customer.press(CANCEL).text == say("uz", "cancelled")
    assert customer.press(NO_RECEIPT).text == say("uz", "expired")
    assert customer.press(CANCEL).text == say("uz", "expired")
    assert notices(owner, world.shop_a) == []


def test_the_no_receipt_button_does_nothing_while_the_amount_is_still_asked_for(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    customer = chat_of(client, owner, world.customer_of_a)
    customer.say("/toladim")
    assert customer.press(NO_RECEIPT).text == say("uz", "expired")
    assert notices(owner, world.shop_a) == []
    # The question about the amount is still open.
    assert customer.say("20000").text == say("uz", "ask_notice_receipt", amount=money("uz", 20000))


def test_a_button_pressed_under_a_message_with_a_photo_downloads_nothing(
    client: TestClient, world: World, owner: psycopg.Connection, telegram_files: FakeTelegramFiles
) -> None:
    telegram_files.files["tg-photo-1"] = JPEG
    customer = chat_of(client, owner, world.customer_of_a)
    customer.say("/toladim")
    customer.say("20000")
    pressed = customer._post(
        {
            "update_id": next(_update_ids),
            "callback_query": {
                "id": "cb-photo",
                "from": customer._from(),
                "message": {
                    "message_id": 900,
                    "chat": {"id": customer.tg_id, "type": "private"},
                    **photo("tg-photo-1", len(JPEG)),
                },
                "data": "v2:lang:uz",
            },
        }
    )
    assert pressed.text == say("uz", "lang_set")
    assert telegram_files.asked == [] and notices(owner, world.shop_a) == []


def test_a_command_ends_an_unfinished_notice(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    customer = chat_of(client, owner, world.customer_of_a)
    customer.say("/toladim")
    customer.say("/qarzim")
    assert customer.say("20000").text.startswith(say("uz", "accounts_header"))
    assert notices(owner, world.shop_a) == []


def test_a_file_that_is_not_a_receipt_is_refused_and_another_may_follow(
    client: TestClient, world: World, owner: psycopg.Connection, file_root: Path, telegram_files: FakeTelegramFiles
) -> None:
    customer = chat_of(client, owner, world.customer_of_a)
    telegram_files.files.update(
        {"lying": HTML, "huge": JPEG + bytes(MAX_FILE_BYTES), "cut": JPEG[:-2], "fine": jpeg(EXIF, trailing=b"tail")}
    )
    customer.say("/toladim")
    customer.say("20000")

    for message in (
        photo("lying", len(HTML)),  # Telegram calls it a photo; its bytes say otherwise
        photo("huge", MAX_FILE_BYTES + len(JPEG)),  # larger than the service accepts
        photo("never-uploaded", 5000),  # Telegram does not have it
        photo("cut", len(JPEG) - 2),  # a photo that is not whole
        {"document": {"file_id": "lying", "file_name": "chek.jpg", "mime_type": "image/jpeg"}},
    ):
        refused = send_file(customer, **message)
        assert refused.text == say("uz", "notice_receipt_invalid"), message
        assert refused.buttons == {"Cheksiz yuborish": NO_RECEIPT, "Bekor": CANCEL}
    assert notices(owner, world.shop_a) == [] and files(owner, world.shop_a) == []
    assert stored_objects(file_root) == []

    assert send_file(customer, **photo("fine", len(JPEG))).text.startswith("✅")
    assert [path.read_bytes() for path in stored_objects(file_root)] == [JPEG]


def test_a_file_nobody_asked_for_is_never_downloaded(
    client: TestClient, world: World, owner: psycopg.Connection, file_root: Path, telegram_files: FakeTelegramFiles
) -> None:
    telegram_files.files["tg-photo-1"] = JPEG
    customer, seller = chat_of(client, owner, world.customer_of_a), chat_of(client, owner, world.seller_a)
    assert send_file(customer, **photo("tg-photo-1", len(JPEG))).text == say("uz", "only_text")
    assert send_file(seller, **photo("tg-photo-1", len(JPEG))).text == say("uz", "only_text")
    # While the amount is still being asked for, a photo is not a receipt yet either.
    customer.say("/toladim")
    assert send_file(customer, **photo("tg-photo-1", len(JPEG))).text == say("uz", "only_text")
    assert telegram_files.asked == []
    assert notices(owner, world.shop_a) == [] and stored_objects(file_root) == []


def test_someone_linked_to_several_shops_chooses_one(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    customer_b, entry_b = uuid.uuid4(), uuid.uuid4()
    owner.execute(
        "INSERT INTO customer (id, shop_id, display_name, name_norm) VALUES (%s, %s, 'Alisher', 'alisher')",
        (customer_b, world.shop_b),
    )
    owner.execute(
        "INSERT INTO customer_link (id, shop_id, customer_id, user_id, status, consent_text_v, consent_at) "
        "VALUES (%s, %s, %s, %s, 'active', 2, now())",
        (uuid.uuid4(), world.shop_b, customer_b, world.customer_of_a),
    )
    owner.execute(
        "INSERT INTO ledger_entry (id, shop_id, customer_id, seq, kind, amount, author_id) "
        "SELECT %s, %s, %s, 1, 'credit', 7000, id FROM membership WHERE shop_id = %s",
        (entry_b, world.shop_b, customer_b, world.shop_b),
    )
    owner.execute(
        "INSERT INTO promise (id, shop_id, entry_id, promised_date, actor) VALUES (%s, %s, %s, %s, 'default')",
        (uuid.uuid4(), world.shop_b, entry_b, today()),
    )
    customer = chat_of(client, owner, world.customer_of_a)

    asked = customer.say("/toladim")
    assert asked.text == say("uz", "notice_choose_shop")
    assert sorted(asked.buttons) == ["Shop A: 50\xa0000\xa0so'm", "Shop B: 7\xa0000\xa0so'm"]
    assert asked.buttons["Shop B: 7\xa0000\xa0so'm"] == f"v2:pn:{link_of(owner, customer_b).hex}"

    chosen = customer.press(asked.button("Shop B"))
    assert chosen.text == say("uz", "ask_notice_amount", shop="Shop B", balance=money("uz", 7000))
    assert customer.say("7001").text == say("uz", "notice_amount_exceeds", balance=money("uz", 7000))
    customer.say("7000")
    assert customer.press(NO_RECEIPT).text == say("uz", "notice_sent", shop="Shop B", amount=money("uz", 7000))
    assert notices(owner, world.shop_a) == []
    assert notices(owner, world.shop_b) == [(customer_b, 7000, "sent", False, None, None, None, False)]
    assert sent_to_staff(owner, world.shop_a) == []
    recipients = [recipient for recipient, _ in sent_to_staff(owner, world.shop_b)]
    assert recipients == [str(chat_of(client, owner, world.owner_b).tg_id)], "only the staff of the chosen shop"


def test_a_shop_button_for_an_account_that_is_not_ones_own_leads_nowhere(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    button = f"v2:pn:{link_of(owner, world.customer_a).hex}"
    for user in (world.stranger, world.owner_a, world.owner_b):
        person = chat_of(client, owner, user)
        assert person.press(button).text == say("uz", "expired")
        person.say("20000")
    assert chat_of(client, owner, world.customer_of_a).press("v2:pn:nonsense").text == say("uz", "expired")
    assert notices(owner, world.shop_a) == []


def test_who_owes_nothing_or_is_linked_nowhere_is_told_so(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    assert chat_of(client, owner, world.stranger).say("/toladim").text == say("uz", "no_accounts")
    assert chat_of(client, owner, world.seller_a).say("/toladim").text == say("uz", "no_accounts")
    assert record(client, world, world.customer_a, "payment", 50000).status_code == 201
    customer = chat_of(client, owner, world.customer_of_a)
    assert customer.say("/toladim").text == say("uz", "notice_nothing_owed", shop="Shop A")
    assert customer.say("20000").text.startswith(say("uz", "accounts_header"))
    assert notices(owner, world.shop_a) == []


def test_the_fourth_waiting_notice_is_refused_in_words(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    for amount in (1000, 2000, 3000):
        seed_notice(owner, world, amount)
    customer = chat_of(client, owner, world.customer_of_a)
    customer.say("/toladim")
    customer.say("4000")
    assert customer.press(NO_RECEIPT).text == say("uz", "PAYMENT_NOTICE_NOT_ALLOWED")
    assert len(notices(owner, world.shop_a)) == 3


def test_a_redelivered_photo_makes_one_notice_and_keeps_one_file(
    client: TestClient, world: World, owner: psycopg.Connection, file_root: Path, telegram_files: FakeTelegramFiles
) -> None:
    """The shop's transaction committed, then the update's own transaction failed: Telegram delivers again."""
    telegram_files.files["tg-photo-1"] = JPEG
    customer = chat_of(client, owner, world.customer_of_a)
    customer.say("/toladim")
    amount_update = customer.say("20000").update_id
    first = send_file(customer, update_id=6_600_000_001, **photo("tg-photo-1", len(JPEG)))
    # Put back what the failed transaction would have left: the update unclaimed, the question unanswered.
    owner.execute("DELETE FROM processed_update WHERE update_id = 6600000001")
    owner.execute("DELETE FROM outbox_message WHERE dedupe_key LIKE 'update:6600000001:%'")
    owner.execute(
        "INSERT INTO chat_pending (id, user_id, kind, payload, expires_at) "
        "VALUES (gen_random_uuid(), %s, 'notice', %s, now() + interval '10 minutes')",
        (world.customer_of_a, Jsonb({"link": link_of(owner, world.customer_a).hex, "amount": 20000})),
    )
    assert amount_update != 6_600_000_001

    again = send_file(customer, update_id=6_600_000_001, **photo("tg-photo-1", len(JPEG)))
    assert again.payloads == first.payloads
    assert len(notices(owner, world.shop_a)) == 1 and len(files(owner, world.shop_a)) == 1
    assert [path.read_bytes() for path in stored_objects(file_root)] == [JPEG]
    assert len(sent_to_staff(owner, world.shop_a)) == 3


# --- staff ---------------------------------------------------------------------------------------------


def test_staff_decline_from_chat_with_a_reason(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    notice = seed_notice(owner, world, 20000)
    seller = chat_of(client, owner, world.seller_a)
    button = f"v2:pnd:{notice.hex}"

    assert seller.press(button).text == say("uz", "ask_decline_reason")
    assert seller.say("yo").text == say("uz", "reason_invalid")
    assert notices(owner, world.shop_a)[0][2] == "sent"
    # The reason was asked once: the next message is an ordinary entry again.
    assert seller.say("Ali 1000").text.startswith("✅ Shop A")

    seller.press(button)
    assert seller.say("Kartaga pul tushmagan").text == say("uz", "notice_declined_staff")
    assert notices(owner, world.shop_a) == [
        (world.customer_a, 20000, "declined", False, None, "Kartaga pul tushmagan", world.seller_a_membership, True)
    ]
    assert told(owner, f"notice:{notice}:declined")[0][1]["text"].endswith("Sabab: Kartaga pul tushmagan")
    assert payments(owner, world.customer_a) == []

    seller.press(button)
    assert seller.say("Yana rad etaman").text == say("uz", "PAYMENT_NOTICE_NOT_OPEN")


def test_only_staff_of_the_notices_own_shop_can_decide_it_from_chat(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    notice = seed_notice(owner, world, 20000)
    for user in (world.owner_b, world.customer_of_a, world.stranger, world.suspended_a):
        person = chat_of(client, owner, user)
        assert person.press(f"v2:pna:{notice.hex}").text == say("uz", "not_found")
        person.press(f"v2:pnd:{notice.hex}")
        assert person.say("Rad etaman").text == say("uz", "not_found")
    manager = chat_of(client, owner, world.manager_a)
    assert manager.press(f"v2:pna:{uuid.uuid4().hex}").text == say("uz", "not_found")
    assert manager.press("v2:pna:nonsense").payloads[0]["method"] == "editMessageReplyMarkup"
    assert notices(owner, world.shop_a)[0][2] == "sent" and payments(owner, world.customer_a) == []


def test_what_the_ledger_refuses_is_said_in_words_and_the_notice_can_still_be_declined(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    notice = seed_notice(owner, world, 50000)
    assert record(client, world, world.customer_a, "payment", 20000).status_code == 201
    owner_chat = chat_of(client, owner, world.owner_a)
    refused = owner_chat.press(f"v2:pna:{notice.hex}")
    assert refused.text == say("uz", "EXCEEDS_BALANCE")
    assert len(refused.payloads) == 1, "the buttons stay: declining is still possible"
    assert notices(owner, world.shop_a)[0][2] == "sent" and len(payments(owner, world.customer_a)) == 1

    stale = seed_notice(owner, world, 1000, days_ago=14.01)
    expired = owner_chat.press(f"v2:pna:{stale.hex}")
    assert expired.text == say("uz", "PAYMENT_NOTICE_NOT_OPEN")
    assert expired.payloads[1]["method"] == "editMessageReplyMarkup", "nothing is left to decide"

    _subscription(owner, world, "state = 'suspended'")
    assert owner_chat.press(f"v2:pna:{notice.hex}").text == say("uz", "SHOP_SUSPENDED")
    assert len(payments(owner, world.customer_a)) == 1


def test_a_redelivered_accept_records_one_payment(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    notice = seed_notice(owner, world, 20000)
    seller = chat_of(client, owner, world.seller_a)
    update = {
        "update_id": 6_600_000_002,
        "callback_query": {
            "id": "cb-notice",
            "from": seller._from(),
            "message": {"message_id": 900, "chat": {"id": seller.tg_id, "type": "private"}},
            "data": f"v2:pna:{notice.hex}",
        },
    }
    first = seller._post(update)
    owner.execute("DELETE FROM processed_update WHERE update_id = 6600000002")
    owner.execute("DELETE FROM outbox_message WHERE dedupe_key LIKE 'update:6600000002:%'")
    again = seller._post(update)
    assert again.payloads == first.payloads
    assert len(payments(owner, world.customer_a)) == 1


def test_the_api_and_the_chat_show_the_same_notice(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    customer = chat_of(client, owner, world.customer_of_a)
    customer.say("/toladim")
    customer.say("20000")
    customer.press(NO_RECEIPT)
    listed = client.get(f"{shop(world)}/payment-notices", headers=as_user(world.seller_a)).json()["items"]
    assert [(n["amount"], n["customer_name"]) for n in listed] == [(20000, "Ali")]
    mine = client.get(f"{ME}/{link_of(owner, world.customer_a)}", headers=as_user(world.customer_of_a)).json()
    assert [n["id"] for n in mine["payment_notices"]] == [listed[0]["id"]]

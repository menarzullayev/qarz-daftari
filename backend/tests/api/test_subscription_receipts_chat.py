"""Paying the subscription in the chat: `/obuna`, a period, then the receipt as a photo or a document
(REQ-054). Updates go through the real webhook; Telegram's file download is a fake behind its port.
"""

import uuid
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from qarz.application.chat_texts import money, say
from qarz.domain.files import MAX_FILE_BYTES

from ..receipt_samples import EXIF, HTML, JPEG, PDF, jpeg
from .conftest import AdminEnv, FakeTelegramFiles, World, make_admin, stored_objects
from .test_chat import chat_of
from .test_disputes import tg
from .test_payment_notices_chat import photo, send_file
from .test_subscription_receipts import CARD, announced, rows, set_subscription, setting, subscription

pytestmark = pytest.mark.db

CANCEL = "v2:srx"


def months_button(world: World, months: int) -> str:
    return f"v2:srm:{world.shop_a.hex}:{months}"


def test_the_owner_pays_by_transfer_and_sends_the_receipt_to_the_bot(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    file_root: Path,
    telegram_files: FakeTelegramFiles,
) -> None:
    make_admin(owner, admin_env, world.admin)
    setting(owner, world.admin, "card_number", CARD)
    setting(owner, world.admin, "price_uzs", 120_000)
    boss = chat_of(client, owner, world.owner_a)
    telegram_files.files["tg-receipt"] = jpeg(EXIF)
    was = subscription(owner, world.shop_a)

    shown = boss.say("/obuna")
    lines = shown.text.split("\n")
    assert lines[-2] == say("uz", "sub_pay_to", card=CARD)
    assert lines[-1] == say("uz", "sub_choose_months")
    assert shown.buttons == {
        f"{months} oy — {money('uz', 120_000 * months)}": months_button(world, months) for months in (1, 3, 6, 12)
    }

    asked = boss.press(months_button(world, 3))
    assert asked.text == say("uz", "ask_sub_receipt", shop="Shop A", months=3, amount=money("uz", 360_000))
    assert asked.buttons == {"Bekor": CANCEL}
    assert rows(owner, world.shop_a) == [] and telegram_files.asked == [], "nothing is stored or fetched yet"

    sent = send_file(boss, **photo("tg-receipt", len(jpeg(EXIF))))
    assert sent.text == say("uz", "sub_receipt_sent", shop="Shop A", months=3, amount=money("uz", 360_000))
    assert telegram_files.asked == ["tg-receipt"]
    assert rows(owner, world.shop_a) == [(360_000, 3, "submitted", None, None, None, True)]
    assert [path.read_bytes() for path in stored_objects(file_root)] == [JPEG], "kept without its metadata"
    assert subscription(owner, world.shop_a) == was
    receipt = owner.execute("SELECT id FROM subscription_receipt WHERE shop_id = %s", (world.shop_a,)).fetchone()
    assert receipt is not None
    assert set(announced(owner, str(receipt[0]))) == {tg(owner, world.admin)}

    # The question is answered: a second photo is not a second receipt.
    assert send_file(boss, **photo("tg-receipt", 10)).text == say("uz", "only_text")
    assert len(rows(owner, world.shop_a)) == 1


def test_a_document_is_accepted_and_a_redelivered_update_sends_one_receipt(
    client: TestClient, world: World, owner: psycopg.Connection, file_root: Path, telegram_files: FakeTelegramFiles
) -> None:
    setting(owner, world.owner_a, "card_number", CARD)
    boss = chat_of(client, owner, world.owner_a)
    telegram_files.files["tg-pdf"] = PDF
    boss.press(months_button(world, 1))
    update_id = 7_000_000 + uuid.uuid4().int % 1_000_000
    first = send_file(boss, update_id=update_id, document={"file_id": "tg-pdf", "file_size": len(PDF)})
    assert first.text == say("uz", "sub_receipt_sent", shop="Shop A", months=1, amount=money("uz", 100_000))
    send_file(boss, update_id=update_id, document={"file_id": "tg-pdf", "file_size": len(PDF)})
    assert rows(owner, world.shop_a) == [(100_000, 1, "submitted", None, None, None, True)]
    assert len(stored_objects(file_root)) == 1


def test_a_file_that_is_no_receipt_is_refused_and_the_question_stays_open(
    client: TestClient, world: World, owner: psycopg.Connection, file_root: Path, telegram_files: FakeTelegramFiles
) -> None:
    setting(owner, world.owner_a, "card_number", CARD)
    boss = chat_of(client, owner, world.owner_a)
    telegram_files.files.update({"tg-html": HTML, "tg-big": JPEG + b"x" * MAX_FILE_BYTES, "tg-good": JPEG})
    boss.press(months_button(world, 6))

    for bad in ("tg-html", "tg-big", "tg-unknown"):
        refused = send_file(boss, document={"file_id": bad, "file_size": 100})
        assert refused.text == say("uz", "sub_receipt_invalid")
        assert refused.buttons == {"Bekor": CANCEL}
    assert rows(owner, world.shop_a) == [] and stored_objects(file_root) == []

    done = send_file(boss, **photo("tg-good", len(JPEG)))
    assert done.text == say("uz", "sub_receipt_sent", shop="Shop A", months=6, amount=money("uz", 600_000))
    assert rows(owner, world.shop_a) == [(600_000, 6, "submitted", None, None, None, True)]


def test_cancelling_or_another_command_ends_the_question(
    client: TestClient, world: World, owner: psycopg.Connection, telegram_files: FakeTelegramFiles
) -> None:
    setting(owner, world.owner_a, "card_number", CARD)
    boss = chat_of(client, owner, world.owner_a)
    telegram_files.files["tg-good"] = JPEG

    boss.press(months_button(world, 1))
    assert boss.press(CANCEL).text == say("uz", "cancelled")
    assert send_file(boss, **photo("tg-good", len(JPEG))).text == say("uz", "only_text")

    boss.press(months_button(world, 1))
    boss.say("/yordam")
    assert send_file(boss, **photo("tg-good", len(JPEG))).text == say("uz", "only_text")
    assert rows(owner, world.shop_a) == []
    assert telegram_files.asked == [], "a file nobody asked for is never fetched"


def test_without_a_card_to_pay_to_no_period_is_offered(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    boss = chat_of(client, owner, world.owner_a)
    shown = boss.say("/obuna")
    assert shown.text.split("\n")[-1] == say("uz", "sub_no_card")
    assert shown.buttons == {}
    # A button kept from before the card was removed asks for nothing.
    assert say("uz", "sub_no_card") in boss.press(months_button(world, 1)).text
    assert owner.execute("SELECT count(*) FROM chat_pending WHERE kind = 'sub_receipt'").fetchone() == (0,)


def test_only_the_owner_of_the_shop_can_start_or_finish_it(
    client: TestClient, world: World, owner: psycopg.Connection, telegram_files: FakeTelegramFiles
) -> None:
    setting(owner, world.owner_a, "card_number", CARD)
    telegram_files.files["tg-good"] = JPEG
    for staff in (world.manager_a, world.seller_a):
        assert chat_of(client, owner, staff).press(months_button(world, 1)).text == say("uz", "forbidden")
    outsider = chat_of(client, owner, world.owner_b)
    assert outsider.press(months_button(world, 1)).text == say("uz", "expired")
    boss = chat_of(client, owner, world.owner_a)
    # A period that is not offered, a shop that is not one, a button with a part missing.
    for button in (f"v2:srm:{world.shop_a.hex}:2", f"v2:srm:{world.shop_a.hex}:0", "v2:srm:zz:1"):
        assert boss.press(button).text == say("uz", "expired"), button
    assert "text" not in boss.press("v2:srm:1").payloads[0], "only the stale buttons are taken away"
    assert owner.execute("SELECT count(*) FROM chat_pending WHERE kind = 'sub_receipt'").fetchone() == (0,)

    # An owner who stopped being the owner between choosing the period and sending the file.
    boss.press(months_button(world, 1))
    owner.execute("UPDATE membership SET status = 'removed' WHERE user_id = %s", (world.owner_a,))
    assert send_file(boss, **photo("tg-good", len(JPEG))).text == say("uz", "expired")
    assert rows(owner, world.shop_a) == []


def test_a_limited_shop_pays_the_same_way_and_too_many_waiting_receipts_are_refused_in_words(
    client: TestClient, world: World, owner: psycopg.Connection, telegram_files: FakeTelegramFiles
) -> None:
    setting(owner, world.owner_a, "card_number", CARD)
    set_subscription(owner, world.shop_a, "limited")
    boss = chat_of(client, owner, world.owner_a)
    telegram_files.files["tg-good"] = JPEG
    for _ in range(3):
        boss.press(months_button(world, 1))
        assert send_file(boss, **photo("tg-good", len(JPEG))).text.startswith("✅")
    boss.press(months_button(world, 1))
    refused = send_file(boss, **photo("tg-good", len(JPEG)))
    assert refused.text == say("uz", "SUBSCRIPTION_RECEIPT_NOT_ALLOWED")
    assert len(rows(owner, world.shop_a)) == 3
    # The question is closed: the owner is not asked for the same file again.
    assert send_file(boss, **photo("tg-good", len(JPEG))).text == say("uz", "only_text")

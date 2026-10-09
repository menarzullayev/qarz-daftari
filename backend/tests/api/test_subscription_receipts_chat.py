"""Paying the subscription in the chat: `/obuna`, the card to pay to, a period, then the receipt as a
photo or a document (REQ-054). Updates go through the real webhook; Telegram's file download is a fake
behind its port.
"""

import uuid
from pathlib import Path
from typing import Any

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
from .test_subscription_receipts import (
    CARD,
    HUMO,
    UZCARD,
    announced,
    paid_to,
    rows,
    set_subscription,
    setting,
    subscription,
)

pytestmark = pytest.mark.db

CANCEL = "v2:srx"
VISA = {"number": "4278310012345678", "label": "Visa · Ipak Yo'li"}
HUMO_TAG, UZCARD_TAG, VISA_TAG = "Humo · Anorbank ··1234", "Uzcard · Kapitalbank ··7890", "Visa · Ipak Yo'li ··5678"


def months_button(world: World, months: int) -> str:
    """The button as it was before a card could be chosen. Messages sent then still carry it."""
    return f"v2:srm:{world.shop_a.hex}:{months}"


def card_months_button(world: World, months: int, place: int | str, last4: str) -> str:
    return f"v2:srm:{world.shop_a.hex}:{months}:{place}:{last4}"


def other_cards(world: World, place: int | str, last4: str) -> str:
    return f"v2:sro:{world.shop_a.hex}:{place}:{last4}"


def card_button(world: World, place: int | str, last4: str) -> str:
    return f"v2:src:{world.shop_a.hex}:{place}:{last4}"


def pending(owner: psycopg.Connection, world: World) -> list[Any]:
    """What the owner of the first shop is being asked a receipt for."""
    return [
        row[0]
        for row in owner.execute(
            "SELECT payload FROM chat_pending WHERE kind = 'sub_receipt' AND user_id = %s", (world.owner_a,)
        ).fetchall()
    ]


def test_the_owner_pays_by_transfer_and_sends_the_receipt_to_the_bot(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    file_root: Path,
    telegram_files: FakeTelegramFiles,
) -> None:
    make_admin(owner, admin_env, world.admin)
    setting(owner, world.admin, "payment_cards", [HUMO])
    setting(owner, world.admin, "price_uzs", 120_000)
    boss = chat_of(client, owner, world.owner_a)
    telegram_files.files["tg-receipt"] = jpeg(EXIF)
    was = subscription(owner, world.shop_a)

    shown = boss.say("/obuna")
    lines = shown.text.split("\n")
    assert lines[-2] == say("uz", "sub_pay_to", label="Humo · Anorbank", card="8600 1234 1234 1234")
    assert lines[-2] == (
        "To'lov uchun karta — Humo · Anorbank: 8600 1234 1234 1234. O'tkazmadan so'ng chekni shu yerga yuboring."
    )
    assert lines[-1] == say("uz", "sub_choose_months")
    # One card: nothing else to choose from, so no button leads to other cards.
    assert shown.buttons == {
        f"{months} oy — {money('uz', 120_000 * months)}": card_months_button(world, months, 0, CARD[-4:])
        for months in (1, 3, 6, 12)
    }

    asked = boss.press(card_months_button(world, 3, 0, CARD[-4:]))
    assert asked.text.splitlines() == [
        say("uz", "ask_sub_receipt", shop="Shop A", months=3, amount=money("uz", 360_000)),
        f"Karta: {HUMO_TAG}",
    ]
    assert asked.buttons == {"Bekor": CANCEL}
    assert pending(owner, world) == [{"shop": world.shop_a.hex, "months": 3, "amount": 360_000, "card": HUMO_TAG}]
    assert rows(owner, world.shop_a) == [] and telegram_files.asked == [], "nothing is stored or fetched yet"

    sent = send_file(boss, **photo("tg-receipt", len(jpeg(EXIF))))
    assert sent.text == say("uz", "sub_receipt_sent", shop="Shop A", months=3, amount=money("uz", 360_000))
    assert telegram_files.asked == ["tg-receipt"]
    assert rows(owner, world.shop_a) == [(360_000, 3, "submitted", None, None, None, True)]
    assert [path.read_bytes() for path in stored_objects(file_root)] == [JPEG], "kept without its metadata"
    assert subscription(owner, world.shop_a) == was
    receipt = owner.execute("SELECT id FROM subscription_receipt WHERE shop_id = %s", (world.shop_a,)).fetchone()
    assert receipt is not None
    told = announced(owner, str(receipt[0]))
    assert set(told) == {tg(owner, world.admin)}
    assert told[tg(owner, world.admin)].splitlines()[1] == f"Karta: {HUMO_TAG}"
    assert CARD not in told[tg(owner, world.admin)], "the reviewer is told the label and four digits only"
    assert paid_to(owner, world.shop_a) == [HUMO_TAG]

    # The question is answered: a second photo is not a second receipt.
    assert send_file(boss, **photo("tg-receipt", 10)).text == say("uz", "only_text")
    assert len(rows(owner, world.shop_a)) == 1


def test_a_document_is_accepted_and_a_redelivered_update_sends_one_receipt(
    client: TestClient, world: World, owner: psycopg.Connection, file_root: Path, telegram_files: FakeTelegramFiles
) -> None:
    setting(owner, world.owner_a, "payment_cards", [HUMO])
    boss = chat_of(client, owner, world.owner_a)
    telegram_files.files["tg-pdf"] = PDF
    boss.press(months_button(world, 1))
    update_id = 7_000_000 + uuid.uuid4().int % 1_000_000
    first = send_file(boss, update_id=update_id, document={"file_id": "tg-pdf", "file_size": len(PDF)})
    assert first.text == say("uz", "sub_receipt_sent", shop="Shop A", months=1, amount=money("uz", 100_000))
    send_file(boss, update_id=update_id, document={"file_id": "tg-pdf", "file_size": len(PDF)})
    assert rows(owner, world.shop_a) == [(100_000, 1, "submitted", None, None, None, True)]
    assert len(stored_objects(file_root)) == 1
    # The button was one from before a card could be chosen: which card was paid to is not known.
    assert paid_to(owner, world.shop_a) == [None]


def test_a_file_that_is_no_receipt_is_refused_and_the_question_stays_open(
    client: TestClient, world: World, owner: psycopg.Connection, file_root: Path, telegram_files: FakeTelegramFiles
) -> None:
    setting(owner, world.owner_a, "payment_cards", [HUMO])
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
    setting(owner, world.owner_a, "payment_cards", [HUMO])
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
    setting(owner, world.owner_a, "payment_cards", [HUMO])
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
    setting(owner, world.owner_a, "payment_cards", [HUMO])
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


# --- several cards to pay to: the primary first, the others behind one button ---------------------------


def test_the_primary_card_is_shown_first_and_the_others_are_one_press_away(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    setting(owner, world.owner_a, "payment_cards", [HUMO, UZCARD, VISA])
    boss = chat_of(client, owner, world.owner_a)

    shown = boss.say("/obuna")
    assert shown.text.split("\n")[-2] == say("uz", "sub_pay_to", label="Humo · Anorbank", card="8600 1234 1234 1234")
    assert UZCARD["number"] not in shown.text and "5614" not in shown.text, "only the primary card is shown"
    months = {
        f"{count} oy — {money('uz', 100_000 * count)}": card_months_button(world, count, 0, "1234")
        for count in (1, 3, 6, 12)
    }
    assert shown.buttons == {**months, "Boshqa karta (2)": other_cards(world, 0, "1234")}
    assert list(shown.buttons)[-1] == "Boshqa karta (2)", "under the periods"

    # The others, each by its label and last four digits, and the way back. The message is replaced.
    listed = boss.press(other_cards(world, 0, "1234"))
    assert listed.payloads[0]["method"] == "editMessageText"
    assert listed.text == say("uz", "sub_choose_card")
    assert listed.buttons == {
        UZCARD_TAG: card_button(world, 1, "7890"),
        VISA_TAG: card_button(world, 2, "5678"),
        "⬅️ Orqaga": card_button(world, 0, "1234"),
    }
    for number in (HUMO["number"], UZCARD["number"], VISA["number"]):
        assert number not in str(listed.payloads), "no whole number in the list or in a button"

    # Back: the offer as it was.
    back = boss.press(card_button(world, 0, "1234"))
    assert (back.text, back.buttons) == (shown.text, shown.buttons)

    # Choosing one shows it in full as the card to pay to, and the periods now carry it.
    chosen = boss.press(card_button(world, 2, "5678"))
    assert chosen.payloads[0]["method"] == "editMessageText"
    assert chosen.text.split("\n")[-2] == say("uz", "sub_pay_to", label="Visa · Ipak Yo'li", card="4278 3100 1234 5678")
    assert chosen.text.split("\n")[:-2] == shown.text.split("\n")[:-2], "the rest of /obuna is as it was"
    assert HUMO["number"][:12] not in chosen.text.replace(" ", "")
    assert chosen.buttons == {
        **{
            label: card_months_button(world, count, 2, "5678")
            for label, count in zip(months, (1, 3, 6, 12), strict=True)
        },
        "Boshqa karta (2)": other_cards(world, 2, "5678"),
    }
    # From there the others are the two it is not.
    assert boss.press(other_cards(world, 2, "5678")).buttons == {
        HUMO_TAG: card_button(world, 0, "1234"),
        UZCARD_TAG: card_button(world, 1, "7890"),
        "⬅️ Orqaga": card_button(world, 2, "5678"),
    }
    assert pending(owner, world) == [], "looking at cards asks for nothing yet"


def test_the_chosen_card_travels_with_the_period_into_the_receipt_and_to_the_reviewers(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    telegram_files: FakeTelegramFiles,
) -> None:
    make_admin(owner, admin_env, world.admin)
    setting(owner, world.admin, "payment_cards", [HUMO, UZCARD])
    boss = chat_of(client, owner, world.owner_a)
    telegram_files.files["tg-good"] = JPEG

    boss.say("/obuna")
    boss.press(other_cards(world, 0, "1234"))
    boss.press(card_button(world, 1, "7890"))
    asked = boss.press(card_months_button(world, 6, 1, "7890"))
    assert asked.text.splitlines() == [
        say("uz", "ask_sub_receipt", shop="Shop A", months=6, amount=money("uz", 600_000)),
        f"Karta: {UZCARD_TAG}",
    ]
    assert pending(owner, world) == [{"shop": world.shop_a.hex, "months": 6, "amount": 600_000, "card": UZCARD_TAG}]
    assert UZCARD["number"] not in str(pending(owner, world)), "the question remembers no whole number"

    # The administrator takes the card off the list while the owner is at the bank: the receipt still
    # says what the owner chose and paid to.
    setting(owner, world.admin, "payment_cards", [HUMO])
    assert send_file(boss, **photo("tg-good", len(JPEG))).text.startswith("✅")
    assert paid_to(owner, world.shop_a) == [UZCARD_TAG]
    receipt = owner.execute("SELECT id FROM subscription_receipt WHERE shop_id = %s", (world.shop_a,)).fetchone()
    assert receipt is not None
    (text,) = announced(owner, str(receipt[0])).values()
    # (A third line may follow: this sample image is sent by other tests as well.)
    assert text.splitlines()[:2] == [
        say("uz", "a_receipt_new", shop="Shop A", amount=money("uz", 600_000), months=6),
        f"Karta: {UZCARD_TAG}",
    ]
    assert UZCARD["number"] not in text and HUMO_TAG not in text


def test_a_button_from_before_the_list_changed_shows_what_is_offered_now(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    setting(owner, world.owner_a, "payment_cards", [HUMO, UZCARD, VISA])
    boss = chat_of(client, owner, world.owner_a)
    boss.say("/obuna")
    # The administrator removes the second card: the third moves into its place.
    setting(owner, world.owner_a, "payment_cards", [HUMO, VISA])
    current = boss.say("/obuna")
    assert current.buttons["Boshqa karta (1)"] == other_cards(world, 0, "1234")

    stale = [
        card_button(world, 1, "7890"),  # the place now holds another card
        card_button(world, 2, "5678"),  # the card is still offered, but not at that place
        card_button(world, 7, "1234"),  # no such place
        card_button(world, 0, "9999"),
        other_cards(world, 1, "7890"),
        other_cards(world, 2, "5678"),
        card_months_button(world, 3, 1, "7890"),
        card_months_button(world, 3, 2, "5678"),
        # Parts that are no place at all: not a number, a sign, digits a card does not carry, too long.
        card_button(world, "x", "1234"),
        card_button(world, "-1", "1234"),
        card_button(world, "١", "5678"),
        card_months_button(world, 3, "001", "5678"),
    ]
    for button in stale:
        again = boss.press(button)
        assert again.payloads[0]["method"] == "editMessageText", button
        assert (again.text, again.buttons) == (current.text, current.buttons), button
        assert UZCARD["label"] not in str(again.payloads), button
    assert pending(owner, world) == [], "a period under a card that is gone asks for no receipt"
    assert paid_to(owner, world.shop_a) == []

    # The buttons of the offer shown now work.
    assert boss.press(card_button(world, 1, "5678")).text.split("\n")[-2] == say(
        "uz", "sub_pay_to", label="Visa · Ipak Yo'li", card="4278 3100 1234 5678"
    )
    boss.press(card_months_button(world, 1, 1, "5678"))
    assert [question["card"] for question in pending(owner, world)] == [VISA_TAG]


def test_when_one_card_is_left_the_list_of_others_is_the_offer_itself(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    setting(owner, world.owner_a, "payment_cards", [HUMO])
    boss = chat_of(client, owner, world.owner_a)
    current = boss.say("/obuna")
    assert not any("Boshqa karta" in label for label in current.buttons)
    # "Other cards" kept from when there were two: there are no others to list.
    again = boss.press(other_cards(world, 0, "1234"))
    assert (again.text, again.buttons) == (current.text, current.buttons)
    # And with no card left at all, nothing is offered.
    setting(owner, world.owner_a, "payment_cards", [])
    for button in (
        other_cards(world, 0, "1234"),
        card_button(world, 0, "1234"),
        card_months_button(world, 1, 0, "1234"),
    ):
        gone = boss.press(button)
        assert gone.text.split("\n")[-1] == say("uz", "sub_no_card") and gone.buttons == {}
    assert pending(owner, world) == []


def test_only_the_owner_sees_the_cards_of_a_shop(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    setting(owner, world.owner_a, "payment_cards", [HUMO, UZCARD])
    for button in (other_cards(world, 0, "1234"), card_button(world, 1, "7890")):
        for staff in (world.manager_a, world.seller_a):
            assert chat_of(client, owner, staff).press(button).text == say("uz", "forbidden")
        assert chat_of(client, owner, world.owner_b).press(button).text == say("uz", "expired")
    boss = chat_of(client, owner, world.owner_a)
    for button in ("v2:sro:zz:0:1234", "v2:src:zz:0:1234"):
        assert boss.press(button).text == say("uz", "expired")
    # A part missing or one too many: not a button of this bot.
    for button in (
        f"v2:sro:{world.shop_a.hex}:0",
        f"v2:src:{world.shop_a.hex}:0:1234:5",
        f"v2:srm:{world.shop_a.hex}:1:0",
    ):
        assert "text" not in boss.press(button).payloads[0], button


def test_every_button_fits_telegrams_limit_with_ten_cards_and_the_longest_labels(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    cards = [{"number": f"86001234567890{place:02d}", "label": "Ж" * 40} for place in range(10)]
    setting(owner, world.owner_a, "payment_cards", cards)
    boss = chat_of(client, owner, world.owner_a)
    shown = boss.say("/obuna")
    assert shown.buttons["Boshqa karta (9)"] == other_cards(world, 0, "9000")
    listed = boss.press(other_cards(world, 0, "9000"))
    assert len(listed.payloads[0]["reply_markup"]["inline_keyboard"]) == 10, "nine other cards and the way back"
    last = boss.press(card_button(world, 9, "9009"))
    for said in (shown, listed, last):
        for row in said.payloads[0]["reply_markup"]["inline_keyboard"]:
            for button in row:
                assert len(button["callback_data"].encode()) <= 64, button
                assert len(button["text"]) <= 64


def test_the_cards_are_offered_in_russian_too(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    setting(owner, world.owner_a, "payment_cards", [HUMO, UZCARD])
    owner.execute("UPDATE app_user SET lang = 'ru' WHERE id = %s", (world.owner_a,))
    boss = chat_of(client, owner, world.owner_a)
    shown = boss.say("/obuna")
    assert shown.text.split("\n")[-2] == (
        "Карта для оплаты — Humo · Anorbank: 8600 1234 1234 1234. После перевода отправьте чек сюда."
    )
    assert shown.buttons["Другая карта (1)"] == other_cards(world, 0, "1234")
    listed = boss.press(other_cards(world, 0, "1234"))
    assert listed.text == "На какую карту будете платить? Выберите."
    assert listed.buttons == {UZCARD_TAG: card_button(world, 1, "7890"), "⬅️ Назад": card_button(world, 0, "1234")}
    asked = boss.press(card_months_button(world, 1, 1, "7890"))
    assert asked.text.splitlines()[1] == f"Карта: {UZCARD_TAG}"

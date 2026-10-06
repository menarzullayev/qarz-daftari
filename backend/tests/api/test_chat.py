"""The staff chat: fast entry, customer questions, date choices, reversal, shops (REQ-006, REQ-008, REQ-064).

Updates go through the real webhook; what the bot would say is read from the outbox.
"""

import itertools
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from qarz.application.chat_texts import day, money, say
from qarz.domain.promise import end_of_week, tashkent_date

from .conftest import WEBHOOK_SECRET, World

pytestmark = pytest.mark.db

SECRET = {"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET}
_update_ids = itertools.count(7_000_000_000)
_message_ids = itertools.count(500)


def today() -> Any:
    return tashkent_date(datetime.now(UTC))


@dataclass
class Said:
    update_id: int
    payloads: list[dict[str, Any]]
    answer: dict[str, Any] | None

    @property
    def text(self) -> str:
        assert len(self.payloads) >= 1, "the bot said nothing"
        return str(self.payloads[0]["text"])

    @property
    def buttons(self) -> dict[str, str]:
        """Label -> callback data of the last message that carries buttons."""
        for payload in reversed(self.payloads):
            if "reply_markup" in payload:
                return {
                    button["text"]: button["callback_data"]
                    for row in payload["reply_markup"]["inline_keyboard"]
                    for button in row
                }
        return {}

    def button(self, label_start: str) -> str:
        matches = [data for label, data in self.buttons.items() if label.startswith(label_start)]
        assert len(matches) == 1, (label_start, self.buttons)
        return matches[0]


class Chat:
    """One person's private chat with the bot."""

    def __init__(self, client: TestClient, owner: psycopg.Connection, tg_id: int, language: str = "uz") -> None:
        self.client, self.owner, self.tg_id, self.language = client, owner, tg_id, language
        self.last_message_id = 0

    def _post(self, update: dict[str, Any]) -> Said:
        response = self.client.post("/tg/webhook", json=update, headers=SECRET)
        assert response.status_code == 200, response.text
        rows = self.owner.execute(
            "SELECT payload FROM outbox_message WHERE recipient = %s AND dedupe_key LIKE %s ORDER BY dedupe_key",
            (str(self.tg_id), f"update:{update['update_id']}:reply%"),
        ).fetchall()
        return Said(update["update_id"], [row[0] for row in rows], response.json() if response.content else None)

    def _from(self) -> dict[str, Any]:
        return {"id": self.tg_id, "language_code": self.language}

    def say(self, text: str, update_id: int | None = None) -> Said:
        self.last_message_id = next(_message_ids)
        return self._post(
            {
                "update_id": update_id or next(_update_ids),
                "message": {
                    "message_id": self.last_message_id,
                    "chat": {"id": self.tg_id, "type": "private"},
                    "from": self._from(),
                    "text": text,
                },
            }
        )

    def press(self, data: str, message_id: int = 900) -> Said:
        return self._post(
            {
                "update_id": next(_update_ids),
                "callback_query": {
                    "id": f"cb-{uuid.uuid4().hex}",
                    "from": self._from(),
                    "message": {"message_id": message_id, "chat": {"id": self.tg_id, "type": "private"}},
                    "data": data,
                },
            }
        )


def chat_of(client: TestClient, owner: psycopg.Connection, user_id: uuid.UUID) -> Chat:
    row = owner.execute("SELECT tg_id FROM app_user WHERE id = %s", (user_id,)).fetchone()
    assert row is not None
    return Chat(client, owner, int(row[0]))


def entries(owner: psycopg.Connection, shop_id: uuid.UUID) -> list[tuple[Any, ...]]:
    return owner.execute(
        "SELECT c.display_name, e.kind, e.amount, e.author_id "
        "FROM ledger_entry e JOIN customer c ON c.id = e.customer_id "
        "WHERE e.shop_id = %s ORDER BY e.created_at, e.seq",
        (shop_id,),
    ).fetchall()


def named(owner: psycopg.Connection, world: World, name: str) -> tuple[int] | None:
    """How many customers of shop A carry this name."""
    row = owner.execute(
        "SELECT count(*) FROM customer WHERE shop_id = %s AND display_name = %s", (world.shop_a, name)
    ).fetchone()
    return None if row is None else (int(row[0]),)


def promises(owner: psycopg.Connection, entry_id: uuid.UUID) -> list[tuple[Any, ...]]:
    return owner.execute(
        "SELECT actor, promised_date FROM promise WHERE entry_id = %s ORDER BY created_at, id", (entry_id,)
    ).fetchall()


def last_entry(owner: psycopg.Connection, shop_id: uuid.UUID) -> uuid.UUID:
    row = owner.execute(
        "SELECT id FROM ledger_entry WHERE shop_id = %s ORDER BY created_at DESC, seq DESC LIMIT 1", (shop_id,)
    ).fetchone()
    assert row is not None
    return uuid.UUID(str(row[0]))


# --- fast entry ---------------------------------------------------------------------------------------


def test_one_message_records_a_credit_sale_and_the_reply_shows_the_balance(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    seller = chat_of(client, owner, world.seller_a)
    said = seller.say("Ali 30000 non va sut")
    assert said.text == say(
        "uz",
        "credit_saved",
        shop="Shop A",  # the active shop is named in every reply (REQ-064)
        name="Ali",
        amount=money("uz", 30000),
        balance=money("uz", 80000),
        date=day(today() + timedelta(days=30)),
    )
    assert len(said.payloads) == 1  # one message, one reply (REQ-N02)
    assert entries(owner, world.shop_a)[-1] == ("Ali", "credit", 30000, world.seller_a_membership)
    assert owner.execute(
        "SELECT note FROM ledger_entry WHERE id = %s", (last_entry(owner, world.shop_a),)
    ).fetchone() == ("non va sut",)
    # One-tap date choices; a seller is not offered reversal.
    assert list(said.buttons) == ["Ertaga", "Hafta oxiri", "2 hafta", "1 oy", "📅 Boshqa sana"]
    assert all(len(data.encode()) <= 64 and data.startswith("v2:") for data in said.buttons.values())


def test_a_manager_is_offered_reversal_and_a_seller_is_not(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    assert "↩️ Bekor qilish" in chat_of(client, owner, world.manager_a).say("Ali 1000").buttons
    assert "↩️ Bekor qilish" in chat_of(client, owner, world.owner_a).say("Ali 1000").buttons
    assert "↩️ Bekor qilish" not in chat_of(client, owner, world.seller_a).say("Ali 1000").buttons


@pytest.mark.parametrize("message", ["Ali -20000", "Ali 20000 berdi", "Али 20 000 оплатил", "ALI 20k to'ladi"])
def test_a_payment_is_recorded_from_chat(
    client: TestClient, world: World, owner: psycopg.Connection, message: str
) -> None:
    said = chat_of(client, owner, world.seller_a).say(message)
    assert said.text == say(
        "uz", "payment_saved", shop="Shop A", name="Ali", amount=money("uz", 20000), balance=money("uz", 30000)
    )
    assert entries(owner, world.shop_a)[-1] == ("Ali", "payment", 20000, world.seller_a_membership)
    assert said.buttons == {}


def test_a_payment_above_the_balance_is_refused_in_words(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    before = entries(owner, world.shop_a)
    said = chat_of(client, owner, world.seller_a).say("Ali -50100")
    assert said.text == say("uz", "EXCEEDS_BALANCE")
    assert entries(owner, world.shop_a) == before


@pytest.mark.parametrize(
    ("message", "key"),
    [
        ("salom", "parse_hint"),
        ("45000", "parse_hint"),
        ("Ali 45,5", "parse_amount_not_whole"),
        ("Ali 2 45000", "parse_ambiguous"),
        ("Ali 50", "amount_range"),
        ("Ali 999999999999", "amount_range"),
    ],
)
def test_what_cannot_be_understood_is_answered_with_a_hint_and_nothing_is_stored(
    client: TestClient, world: World, owner: psycopg.Connection, message: str, key: str
) -> None:
    before = entries(owner, world.shop_a)
    pending = owner.execute("SELECT count(*) FROM chat_pending").fetchone()
    said = chat_of(client, owner, world.seller_a).say(message)
    assert said.text == say("uz", key)
    assert entries(owner, world.shop_a) == before
    assert owner.execute("SELECT count(*) FROM chat_pending").fetchone() == pending


def test_replies_follow_the_persons_language(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    owner.execute("UPDATE app_user SET lang = 'ru' WHERE id = %s", (world.seller_a,))
    said = chat_of(client, owner, world.seller_a).say("Али 1000")
    assert said.text.startswith("✅ Shop A\nAli: +1 000 сум\nВсего долг: 51 000 сум")
    assert "Завтра" in said.buttons


def test_in_limited_mode_chat_refuses_credit_and_still_takes_payments(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    owner.execute("UPDATE subscription SET state = 'limited' WHERE shop_id = %s", (world.shop_a,))
    seller = chat_of(client, owner, world.seller_a)
    before = entries(owner, world.shop_a)
    assert seller.say("Ali 1000").text == say("uz", "SUBSCRIPTION_LIMITED")
    assert seller.say("Yangi 1000").text == say("uz", "SUBSCRIPTION_LIMITED")  # refused before any question
    assert entries(owner, world.shop_a) == before
    assert seller.say("Ali -1000").text.startswith("✅ Shop A")


# --- unknown and ambiguous names (REQ-006) -------------------------------------------------------------


def test_an_unknown_name_offers_to_create_the_customer(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    seller = chat_of(client, owner, world.seller_a)
    before = entries(owner, world.shop_a)
    asked = seller.say("Dilshod 45000 un")
    assert asked.text == say("uz", "unknown_customer_credit", shop="Shop A", name="Dilshod", amount=money("uz", 45000))
    assert entries(owner, world.shop_a) == before, "nothing is written until the seller answers"
    assert named(owner, world, "Dilshod") == (0,)

    done = seller.press(asked.button("➕ Qo'shish"), seller.last_message_id)
    assert done.answer is not None and done.answer["method"] == "answerCallbackQuery"
    assert done.payloads[0]["method"] == "editMessageText"
    assert done.payloads[0]["message_id"] == seller.last_message_id
    assert done.text.startswith("✅ Shop A\nDilshod: +45 000 so'm\nJami qarzi: 45 000 so'm")
    assert entries(owner, world.shop_a)[-1] == ("Dilshod", "credit", 45000, world.seller_a_membership)
    assert "Ertaga" in done.buttons

    # The same button again does nothing more.
    again = seller.press(asked.button("➕ Qo'shish"), seller.last_message_id)
    assert again.text == say("uz", "expired")
    assert len(entries(owner, world.shop_a)) == len(before) + 1
    assert named(owner, world, "Dilshod") == (1,)


def test_a_question_can_be_cancelled(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    seller = chat_of(client, owner, world.seller_a)
    before = entries(owner, world.shop_a)
    asked = seller.say("Dilshod 45000")
    assert seller.press(asked.button("Bekor")).text == say("uz", "cancelled")
    assert seller.press(asked.button("➕ Qo'shish")).text == say("uz", "expired")
    assert entries(owner, world.shop_a) == before
    assert named(owner, world, "Dilshod") == (0,)


def test_a_payment_for_an_unknown_name_is_not_offered(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    said = chat_of(client, owner, world.seller_a).say("Dilshod -5000")
    assert said.text == say("uz", "unknown_customer_payment", shop="Shop A", name="Dilshod")
    assert said.buttons == {}
    assert named(owner, world, "Dilshod") == (0,)


def test_part_of_a_name_asks_which_customer(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    seller = chat_of(client, owner, world.seller_a)
    before = entries(owner, world.shop_a)
    asked = seller.say("Val 7000")
    assert asked.text == say("uz", "pick_customer", shop="Shop A", name="Val", amount=money("uz", 7000))
    assert list(asked.buttons) == [f"Vali · {money('uz', 0)}", "➕ Yangi mijoz: Val", "Bekor"]
    assert entries(owner, world.shop_a) == before

    done = seller.press(asked.button("Vali"))
    assert done.text.startswith("✅ Shop A\nVali: +7 000")
    assert entries(owner, world.shop_a)[-1] == ("Vali", "credit", 7000, world.seller_a_membership)


def test_two_customers_with_the_same_name_are_told_apart_by_balance(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    twin = uuid.uuid4()
    owner.execute(
        "INSERT INTO customer (id, shop_id, display_name, name_norm) VALUES (%s, %s, 'Ali', 'ali')",
        (twin, world.shop_a),
    )
    seller = chat_of(client, owner, world.seller_a)
    asked = seller.say("Ali 3000")
    assert list(asked.buttons)[:2] == [f"Ali · {money('uz', 50000)}", f"Ali · {money('uz', 0)}"]
    seller.press(asked.buttons[f"Ali · {money('uz', 0)}"])
    row = owner.execute("SELECT customer_id, amount FROM ledger_entry WHERE customer_id = %s", (twin,)).fetchall()
    assert row == [(twin, 3000)]


def test_a_button_offers_only_what_was_listed_and_only_to_who_asked(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    seller, manager = chat_of(client, owner, world.seller_a), chat_of(client, owner, world.manager_a)
    before = entries(owner, world.shop_a)
    asked = seller.say("Val 7000")
    pick = asked.button("Vali")  # v2:pk:<pending>:0
    pending = pick.split(":")[2]

    # A forged index, and another member pressing someone else's question.
    assert seller.press(f"v2:pk:{pending}:7").text == say("uz", "expired")
    asked = seller.say("Val 7000")
    pick = asked.button("Vali")
    assert manager.press(pick).text == say("uz", "expired")
    assert manager.press(asked.button("➕ Yangi mijoz")).text == say("uz", "expired")
    assert entries(owner, world.shop_a) == before
    # The manager's attempt did not use it up: the seller can still answer.
    assert seller.press(pick).text.startswith("✅ Shop A\nVali")


def test_a_question_expires(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    seller = chat_of(client, owner, world.seller_a)
    before = entries(owner, world.shop_a)
    asked = seller.say("Dilshod 45000")
    owner.execute(
        "UPDATE chat_pending SET created_at = now() - interval '20 minutes', expires_at = now() - interval '5 minutes' "
        "WHERE user_id = %s",
        (world.seller_a,),
    )
    assert seller.press(asked.button("➕ Qo'shish")).text == say("uz", "expired")
    assert entries(owner, world.shop_a) == before


@pytest.mark.parametrize(
    "data", ["", "v1:nc:x", "v2", "v2:nc", "v2:nc:zzz", "v2:pd:zz:t", "v2:rvok:" + "0" * 32, "junk"]
)
def test_malformed_or_unknown_button_data_changes_nothing(
    client: TestClient, world: World, owner: psycopg.Connection, data: str
) -> None:
    before = entries(owner, world.shop_a)
    said = chat_of(client, owner, world.owner_a).press(data)
    assert said.answer is not None
    assert entries(owner, world.shop_a) == before


# --- promised date (REQ-008) --------------------------------------------------------------------------


def test_the_author_picks_the_date_with_one_tap_once(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    seller = chat_of(client, owner, world.seller_a)
    saved = seller.say("Ali 1000")
    entry = last_entry(owner, world.shop_a)
    assert promises(owner, entry) == [("default", today() + timedelta(days=30))]

    chosen = seller.press(saved.button("Hafta oxiri"), seller.last_message_id)
    assert chosen.payloads[0]["method"] == "editMessageText"
    assert chosen.text.endswith(f"To'lash muddati: {day(end_of_week(today()))}")
    assert chosen.buttons == {}, "the choices are gone once one is taken"
    assert promises(owner, entry) == [("default", today() + timedelta(days=30)), ("staff", end_of_week(today()))]

    again = seller.press(saved.button("Ertaga"), seller.last_message_id)
    assert again.text == say("uz", "promise_closed")
    assert len(promises(owner, entry)) == 2


def test_another_date_can_be_typed(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    seller = chat_of(client, owner, world.seller_a)
    saved = seller.say("Ali 1000")
    entry = last_entry(owner, world.shop_a)
    assert seller.press(saved.button("📅 Boshqa sana")).text == say("uz", "ask_date")

    target = today() + timedelta(days=12)
    typed = seller.say(target.strftime("%d.%m"))
    assert typed.text.endswith(f"To'lash muddati: {day(target)}")
    assert promises(owner, entry)[-1] == ("staff", target)
    # The question is answered: the next date-like message is not taken as a date again.
    assert seller.say(target.strftime("%d.%m")).text == say("uz", "parse_hint")


def test_a_typed_date_outside_the_allowed_range_is_refused(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    seller = chat_of(client, owner, world.seller_a)
    saved = seller.say("Ali 1000")
    entry = last_entry(owner, world.shop_a)
    seller.press(saved.button("📅 Boshqa sana"))
    assert seller.say("01.01.2020").text == say("uz", "PROMISE_BEFORE_SALE")
    assert len(promises(owner, entry)) == 1


def test_while_a_date_is_awaited_an_ordinary_entry_still_works(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    seller = chat_of(client, owner, world.seller_a)
    saved = seller.say("Ali 1000")
    seller.press(saved.button("📅 Boshqa sana"))
    assert seller.say("Ali 2000").text.startswith("✅ Shop A\nAli: +2 000")


def test_someone_who_did_not_write_the_entry_cannot_set_its_date_unless_a_manager(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    other_seller = uuid.uuid4()
    owner.execute("INSERT INTO app_user (id, tg_id, lang) VALUES (%s, %s, 'uz')", (other_seller, 77_000_001))
    owner.execute(
        "INSERT INTO membership (id, shop_id, user_id, role, status) VALUES (%s, %s, %s, 'seller', 'active')",
        (uuid.uuid4(), world.shop_a, other_seller),
    )
    saved = chat_of(client, owner, world.seller_a).say("Ali 1000")
    entry = last_entry(owner, world.shop_a)

    refused = Chat(client, owner, 77_000_001).press(saved.button("Ertaga"))
    assert refused.text == say("uz", "forbidden")
    assert len(promises(owner, entry)) == 1

    by_manager = chat_of(client, owner, world.manager_a).press(saved.button("Ertaga"))
    assert by_manager.text.endswith(day(today() + timedelta(days=1)))
    assert len(promises(owner, entry)) == 2


# --- reversal from chat --------------------------------------------------------------------------------


def test_a_manager_reverses_from_chat_after_confirming(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    manager = chat_of(client, owner, world.manager_a)
    saved = manager.say("Ali 4000")
    count_before = len(entries(owner, world.shop_a))

    asked = manager.press(saved.button("↩️"), manager.last_message_id)
    assert asked.payloads[0]["method"] == "editMessageReplyMarkup"
    assert list(asked.buttons) == ["Ha, bekor qilinsin", "Yo'q"]
    assert len(entries(owner, world.shop_a)) == count_before, "asking is not doing"

    done = manager.press(asked.button("Ha"), manager.last_message_id)
    assert done.text == say(
        "uz", "reversed", shop="Shop A", name="Ali", amount=money("uz", 4000), balance=money("uz", 50000)
    )
    assert entries(owner, world.shop_a)[-1] == ("Ali", "reversal", 4000, world.manager_a_membership)
    assert manager.press(asked.button("Ha")).text == say("uz", "ALREADY_REVERSED")
    assert len(entries(owner, world.shop_a)) == count_before + 1


def test_declining_the_confirmation_reverses_nothing(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    manager = chat_of(client, owner, world.manager_a)
    saved = manager.say("Ali 4000")
    before = entries(owner, world.shop_a)
    asked = manager.press(saved.button("↩️"), manager.last_message_id)
    manager.press(asked.button("Yo'q"), manager.last_message_id)
    assert entries(owner, world.shop_a) == before


def test_a_seller_cannot_reverse_by_forging_the_button(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    before = entries(owner, world.shop_a)
    said = chat_of(client, owner, world.seller_a).press(f"v2:rvok:{world.entry_a.hex}")
    assert said.text == say("uz", "forbidden")
    assert entries(owner, world.shop_a) == before


@pytest.mark.parametrize("action", ["rvok:{entry}", "pd:{entry}:t"])
def test_buttons_do_not_reach_another_shops_entry(
    client: TestClient, world: World, owner: psycopg.Connection, action: str
) -> None:
    """owner_b, the most privileged member of shop B, presses a button naming shop A's entry."""
    before = (entries(owner, world.shop_a), promises(owner, world.entry_a))
    said = chat_of(client, owner, world.owner_b).press("v2:" + action.format(entry=world.entry_a.hex))
    assert said.text == say("uz", "not_found")
    assert (entries(owner, world.shop_a), promises(owner, world.entry_a)) == before


# --- shops ---------------------------------------------------------------------------------------------


def test_a_new_person_opens_a_shop_and_records_the_first_sale(client: TestClient, owner: psycopg.Connection) -> None:
    person = Chat(client, owner, 88_000_001)
    welcome = person.say("/start")
    assert welcome.text == say("uz", "welcome_new")
    assert person.press(welcome.button("🏪")).text == say("uz", "ask_shop_name")

    created = person.say("  Baraka   do'koni ")
    assert created.text == say("uz", "shop_created", shop="Baraka do'koni")
    row = owner.execute(
        "SELECT s.id, m.role, sub.state, u.active_shop = s.id FROM shop s "
        "JOIN membership m ON m.shop_id = s.id JOIN app_user u ON u.id = m.user_id "
        "JOIN subscription sub ON sub.shop_id = s.id WHERE u.tg_id = 88000001"
    ).fetchall()
    assert [(r[1], r[2], r[3]) for r in row] == [("owner", "trial", True)]
    shop_id = row[0][0]

    asked = person.say("Ali 45000")
    done = person.press(asked.button("➕ Qo'shish"))
    assert done.text.startswith("✅ Baraka do'koni\nAli: +45 000")
    assert [e[:3] for e in entries(owner, shop_id)] == [("Ali", "credit", 45000)]
    # The next message is an entry, not another shop name.
    assert person.say("Ali 5000").text.startswith("✅ Baraka do'koni\nAli: +5 000")
    assert owner.execute(
        "SELECT count(*) FROM shop s JOIN membership m ON m.shop_id = s.id JOIN app_user u "
        "ON u.id = m.user_id WHERE u.tg_id = 88000001"
    ).fetchone() == (1,)


def test_a_shop_name_that_is_too_long_is_asked_again(client: TestClient, owner: psycopg.Connection) -> None:
    person = Chat(client, owner, 88_000_002)
    person.press(person.say("/start").button("🏪"))
    assert person.say("x" * 81).text == say("uz", "shop_name_invalid")
    assert person.say("Yaxshi nom").text == say("uz", "shop_created", shop="Yaxshi nom")


def test_someone_with_no_shop_cannot_record_anything(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    before = entries(owner, world.shop_a)
    for user in (world.stranger, world.customer_of_a, world.suspended_a):
        assert chat_of(client, owner, user).say("Ali 45000").text == say("uz", "welcome_new")
    assert entries(owner, world.shop_a) == before


def test_chat_entry_applies_to_the_chosen_shop_only(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    owner.execute(
        "INSERT INTO membership (id, shop_id, user_id, role, status) VALUES (%s, %s, %s, 'seller', 'active')",
        (uuid.uuid4(), world.shop_b, world.seller_a),
    )
    seller = chat_of(client, owner, world.seller_a)
    before = (entries(owner, world.shop_a), entries(owner, world.shop_b))

    asked = seller.say("Ali 1000")
    assert asked.text == say("uz", "choose_shop")
    assert sorted(asked.buttons) == ["Shop A", "Shop B"]
    assert (entries(owner, world.shop_a), entries(owner, world.shop_b)) == before, "no shop chosen, nothing written"

    assert seller.press(asked.buttons["Shop B"]).text == say("uz", "shop_switched", shop="Shop B")
    # Ali is a customer of shop A; in shop B the name is unknown.
    assert seller.say("Ali 1000").text.startswith("Shop B\n«Ali» degan mijoz topilmadi")
    assert seller.press(seller.say("/dokon").buttons["Shop A"]).text == say("uz", "shop_switched", shop="Shop A")
    assert seller.say("Ali 1000").text.startswith("✅ Shop A\nAli: +1 000")
    assert entries(owner, world.shop_b) == before[1]


def test_a_shop_one_is_not_a_member_of_cannot_be_made_active(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    seller = chat_of(client, owner, world.seller_a)
    for shop_id in (world.shop_b, uuid.uuid4()):
        assert seller.press(f"v2:shop:{shop_id.hex}").text == say("uz", "expired")
    assert owner.execute("SELECT active_shop FROM app_user WHERE id = %s", (world.seller_a,)).fetchone() == (None,)


def test_an_invitation_link_joins_the_shop(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    stranger = chat_of(client, owner, world.stranger)
    assert stranger.say("/start s_not-a-real-token-0123456789").text == say("uz", "invitation_invalid")
    assert stranger.say(f"/start s_{world.invitation_a_token}").text == say("uz", "joined_shop", shop="Shop A")
    assert owner.execute(
        "SELECT role, status FROM membership WHERE shop_id = %s AND user_id = %s", (world.shop_a, world.stranger)
    ).fetchone() == ("seller", "active")
    assert stranger.say("Ali 1000").text.startswith("✅ Shop A")
    # Used once.
    other = Chat(client, owner, 88_000_003)
    assert other.say(f"/start s_{world.invitation_a_token}").text == say("uz", "invitation_invalid")


def test_the_language_can_be_changed(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    seller = chat_of(client, owner, world.seller_a)
    asked = seller.say("/til")
    assert list(asked.buttons) == ["O'zbekcha", "Русский"]
    assert seller.press(asked.buttons["Русский"]).text == say("ru", "lang_set")
    assert owner.execute("SELECT lang FROM app_user WHERE id = %s", (world.seller_a,)).fetchone() == ("ru",)
    assert seller.say("/yordam").text == say("ru", "help")
    seller.press("v2:lang:en")  # an unknown language is ignored
    assert owner.execute("SELECT lang FROM app_user WHERE id = %s", (world.seller_a,)).fetchone() == ("ru",)


def test_commands(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    seller = chat_of(client, owner, world.seller_a)
    assert seller.say("/start").text == say("uz", "welcome_staff", shop="Shop A")
    assert seller.say("/yordam@qarzdaftari_dev_bot").text == say("uz", "help")
    assert seller.say("/obuna").text == say("uz", "soon")
    assert seller.say("/nimadir").text == say("uz", "help")


# --- delivery guarantees -------------------------------------------------------------------------------


def test_a_redelivered_message_writes_one_entry(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    seller = chat_of(client, owner, world.seller_a)
    before = len(entries(owner, world.shop_a))
    first = seller.say("Ali 1000", update_id=6_500_000_001)
    second = seller.say("Ali 1000", update_id=6_500_000_001)
    assert first.payloads == second.payloads
    assert len(entries(owner, world.shop_a)) == before + 1


def test_an_update_whose_reply_was_lost_is_not_written_twice(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    """The shop's transaction committed, then the update's own transaction failed: Telegram delivers again."""
    seller = chat_of(client, owner, world.seller_a)
    before = len(entries(owner, world.shop_a))
    first = seller.say("Ali 1000", update_id=6_500_000_002)
    owner.execute("DELETE FROM processed_update WHERE update_id = 6500000002")
    owner.execute("DELETE FROM outbox_message WHERE dedupe_key LIKE 'update:6500000002:%'")

    again = seller.say("Ali 1000", update_id=6_500_000_002)
    assert again.payloads == first.payloads, "the reply is produced again, with the same balance"
    assert len(entries(owner, world.shop_a)) == before + 1


def test_updates_that_are_not_a_persons_private_chat_are_ignored(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    before = entries(owner, world.shop_a)
    seller_tg = chat_of(client, owner, world.seller_a).tg_id
    updates = [
        # a group in which the seller writes
        {
            "message": {
                "message_id": 1,
                "chat": {"id": -100500, "type": "group"},
                "from": {"id": seller_tg},
                "text": "Ali 1000",
            }
        },
        # a group that claims the seller's own identifier as its chat identifier
        {
            "message": {
                "message_id": 1,
                "chat": {"id": seller_tg, "type": "group"},
                "from": {"id": seller_tg},
                "text": "Ali 1000",
            }
        },
        # a private chat whose sender is someone else than the chat
        {
            "message": {
                "message_id": 1,
                "chat": {"id": 424242, "type": "private"},
                "from": {"id": seller_tg},
                "text": "Ali 1000",
            }
        },
        # a button pressed under a message in a group
        {
            "callback_query": {
                "id": "cb-1",
                "from": {"id": seller_tg},
                "message": {"message_id": 1, "chat": {"id": -100500, "type": "group"}},
                "data": f"v2:rvok:{world.entry_a.hex}",
            }
        },
        # a button with no message at all
        {"callback_query": {"id": "cb-2", "from": {"id": seller_tg}, "data": f"v2:rvok:{world.entry_a.hex}"}},
    ]
    for update in updates:
        update_id = next(_update_ids)
        response = client.post("/tg/webhook", json={"update_id": update_id, **update}, headers=SECRET)
        assert response.status_code == 200
        assert owner.execute(
            "SELECT count(*) FROM outbox_message WHERE dedupe_key LIKE %s", (f"update:{update_id}:%",)
        ).fetchone() == (0,)
    assert entries(owner, world.shop_a) == before


def test_a_message_without_text_is_answered_and_ignored(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    seller = chat_of(client, owner, world.seller_a)
    update_id = next(_update_ids)
    update = {
        "update_id": update_id,
        "message": {
            "message_id": 5,
            "chat": {"id": seller.tg_id, "type": "private"},
            "from": {"id": seller.tg_id},
            "photo": [{"file_id": "x"}],
        },
    }
    assert client.post("/tg/webhook", json=update, headers=SECRET).status_code == 200
    row = owner.execute(
        "SELECT payload->>'text' FROM outbox_message WHERE dedupe_key = %s", (f"update:{update_id}:reply",)
    ).fetchone()
    assert row == (say("uz", "only_text"),)


def test_a_payment_is_never_offered_for_someone_who_owes_nothing(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    said = chat_of(client, owner, world.seller_a).say("Val -500")  # Vali matches, but owes nothing
    assert said.text == say("uz", "unknown_customer_payment", shop="Shop A", name="Val")
    assert said.buttons == {}


def test_a_payment_question_cannot_be_turned_into_a_new_customer(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    seller = chat_of(client, owner, world.seller_a)
    before = (entries(owner, world.shop_a), named(owner, world, "Al"))
    asked = seller.say("Al -1000")  # part of "Ali", who owes
    assert list(asked.buttons) == [f"Ali · {money('uz', 50000)}", "Bekor"]
    pending = asked.button("Ali").split(":")[2]
    assert seller.press(f"v2:nc:{pending}").text == say("uz", "expired")
    assert (entries(owner, world.shop_a), named(owner, world, "Al")) == before


def test_buttons_of_another_version_are_not_acted_on(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    seller = chat_of(client, owner, world.seller_a)
    asked = seller.say("Dilshod 45000")
    old = asked.button("➕ Qo'shish").replace("v2:", "v1:", 1)
    said = seller.press(old)
    assert all("text" not in payload for payload in said.payloads), "an unknown version gets no answer in words"
    assert named(owner, world, "Dilshod") == (0,)
    # The question itself is untouched and can still be answered.
    assert seller.press(asked.button("➕ Qo'shish")).text.startswith("✅ Shop A\nDilshod")

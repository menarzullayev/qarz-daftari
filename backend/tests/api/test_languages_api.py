"""Six languages through the API and the chat: choosing one, keeping it, and being answered in it.

The rules themselves are in `tests/test_languages.py`; this file proves that every place a language comes
in or goes out keeps to them: a person's own choice (`/til`, PATCH /me), the language a person starts in
(Telegram's `language_code`), a shop's language, the wording of a refusal, and the bot's replies.
"""

import zlib

import psycopg
import pytest
from fastapi.testclient import TestClient

from qarz.application import texts_en, texts_kaa, texts_tg
from qarz.application.chat_texts import LANGUAGE_NAMES, UZ, say
from qarz.domain.languages import LANGUAGES
from qarz.domain.uz_cyrillic import to_cyrillic

from .conftest import SessionClient, World, as_user
from .test_auth import ME, WEBAPP, _bearer, _fresh
from .test_chat import Chat, chat_of
from .test_shops import as_owner_writing

pytestmark = pytest.mark.db

FORBIDDEN = {
    "uz": "Bu amal uchun sizning rolingiz yetarli emas.",
    "uz-Cyrl": "Бу амал учун сизнинг ролингиз етарли эмас.",
    "ru": "Вашей роли недостаточно для этого действия.",
    "tg": texts_tg.ERRORS["FORBIDDEN_ROLE"],
    "kaa": texts_kaa.ERRORS["FORBIDDEN_ROLE"],
    "en": texts_en.ERRORS["FORBIDDEN_ROLE"],
}


def stored(owner: psycopg.Connection, user_id: object) -> str:
    row = owner.execute("SELECT lang FROM app_user WHERE id = %s", (user_id,)).fetchone()
    assert row is not None
    return str(row[0])


# --- a person's own choice ----------------------------------------------------------------------------


@pytest.mark.parametrize("lang", LANGUAGES)
def test_a_person_chooses_any_of_the_six_and_it_is_kept(
    client: TestClient, world: World, owner: psycopg.Connection, lang: str
) -> None:
    changed = client.patch(ME, json={"lang": lang}, headers=as_user(world.seller_a))
    assert changed.status_code == 200, changed.text
    assert changed.json()["lang"] == lang
    assert stored(owner, world.seller_a) == lang
    assert client.get(ME, headers=as_user(world.seller_a)).json()["lang"] == lang


@pytest.mark.parametrize("lang", ["de", "kk", "uz-cyrl", "uz_Cyrl", "UZ", "en-US", "", " uz"])
def test_anything_else_is_refused_and_changes_nothing(
    client: TestClient, world: World, owner: psycopg.Connection, lang: str
) -> None:
    refused = client.patch(ME, json={"lang": lang}, headers=as_user(world.seller_a))
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["fields"] == {"lang": "must be one of: uz, uz-Cyrl, ru, tg, kaa, en"}
    assert stored(owner, world.seller_a) == "uz"


@pytest.mark.parametrize("lang", LANGUAGES)
def test_a_refusal_is_worded_in_the_callers_language(
    client: TestClient, world: World, owner: psycopg.Connection, lang: str
) -> None:
    owner.execute("UPDATE app_user SET lang = %s WHERE id = %s", (lang, world.seller_a))
    refusal = client.get(f"/api/v1/shops/{world.shop_a}", headers=as_user(world.seller_a))
    assert refusal.status_code == 403
    assert refusal.json()["error"]["message"] == FORBIDDEN[lang]
    assert len(set(FORBIDDEN.values())) == len(LANGUAGES), "each language in its own words"


# --- the language a person starts in ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("telegram", "expected"),
    [
        ("uz", "uz"),
        ("ru", "ru"),
        ("tg", "tg"),
        ("en", "en"),
        ("en-US", "en"),
        ("kaa", "kaa"),
        ("kk", "kaa"),
        ("uz-Cyrl", "uz-Cyrl"),
        ("de", "uz"),
        ("fa", "uz"),
    ],
)
def test_a_new_person_starts_in_the_language_telegram_suggests(
    session_client: SessionClient, owner: psycopg.Connection, telegram: str, expected: str
) -> None:
    tg_id = 6_100_000 + zlib.crc32(telegram.encode()) % 800_000  # one person for each case, the same every run
    owner.execute("DELETE FROM app_user WHERE tg_id = %s", (tg_id,))
    signed_in = session_client.http.post(WEBAPP, json={"init_data": _fresh(tg_id=tg_id, language=telegram)})
    assert signed_in.status_code == 200, signed_in.text
    assert session_client.http.get(ME, headers=_bearer(signed_in.json()["token"])).json()["lang"] == expected
    assert owner.execute("SELECT lang FROM app_user WHERE tg_id = %s", (tg_id,)).fetchone() == (expected,)


def test_the_persons_own_choice_outlives_what_telegram_says(
    session_client: SessionClient, client: TestClient, owner: psycopg.Connection
) -> None:
    tg_id = 6_950_001
    first = session_client.http.post(WEBAPP, json={"init_data": _fresh(tg_id=tg_id, language="ru")})
    assert first.status_code == 200, first.text
    chat = Chat(client, owner, tg_id, language="ru")
    asked = chat.say("/til")
    assert chat.press(asked.buttons["Тоҷикӣ"]).text == say("tg", "lang_set")
    # Telegram goes on reporting Russian, at the next sign-in and in every message: Tajik stays.
    again = session_client.http.post(WEBAPP, json={"init_data": _fresh(tg_id=tg_id, language="ru")})
    assert session_client.http.get(ME, headers=_bearer(again.json()["token"])).json()["lang"] == "tg"
    assert chat.say("/yordam").text == say("tg", "help")
    chat.language = "en"
    assert chat.say("/yordam").text == say("tg", "help")


# --- the bot ---------------------------------------------------------------------------------------------


def test_the_bot_offers_the_six_languages_two_to_a_row(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    seller = chat_of(client, owner, world.seller_a)
    asked = seller.say("/til")
    assert asked.text == say("uz", "lang_prompt")
    assert list(asked.buttons) == list(LANGUAGE_NAMES.values())
    assert list(asked.buttons.values()) == [f"v2:lang:{code}" for code in LANGUAGES]
    rows = asked.payloads[0]["reply_markup"]["inline_keyboard"]
    assert [len(row) for row in rows] == [2, 2, 2]


@pytest.mark.parametrize("lang", LANGUAGES)
def test_the_bot_answers_in_the_language_chosen(
    client: TestClient, world: World, owner: psycopg.Connection, lang: str
) -> None:
    seller = chat_of(client, owner, world.seller_a)
    asked = seller.say("/til")
    assert seller.press(asked.buttons[LANGUAGE_NAMES[lang]]).text == say(lang, "lang_set")
    assert stored(owner, world.seller_a) == lang
    assert seller.say("/yordam").text == say(lang, "help")
    assert seller.say("/til").text == say(lang, "lang_prompt")
    if lang != "uz":
        assert say(lang, "help") != say("uz", "help")
    # What the person must type is the same in every language: the parser knows no other words.
    for typed in ("/dokon", "/til", "/yordam", "45000"):
        assert typed in say(lang, "help"), typed


def test_the_bot_writes_uzbek_cyrillic_by_rule_and_leaves_names_as_typed(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    owner.execute("UPDATE app_user SET lang = 'uz-Cyrl' WHERE id = %s", (world.seller_a,))
    said = chat_of(client, owner, world.seller_a).say("Ali 1000")
    nbsp = " "
    assert said.text.startswith(f"✅ Shop A\nAli: +1{nbsp}000{nbsp}сўм\nЖами қарзи: 51{nbsp}000{nbsp}сўм")
    assert to_cyrillic(UZ["tomorrow"]) in said.buttons


def test_a_payment_typed_in_cyrillic_is_understood(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    """The help text in Uzbek Cyrillic shows "Али 20000 берди": what it shows must work when typed."""
    owner.execute("UPDATE app_user SET lang = 'uz-Cyrl' WHERE id = %s", (world.seller_a,))
    help_text = say("uz-Cyrl", "help")
    assert "берди" in help_text and "/til" in help_text
    said = chat_of(client, owner, world.seller_a).say("Ali 1000 берди")
    assert said.text == say(
        "uz-Cyrl", "payment_saved", shop="Shop A", name="Ali", amount="1 000 сўм", balance="49 000 сўм"
    )


# --- a shop's language ------------------------------------------------------------------------------------


@pytest.mark.parametrize("lang", LANGUAGES)
def test_a_shop_speaks_any_of_the_six(client: TestClient, world: World, owner: psycopg.Connection, lang: str) -> None:
    changed = client.patch(f"/api/v1/shops/{world.shop_a}", json={"lang": lang}, headers=as_owner_writing(world))
    if lang == "uz":
        # Nothing to change: the shop already speaks Uzbek.
        assert changed.status_code in (200, 422), changed.text
    else:
        assert changed.status_code == 200, changed.text
        assert changed.json()["lang"] == lang
    assert owner.execute("SELECT lang FROM shop WHERE id = %s", (world.shop_a,)).fetchone() == (lang,)

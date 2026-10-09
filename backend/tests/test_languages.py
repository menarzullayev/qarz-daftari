"""Six languages on the server: which they are, how one is chosen, and what a language that trails says.

Uzbek and Russian mirror each other key for key (`test_chat_texts.py`, `test_export_rules.py`). Tajik,
Karakalpak and English may trail behind while other work adds Uzbek and Russian text: a text they lack is
read in Uzbek and is not a mistake. So they are checked here for what they have: no key that Uzbek does
not have, the same fields to fill in, no empty text. `scripts/i18n_missing.py` lists what is absent; the
final pass fills it and then makes completeness part of this file
(docs/10-operations/translation-review.md). Uzbek Cyrillic has no texts of its own: it is Uzbek, rewritten.
"""

import string
from collections.abc import Mapping

import pytest

from qarz.application import chat_texts, export_texts, texts_en, texts_kaa, texts_tg
from qarz.application import permissions as permission_answers
from qarz.application.chat_texts import LANGUAGE_NAMES, UZ, money, say, template
from qarz.domain import languages, permissions
from qarz.domain.languages import LANGUAGES, from_telegram, is_language, sms_language
from qarz.domain.money import Currency, format_money
from qarz.domain.uz_cyrillic import to_cyrillic
from qarz.interface.errors import _MESSAGES, _STATUS, _WORDINGS, error_response, message_text

TRAILING = {"tg": texts_tg, "kaa": texts_kaa, "en": texts_en}
CYRILLIC = languages.UZ_CYRILLIC


def _fields(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


# --- which languages there are ------------------------------------------------------------------------


def test_the_six_languages_and_their_tags() -> None:
    assert LANGUAGES == ("uz", "uz-Cyrl", "ru", "tg", "kaa", "en")
    assert languages.DEFAULT == "uz"
    assert tuple(LANGUAGE_NAMES) == LANGUAGES
    assert LANGUAGE_NAMES == {
        "uz": "O'zbekcha",
        "uz-Cyrl": "Ўзбекча",
        "ru": "Русский",
        "tg": "Тоҷикӣ",
        "kaa": "Qaraqalpaqsha",
        "en": "English",
    }
    assert set(chat_texts.CATALOGS) == set(LANGUAGES) == set(export_texts.CATALOGS)
    assert languages.ALLOWED == "must be one of: uz, uz-Cyrl, ru, tg, kaa, en"


@pytest.mark.parametrize("code", ["uz", "uz-Cyrl", "ru", "tg", "kaa", "en"])
def test_a_language_is_its_exact_tag(code: str) -> None:
    assert is_language(code)


@pytest.mark.parametrize("code", ["kk", "de", "UZ", "uz-cyrl", "uz_Cyrl", "en-US", "", " uz", None, 5, ["uz"]])
def test_anything_else_is_not_a_language(code: object) -> None:
    assert not is_language(code)


# --- the language a person starts in ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("telegram", "language"),
    [
        ("uz", "uz"),
        ("UZ", "uz"),
        ("uz-Latn", "uz"),
        ("uz-Cyrl", "uz-Cyrl"),
        ("uz_Cyrl", "uz-Cyrl"),
        ("uz-cyrl-UZ", "uz-Cyrl"),
        ("ru", "ru"),
        ("ru-RU", "ru"),
        ("tg", "tg"),
        ("tg-TJ", "tg"),
        ("kaa", "kaa"),
        ("kk", "kaa"),
        ("kk-KZ", "kaa"),
        ("en", "en"),
        ("en-US", "en"),
        ("EN_gb", "en"),
    ],
)
def test_telegrams_language_gives_the_language_a_person_starts_in(telegram: str, language: str) -> None:
    assert from_telegram(telegram) == language
    assert is_language(from_telegram(telegram))


@pytest.mark.parametrize("telegram", ["de", "fa", "ky", "tr", "uk", "rus", "kkx", "", "  ", None])
def test_an_unknown_telegram_language_starts_in_uzbek(telegram: str | None) -> None:
    assert from_telegram(telegram) == "uz"


def test_an_sms_is_uzbek_or_russian_whatever_the_reader() -> None:
    assert languages.SMS_LANGUAGES == ("uz", "ru")
    assert [sms_language(code) for code in LANGUAGES] == ["uz", "uz", "ru", "uz", "uz", "uz"]
    assert sms_language("kk") == sms_language(None) == "uz"


# --- what a language that trails behind has -----------------------------------------------------------


@pytest.mark.parametrize("lang", sorted(TRAILING))
def test_the_chat_texts_have_no_unknown_key_the_same_fields_and_no_empty_text(lang: str) -> None:
    own = TRAILING[lang].CHAT
    assert chat_texts.CATALOGS[lang] is own
    assert sorted(set(own) - set(UZ)) == [], "a key that Uzbek does not have"
    for key, text in own.items():
        assert isinstance(text, str) and text.strip(), key
        assert _fields(text) == _fields(UZ[key]), key


@pytest.mark.parametrize("lang", sorted(TRAILING))
def test_the_export_texts_have_no_unknown_key_and_lists_of_the_same_length(lang: str) -> None:
    own = TRAILING[lang].EXPORT
    assert sorted(set(own) - set(export_texts.UZ)) == []
    for key, entry in own.items():
        source = export_texts.UZ[key]
        assert isinstance(entry, str) == isinstance(source, str), key
        if isinstance(entry, str):
            assert entry.strip(), key
        else:
            assert len(entry) == len(source) and all(title.strip() for title in entry), key


@pytest.mark.parametrize("lang", sorted(TRAILING))
def test_a_sheet_name_fits_what_a_spreadsheet_allows(lang: str) -> None:
    """Excel refuses a sheet name over 31 characters or with one of these signs in it."""
    for key in (key for key in export_texts.UZ if key.startswith("sheet_")):
        name = export_texts.word(lang, key)
        assert 0 < len(name) <= 31, (key, name)
        assert not set(name) & set("[]:*?/\\"), (key, name)
    names = [export_texts.word(lang, key) for key in export_texts.UZ if key.startswith("sheet_")]
    assert len(names) == len(set(names)), "two sheets of one workbook cannot share a name"


@pytest.mark.parametrize("lang", sorted(TRAILING))
def test_the_refusals_have_no_unknown_code_the_same_fields_and_no_empty_text(lang: str) -> None:
    own = TRAILING[lang].ERRORS
    assert sorted(set(own) - set(_MESSAGES["uz"])) == []
    for code, text in own.items():
        assert text.strip(), code
        assert _fields(text) == _fields(_MESSAGES["uz"][code]), code


@pytest.mark.parametrize("lang", sorted(TRAILING))
def test_the_permission_names_are_of_permissions_that_exist(lang: str) -> None:
    known = {f"group.{group.key}" for group in permissions.GROUPS} | {item.key for item in permissions.CATALOGUE}
    own = TRAILING[lang].PERMISSIONS
    assert sorted(set(own) - known) == []
    assert all(name.strip() for name in own.values())


@pytest.mark.parametrize("lang", sorted(TRAILING))
def test_bot_commands_and_the_products_name_are_left_as_uzbek_has_them(lang: str) -> None:
    import re

    def kept(text: str) -> list[str]:
        return sorted(re.findall(r"(?:^|[\s(])(/[a-z_]+)", text)) + sorted(re.findall("Qarz Daftari", text))

    for key, text in TRAILING[lang].CHAT.items():
        assert kept(text) == kept(UZ[key]), key


def test_the_checks_above_would_notice_each_kind_of_mistake() -> None:
    """The counterpart: an unknown key, a renamed field and a dropped field are each seen."""
    assert sorted({"cancelled", "no_such_key"} - set(UZ)) == ["no_such_key"]
    assert _fields("{shop}\n{name}: +{amount}") != _fields("{shop}\n{ном}: +{amount}")
    assert _fields("{shop}: {name}") != _fields("{shop}")
    assert _fields("{second} / {first}") == _fields("{first} va {second}")


# --- what is said when a language does not have a text ------------------------------------------------


@pytest.mark.parametrize("lang", sorted(TRAILING))
def test_a_text_the_language_does_not_have_is_read_in_uzbek(lang: str, monkeypatch: pytest.MonkeyPatch) -> None:
    assert say(lang, "cancelled") == TRAILING[lang].CHAT["cancelled"] != UZ["cancelled"]
    monkeypatch.delitem(TRAILING[lang].CHAT, "cancelled")
    monkeypatch.delitem(TRAILING[lang].CHAT, "shop_switched")
    template.cache_clear()
    try:
        assert say(lang, "cancelled") == UZ["cancelled"]
        assert say(lang, "shop_switched", shop="Baraka") == UZ["shop_switched"].format(shop="Baraka")
        # The rest of the language is untouched.
        assert say(lang, "soon") == TRAILING[lang].CHAT["soon"]
    finally:
        template.cache_clear()


def test_a_key_that_uzbek_does_not_have_is_a_mistake_in_every_language() -> None:
    for lang in (*LANGUAGES, "kk"):
        with pytest.raises(KeyError):
            say(lang, "no_such_key")
        with pytest.raises(KeyError):
            say(lang, "shop_switched")  # its value is missing


def test_every_chat_text_can_be_said_in_every_language() -> None:
    for lang in LANGUAGES:
        for key, source in UZ.items():
            said = template(lang, key)
            assert said.strip(), (lang, key)
            assert _fields(said) == _fields(source), (lang, key)
            said.format(**dict.fromkeys(_fields(source), "x"))


def test_every_export_word_and_refusal_can_be_said_in_every_language() -> None:
    for lang in LANGUAGES:
        for key, source in export_texts.UZ.items():
            if isinstance(source, str):
                assert export_texts.word(lang, key).strip(), (lang, key)
            else:
                assert len(export_texts.header(lang, key)) == len(source), (lang, key)
        for code in _MESSAGES["uz"]:
            assert message_text(lang, code).strip(), (lang, code)
    assert export_texts.word("tg", "no_such_word", "as given") == "as given"
    with pytest.raises(KeyError):
        export_texts.word("tg", "no_such_word")


# --- Uzbek Cyrillic ------------------------------------------------------------------------------------


def test_uzbek_cyrillic_is_the_uzbek_text_rewritten_not_a_text_of_its_own() -> None:
    assert chat_texts.UZ_CYRILLIC == {} == export_texts.UZ_CYRILLIC, "an entry here is a deliberate exception: say why"
    assert say(CYRILLIC, "cancelled") == to_cyrillic(UZ["cancelled"]) == "Бекор қилинди. Ҳеч нарса ёзилмади."
    assert say(CYRILLIC, "lang_set") == "Тил ўзгартирилди: ўзбекча."
    assert say(CYRILLIC, "shop_switched", shop="Baraka savdo") == "Фаол дўкон: Baraka savdo"
    assert export_texts.word(CYRILLIC, "sheet_customers") == "Мижозлар"
    assert export_texts.header(CYRILLIC, "months") == tuple(map(to_cyrillic, export_texts.header("uz", "months")))
    assert message_text(CYRILLIC, "NOT_FOUND") == "Топилмади."


def test_a_name_typed_by_a_person_is_never_rewritten() -> None:
    """Only the wording is transliterated: a shop's and a customer's name stay as they were typed."""
    said = say(CYRILLIC, "credit_saved", shop="Ziyo market", name="Ali Valiyev", amount="1 so'm", balance="2", date="x")
    assert "Ziyo market" in said and "Ali Valiyev" in said and "1 so'm" in said


def test_a_hand_written_uzbek_cyrillic_text_wins_over_the_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(chat_texts.UZ_CYRILLIC, "cancelled", "Бекор.")
    template.cache_clear()
    try:
        assert say(CYRILLIC, "cancelled") == "Бекор."
        assert say(CYRILLIC, "soon") == to_cyrillic(UZ["soon"])
    finally:
        template.cache_clear()


# --- money --------------------------------------------------------------------------------------------


def test_money_has_the_same_digits_and_each_languages_word() -> None:
    nbsp = " "
    assert [money(lang, 1_250_000).replace(nbsp, " ") for lang in LANGUAGES] == [
        "1 250 000 so'm",
        "1 250 000 сўм",
        "1 250 000 сум",
        "1 250 000 сӯм",
        "1 250 000 swm",
        "1 250 000 soum",
    ]
    assert {money(lang, 125_050, Currency.USD) for lang in (*LANGUAGES, "kk")} == {f"1{nbsp}250.50{nbsp}$"}
    assert format_money(Currency.UZS, 45_000, "kk") == format_money(Currency.UZS, 45_000, "uz")
    # The unit a text's own catalog names is the unit the money module writes.
    for lang in ("uz", "ru", "tg", "kaa", "en"):
        assert money(lang, 1).endswith(nbsp + template(lang, "currency")), lang
    assert money(CYRILLIC, 1).endswith(nbsp + to_cyrillic(UZ["currency"]))


# --- errors and permission names as the API gives them -------------------------------------------------


def _message(lang: str, code: str, fields: dict[str, str] | None = None) -> str:
    import json

    body = json.loads(bytes(error_response(code, lang, fields).body))
    message: str = body["error"]["message"]
    return message


def test_a_refusal_is_worded_in_the_callers_language() -> None:
    assert _message("uz", "NOT_FOUND") == "Topilmadi."
    assert _message("ru", "NOT_FOUND") == "Не найдено."
    assert _message("uz-Cyrl", "NOT_FOUND") == "Топилмади."
    assert _message("en", "NOT_FOUND") == texts_en.ERRORS["NOT_FOUND"]
    assert _message("tg", "NOT_FOUND") == texts_tg.ERRORS["NOT_FOUND"]
    assert _message("kaa", "NOT_FOUND") == texts_kaa.ERRORS["NOT_FOUND"]
    assert _message("kk", "NOT_FOUND") == "Topilmadi.", "an unknown language is answered in Uzbek"
    for lang in LANGUAGES:
        assert _message(lang, "NO_SUCH_CODE") == message_text(lang, "ERROR")
        assert "30" in _message(lang, "FREE_PLAN_FULL", {"limit": "30"})
        assert "{" not in _message(lang, "FREE_PLAN_FULL", {"limit": "30"})


def test_a_more_exact_wording_is_said_under_its_own_code_only() -> None:
    """`AppError.wording`: the same code, status and fields, with words that explain."""
    import json

    for lang in (*LANGUAGES, "kk"):
        said = error_response("VALIDATION", lang, {"lines": "x"}, "GOODS_NOT_IN_DOLLARS")
        body = json.loads(bytes(said.body))["error"]
        assert (said.status_code, body["code"], body["fields"]) == (422, "VALIDATION", {"lines": "x"})
        assert body["message"] == message_text(lang, "GOODS_NOT_IN_DOLLARS") != message_text(lang, "VALIDATION")
        # A wording of another code, or one nobody wrote, changes nothing: the code's own words are said.
        for code, wording in (("NOT_FOUND", "GOODS_NOT_IN_DOLLARS"), ("VALIDATION", "NO_SUCH"), ("VALIDATION", None)):
            plain = error_response(code, lang, None, wording)
            assert json.loads(bytes(plain.body)) == json.loads(bytes(error_response(code, lang).body))
    for lang in ("tg", "kaa", "en"):
        assert message_text(lang, "GOODS_NOT_IN_DOLLARS") != message_text("uz", "GOODS_NOT_IN_DOLLARS"), lang
    assert set(_WORDINGS.values()) <= set(_STATUS), "a wording belongs to a code that exists"
    assert not set(_WORDINGS) & set(_STATUS), "and is not itself a code a client could be sent"


def test_a_refusal_the_language_does_not_have_is_worded_in_uzbek(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(texts_en.ERRORS, "NOT_FOUND")
    message_text.cache_clear()
    try:
        assert _message("en", "NOT_FOUND") == "Topilmadi."
        assert _message("en", "VALIDATION") == texts_en.ERRORS["VALIDATION"]
    finally:
        message_text.cache_clear()


def test_a_permission_is_named_in_every_language_that_has_it() -> None:
    label = permission_answers.label("customers.create", "Mijoz qo'shish", "Добавлять клиента")
    assert label["uz"] == "Mijoz qo'shish" and label["ru"] == "Добавлять клиента"
    assert label["uz-Cyrl"] == "Мижоз қўшиш"
    assert {lang: label[lang] for lang in TRAILING} == {
        lang: module.PERMISSIONS["customers.create"] for lang, module in TRAILING.items()
    }
    # A permission another module adds has Uzbek and Russian only: the answer has what there is.
    new = permission_answers.label("cash.record", "Kassaga yozish", "Записывать в кассу")
    assert set(new) == {"uz", "uz-Cyrl", "ru"} and new["uz-Cyrl"] == "Кассага ёзиш"


def test_the_catalogue_of_permissions_names_each_in_all_six(monkeypatch: pytest.MonkeyPatch) -> None:
    body = permission_answers.catalogue_body()
    labels: list[Mapping[str, str]] = [group["label"] for group in body["groups"]]
    labels += [item["label"] for group in body["groups"] for item in group["permissions"]]
    assert len(labels) == len(permissions.GROUPS) + len(permissions.CATALOGUE)
    for label in labels:
        assert set(label) == set(LANGUAGES), label
        assert all(text.strip() for text in label.values())

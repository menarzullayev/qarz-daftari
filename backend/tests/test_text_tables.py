"""No table of texts in the code outside the catalogs (ADR-021).

The expansion left a few tables of two languages in the code itself: the names of stock units and of
write-off reasons, the cash book's categories, the notes a stock document writes, the states in the
network's export sheets. They were Uzbek and Russian and nothing measured them, so a reader of the four
other languages was shown Uzbek. They are catalog entries now (`export_texts`, `chat_texts`), which the
completeness check holds to every language (`scripts/i18n_missing.py --strict`, `tests/test_languages.py`).

Two things are held here. Each list of the domain that a person is shown by name has its name in the
catalog, so that a new unit, reason, category or state cannot be added without one. And the source has no
table that names some languages and not all, so that such a table cannot be written again unnoticed.
"""

import ast
from pathlib import Path

import pytest

from qarz.application import cash_feed, chat_texts, export_texts
from qarz.application.chat_texts import say
from qarz.application.export_texts import in_every_language, word
from qarz.application.stock import settings_body
from qarz.domain import cash, languages, network, stock
from qarz.domain.names import normalize_name

SOURCE = Path(__file__).resolve().parent.parent / "src" / "qarz"
WRITTEN = ("uz", "ru", "tg", "kaa", "en")  # Uzbek Cyrillic is made from Uzbek

# What each list of the domain is called in the catalog of words.
NAMED = {
    **{f"unit_{unit.key}": "a unit" for unit in stock.UNITS},
    **{f"reason_{reason}": "a write-off reason" for reason in stock.WRITE_OFF_REASONS},
    **{f"cash_category_{category.key}": "a default category" for category in cash.DEFAULT_CATEGORIES},
    **{f"cash_category_{key}": "a category of the stock" for key in cash.STOCK_CATEGORIES},
    **{f"net_link_{state}": "a link's state" for state in network.LINK_STATES},
    **{f"net_order_{state}": "an order's state" for state in network.ORDER_STATES},
    **{f"net_note_{state}": "a note's state" for state in network.NOTE_STATES},
    **{f"net_payment_{state}": "a payment's state" for state in network.PAYMENT_STATES},
}


@pytest.mark.parametrize("key", sorted(NAMED))
def test_everything_the_domain_lists_by_key_has_a_name_in_every_language(key: str) -> None:
    assert key in export_texts.UZ, f"{NAMED[key]} without a name: add `{key}` to export_texts and every language"
    for lang in WRITTEN:
        own = export_texts.CATALOGS[lang].get(key)
        assert isinstance(own, str) and own.strip(), f"{key} is not named in {lang}"
    assert set(in_every_language(key)) == set(languages.LANGUAGES)


def test_a_key_the_domain_adds_without_a_name_is_noticed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The counterpart: take one name out of one language, and one out of Uzbek, and the test above fails."""
    monkeypatch.delitem(export_texts.CATALOGS["kaa"], "unit_qop")  # type: ignore[attr-defined]
    with pytest.raises(AssertionError, match="unit_qop is not named in kaa"):
        test_everything_the_domain_lists_by_key_has_a_name_in_every_language("unit_qop")
    monkeypatch.delitem(export_texts.UZ, "net_note_void")
    with pytest.raises(AssertionError, match="a note's state without a name"):
        test_everything_the_domain_lists_by_key_has_a_name_in_every_language("net_note_void")


def test_the_stock_settings_name_units_and_reasons_in_all_six_languages() -> None:
    body = settings_body(refuse_negative=False, currencies=("UZS",), cash_book=False)
    assert [unit["key"] for unit in body["units"]] == [unit.key for unit in stock.UNITS]
    for named in (*body["units"], *body["write_off_reasons"]):
        assert set(named["label"]) == set(languages.LANGUAGES), named["key"]
        assert all(text.strip() for text in named["label"].values())
    labels = {unit["key"]: unit["label"] for unit in body["units"]}
    assert labels["qop"] == {"uz": "qop", "uz-Cyrl": "қоп", "ru": "мешок", "tg": "халта", "kaa": "qap", "en": "sack"}
    reasons = {reason["key"]: reason["label"] for reason in body["write_off_reasons"]}
    assert (reasons["lost"]["uz"], reasons["lost"]["ru"], reasons["lost"]["en"]) == ("Yo'qolgan", "Утерян", "Lost")


@pytest.mark.parametrize("lang", languages.LANGUAGES)
def test_the_cash_books_categories_are_names_a_shop_could_have_typed_in_every_language(lang: str) -> None:
    """A category is stored under its matching form, unique per shop and direction: no two defaults of one
    direction may fold to the same form in any language, the stock's own two included."""
    names = cash_feed.default_names(lang)
    assert len(names) == len(cash.DEFAULT_CATEGORIES) == 10
    assert sum(1 for _, _, system_key in names if system_key is not None) == 1
    made = [(direction, name) for direction, name, _ in names]
    made += [(cash.Direction.EXPENSE, word(lang, f"cash_category_{key}")) for key in cash.STOCK_CATEGORIES]
    forms = [(direction, cash.category_name(name)[1]) for direction, name in made]
    assert len(set(forms)) == len(forms), forms
    for _, name in made:
        assert cash.category_name(name)[0] == name
        # Room for the number a name gets when the shop already has a category called exactly so.
        assert len(name) <= cash.MAX_CATEGORY_NAME - 3, name


def test_the_notes_a_stock_document_writes_are_said_in_every_language() -> None:
    for key in ("stock_purchase_note", "stock_return_note"):
        said = {lang: say(lang, key, number=7) for lang in languages.LANGUAGES}
        assert all("7" in text and "{" not in text for text in said.values()), said
        assert len(set(said.values())) == len(languages.LANGUAGES), "each language has its own wording"
        assert all(len(text) <= stock.MAX_NOTE for text in said.values())
    assert say("uz", "stock_purchase_note", number=3) == "Kirim № 3"
    assert say("ru", "stock_return_note", number=3) == "Возврат товара, документ № 3"
    assert chat_texts.UZ["stock_return_note"] == "Tovar qaytarildi, hujjat № {number}"


def test_a_unit_is_matched_by_its_key_whatever_it_is_called() -> None:
    """The names are for reading. What is stored and compared is the key, which no language changes."""
    for unit in stock.UNITS:
        assert unit.key == normalize_name(unit.key)
        assert word("uz", f"unit_{unit.key}").strip()


# --- no table of some languages in the code ----------------------------------------------------------------

# The catalogs themselves, which the completeness checks measure, and the few tables that are not texts
# of ours or are meant to have fewer languages. Each with its reason; nothing else may name a language.
ALLOWED = {
    "application/chat_texts.py": "a catalog (CHAT), measured",
    "application/export_texts.py": "a catalog (EXPORT), measured",
    "interface/errors.py": "a catalog (ERRORS), measured",
    "application/permissions.py": "names from the domain's catalogue and PERMISSIONS, measured",
    "domain/permissions.py": "the catalogue of permissions: Uzbek and Russian here, the rest in PERMISSIONS, measured",
    "application/online_payment.py": "Payme's protocol asks for exactly uz, ru and en",
    "domain/languages.py": "the list of languages itself, Telegram's codes, and the SMS languages",
}
FEW = {"uz", "ru"}
ALL = set(WRITTEN)


def _tables_of_some_languages(tree: ast.AST) -> list[int]:
    """Lines of the source where texts are given by language for some languages and not all: a dictionary
    with the keys "uz" and "ru", or a class with fields `uz` and `ru`."""
    found: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            keys = {key.value for key in node.keys if isinstance(key, ast.Constant) and isinstance(key.value, str)}
            if keys >= FEW and not keys >= ALL:
                found.append(node.lineno)
        elif isinstance(node, ast.ClassDef):
            fields = {
                item.target.id
                for item in node.body
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)
            }
            if fields >= FEW and not fields >= ALL:
                found.append(node.lineno)
        elif isinstance(node, ast.IfExp | ast.Compare):
            # `ru if lang == "ru" else uz`: the two-language choice itself.
            compare = node.test if isinstance(node, ast.IfExp) else node
            if (
                isinstance(compare, ast.Compare)
                and isinstance(compare.left, ast.Name)
                and compare.left.id == "lang"
                and any(isinstance(right, ast.Constant) and right.value == "ru" for right in compare.comparators)
            ):
                found.append(node.lineno)
    return sorted(set(found))


def test_no_source_file_has_a_table_of_texts_in_some_languages_only() -> None:
    offenders: list[str] = []
    for path in sorted(SOURCE.rglob("*.py")):
        name = path.relative_to(SOURCE).as_posix()
        if name in ALLOWED or name.startswith("application/texts_"):
            continue
        lines = _tables_of_some_languages(ast.parse(path.read_text(encoding="utf-8")))
        offenders += [f"{name}:{line}" for line in lines]
    assert offenders == [], (
        "texts by language outside the catalogs; move them to chat_texts or export_texts, "
        f"in every language: {offenders}"
    )
    assert all((SOURCE / name).is_file() for name in ALLOWED), "an allowed file that no longer exists"


@pytest.mark.parametrize(
    "source",
    [
        'NOTE = {"uz": "Kirim", "ru": "Приход"}',
        "class Unit:\n    key: str\n    uz: str\n    ru: str",
        'def name(lang, uz, ru):\n    return ru if lang == "ru" else uz',
        'def name(lang):\n    if lang == "ru":\n        return "Приход"\n    return "Kirim"',
    ],
)
def test_the_search_finds_each_shape_such_a_table_had(source: str) -> None:
    assert _tables_of_some_languages(ast.parse(source)) != []


@pytest.mark.parametrize(
    "source",
    [
        'UNITS = {"uz": "so\'m", "uz-Cyrl": "сўм", "ru": "сум", "tg": "сӯм", "kaa": "swm", "en": "soum"}',
        'LANGS = ("uz", "ru")',
        'def name(lang):\n    return word(lang, "unit_kg")',
    ],
)
def test_the_search_leaves_a_table_of_every_language_alone(source: str) -> None:
    assert _tables_of_some_languages(ast.parse(source)) == []

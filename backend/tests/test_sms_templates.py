"""The SMS texts listed for registration with Eskiz are the texts the code sends, and all of them.

Eskiz sends only a text that matches a template approved in its cabinet. The list a person registers from
is a table in runbook 12 (`docs/10-operations/runbooks.md`); a wording changed in the code and not there
would be refused by Eskiz in production and by nothing before it. So the table is read here and compared.
"""

import re
from pathlib import Path

import pytest

from qarz.application.chat_texts import CATALOGS
from qarz.application.reminders import reminder_text
from qarz.domain.languages import LANGUAGES, SMS_LANGUAGES
from qarz.domain.reminders import TEMPLATES, Channel, ReminderKind, ReminderPlan
from qarz.infrastructure.eskiz_sms import wire_text

RUNBOOKS = Path(__file__).resolve().parents[2] / "docs" / "10-operations" / "runbooks.md"
HEADING = "### Templates to register in the Eskiz cabinet"
_ROW = re.compile(r"^\| `(?P<key>[a-z_]+)` \| (?P<lang>[a-z]{2}) \| `(?P<text>[^`]+)` \|$")

Template = tuple[str, str, str]  # catalog key, language, text


def documented(document: str) -> list[Template]:
    """The rows of the table under the heading, up to the next heading."""
    section = document.split(HEADING, 1)[1]
    section = re.split(r"^#{1,6} ", section, maxsplit=1, flags=re.MULTILINE)[0]
    return [
        (found["key"], found["lang"], found["text"]) for line in section.splitlines() if (found := _ROW.match(line))
    ]


def in_the_code() -> set[Template]:
    return {(key, lang, text) for lang, catalog in CATALOGS.items() for key, text in catalog.items() if "sms" in key}


def sendable() -> set[str]:
    """Every text a reminder can be by SMS, with the variable parts left as their names.

    For a reader of every language the product has, and of one it does not: whatever the language, the
    text that goes out is one of the registered ones.
    """
    return {
        wire_text(reminder_text(lang, template, ReminderPlan(kind, 70_000), Channel.SMS, shop="{shop}", name="{name}"))
        for lang in (*LANGUAGES, "kk", "")
        for template in TEMPLATES
        for kind in ReminderKind
    }


def with_an_amount(text: str, lang: str) -> str:
    return text.replace("{amount}", f"70 000 {CATALOGS[lang]['currency']}")


def test_the_documented_templates_are_exactly_the_texts_in_the_code() -> None:
    rows = documented(RUNBOOKS.read_text(encoding="utf-8"))
    assert rows, "the table was not found"
    assert len(rows) == len(set(rows)), "a template is listed twice"
    assert set(rows) == in_the_code()


def test_the_documented_templates_are_exactly_what_a_reminder_can_send() -> None:
    """Not only the catalog: what the reminder code produces for the SMS channel, as it goes on the wire."""
    rows = documented(RUNBOOKS.read_text(encoding="utf-8"))
    assert {with_an_amount(text, lang) for _, lang, text in rows} == sendable()
    assert len(sendable()) == len(SMS_LANGUAGES) * len(ReminderKind), (
        "one per SMS language and kind, whatever the shop chose"
    )


def test_each_template_has_the_three_variable_parts_and_ordinary_spaces() -> None:
    for key, lang, text in documented(RUNBOOKS.read_text(encoding="utf-8")):
        assert sorted(re.findall(r"\{([a-z]+)\}", text)) == ["amount", "name", "shop"], (key, lang)
        assert " " not in text and text == text.strip()


def test_a_wording_that_differs_from_the_document_is_noticed() -> None:
    """The counterpart: a changed word, a missing row and an extra row each make the comparison fail."""
    document = RUNBOOKS.read_text(encoding="utf-8")
    changed = document.replace("bugun {amount} to'lash kuni. Rahmat.", "bugun {amount} to'lash kuni.", 1)
    assert changed != document and set(documented(changed)) != in_the_code()
    row = "| `sms_overdue` | ru | `{shop}: {name}, срок оплаты долга {amount} прошёл. Пожалуйста, оплатите.` |\n"
    assert row in document
    assert set(documented(document.replace(row, "", 1))) == in_the_code() - {
        ("sms_overdue", "ru", "{shop}: {name}, срок оплаты долга {amount} прошёл. Пожалуйста, оплатите.")
    }
    extra = document.replace(row, row + "| `sms_thanks` | uz | `{shop}: rahmat.` |\n", 1)
    assert set(documented(extra)) - in_the_code() == {("sms_thanks", "uz", "{shop}: rahmat.")}


def test_sms_exists_in_uzbek_and_russian_only() -> None:
    """Each wording must be registered with the provider, and only these two languages' are. The other
    languages have no SMS text of their own, so that none can be sent unregistered."""
    assert SMS_LANGUAGES == ("uz", "ru")
    assert {lang for _, lang, _ in in_the_code()} == set(SMS_LANGUAGES)
    for lang in set(LANGUAGES) - set(SMS_LANGUAGES):
        assert [key for key in CATALOGS[lang] if "sms" in key] == [], lang


@pytest.mark.parametrize("lang", [*sorted(set(LANGUAGES) - set(SMS_LANGUAGES)), "kk"])
@pytest.mark.parametrize("kind", list(ReminderKind))
def test_a_reader_of_another_language_gets_the_uzbek_sms_whole(lang: str, kind: ReminderKind) -> None:
    """Not a Tajik sentence, and not an Uzbek sentence with a Tajik unit in it: the registered text."""
    plan = ReminderPlan(kind, 70_000)
    sent = reminder_text(lang, 1, plan, Channel.SMS, shop="Baraka", name="Ali")
    assert sent == reminder_text("uz", 1, plan, Channel.SMS, shop="Baraka", name="Ali")
    assert "so'm" in sent
    # By Telegram the same reminder is in the reader's own language.
    if lang != "kk":
        assert reminder_text(lang, 1, plan, Channel.TELEGRAM, shop="Baraka", name="Ali") != reminder_text(
            "uz", 1, plan, Channel.TELEGRAM, shop="Baraka", name="Ali"
        )


def test_a_new_sms_text_in_the_code_must_be_documented() -> None:
    """Any catalog key that names SMS belongs in the table: a third text cannot be added quietly."""
    keys = {key for catalog in CATALOGS.values() for key in catalog if "sms" in key}
    assert keys == {"sms_due_today", "sms_overdue"}
    assert {key for key, _, _ in in_the_code()} == keys

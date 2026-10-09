"""Uzbek Cyrillic is made from Uzbek Latin by rule, and the rule is the same on the server and in the browser.

`tests/data/uz_cyrillic_cases.json` holds the cases and the exception tables. This file holds the server's
implementation to it; `frontend/src/i18n/uzCyrillic.test.ts` holds the browser's twin to the same file.
"""

import json
import re
import string
from pathlib import Path
from typing import Any

import pytest

from qarz.application import chat_texts, export_texts
from qarz.domain import uz_cyrillic
from qarz.domain.uz_cyrillic import to_cyrillic
from qarz.interface.errors import _MESSAGES

FIXTURE: dict[str, Any] = json.loads(
    (Path(__file__).resolve().parent / "data" / "uz_cyrillic_cases.json").read_text(encoding="utf-8")
)
CASES: list[tuple[str, str]] = [(latin, cyrillic) for latin, cyrillic in FIXTURE["cases"]]
WRONG: list[tuple[str, str]] = [(latin, wrong) for latin, wrong in FIXTURE["wrong"]]


def _uzbek_texts() -> list[str]:
    texts = [*chat_texts.UZ.values(), *_MESSAGES["uz"].values()]
    for entry in export_texts.UZ.values():
        texts.extend([entry] if isinstance(entry, str) else entry)
    return texts


def _fields(template: str) -> list[str]:
    return sorted(name for _, name, _, _ in string.Formatter().parse(template) if name)


def test_there_are_cases_to_run() -> None:
    assert len(CASES) > 100
    assert len(WRONG) > 10
    assert len(_uzbek_texts()) > 350


@pytest.mark.parametrize(("latin", "cyrillic"), CASES)
def test_the_shared_cases(latin: str, cyrillic: str) -> None:
    assert to_cyrillic(latin) == cyrillic


@pytest.mark.parametrize(("latin", "wrong"), WRONG)
def test_what_a_careless_rule_would_write_never_comes_out(latin: str, wrong: str) -> None:
    assert to_cyrillic(latin) != wrong


def test_the_tables_are_the_shared_ones() -> None:
    """The browser's twin is held to the same file, so the two cannot drift apart word by word."""
    assert sorted(uz_cyrillic.BRANDS) == FIXTURE["brands"]
    assert FIXTURE["product"] == uz_cyrillic.PRODUCT
    assert sorted(uz_cyrillic.CODES) == FIXTURE["codes"]
    assert sorted(uz_cyrillic.UPPER_WORDS) == FIXTURE["upper_words"]
    assert FIXTURE["words"] == uz_cyrillic.WORDS
    assert FIXTURE["stems"] == uz_cyrillic.STEMS


@pytest.mark.parametrize(
    "kept",
    [
        "{name}",
        "{count}",
        "/obuna",
        "/start",
        "https://t.me/qarz_bot?start=abc",
        "yordam@qarz.uz",
        "@qarz_bot",
        "Qarz Daftari",
        "Telegram",
        "Excel",
        "SMS",
        "UZS",
        "USD",
        "QR",
        "hisobot.xlsx",
        "1 250.50 $",
        "45 000",
        "Русский",
    ],
)
def test_what_is_never_touched(kept: str) -> None:
    assert to_cyrillic(kept) == kept
    # Inside a sentence too, with Uzbek on both sides of it.
    assert to_cyrillic(f"Bu {kept} uchun") == f"Бу {kept} учун"


def test_text_that_is_already_cyrillic_comes_back_as_it_is() -> None:
    for latin, _ in CASES:
        once = to_cyrillic(latin)
        assert to_cyrillic(once) == once, latin


def test_every_uzbek_text_keeps_its_fields_commands_and_lines() -> None:
    for text in _uzbek_texts():
        made = to_cyrillic(text)
        assert _fields(made) == _fields(text), text
        assert re.findall(r"(?:^|\s)/[a-z_]+", made) == re.findall(r"(?:^|\s)/[a-z_]+", text), text
        assert made.count("\n") == text.count("\n"), text
        assert made.strip(), text
        # It can still be filled in: the transliteration left the format string a format string.
        made.format(**dict.fromkeys(_fields(text), "x"))


def test_every_uzbek_text_keeps_its_digits_signs_and_emoji() -> None:
    def rest(text: str) -> str:
        without_fields = re.sub(r"\{[^{}]*\}", "", text)
        return "".join(ch for ch in without_fields if not (ch.isalpha() or ch in "'ʻʼ’‘`" or ch == "́"))

    for text in _uzbek_texts():
        assert rest(to_cyrillic(text)) == rest(text), text


def test_no_word_is_left_half_written() -> None:
    """Two scripts meet inside a word only where a brand takes an Uzbek ending ("Telegramда")."""
    mixed = re.compile(r"[Ѐ-ӿ][a-z']|[a-z'][Ѐ-ӿ]")
    brand = re.compile("(?:" + "|".join(uz_cyrillic.BRANDS) + r")'?[Ѐ-ӿ]")
    left = [
        made
        for made in map(to_cyrillic, _uzbek_texts())
        if mixed.search(re.sub(r"\{[^{}]*\}", " ", made)) and not brand.search(made)
    ]
    assert left == []


def test_the_check_above_would_notice_a_half_written_word() -> None:
    mixed = re.compile(r"[Ѐ-ӿ][a-z']|[a-z'][Ѐ-ӿ]")
    assert mixed.search("до'кон")
    assert mixed.search("дўкoн")  # a Latin "o" among Cyrillic letters
    assert not mixed.search(to_cyrillic("do'kon"))

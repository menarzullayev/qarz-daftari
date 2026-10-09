"""The product's name is written in one place: `src/qarz/domain/brand.json`.

Both sides read that file, every text says `{brand}` where the name belongs, and nothing else in the
repository writes the name, a former name or the initials an icon once showed. The last tests here plant
a literal and show the search finds it.

Technical identifiers are not the brand and are not looked for: the package `qarz`, `QD_*` variables,
`X-Qarz-*` headers, `qd.*` storage keys, `--qd-*` style variables, the Compose project, the database and
its roles, the repository's name (docs/08-technical-spec/OUTPUT.md, "Brand").
"""

import fnmatch
import json
import re
import subprocess
from collections.abc import Iterable
from pathlib import Path

import pytest

from qarz.application import admin_access, chat_texts
from qarz.application.chat_texts import say, tagline, template
from qarz.domain import brand, uz_cyrillic
from qarz.domain.languages import LANGUAGES
from qarz.domain.uz_cyrillic import to_cyrillic
from qarz.interface import api_description

REPO = Path(__file__).resolve().parents[2]
DEFINITION = "backend/src/qarz/domain/brand.json"

# --- one definition, read by both sides ---------------------------------------------------------------


def tracked() -> list[str]:
    listed = subprocess.run(  # noqa: S603
        ["git", "-C", str(REPO), "ls-files", "-z"],  # noqa: S607
        capture_output=True,
        check=True,
    )
    return [path for path in listed.stdout.decode("utf-8").split("\0") if path]


def test_the_definition_is_one_file_and_the_package_reads_it() -> None:
    assert brand.SOURCE == REPO / DEFINITION
    assert [path for path in tracked() if path.rsplit("/", 1)[-1].lower().startswith("brand.")] == [
        DEFINITION,
        "backend/src/qarz/domain/brand.py",
        "frontend/src/brand.test.ts",
        "frontend/src/shared/brand.ts",
    ]
    data = json.loads((REPO / DEFINITION).read_text(encoding="utf-8"))
    assert (data["name"], data["short_name"]) == (brand.NAME, brand.SHORT_NAME)
    assert dict(brand.MARK) == data["mark"]
    assert dict(brand.TAGLINES) == data["tagline"]
    assert [list(old) for old in brand.FORMER] == [[old["name"], old["initials"]] for old in data["former"]]


def test_the_front_end_reads_the_same_file_in_a_checkout_and_in_the_proxy_image() -> None:
    """One file, two readers: the front end has no copy, and its image is built with this very file."""
    module = (REPO / "frontend/src/shared/brand.ts").read_text(encoding="utf-8")
    imports = re.findall(r'^import .* from "([^"]+\.json)"', module, flags=re.MULTILINE)
    assert imports == ["../../../" + DEFINITION]
    assert f'BRAND_SOURCE = "{DEFINITION}"' in module
    # The proxy image is built from the repository's root with everything ignored but what is named.
    ignore = (REPO / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert f"!{DEFINITION}" in ignore
    dockerfile = (REPO / "deploy/production/nginx/Dockerfile").read_text(encoding="utf-8")
    assert "WORKDIR /frontend\n" in dockerfile
    # `../../../backend/...` from /frontend/src/shared is /backend/...: the copy lands exactly there,
    # before the build runs.
    copy = f"COPY {DEFINITION} /{DEFINITION}\n"
    assert copy in dockerfile
    assert dockerfile.index(copy) < dockerfile.index("RUN npm run build")
    # The backend image takes the whole of src/, and an installed package carries the file as data.
    assert "COPY src ./src\n" in (REPO / "backend/Dockerfile").read_text(encoding="utf-8")
    assert '"qarz.domain" = ["brand.json"]' in (REPO / "backend/pyproject.toml").read_text(encoding="utf-8")


def _definition(tmp_path: Path, **changes: object) -> Path:
    data = json.loads((REPO / DEFINITION).read_text(encoding="utf-8")) | changes
    path = tmp_path / "brand.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "changes",
    [
        {"name": ""},
        {"name": " Padded "},
        {"name": "With {brace}"},
        {"short_name": "Longer than a launcher shows"},
        {"mark": {"canvas": 96, "corner": 22, "blocks": []}},
        {"mark": {"about": "", "canvas": 96, "corner": 22, "blocks": []}},
        {"mark": {"about": "", "canvas": 96, "corner": 60, "blocks": [{"x": 22, "y": 22, "w": 20, "h": 52, "r": 5}]}},
        # Outside the middle 80% of the canvas: a launcher would cut it.
        {"mark": {"about": "", "canvas": 96, "corner": 22, "blocks": [{"x": 4, "y": 22, "w": 20, "h": 52, "r": 5}]}},
        {"mark": {"about": "", "canvas": 96, "corner": 22, "blocks": [{"x": 22, "y": 22, "w": 20, "h": 70, "r": 5}]}},
        {"mark": {"about": "", "canvas": 96, "corner": 22, "blocks": [{"x": 22, "y": 22, "w": 20, "h": 52}]}},
        {"tagline": {"uz": "faqat o'zbekcha"}},
        {"former": [{"name": "", "initials": "XX"}]},
        {"extra": 1},
    ],
)
def test_a_definition_that_would_break_a_text_or_an_icon_is_refused(tmp_path: Path, changes: dict[str, object]) -> None:
    assert brand.load(_definition(tmp_path))[0] == brand.NAME
    with pytest.raises(ValueError, match=r"brand\.json"):
        brand.load(_definition(tmp_path, **changes))


# --- every text is filled from it ---------------------------------------------------------------------


def test_every_chat_text_names_the_product_through_the_placeholder() -> None:
    named = [key for key, text in chat_texts.UZ.items() if brand.PLACEHOLDER in text]
    assert {"welcome_new", "consent_v2", "ops_title", "ops_test", "ops_db_down", "ops_db_up"} <= set(named)
    for lang in (*LANGUAGES, "kk"):
        for key in chat_texts.UZ:
            said = template(lang, key)
            assert brand.PLACEHOLDER not in said and brand.TAGLINE_PLACEHOLDER not in said, (lang, key)
            assert (brand.NAME in said) == (key in named), (lang, key)
    # A language's own text names the product where Uzbek does, and nowhere else.
    for lang, catalog in chat_texts.CATALOGS.items():
        for key, text in catalog.items():
            assert (brand.PLACEHOLDER in text) == (key in named), (lang, key)


def test_the_greeting_says_the_name_and_the_line_on_the_product_in_each_language() -> None:
    for lang in LANGUAGES:
        first = say(lang, "welcome_new").splitlines()[0]
        assert first.endswith(f"{brand.NAME} — {tagline(lang)}."), lang
    assert tagline("uz-Cyrl") == to_cyrillic(brand.TAGLINES["uz"]) != brand.TAGLINES["uz"]
    assert tagline("kk") == brand.TAGLINES["uz"]
    assert len({tagline(lang) for lang in LANGUAGES}) == len(LANGUAGES)


def test_uzbek_cyrillic_leaves_the_name_alone_without_being_told() -> None:
    """No table of the transliterator holds the name: it comes from the definition."""
    assert uz_cyrillic.PRODUCT == brand.NAME
    assert brand.NAME not in uz_cyrillic.BRANDS
    assert to_cyrillic(brand.NAME) == brand.NAME
    assert to_cyrillic(f"Bu {brand.NAME} xizmati") == f"Бу {brand.NAME} хизмати"
    assert to_cyrillic(f"{brand.NAME} — do'kon hisobi") == f"{brand.NAME} — дўкон ҳисоби"
    # The name written without its capitals is ordinary text, like any other word.
    assert to_cyrillic(brand.NAME.lower()) != brand.NAME.lower()
    if " " not in brand.NAME:
        # A single word takes an Uzbek ending, which is still transliterated.
        assert to_cyrillic(f"{brand.NAME}da") == f"{brand.NAME}да"
    said = say("uz-Cyrl", "consent_v2", shop="Baraka")
    assert brand.NAME in said and "хизмати" in said
    # A name that was the product's once is ordinary Uzbek now.
    for old, _ in brand.FORMER:
        assert to_cyrillic(old) != old


def test_the_api_and_the_second_factor_are_named_from_it() -> None:
    assert admin_access.ISSUER == brand.NAME
    assert api_description.describe()["info"]["title"] == brand.NAME
    committed = json.loads((REPO / "backend/openapi.json").read_text(encoding="utf-8"))
    assert committed["info"]["title"] == brand.NAME


# --- nothing else writes it ---------------------------------------------------------------------------

# Files that are not text a person wrote: pictures, and lock files whose hashes hold every pair of letters.
NOT_SEARCHED = ("*.png", "*.lock", "*/package-lock.json")

# Where the name may be written out. `current` allows the name of today only: a file made from the
# definition and held to it by a check of its own. `any` allows former names too: prose and history.
ALLOWED_FILES: tuple[tuple[str, str, str], ...] = (
    (DEFINITION, "any", "the definition itself"),
    # Documents are prose for people. The ones that describe the running product are renamed by hand
    # with the product (the checklist in docs/08-technical-spec/OUTPUT.md, "Brand"); evidence, decisions,
    # the plan's history and the project's records keep the name they were written under.
    ("*.md", "any", "documents"),
    ("docs/*", "any", "documents and their attachments"),
    (".project-alpha/*", "any", "the project's records"),
    # Made from the definition; each has a check that fails when it is not what the definition gives.
    ("backend/openapi.json", "current", "`python -m qarz.interface.api_description --check`"),
    ("frontend/public/panel/manifest.webmanifest", "current", "`node scripts/pwaIcons.ts --check`"),
    ("frontend/src/app/__snapshots__/*.snap", "current", "the Mini App's recorded markup"),
)
# Single lines elsewhere: (file, what the line must contain, why).
ALLOWED_LINES: tuple[tuple[str, str, str], ...] = (
    (
        "backend/migrations/sql/0001_initial.sql",
        "release 1 schema (PostgreSQL 16).",
        "an applied migration is never edited",
    ),
    ("backend/tests/api/test_chat.py", 'seller.say("/yordam@', "a bot's username is configuration"),
    ("frontend/src/shared/qr.test.ts", 'readBotUsername(" @', "a bot's username is configuration"),
)

_EDGE = "A-Za-z0-9_"


def patterns(names: Iterable[str], initials: Iterable[str]) -> re.Pattern[str]:
    """A name in any capitals, with its spaces, without them or with them as an address writes them
    (`%20`, `+`); initials as a word of their own, in capitals."""
    spelled = [r"(?:\s|%20|\+)*".join(f"(?i:{re.escape(word)})" for word in name.split()) for name in names]
    alone = [rf"(?<![{_EDGE}-]){re.escape(letters)}(?![{_EDGE}-])" for letters in initials]
    return re.compile("|".join([*spelled, *alone]))


CURRENT = patterns([brand.NAME, brand.SHORT_NAME], [])
FORMER = patterns([old for old, _ in brand.FORMER], [letters for _, letters in brand.FORMER]) if brand.FORMER else None


def _matches(path: str, globs: Iterable[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, glob) for glob in globs)


def _allowed_whole(path: str) -> bool:
    return any(kind == "any" and fnmatch.fnmatchcase(path, glob) for glob, kind, _ in ALLOWED_FILES)


def literals(files: Iterable[tuple[str, str]]) -> list[str]:
    """Every line of the given (path, text) files that writes the brand out and is not allowed to."""
    found = []
    for path, text in files:
        if _matches(path, NOT_SEARCHED):
            continue
        kinds = {kind for glob, kind, _ in ALLOWED_FILES if fnmatch.fnmatchcase(path, glob)}
        if "any" in kinds:
            continue
        allowed = [needle for file, needle, _ in ALLOWED_LINES if file == path]
        for number, line in enumerate(text.splitlines(), start=1):
            if any(needle in line for needle in allowed):
                continue
            hit = (None if "current" in kinds else CURRENT.search(line)) or (FORMER.search(line) if FORMER else None)
            if hit:
                found.append(f"{path}:{number}: {hit.group()!r}")
    return found


def _repository() -> Iterable[tuple[str, str]]:
    for path in tracked():
        if _matches(path, NOT_SEARCHED) or _allowed_whole(path):
            continue  # a document is never opened: a change of documents alone need not run this test
        file = REPO / path
        if not file.is_file():
            continue  # removed in the working tree, not yet committed
        try:
            yield path, file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            pytest.fail(f"{path} is tracked, is not UTF-8 text, and is not in NOT_SEARCHED")


def test_the_name_is_written_nowhere_but_in_its_definition() -> None:
    assert literals(_repository()) == []


def test_every_exception_is_still_needed() -> None:
    """An allowed file or line that no longer exists, or no longer holds the name, is taken off the list."""
    files = dict(_repository())
    everything = patterns(
        [brand.NAME, brand.SHORT_NAME, *(old for old, _ in brand.FORMER)],
        [letters for _, letters in brand.FORMER],
    )
    for glob, kind, why in ALLOWED_FILES:
        if kind == "any":
            continue
        assert any(fnmatch.fnmatchcase(path, glob) and everything.search(text) for path, text in files.items()), (
            glob,
            why,
        )
    for path, needle, why in ALLOWED_LINES:
        lines = [line for line in files.get(path, "").splitlines() if needle in line]
        assert len(lines) == 1 and everything.search(lines[0]), (path, needle, why)


@pytest.mark.parametrize(
    "planted",
    [
        f'TITLE = "{brand.NAME}"',
        f"<title>{brand.NAME.upper()} — panel</title>",
        f"# written without its spaces: {brand.NAME.replace(' ', '').lower()}",
        f'"app.name": "{brand.SHORT_NAME}",',
    ],
)
def test_a_planted_literal_is_found(planted: str) -> None:
    clean = 'TITLE = brand.NAME\nKEY = "QD_SECRET"\nHEADER = "X-Qarz-Shop"\nstore("qd.language")\n'
    assert literals([("backend/src/qarz/interface/http.py", clean)]) == []
    found = literals([("backend/src/qarz/interface/http.py", clean + planted + "\n")])
    assert len(found) == 1 and found[0].startswith("backend/src/qarz/interface/http.py:5: ")
    # In a text catalog, a script, a page and a deployment file alike.
    for path in (
        "frontend/src/i18n/uz.ts",
        "frontend/panel/index.html",
        "deploy/production/compose.yml",
        "e2e/tests/x.spec.ts",
    ):
        assert len(literals([(path, planted)])) == 1, path
    # A document may say it; a file made from the definition may say today's name.
    assert literals([("docs/10-operations/runbooks.md", planted), ("backend/openapi.json", planted)]) == []


def test_a_former_name_is_found_even_in_a_file_made_from_the_definition() -> None:
    old = patterns(["Old Name"], ["ON"])
    assert old.search('"title": "Old Name"') and old.search("oldname_bot") and old.search('"ON"')
    assert old.search("otpauth://totp/Old%20Name%3Aadmin-1") and old.search("issuer=Old+Name")
    assert not old.search("ON_SECRET") and not old.search("--on-accent") and not old.search("SEASON")
    # The technical names that merely look like the brand are not the brand.
    for technical in (
        "qarz-daftari/backend:ci",
        "QD_DATABASE_URL",
        "X-Qarz-Shop",
        "qd.language",
        "--qd-bg",
        "qd_app",
        "QD-1",
    ):
        assert not CURRENT.search(technical), technical
        assert FORMER is None or not FORMER.search(technical), technical
    for old, letters in brand.FORMER:
        for planted in (
            f'TITLE = "{old}"',
            f"bot = '{old.replace(' ', '').lower()}_bot'",
            f'<text x="1">{letters}</text>',
        ):
            assert len(literals([("frontend/src/shared/Shell.tsx", planted)])) == 1, planted
    if FORMER is not None:
        text = f'{{"info": {{"title": "{brand.FORMER[0][0]}"}}}}'
        assert literals([("backend/openapi.json", text)]) == [f"backend/openapi.json:1: {brand.FORMER[0][0]!r}"]

"""Pure rules of the shared catalogue: names, search words, the price hint, photo keys, the seed's rows.

Each rule has the case that works and the case that must be refused or left out. The products here are
invented; nothing of the real seed is in the repository.
"""

import pytest

from qarz.application.shared_catalog_import import sections_of, seed_item
from qarz.domain import shared_catalog
from qarz.domain.catalog import MAX_NAME_LENGTH

SECTIONS = {"Ichimliklar": "drinks", "Сладкий ряд": "sweets"}


# --- names --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("lang", "expected"),
    [
        ("ru", "Чай зелёный Тоғ"),
        ("uz", "Tog' ko'k choyi"),
        ("uz-Cyrl", "Тоғ кўк чойи"),
        ("tg", "Tog' ko'k choyi"),
        ("kaa", "Tog' ko'k choyi"),
        ("en", "Tog' ko'k choyi"),
    ],
)
def test_the_name_is_in_the_readers_language(lang: str, expected: str) -> None:
    assert shared_catalog.display_name(lang, "Чай зелёный Тоғ", "Tog' ko'k choyi") == expected


@pytest.mark.parametrize("lang", ["uz", "uz-Cyrl", "tg", "kaa", "en"])
def test_a_missing_uzbek_name_falls_back_to_the_russian_one_unchanged(lang: str) -> None:
    """Most rows have no Uzbek name yet: the Russian one is shown as it is, never transliterated."""
    assert shared_catalog.display_name(lang, "Чай зелёный", None) == "Чай зелёный"
    assert shared_catalog.display_name("ru", None, "Ko'k choy") == "Ko'k choy"


def test_a_shared_name_is_trimmed_and_a_blank_one_is_none() -> None:
    assert shared_catalog.shared_name("  Ko'k   choy ") == "Ko'k choy"
    assert shared_catalog.shared_name("   ") is None
    assert shared_catalog.shared_name(None) is None


@pytest.mark.parametrize("bad", ["x" * (shared_catalog.MAX_SHARED_NAME + 1), "ь"])
def test_a_shared_name_too_long_or_without_a_letter_is_refused(bad: str) -> None:
    with pytest.raises(ValueError):
        shared_catalog.shared_name(bad)


def test_a_picked_item_is_named_with_its_package_so_two_sizes_are_two_items() -> None:
    assert shared_catalog.picked_name("Ko'k choy", "100 g") == "Ko'k choy 100 g"
    assert shared_catalog.picked_name("Ko'k choy", "250 g") != shared_catalog.picked_name("Ko'k choy", "100 g")
    # A name that already says its size does not say it twice, in either script.
    assert shared_catalog.picked_name("Чай зелёный 100 г", "100 г") == "Чай зелёный 100 г"
    assert shared_catalog.picked_name("Ko'k choy 100 g", "100 г") == "Ko'k choy 100 g"
    assert shared_catalog.picked_name("Ko'k choy", None) == "Ko'k choy"


def test_a_picked_name_fits_what_an_item_may_hold_and_keeps_the_package() -> None:
    name = shared_catalog.picked_name("Shokolad " + "juda " * 40, "90 g")
    assert len(name) <= MAX_NAME_LENGTH
    assert name.endswith(" 90 g")


def test_a_picked_name_with_nothing_usable_is_refused() -> None:
    with pytest.raises(ValueError):
        shared_catalog.picked_name("   ", None)


# --- search -------------------------------------------------------------------------------------------


def test_the_search_form_holds_both_names_and_the_package_in_one_script() -> None:
    norm = shared_catalog.search_norm("Чай зелёный", "Ko'k choy", "100 г")
    assert norm == "chay zelyoniy ko'k choy 100 g"
    assert shared_catalog.search_norm(None, "Ko'k choy", None) == "ko'k choy"
    assert shared_catalog.search_norm(None, None, None) == ""


def test_a_query_in_either_script_becomes_the_same_words() -> None:
    assert shared_catalog.search_terms("Чай  100") == ["chay", "100"]
    assert shared_catalog.search_terms("chay 100") == ["chay", "100"]
    assert shared_catalog.search_terms("choy choy") == ["choy"], "a word typed twice is asked once"
    assert shared_catalog.search_terms(None) == []
    assert shared_catalog.search_terms("   ") == []


def test_a_query_is_cut_to_a_few_words_and_a_long_one_is_refused() -> None:
    assert len(shared_catalog.search_terms("a b c d e f g")) == shared_catalog.MAX_TERMS
    with pytest.raises(ValueError):
        shared_catalog.search_terms("x" * (shared_catalog.MAX_QUERY + 1))


def test_a_category_is_one_of_ours() -> None:
    assert len(shared_catalog.CATEGORIES) == 21 and shared_catalog.CATEGORIES[-1] == shared_catalog.OTHER
    assert shared_catalog.category("tea") == "tea"
    assert shared_catalog.category(None) == "other"
    with pytest.raises(ValueError):
        shared_catalog.category("weapons")


# --- the price hint and the photo ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "stored"),
    [(12_990, 12_990), (0, None), (-5, None), (None, None), (True, None), (shared_catalog.MAX_PRICE_HINT + 1, None)],
)
def test_a_price_hint_is_a_positive_amount_or_nothing(raw: object, stored: int | None) -> None:
    assert shared_catalog.price_hint(raw) == stored  # type: ignore[arg-type]


def test_a_photo_is_kept_under_its_own_hash_and_nothing_else_is_a_key() -> None:
    key = "ab" + "0" * 62
    assert shared_catalog.image_object_key(key) == f"catalog/ab/{key}"
    for bad in ("", "ab", key.upper(), key[:-1] + "g", "../" + key[3:], key + "0"):
        assert not shared_catalog.is_image_key(bad)
        with pytest.raises(ValueError):
            shared_catalog.image_object_key(bad)


# --- the seed's rows -----------------------------------------------------------------------------------


def row(**changed: object) -> dict[str, object]:
    return {
        "id": "00112233aabbccdd",
        "name_ru": "Чай зелёный Тоғ",
        "name_uz": "",
        "amount": "100 г",
        "path": "Ichimliklar > Choy > Ko'k",
        "shop": "somewhere",
        "shops": ["somewhere"],
        "price_hint": 12_990,
        "img_url": "https://example.invalid/photo/400x400",
        **changed,
    }


def test_a_seed_row_becomes_an_item_and_keeps_no_address_of_where_it_came_from() -> None:
    item = seed_item(row(), SECTIONS)
    assert item is not None
    assert (item.source_key, item.name_ru, item.name_uz) == ("00112233aabbccdd", "Чай зелёный Тоғ", None)
    assert (item.category, item.subcategory, item.amount, item.price_hint) == ("drinks", "Choy > Ko'k", "100 г", 12_990)
    assert item.search_norm == "chay zelyoniy tog' 100 g"
    assert item.image_key is None
    assert not any("example.invalid" in str(value) or value == "somewhere" for value in vars(item).values())


def test_an_unknown_section_is_other_and_a_row_without_a_price_has_no_hint() -> None:
    item = seed_item(row(path="Yangiliklar > Chegirma", price_hint=0), SECTIONS)
    assert item is not None
    assert (item.category, item.subcategory, item.price_hint) == ("other", "Chegirma", None)
    lone = seed_item(row(path="Сладкий ряд", amount=""), SECTIONS)
    assert lone is not None and (lone.category, lone.subcategory, lone.amount) == ("sweets", None, None)


@pytest.mark.parametrize(
    "changed",
    [
        {"id": "short"},
        {"id": "00112233AABBCCDD"},
        {"id": "../../etc/passwd0"},
        {"id": 7},
        {"name_ru": "", "name_uz": ""},
        {"name_ru": "ь", "name_uz": ""},
    ],
)
def test_a_row_without_a_usable_key_or_name_is_left_out(changed: dict[str, object]) -> None:
    assert seed_item(row(**changed), SECTIONS) is None
    assert seed_item("not a row", SECTIONS) is None


def test_the_categories_file_maps_sections_and_names_only_our_categories() -> None:
    sections = sections_of(
        {"categories": {"tea": {"uz": "Choy", "ru": "Чай", "from": ["Choy  va kofe"]}, "other": {"from": ["*"]}}}
    )
    assert sections == {"Choy va kofe": "tea"}
    with pytest.raises(ValueError, match="does not have"):
        sections_of({"categories": {"weapons": {"from": ["Qurollar"]}}})
    with pytest.raises(ValueError):
        sections_of(["tea"])

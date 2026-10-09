"""A read-only link's token and what the page may say of a name (qarz.domain.customer_share)."""

import hashlib
from datetime import timedelta

import pytest

from qarz.domain.customer_share import (
    SHARE_LIFETIME,
    SWITCH,
    TOKEN_BYTES,
    TOKEN_LENGTH,
    first_name,
    is_token,
    new_token,
    same_hash,
    token_hash,
)
from qarz.domain.platform_settings import SETTINGS, effective


def test_a_token_is_256_random_bits_in_url_safe_text() -> None:
    tokens = {new_token() for _ in range(200)}
    assert len(tokens) == 200
    assert TOKEN_BYTES * 8 == 256 >= 128
    assert all(len(token) == TOKEN_LENGTH and is_token(token) for token in tokens)


def test_only_the_hash_of_a_token_is_looked_up() -> None:
    token = new_token()
    assert token_hash(token) == hashlib.sha256(token.encode()).digest()
    assert same_hash(token_hash(token), token_hash(token))
    assert not same_hash(token_hash(token), token_hash(new_token()))
    assert not same_hash(token_hash(token), token_hash(token)[:-1])


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "short",
        "A" * (TOKEN_LENGTH - 1),
        "A" * (TOKEN_LENGTH + 1),
        "A" * (TOKEN_LENGTH - 1) + "=",
        "A" * (TOKEN_LENGTH - 1) + "/",
        "A" * (TOKEN_LENGTH - 1) + "+",
        "A" * (TOKEN_LENGTH - 1) + " ",
        "A" * (TOKEN_LENGTH - 1) + "\n",
        "я" * TOKEN_LENGTH,
        "١" * TOKEN_LENGTH,
        b"A" * TOKEN_LENGTH,
        12345,
        ["A" * TOKEN_LENGTH],
    ],
)
def test_what_is_not_shaped_like_a_token_is_never_looked_up(value: object) -> None:
    assert not is_token(value)


@pytest.mark.parametrize(
    ("typed", "shown"),
    [
        ("Ali", "Ali"),
        ("Ali Valiyev", "Ali"),
        ("  Ali   Valiyev  ", "Ali"),
        ("Ali, 5-uy qarzdor", "Ali"),
        ("Ali. qo'shni", "Ali"),
        ("G'ani aka", "G'ani"),
        ("Алишер Усмонов", "Алишер"),
        ("Anonim 1A2B3C", "Anonim"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_the_page_shows_the_first_word_of_the_name_and_no_more(typed: str, shown: str) -> None:
    assert first_name(typed) == shown


def test_a_link_lives_ninety_days_and_the_switch_is_off_until_turned_on() -> None:
    assert timedelta(days=90) == SHARE_LIFETIME
    assert SETTINGS[SWITCH].kind == "switch" and SETTINGS[SWITCH].needs_code
    assert effective(SWITCH, None) is False

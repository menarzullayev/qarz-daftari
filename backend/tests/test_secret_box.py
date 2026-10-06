"""Secrets stored in the database are unreadable without the key and cannot be moved between rows."""

import base64

import pytest

from qarz.application.ports import SecretUnreadable
from qarz.infrastructure.secret_box import SecretBox

KEY = base64.b64encode(bytes(range(32))).decode()
OTHER_KEY = base64.b64encode(bytes(range(1, 33))).decode()
SECRET = b"12345678901234567890"


def test_what_is_encrypted_comes_back_only_with_the_same_key_and_context() -> None:
    box = SecretBox(KEY)
    sealed = box.encrypt(SECRET, b"user-1")
    assert box.decrypt(sealed, b"user-1") == SECRET
    assert SECRET not in sealed
    with pytest.raises(SecretUnreadable):
        SecretBox(OTHER_KEY).decrypt(sealed, b"user-1")


def test_a_secret_copied_into_another_row_does_not_decrypt() -> None:
    box = SecretBox(KEY)
    with pytest.raises(SecretUnreadable):
        box.decrypt(box.encrypt(SECRET, b"user-1"), b"user-2")


def test_the_same_secret_never_encrypts_to_the_same_bytes() -> None:
    box = SecretBox(KEY)
    assert box.encrypt(SECRET, b"user-1") != box.encrypt(SECRET, b"user-1")


def test_changed_bytes_are_refused() -> None:
    box = SecretBox(KEY)
    sealed = bytearray(box.encrypt(SECRET, b"user-1"))
    for position in (0, 1, len(sealed) // 2, len(sealed) - 1):
        damaged = bytearray(sealed)
        damaged[position] ^= 0x01
        with pytest.raises(SecretUnreadable):
            box.decrypt(bytes(damaged), b"user-1")


@pytest.mark.parametrize("stored", [b"", b"test-only", b"\x01", b"\x01" + bytes(12), b"\x02" + bytes(40)])
def test_bytes_that_were_never_ours_are_refused(stored: bytes) -> None:
    with pytest.raises(SecretUnreadable):
        SecretBox(KEY).decrypt(stored, b"user-1")


@pytest.mark.parametrize(
    "key",
    ["", "not base64 !!", base64.b64encode(bytes(31)).decode(), base64.b64encode(bytes(33)).decode()],
)
def test_a_key_that_is_not_32_bytes_of_base64_refuses_to_start(key: str) -> None:
    with pytest.raises(ValueError, match="secrets key"):
        SecretBox(key)

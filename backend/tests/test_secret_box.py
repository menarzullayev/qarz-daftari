"""Secrets stored in the database are unreadable without the key and cannot be moved between rows."""

import pytest

from qarz.application.ports import SecretUnreadable
from qarz.domain import file_links
from qarz.infrastructure.secret_box import SecretBox, derive_key

KEY = "a-server-secret-for-tests-0123456789"
OTHER_KEY = "another-server-secret-0123456789"
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


@pytest.mark.parametrize("secret", ["", "short", "x" * 15])
def test_a_server_secret_that_is_too_short_refuses_to_start(secret: str) -> None:
    with pytest.raises(ValueError, match="too short"):
        SecretBox(secret)


def test_a_sixteen_character_secret_is_accepted() -> None:
    box = SecretBox("x" * 16)
    assert box.decrypt(box.encrypt(SECRET, b"u"), b"u") == SECRET


def test_the_encryption_key_is_derived_for_this_purpose_only() -> None:
    """One server secret serves file links and stored secrets; neither may be given the other's key."""
    encryption, links = derive_key(KEY), file_links.derive_key(KEY)
    assert len(encryption) == 32
    assert encryption != links
    assert encryption != KEY.encode()
    assert KEY.encode() not in encryption
    assert derive_key(KEY) == encryption, "the same secret always gives the same key"
    assert derive_key(OTHER_KEY) != encryption

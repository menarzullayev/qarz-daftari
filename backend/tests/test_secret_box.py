"""Secrets stored in the database are unreadable without the key and cannot be moved between rows."""

from typing import Any

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from qarz.application.ports import SecretUnreadable
from qarz.domain import file_links
from qarz.infrastructure.secret_box import SecretBox, derive_key, key_id

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


# --- rotating the server secret (operations runbook 4) --------------------------------------------------

NEW_KEY = "the-next-server-secret-0123456789"


def _unlabelled(secret_key: str, plaintext: bytes, context: bytes) -> bytes:
    """A value as it was stored before ciphertexts said which key made them (version 1)."""
    nonce = bytes(range(12))
    return b"\x01" + nonce + AESGCM(derive_key(secret_key)).encrypt(nonce, plaintext, context)


def test_a_new_value_says_which_key_made_it_without_showing_the_key() -> None:
    sealed = SecretBox(KEY).encrypt(SECRET, b"user-1")
    assert sealed[:1] == b"\x02" and sealed[1:5] == key_id(derive_key(KEY))
    assert key_id(derive_key(KEY)) != key_id(derive_key(OTHER_KEY))
    assert key_id(derive_key(KEY)) not in derive_key(KEY)
    assert KEY.encode() not in sealed and derive_key(KEY) not in sealed


@pytest.mark.parametrize("old_form", [True, False], ids=["stored before key ids", "stored with a key id"])
def test_a_value_reads_under_the_current_key_under_the_previous_one_and_under_neither(old_form: bool) -> None:
    sealed = _unlabelled(KEY, SECRET, b"user-1") if old_form else SecretBox(KEY).encrypt(SECRET, b"user-1")
    assert SecretBox(KEY).decrypt(sealed, b"user-1") == SECRET, "current"
    assert SecretBox(NEW_KEY, KEY).decrypt(sealed, b"user-1") == SECRET, "previous, during a rotation"
    with pytest.raises(SecretUnreadable):
        SecretBox(NEW_KEY).decrypt(sealed, b"user-1")
    with pytest.raises(SecretUnreadable):
        SecretBox(NEW_KEY, OTHER_KEY).decrypt(sealed, b"user-1")
    with pytest.raises(SecretUnreadable):
        SecretBox(NEW_KEY, KEY).decrypt(sealed, b"user-2")


def test_the_previous_key_only_reads() -> None:
    during = SecretBox(NEW_KEY, KEY)
    sealed = during.encrypt(SECRET, b"user-1")
    assert SecretBox(NEW_KEY).decrypt(sealed, b"user-1") == SECRET
    with pytest.raises(SecretUnreadable):
        SecretBox(KEY).decrypt(sealed, b"user-1")


def test_the_current_key_is_tried_before_the_previous_one(monkeypatch: pytest.MonkeyPatch) -> None:
    box = SecretBox(NEW_KEY, KEY)
    asked: list[str] = []
    for name in ("_current", "_previous"):
        key = getattr(box, name)

        def spy(ciphertext: bytes, context: bytes, name: str = name, real: Any = key.open) -> Any:
            asked.append(name)
            return real(ciphertext, context)

        monkeypatch.setattr(key, "open", spy)
    assert box.decrypt(SecretBox(NEW_KEY).encrypt(SECRET, b"u"), b"u") == SECRET
    assert asked == ["_current"], "the previous key is not consulted for what the current one reads"
    asked.clear()
    assert box.decrypt(SecretBox(KEY).encrypt(SECRET, b"u"), b"u") == SECRET
    assert asked == ["_current", "_previous"]


def test_the_key_id_cannot_be_swapped() -> None:
    sealed = SecretBox(KEY).encrypt(SECRET, b"user-1")
    forged = sealed[:1] + key_id(derive_key(NEW_KEY)) + sealed[5:]
    for box in (SecretBox(KEY), SecretBox(NEW_KEY), SecretBox(NEW_KEY, KEY)):
        with pytest.raises(SecretUnreadable):
            box.decrypt(forged, b"user-1")


def test_resealing_moves_a_value_to_the_current_key_once() -> None:
    during = SecretBox(NEW_KEY, KEY)
    for stored in (SecretBox(KEY).encrypt(SECRET, b"user-1"), _unlabelled(KEY, SECRET, b"user-1")):
        moved = during.reseal(stored, b"user-1")
        assert moved is not None and moved != stored and SECRET not in moved
        assert SecretBox(NEW_KEY).decrypt(moved, b"user-1") == SECRET, "readable without the previous key"
        assert during.reseal(moved, b"user-1") is None, "a second time there is nothing to do"
    assert during.reseal(_unlabelled(NEW_KEY, SECRET, b"user-1"), b"user-1") is None, "current already"


def test_resealing_refuses_what_neither_key_reads() -> None:
    stored = SecretBox(KEY).encrypt(SECRET, b"user-1")
    for box in (SecretBox(NEW_KEY), SecretBox(NEW_KEY, OTHER_KEY)):
        with pytest.raises(SecretUnreadable):
            box.reseal(stored, b"user-1")
    with pytest.raises(SecretUnreadable):
        SecretBox(NEW_KEY, KEY).reseal(stored, b"user-2")
    with pytest.raises(SecretUnreadable):
        SecretBox(NEW_KEY, KEY).reseal(b"test-only", b"user-1")


def test_a_previous_secret_that_is_too_short_refuses_to_start_and_the_same_secret_is_no_rotation() -> None:
    with pytest.raises(ValueError, match="too short"):
        SecretBox(KEY, "short")
    sealed = SecretBox(OTHER_KEY).encrypt(SECRET, b"user-1")
    for box in (SecretBox(KEY, KEY), SecretBox(KEY, "")):
        with pytest.raises(SecretUnreadable):
            box.decrypt(sealed, b"user-1")
        assert box.reseal(box.encrypt(SECRET, b"user-1"), b"user-1") is None

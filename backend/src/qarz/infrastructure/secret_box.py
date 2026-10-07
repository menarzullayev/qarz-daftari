"""Encryption of secrets the application stores in the database (specification, "Security": the
administrator's second-factor secret is encrypted at rest).

AES-256-GCM with a random nonce per value. The key is derived from the server secret in the environment
(`QD_SECRETS_KEY`) for this purpose alone: the same secret also gives the key that signs file links
(`qarz.domain.file_links`), and neither key is the secret itself or the other key. Nothing of it reaches
the database, so a copy of the database alone yields no second factor.

Two stored forms are read:

- version 1: one version byte, the 12-byte nonce, then the ciphertext with its tag. It does not say
  which key made it.
- version 2, the only form written: one version byte, a 4-byte key identifier, the nonce, then the
  ciphertext with its tag. The identifier is derived from the key and tells nothing about it; it is
  authenticated with the ciphertext, so it cannot be swapped.

During a rotation of the server secret the previous one is given too (`QD_SECRETS_KEY_PREVIOUS`). The
current key is always tried first; the previous key only reads, never writes. `reseal` moves a value from
the previous key to the current one (`python -m qarz.interface.rotate_secrets`).
"""

import hashlib
import hmac
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from qarz.application.ports import SecretUnreadable

# Says what the derived key is for, so that the same server secret used for another purpose gives another key.
KEY_LABEL = b"qarz-daftari/admin-second-factor/v1"
KEY_ID_LABEL = b"qarz-daftari/admin-second-factor/key-id/v1"
MIN_SECRET_LENGTH = 16
_NONCE_BYTES = 12
_KEY_ID_BYTES = 4
_UNLABELLED = b"\x01"
_LABELLED = b"\x02"


def derive_key(secret: str) -> bytes:
    """The 32-byte key that encrypts second-factor secrets, derived from the server secret."""
    if len(secret) < MIN_SECRET_LENGTH:
        raise ValueError("the server secret is too short to encrypt stored secrets")
    return hmac.new(secret.encode("utf-8"), KEY_LABEL, hashlib.sha256).digest()


def key_id(key: bytes) -> bytes:
    """What a ciphertext carries to say which key made it. One-way: it does not help to find the key."""
    return hmac.new(key, KEY_ID_LABEL, hashlib.sha256).digest()[:_KEY_ID_BYTES]


class _Key:
    def __init__(self, server_secret: str) -> None:
        key = derive_key(server_secret)
        self.aead = AESGCM(key)
        self.header = _LABELLED + key_id(key)

    def open(self, ciphertext: bytes, context: bytes) -> bytes | None:
        """The plaintext if this key made the ciphertext for this context, else None."""
        if ciphertext[:1] == _LABELLED:
            start = len(self.header)
            if not hmac.compare_digest(ciphertext[:start], self.header):
                return None
            associated = self.header + context
        elif ciphertext[:1] == _UNLABELLED:
            start, associated = 1, context
        else:
            return None
        nonce, sealed = ciphertext[start : start + _NONCE_BYTES], ciphertext[start + _NONCE_BYTES :]
        if len(nonce) != _NONCE_BYTES or not sealed:
            return None
        try:
            return self.aead.decrypt(nonce, sealed, associated)
        except InvalidTag:
            return None


class SecretBox:
    def __init__(self, server_secret: str, previous_secret: str = "") -> None:
        self._current = _Key(server_secret)
        # The same secret given twice is no rotation. A previous secret that is too short refuses to
        # start, like the current one.
        self._previous = _Key(previous_secret) if previous_secret and previous_secret != server_secret else None

    def encrypt(self, plaintext: bytes, context: bytes) -> bytes:
        nonce = secrets.token_bytes(_NONCE_BYTES)
        header = self._current.header
        return header + nonce + self._current.aead.encrypt(nonce, plaintext, header + context)

    def decrypt(self, ciphertext: bytes, context: bytes) -> bytes:
        plaintext = self._current.open(ciphertext, context)
        if plaintext is None and self._previous is not None:
            plaintext = self._previous.open(ciphertext, context)
        if plaintext is None:
            raise SecretUnreadable()
        return plaintext

    def reseal(self, ciphertext: bytes, context: bytes) -> bytes | None:
        """The same secret under the current key, or None when the current key already reads it.

        Raises SecretUnreadable when neither key does. The plaintext never leaves this method.
        """
        if self._current.open(ciphertext, context) is not None:
            return None
        plaintext = None if self._previous is None else self._previous.open(ciphertext, context)
        if plaintext is None:
            raise SecretUnreadable()
        return self.encrypt(plaintext, context)

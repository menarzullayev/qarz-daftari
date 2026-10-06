"""Encryption of secrets the application stores in the database (specification, "Security": the
administrator's second-factor secret is encrypted at rest).

AES-256-GCM with a random nonce per value. The key comes from the environment (`QD_SECRETS_KEY`) and
never reaches the database, so a copy of the database alone yields no second factor. The stored form is
one version byte, the 12-byte nonce, then the ciphertext with its tag.
"""

import base64
import binascii
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from qarz.application.ports import SecretUnreadable

KEY_BYTES = 32
_NONCE_BYTES = 12
_VERSION = b"\x01"


class SecretBox:
    def __init__(self, key_base64: str) -> None:
        try:
            key = base64.b64decode(key_base64, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValueError("the secrets key must be base64") from error
        if len(key) != KEY_BYTES:
            raise ValueError(f"the secrets key must be {KEY_BYTES} bytes, base64-encoded")
        self._aead = AESGCM(key)

    def encrypt(self, plaintext: bytes, context: bytes) -> bytes:
        nonce = secrets.token_bytes(_NONCE_BYTES)
        return _VERSION + nonce + self._aead.encrypt(nonce, plaintext, context)

    def decrypt(self, ciphertext: bytes, context: bytes) -> bytes:
        if ciphertext[:1] != _VERSION or len(ciphertext) <= 1 + _NONCE_BYTES:
            raise SecretUnreadable()
        nonce, sealed = ciphertext[1 : 1 + _NONCE_BYTES], ciphertext[1 + _NONCE_BYTES :]
        try:
            return self._aead.decrypt(nonce, sealed, context)
        except InvalidTag as error:
            raise SecretUnreadable() from error

"""Encryption of secrets the application stores in the database (specification, "Security": the
administrator's second-factor secret is encrypted at rest).

AES-256-GCM with a random nonce per value. The key is derived from the server secret in the environment
(`QD_SECRETS_KEY`) for this purpose alone: the same secret also gives the key that signs file links
(`qarz.domain.file_links`), and neither key is the secret itself or the other key. Nothing of it reaches
the database, so a copy of the database alone yields no second factor. The stored form is one version
byte, the 12-byte nonce, then the ciphertext with its tag.
"""

import hashlib
import hmac
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from qarz.application.ports import SecretUnreadable

# Says what the derived key is for, so that the same server secret used for another purpose gives another key.
KEY_LABEL = b"qarz-daftari/admin-second-factor/v1"
MIN_SECRET_LENGTH = 16
_NONCE_BYTES = 12
_VERSION = b""


def derive_key(secret: str) -> bytes:
    """The 32-byte key that encrypts second-factor secrets, derived from the server secret."""
    if len(secret) < MIN_SECRET_LENGTH:
        raise ValueError("the server secret is too short to encrypt stored secrets")
    return hmac.new(secret.encode("utf-8"), KEY_LABEL, hashlib.sha256).digest()


class SecretBox:
    def __init__(self, server_secret: str) -> None:
        self._aead = AESGCM(derive_key(server_secret))

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

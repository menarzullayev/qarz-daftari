"""Signed links to stored files (ADR-020: "served only through short-lived signed links after
authorization"; technical specification, "Security": "signed links valid 5 minutes").

A link is given only to someone who was just authorized to see the file. It names the shop, the file and
the moment it stops working, and carries an HMAC-SHA-256 over exactly those bytes. Whoever holds it can
fetch the file until then and nobody can make or change one without the server's secret.
"""

import base64
import binascii
import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from uuid import UUID

LINK_LIFETIME = timedelta(minutes=5)
# Says what the derived key is for, so that the same server secret used for another purpose gives another key.
KEY_LABEL = b"qarz-daftari/file-link/v1"
MIN_SECRET_LENGTH = 16
_PAYLOAD_BYTES, _MAC_BYTES = 16 + 16 + 8, 32
# Unpadded base64url of 40 and of 32 bytes.
_PAYLOAD_CHARS, _MAC_CHARS = 54, 43
_ALPHABET = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


def derive_key(secret: str) -> bytes:
    """The key that signs file links, derived from the server secret and never the secret itself."""
    if len(secret) < MIN_SECRET_LENGTH:
        raise ValueError("the server secret is too short to sign file links")
    return hmac.new(secret.encode("utf-8"), KEY_LABEL, hashlib.sha256).digest()


def _encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _decode(text: str, length: int) -> bytes | None:
    if not all(ch in _ALPHABET for ch in text):
        return None
    try:
        raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError):
        return None
    # Re-encoded and compared, so that each link has exactly one spelling.
    return raw if len(raw) == length and _encode(raw) == text else None


def expiry(now: datetime) -> datetime:
    """When a link made now stops working: five minutes on, at whole seconds."""
    return datetime.fromtimestamp(int(now.timestamp()), UTC) + LINK_LIFETIME


def sign(key: bytes, shop_id: UUID, file_id: UUID, expires_at: datetime) -> str:
    payload = shop_id.bytes + file_id.bytes + int(expires_at.timestamp()).to_bytes(8, "big")
    return _encode(payload) + "." + _encode(hmac.new(key, payload, hashlib.sha256).digest())


def verify(key: bytes, token: str, now: datetime) -> tuple[UUID, UUID] | None:
    """Shop and file of a link that this key signed and that has not expired; None for anything else.

    The signature is checked before anything in the link is believed, in constant time. A link works
    through the last second of its five minutes and not after.
    """
    if len(token) != _PAYLOAD_CHARS + 1 + _MAC_CHARS or token[_PAYLOAD_CHARS] != ".":
        return None
    payload = _decode(token[:_PAYLOAD_CHARS], _PAYLOAD_BYTES)
    mac = _decode(token[_PAYLOAD_CHARS + 1 :], _MAC_BYTES)
    if payload is None or mac is None:
        return None
    if not hmac.compare_digest(mac, hmac.new(key, payload, hashlib.sha256).digest()):
        return None
    if int(now.timestamp()) > int.from_bytes(payload[32:], "big"):
        return None
    return UUID(bytes=payload[:16]), UUID(bytes=payload[16:32])

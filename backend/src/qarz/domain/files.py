"""Rules for files the service keeps (technical specification, "Security" and "Retention"; ADR-020).

A file is accepted for what its first bytes say it is, never for its name or the type the sender declared.
It is stored under a random key that says nothing about the shop or the person.
"""

from datetime import datetime, timedelta
from enum import StrEnum

MAX_FILE_BYTES = 5 * 1024 * 1024
# A customer's payment-notice receipt is kept 90 days after the notice closes.
RECEIPT_RETENTION = timedelta(days=90)

JPEG, PNG, WEBP, PDF = "image/jpeg", "image/png", "image/webp", "application/pdf"
RECEIPT_TYPES = frozenset({JPEG, PNG, WEBP, PDF})
EXTENSIONS = {JPEG: "jpg", PNG: "png", WEBP: "webp", PDF: "pdf"}

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_KEY_CHARACTERS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
MAX_KEY_SEGMENTS = 4
MAX_KEY_SEGMENT_LENGTH = 128


class FileRefusal(StrEnum):
    EMPTY = "empty"
    TOO_LARGE = "too_large"
    TYPE = "type"  # not a JPEG, PNG or WebP image and not a PDF


def sniff(data: bytes) -> str | None:
    """The type the content itself claims by its leading bytes, or None when it is none we accept."""
    if data[:3] == b"\xff\xd8\xff":
        return JPEG
    if data[:8] == _PNG_SIGNATURE:
        return PNG
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return WEBP
    if data[:5] == b"%PDF-":
        return PDF
    return None


def check_receipt(data: bytes) -> str | FileRefusal:
    """The type of an acceptable receipt, or why it is refused. Exactly MAX_FILE_BYTES is still accepted."""
    if not data:
        return FileRefusal.EMPTY
    if len(data) > MAX_FILE_BYTES:
        return FileRefusal.TOO_LARGE
    return sniff(data) or FileRefusal.TYPE


def is_safe_key(key: str) -> bool:
    """Whether an object key is one the service could have made: a few short segments of plain characters.

    No dot, no backslash, no colon, no empty segment and no leading slash can pass, so a key can never
    name a parent directory, an absolute path, a drive or a hidden file.
    """
    segments = key.split("/")
    return len(segments) <= MAX_KEY_SEGMENTS and all(
        0 < len(segment) <= MAX_KEY_SEGMENT_LENGTH and all(ch in _KEY_CHARACTERS for ch in segment)
        for segment in segments
    )


def object_key(token: str) -> str:
    """Where an object goes: two levels, so that no directory grows without bound."""
    return f"{token[:2]}/{token}"


def receipt_delete_after(closed_at: datetime) -> datetime:
    return closed_at + RECEIPT_RETENTION

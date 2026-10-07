"""Rules for files the service keeps (technical specification, "Security" and "Retention"; ADR-020).

A file is accepted for what its first bytes say it is, never for its name or the type the sender declared.
It is stored under a random key that says nothing about the shop or the person.
"""

import zlib
from datetime import datetime, timedelta
from enum import StrEnum

MAX_FILE_BYTES = 5 * 1024 * 1024
# A customer's payment-notice receipt is kept 90 days after the notice closes.
RECEIPT_RETENTION = timedelta(days=90)

JPEG, PNG, WEBP, PDF = "image/jpeg", "image/png", "image/webp", "application/pdf"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"  # what the service itself writes
RECEIPT_TYPES = frozenset({JPEG, PNG, WEBP, PDF})
EXTENSIONS = {JPEG: "jpg", PNG: "png", WEBP: "webp", PDF: "pdf", XLSX: "xlsx"}

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_KEY_CHARACTERS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
MAX_KEY_SEGMENTS = 4
MAX_KEY_SEGMENT_LENGTH = 128


class FileRefusal(StrEnum):
    EMPTY = "empty"
    TOO_LARGE = "too_large"
    TYPE = "type"  # not a JPEG, PNG or WebP image and not a PDF
    MALFORMED = "malformed"  # starts like one of them, but is not a whole, well-formed file of that type


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


# --- the whole file, not only its first bytes (security review, P36-1) ---------------------------------
#
# A receipt is taken apart by its container format and put together again from the parts an image needs.
# What that removes: metadata (EXIF with the place a photo was taken, XMP, comments, text chunks), anything
# after the end of the image, and any file that merely starts like an image. Nothing is decoded and
# nothing is re-compressed, so the picture itself is byte for byte what was sent. A PDF cannot be cleaned
# this way; it is only checked for its beginning and its end.

_JPEG_FRAMES = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}
# Application segments an image needs to look right: JFIF, the colour profile, Adobe's colour transform.
_JPEG_KEPT_APP = frozenset({0xE0, 0xE2, 0xEE})
_PNG_KEPT = frozenset(
    {b"tRNS", b"gAMA", b"cHRM", b"sRGB", b"iCCP", b"sBIT", b"bKGD", b"pHYs", b"acTL", b"fcTL", b"fdAT"}
)
_WEBP_DROPPED = frozenset({b"EXIF", b"XMP "})
_WEBP_IMAGE = frozenset({b"VP8 ", b"VP8L", b"ANMF"})
_WEBP_EXIF_AND_XMP = 0x08 | 0x04
_PDF_TAIL = 1024


def _clean_jpeg(data: bytes) -> bytes | None:
    out = bytearray(b"\xff\xd8")
    size, at = len(data), 2
    framed = scanned = False
    while True:
        if at >= size or data[at] != 0xFF:
            return None
        while at < size and data[at] == 0xFF:  # a marker may be padded with any number of FF bytes
            at += 1
        if at >= size:
            return None
        marker = data[at]
        at += 1
        if marker == 0xD9:  # the end of the image; whatever follows it is dropped
            return bytes(out + b"\xff\xd9") if scanned else None
        if marker in (0x00, 0x01, 0xD8) or 0xD0 <= marker <= 0xD7 or at + 2 > size:
            return None
        length = int.from_bytes(data[at : at + 2], "big")
        if length < 2:  # one that runs past the end of the file is caught where the next marker should be
            return None
        if marker in _JPEG_KEPT_APP or not (0xE0 <= marker <= 0xEF or marker == 0xFE):
            out += b"\xff" + bytes([marker]) + data[at : at + length]
        at += length
        if marker in _JPEG_FRAMES:
            framed = True
        if marker == 0xDA:  # a scan: compressed data up to the next real marker
            if not framed:
                return None
            scanned = True
            start = at
            while True:
                at = data.find(b"\xff", at)
                after = at
                while 0 <= after < size and data[after] == 0xFF:  # the FF itself and any fill bytes
                    after += 1
                if at < 0 or after >= size:
                    return None
                if data[after] == 0x00 or 0xD0 <= data[after] <= 0xD7:
                    at = after + 1  # a stuffed FF or a restart marker: still inside the scan
                else:
                    break  # a real marker: the scan ends before its first FF
            out += data[start:at]


def _clean_png(data: bytes) -> bytes | None:
    out = bytearray(_PNG_SIGNATURE)
    size, at = len(data), len(_PNG_SIGNATURE)
    first, has_data = True, False
    while True:
        if at + 12 > size:
            return None
        length = int.from_bytes(data[at : at + 4], "big")
        kind = data[at + 4 : at + 8]
        end = at + 12 + length
        if end > size or not (kind.isalpha() and kind.isascii()):
            return None
        if zlib.crc32(data[at + 4 : end - 4]) != int.from_bytes(data[end - 4 : end], "big"):
            return None
        if first != (kind == b"IHDR") or (first and length != 13):
            return None
        first = False
        if kind == b"IEND":  # whatever follows the end chunk is dropped
            return bytes(out + data[at:end]) if has_data and length == 0 else None
        has_data = has_data or kind == b"IDAT"
        # An upper-case first letter marks a chunk the image cannot do without.
        if kind[:1].isupper() or kind in _PNG_KEPT:
            out += data[at:end]
        at = end


def _clean_webp(data: bytes) -> bytes | None:
    declared = int.from_bytes(data[4:8], "little")
    end = declared + 8
    if declared < 4 or declared % 2 or end > len(data):
        return None
    chunks: list[tuple[bytes, bytes]] = []
    at = 12
    while at < end:
        length = int.from_bytes(data[at + 4 : at + 8], "little")
        stop = at + 8 + length
        if at + 8 > end or stop + (length & 1) > end:
            return None
        chunks.append((data[at : at + 4], data[at + 8 : stop]))
        at = stop + (length & 1)
    if not chunks:
        return None
    kind, payload = chunks[0]
    if kind in (b"VP8 ", b"VP8L"):
        kept = chunks[:1]
    elif kind == b"VP8X" and len(payload) == 10 and any(name in _WEBP_IMAGE for name, _ in chunks[1:]):
        header = bytes([payload[0] & ~_WEBP_EXIF_AND_XMP & 0xFF]) + payload[1:]
        kept = [(kind, header)] + [chunk for chunk in chunks[1:] if chunk[0] not in _WEBP_DROPPED]
    else:
        return None
    body = b"WEBP" + b"".join(
        name + len(content).to_bytes(4, "little") + content + b"\x00" * (len(content) & 1) for name, content in kept
    )
    return b"RIFF" + len(body).to_bytes(4, "little") + body


def _checked_pdf(data: bytes) -> bytes | None:
    return data if b"%%EOF" in data[-_PDF_TAIL:] else None


_CLEANERS = {JPEG: _clean_jpeg, PNG: _clean_png, WEBP: _clean_webp, PDF: _checked_pdf}


def clean_receipt(data: bytes) -> tuple[str, bytes] | FileRefusal:
    """The type of a receipt and the content to keep, or why it is refused.

    The size limit applies to what was sent. An image is returned without its metadata and without
    anything after its end; a file that is not whole and well formed for its type is refused.
    """
    kind = check_receipt(data)
    if isinstance(kind, FileRefusal):
        return kind
    cleaned = _CLEANERS[kind](data)
    return FileRefusal.MALFORMED if cleaned is None else (kind, cleaned)

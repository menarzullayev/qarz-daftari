"""What files the service accepts and where it puts them (ADR-020; specification, "Security")."""

from datetime import UTC, datetime, timedelta

import pytest

from qarz.domain.files import (
    MAX_FILE_BYTES,
    RECEIPT_RETENTION,
    FileRefusal,
    check_receipt,
    is_safe_key,
    object_key,
    receipt_delete_after,
    sniff,
)

JPEG = b"\xff\xd8\xff\xe0" + bytes(16)
PNG = b"\x89PNG\r\n\x1a\n" + bytes(16)
WEBP = b"RIFF\x10\x00\x00\x00WEBP" + bytes(16)
PDF = b"%PDF-1.4\n"


@pytest.mark.parametrize(
    ("content", "mime"),
    [(JPEG, "image/jpeg"), (PNG, "image/png"), (WEBP, "image/webp"), (PDF, "application/pdf")],
)
def test_the_four_accepted_types_are_known_by_their_first_bytes(content: bytes, mime: str) -> None:
    assert sniff(content) == mime
    assert check_receipt(content) == mime


@pytest.mark.parametrize(
    "content",
    [
        b"\xff\xd8",  # one byte short of a JPEG signature
        b"\xff\xd8\x00\xe0",
        b"\x89PNG\r\n\x1a",  # one byte short of a PNG signature
        b"\x89PNG\n\x1a\n" + bytes(8),  # the line ending a text transfer would have damaged
        b"RIFF\x10\x00\x00\x00WAVE",  # a RIFF container that is not WebP
        b"RIFF\x10\x00\x00\x00WEB",
        b"WEBP",
        b"%PDF",  # no dash
        b"%pdf-1.4",
        b" %PDF-1.4",  # the signature is not at the very start
        b"\n\xff\xd8\xff",
        b"GIF89a",
        b"BM" + bytes(16),
        b"<svg xmlns='http://www.w3.org/2000/svg'/>",
        b"<html>",
        b"PK\x03\x04",
        b"MZ",
        b"\x00",
    ],
)
def test_anything_else_is_not_a_receipt(content: bytes) -> None:
    assert sniff(content) is None
    assert check_receipt(content) is FileRefusal.TYPE


def test_size_limits_on_both_sides() -> None:
    assert check_receipt(b"") is FileRefusal.EMPTY
    assert check_receipt(JPEG[:3]) == "image/jpeg", "the shortest content that is a type at all"
    at_limit = JPEG + bytes(MAX_FILE_BYTES - len(JPEG))
    assert len(at_limit) == MAX_FILE_BYTES == 5 * 1024 * 1024
    assert check_receipt(at_limit) == "image/jpeg"
    assert check_receipt(at_limit + b"\x00") is FileRefusal.TOO_LARGE
    # Size is judged before type: an oversized file is not even looked into.
    assert check_receipt(bytes(MAX_FILE_BYTES + 1)) is FileRefusal.TOO_LARGE


def test_keys_the_service_makes_are_safe() -> None:
    key = object_key("ab" + "c" * 62)
    assert key == "ab/ab" + "c" * 62
    assert is_safe_key(key)
    assert is_safe_key("a") and is_safe_key("A-b_9/z") and is_safe_key("a/b/c/d")
    assert is_safe_key("x" * 128)


@pytest.mark.parametrize(
    "key",
    [
        "",
        "/",
        "/etc/passwd",
        "a/",
        "/a",
        "a//b",
        "..",
        "../a",
        "a/../b",
        "a/..",
        ".",
        "./a",
        ".hidden",
        "a.jpg",
        "a\\b",
        "..\\a",
        "C:/Windows/win.ini",
        "C:",
        "a:b",
        "a b",
        "a\x00b",
        "a\nb",
        "a%2e%2e",
        "a/b/c/d/e",  # deeper than any key the service makes
        "x" * 129,
        "ключ",
        "ａ",  # a full-width letter is not a plain one
        "~",
        "a?b",
        "a#b",
    ],
)
def test_a_key_that_could_name_anything_else_is_not_safe(key: str) -> None:
    assert not is_safe_key(key)


def test_a_receipt_is_kept_ninety_days_after_its_notice_closes() -> None:
    closed = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
    assert timedelta(days=90) == RECEIPT_RETENTION
    assert receipt_delete_after(closed) == datetime(2027, 1, 5, 12, 0, tzinfo=UTC)

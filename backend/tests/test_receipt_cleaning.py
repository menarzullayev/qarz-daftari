"""The whole file, not only its first bytes (security review, P36-1).

A receipt is taken apart by its container format and put together again without metadata and without
anything after the end of the image. A file that is not whole and well formed for its type is refused.
"""

import pytest

from qarz.domain.files import MAX_FILE_BYTES, FileRefusal, clean_receipt, sniff

from . import receipt_samples as S


@pytest.mark.parametrize(
    ("content", "mime"),
    [
        (S.JPEG, "image/jpeg"),
        (S.PROGRESSIVE_JPEG, "image/jpeg"),
        (S.jpeg(keep=S.ICC + S.ADOBE), "image/jpeg"),
        (S.PNG, "image/png"),
        (S.png(keep=S.GAMMA + S.PHYS), "image/png"),
        (S.WEBP, "image/webp"),
        (S.EXTENDED_WEBP, "image/webp"),
        (S.container(S.VP8), "image/webp"),
        (S.PDF, "application/pdf"),
    ],
    ids=["jpeg", "progressive", "jpeg-colour", "png", "png-colour", "webp", "webp-extended", "webp-lossy", "pdf"],
)
def test_a_whole_file_without_metadata_is_kept_byte_for_byte(content: bytes, mime: str) -> None:
    assert clean_receipt(content) == (mime, content)


CLEANED = {
    "jpeg-exif": (S.jpeg(S.EXIF), S.JPEG),
    "jpeg-xmp-iptc-comment": (S.jpeg(S.XMP, S.PHOTOSHOP, S.COMMENT), S.JPEG),
    "jpeg-keeps-colour": (S.jpeg(S.EXIF, keep=S.ICC + S.ADOBE), S.jpeg(keep=S.ICC + S.ADOBE)),
    "jpeg-trailing-zip": (S.jpeg(trailing=b"PK\x03\x04 a second file"), S.JPEG),
    "jpeg-trailing-html": (S.jpeg(trailing=S.HTML), S.JPEG),
    # A marker padded with fill bytes is the same marker.
    "jpeg-fill-bytes": (S.JPEG[:-2] + b"\xff\xff\xff\xd9", S.JPEG),
    "png-text": (S.png(S.TEXT), S.PNG),
    "png-exif-time-text": (
        S.png(S.PNG_EXIF, S.TIME, S.chunk(b"zTXt", b"k\x00\x00x"), S.chunk(b"iTXt", b"k\x00\x00\x00\x00\x00v")),
        S.PNG,
    ),
    "png-private": (S.png(S.chunk(b"prVt", b"an unknown private chunk")), S.PNG),
    "png-keeps-colour": (S.png(S.TEXT, keep=S.GAMMA + S.PHYS), S.png(keep=S.GAMMA + S.PHYS)),
    "png-trailing": (S.png(trailing=S.HTML), S.PNG),
    "webp-exif-xmp": (S.WEBP_WITH_METADATA, S.EXTENDED_WEBP),
    "webp-simple-with-exif": (S.container(S.VP8L, S.WEBP_EXIF), S.WEBP),
    "webp-trailing": (S.container(S.VP8L, trailing=S.HTML), S.WEBP),
}


@pytest.mark.parametrize(("sent", "kept"), CLEANED.values(), ids=CLEANED.keys())
def test_metadata_and_whatever_follows_the_image_are_removed_and_nothing_else(sent: bytes, kept: bytes) -> None:
    assert sent != kept
    assert clean_receipt(sent) == (sniff(sent), kept)
    for private in (b"GPSLatitude", b"8600 1234", b"Ali Valiyev", b"uy manzili", b"script", b"PK\x03\x04"):
        assert private not in kept


MALFORMED = {
    "jpeg-then-html": b"\xff\xd8\xff" + S.HTML,
    "jpeg-no-scan": b"\xff\xd8" + S.JFIF + S.EOI,
    "jpeg-scan-before-frame": b"\xff\xd8" + S.JFIF + S.segment(0xDA, bytes(6)) + b"\x12" + S.EOI,
    "jpeg-no-end": S.JPEG[:-2],
    "jpeg-end-cut": S.JPEG[:-1],
    "jpeg-segment-beyond-the-file": b"\xff\xd8\xff\xe0\xff\xff" + bytes(40),
    "jpeg-segment-length-one": b"\xff\xd8\xff\xe0\x00\x01" + S.JPEG[2:],
    "jpeg-second-start": b"\xff\xd8\xff\xd8" + S.JPEG[2:],
    "jpeg-garbage-between-segments": b"\xff\xd8" + S.JFIF + b"\x00" + S.JPEG[2 + len(S.JFIF) :],
    "jpeg-only-fill": b"\xff\xd8" + b"\xff" * 20,
    "png-wrong-checksum": S.PNG[:-4] + b"\x00\x00\x00\x00",
    "png-no-end": S.PNG_SIGNATURE + S.IHDR + S.IDAT,
    "png-no-data": S.PNG_SIGNATURE + S.IHDR + S.IEND,
    "png-header-not-first": S.PNG_SIGNATURE + S.GAMMA + S.IHDR + S.IDAT + S.IEND,
    "png-header-short": S.PNG_SIGNATURE + S.chunk(b"IHDR", bytes(12)) + S.IDAT + S.IEND,
    "png-second-header": S.PNG_SIGNATURE + S.IHDR + S.IHDR + S.IDAT + S.IEND,
    "png-end-with-content": S.PNG_SIGNATURE + S.IHDR + S.IDAT + S.chunk(b"IEND", b"x"),
    "png-chunk-beyond-the-file": S.PNG_SIGNATURE + S.IHDR + b"\x7f\xff\xff\xffIDAT" + bytes(40),
    "png-chunk-name-not-letters": S.PNG_SIGNATURE + S.IHDR + S.chunk(b"ID\x00T", b"x") + S.IDAT + S.IEND,
    "png-signature-only": S.PNG_SIGNATURE,
    "png-then-html": S.PNG_SIGNATURE + S.HTML,
    "webp-size-beyond-the-file": S.WEBP[:-2],
    "webp-odd-size": b"RIFF\x05\x00\x00\x00WEBPx",
    "webp-no-chunks": b"RIFF\x04\x00\x00\x00WEBP",
    "webp-unknown-first-chunk": S.container(S.WEBP_EXIF, S.VP8L),
    "webp-extended-without-image": S.container(S.vp8x(0x08), S.WEBP_EXIF),
    "webp-extended-header-short": S.container(S.riff(b"VP8X", bytes(8)), S.VP8),
    "webp-chunk-beyond-the-container": b"RIFF\x10\x00\x00\x00WEBPVP8L\xff\x00\x00\x00" + bytes(4),
    "webp-chunk-header-cut": b"RIFF\x08\x00\x00\x00WEBPVP8L",
    "pdf-no-end": b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n",
    "pdf-end-too-early": S.PDF + bytes(2000),
}


@pytest.mark.parametrize("content", MALFORMED.values(), ids=MALFORMED.keys())
def test_a_file_that_only_starts_like_an_image_or_is_not_whole_is_refused(content: bytes) -> None:
    assert sniff(content) is not None, "the first bytes alone would have accepted it"
    assert clean_receipt(content) is FileRefusal.MALFORMED


@pytest.mark.parametrize("whole", [S.JPEG, S.PROGRESSIVE_JPEG, S.PNG, S.WEBP, S.EXTENDED_WEBP], ids=range(5))
def test_no_file_cut_short_anywhere_is_accepted(whole: bytes) -> None:
    for length in range(len(whole)):
        assert isinstance(clean_receipt(whole[:length]), FileRefusal), length


def test_the_refusals_of_the_first_look_still_come_first() -> None:
    assert clean_receipt(b"") is FileRefusal.EMPTY
    assert clean_receipt(S.HTML) is FileRefusal.TYPE
    assert clean_receipt(S.JPEG + bytes(MAX_FILE_BYTES)) is FileRefusal.TOO_LARGE
    # The limit is on what was sent, even when what would be kept is small.
    assert clean_receipt(S.jpeg(trailing=bytes(MAX_FILE_BYTES - len(S.JPEG)))) == ("image/jpeg", S.JPEG)
    assert clean_receipt(S.jpeg(trailing=bytes(MAX_FILE_BYTES - len(S.JPEG) + 1))) is FileRefusal.TOO_LARGE

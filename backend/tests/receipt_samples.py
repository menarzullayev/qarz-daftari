"""Small, well-formed files of each accepted receipt type, built by hand so that tests can add to them
exactly what a cleaner must remove: metadata, comments, and bytes after the end of the image."""

import zlib

# --- JPEG ----------------------------------------------------------------------------------------------


def segment(marker: int, payload: bytes) -> bytes:
    return bytes([0xFF, marker]) + (len(payload) + 2).to_bytes(2, "big") + payload


JFIF = segment(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")
EXIF = segment(0xE1, b"Exif\x00\x00II*\x00GPSLatitude 41.2995 GPSLongitude 69.2401")
XMP = segment(0xE1, b"http://ns.adobe.com/xap/1.0/\x00<x:xmpmeta>owner: Ali Valiyev</x:xmpmeta>")
ICC = segment(0xE2, b"ICC_PROFILE\x00\x01\x01" + bytes(24))
PHOTOSHOP = segment(0xED, b"Photoshop 3.0\x008BIM caption: uy manzili")
ADOBE = segment(0xEE, b"Adobe\x00\x64\x00\x00\x00\x00\x01")
COMMENT = segment(0xFE, b"taken at home, card 8600 1234")
_TABLES = segment(0xDB, bytes(65)) + segment(0xC0, b"\x08\x00\x01\x00\x01\x01\x01\x11\x00") + segment(0xC4, bytes(20))
# A scan with a stuffed FF, a restart marker and line-ending bytes in its compressed data.
_SCAN = segment(0xDA, b"\x01\x01\x00\x00\x3f\x00") + b"\x12\xff\x00\x34\r\n\xff\xd0\x56\r\n--tail\r\x0b\x0c"
EOI = b"\xff\xd9"


def jpeg(*metadata: bytes, keep: bytes = b"", trailing: bytes = b"") -> bytes:
    """`metadata` segments are what a cleaner drops; `keep` segments are ones an image needs."""
    return b"\xff\xd8" + JFIF + keep + b"".join(metadata) + _TABLES + _SCAN + EOI + trailing


JPEG = jpeg()
PROGRESSIVE_JPEG = (
    b"\xff\xd8"
    + JFIF
    + segment(0xDB, bytes(65))
    + segment(0xC2, b"\x08\x00\x01\x00\x01\x01\x01\x11\x00")
    + segment(0xC4, bytes(20))
    + segment(0xDA, b"\x01\x01\x00\x00\x00\x01")
    + b"\xaa\xff\x00\xbb"
    + segment(0xC4, bytes(20))
    + segment(0xDA, b"\x01\x01\x00\x01\x3f\x00")
    + b"\xcc\xdd"
    + EOI
)

# --- PNG -----------------------------------------------------------------------------------------------

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def chunk(kind: bytes, payload: bytes = b"") -> bytes:
    return len(payload).to_bytes(4, "big") + kind + payload + zlib.crc32(kind + payload).to_bytes(4, "big")


IHDR = chunk(b"IHDR", (1).to_bytes(4, "big") + (1).to_bytes(4, "big") + b"\x08\x00\x00\x00\x00")
IDAT = chunk(b"IDAT", zlib.compress(b"\x00\x7f"))
IEND = chunk(b"IEND")
TEXT = chunk(b"tEXt", b"Comment\x00card 8600 1234, Ali Valiyev")
PNG_EXIF = chunk(b"eXIf", b"II*\x00GPSLatitude 41.2995")
TIME = chunk(b"tIME", b"\x07\xea\x0a\x07\x0c\x00\x00")
GAMMA = chunk(b"gAMA", (45455).to_bytes(4, "big"))
PHYS = chunk(b"pHYs", bytes(9))


def png(*metadata: bytes, keep: bytes = b"", trailing: bytes = b"") -> bytes:
    return PNG_SIGNATURE + IHDR + keep + b"".join(metadata) + IDAT + IEND + trailing


PNG = png()

# --- WebP ----------------------------------------------------------------------------------------------


def riff(kind: bytes, payload: bytes) -> bytes:
    return kind + len(payload).to_bytes(4, "little") + payload + b"\x00" * (len(payload) & 1)


def container(*chunks: bytes, trailing: bytes = b"") -> bytes:
    body = b"WEBP" + b"".join(chunks)
    return b"RIFF" + len(body).to_bytes(4, "little") + body + trailing


VP8L = riff(b"VP8L", b"\x2f\x00\x00\x00\x00\x07\x10\xfd\x8f\xfe\x07")  # odd length: padded
VP8 = riff(b"VP8 ", b"\x30\x01\x00\x9d\x01\x2a\x01\x00\x01\x00")
WEBP_EXIF = riff(b"EXIF", b"II*\x00GPSLatitude 41.2995")
WEBP_XMP = riff(b"XMP ", b"<x:xmpmeta>owner: Ali Valiyev</x:xmpmeta>")
WEBP_ICC = riff(b"ICCP", bytes(16))


def vp8x(flags: int) -> bytes:
    return riff(b"VP8X", bytes([flags]) + bytes(9))


WEBP = container(VP8L)
EXTENDED_WEBP = container(vp8x(0x20), WEBP_ICC, VP8)
WEBP_WITH_METADATA = container(vp8x(0x20 | 0x08 | 0x04), WEBP_ICC, VP8, WEBP_EXIF, WEBP_XMP)

# --- PDF -----------------------------------------------------------------------------------------------

PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\nstartxref\n9\n%%EOF\n"

HTML = b"<!doctype html><script>alert(1)</script>"

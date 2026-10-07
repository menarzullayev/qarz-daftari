"""Reading the body of a new payment notice: JSON, or a form with a receipt parsed by the standard library."""

import asyncio
import random
from collections.abc import AsyncIterator

import pytest

from qarz.application.errors import ValidationFailed
from qarz.interface.payment_notices_api import parse_form, parse_notice, read_limited

BOUNDARY = "----qd-test-boundary-7f3a"
FORM = f"multipart/form-data; boundary={BOUNDARY}"


def form(*parts: tuple[str, bytes, str | None], boundary: str = BOUNDARY) -> bytes:
    """A form body as a browser builds it: each part is name, content and an optional file name."""
    body = b""
    for name, content, filename in parts:
        disposition = f'form-data; name="{name}"' + ("" if filename is None else f'; filename="{filename}"')
        head = f"--{boundary}\r\nContent-Disposition: {disposition}\r\n"
        if filename is not None:
            head += "Content-Type: application/octet-stream\r\n"
        body += head.encode() + b"\r\n" + content + b"\r\n"
    return body + f"--{boundary}--\r\n".encode()


AWKWARD = [
    bytes(range(256)),
    bytes(range(256)) * 3,
    b"\r",
    b"\n",
    b"\r\n",
    b"\r\r\n",
    b"\n\r",
    b"abc\r",
    b"abc\n",
    b"abc\r\n",
    b"\r\nabc",
    b"\r\n\r\n",
    b"\x0b\x0c\x1c\x1d\x1e\x85",  # what str.splitlines treats as line ends
    b"\xc2\x85\xe2\x80\xa8\xe2\x80\xa9",  # the same as UTF-8: NEL, LS, PS
    b"--",
    b"--not-the-boundary\r\n",
    b"\r\n--" + BOUNDARY[:-1].encode() + b"\r\n",  # almost the boundary
    b'Content-Disposition: form-data; name="amount"\r\n\r\n999',  # header-looking content
    b"=?utf-8?q?encoded-word?=",
    b"\xff\xd8\xff" + b"\x00" * 1000,
    b"\x00",
    b" ",
    b" leading and trailing spaces ",
    b"\t\r\n\t",
]


@pytest.mark.parametrize("content", AWKWARD, ids=range(len(AWKWARD)))
def test_a_file_comes_out_of_the_form_byte_for_byte(content: bytes) -> None:
    fields = parse_form(FORM, form(("amount", b"20000", None), ("receipt", content, "chek.jpg")))
    assert fields == {"amount": b"20000", "receipt": content}


def test_random_binary_content_survives_the_form() -> None:
    rng = random.Random(20261007)  # noqa: S311  (reproducible test data, not a secret)
    for _ in range(300):
        size = rng.choice([1, 2, 3, 7, 64, 255, 256, 1000, 4096, 70_000])
        # Line-ending bytes are made far more frequent than chance, since they are what a text parser mangles.
        alphabet = bytes(range(256)) + b"\r\n" * 40 + b"-" * 20
        content = bytes(rng.choice(alphabet) for _ in range(size))
        if b"--" + BOUNDARY.encode() in content:
            continue
        order = [("amount", b"20000", None), ("receipt", content, "x")]
        rng.shuffle(order)
        assert parse_form(FORM, form(*order))["receipt"] == content


def test_a_large_file_survives_the_form() -> None:
    content = random.Random(7).randbytes(5 * 1024 * 1024)  # noqa: S311
    assert parse_form(FORM, form(("receipt", content, "x"), ("amount", b"100", None)))["receipt"] == content


def test_the_parts_own_name_and_type_are_ignored() -> None:
    body = (
        (
            f"--{BOUNDARY}\r\n"
            'Content-Disposition: form-data; name="receipt"; filename="../../etc/passwd"\r\n'
            "Content-Type: text/html; charset=utf-16\r\n\r\n"
        ).encode()
        + b"\xff\xd8\xff\xe0binary"
        + f"\r\n--{BOUNDARY}--\r\n".encode()
    )
    assert parse_form(FORM, body) == {"receipt": b"\xff\xd8\xff\xe0binary"}


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"amount=20000",
        f"--{BOUNDARY}\r\n".encode(),
        form(("amount", b"1", None), ("amount", b"2", None)),
        form(("receipt", b"a", "a"), ("receipt", b"b", "b")),
        form(("amount", b"1", None), ("note", b"x", None)),
        form(("status", b"accepted", None)),
        f"--{BOUNDARY}\r\nContent-Type: text/plain\r\n\r\n20000\r\n--{BOUNDARY}--\r\n".encode(),  # a part with no name
        form(("amount", b"20000", None), boundary="another-boundary"),
    ],
)
def test_a_form_with_other_fields_repeated_fields_or_no_parts_is_refused(body: bytes) -> None:
    with pytest.raises(ValidationFailed):
        parse_notice(FORM, body)


def test_a_form_that_was_cut_off_on_the_way_is_refused_whole() -> None:
    """A connection that drops mid-upload leaves no closing boundary: half a receipt must not be kept."""
    whole = form(("amount", b"20000", None), ("receipt", b"\xff\xd8\xff" + bytes(5000), "chek.jpg"))
    assert parse_notice(FORM, whole)[0] == 20000
    closing = f"--{BOUNDARY}--\r\n".encode()
    # Losing only the line break after the closing boundary loses nothing of the content.
    assert parse_notice(FORM, whole[:-2]) == parse_notice(FORM, whole)
    for cut in (whole[:-3], whole[: -len(closing)], whole[: -len(closing) - 2], whole[: len(whole) // 2], whole[:200]):
        with pytest.raises(ValidationFailed):
            parse_notice(FORM, cut)
    # The amount alone, with the receipt part and the end of the form missing.
    with pytest.raises(ValidationFailed):
        parse_notice(FORM, form(("amount", b"20000", None))[: -len(closing)])


@pytest.mark.parametrize(
    "content_type",
    [
        "multipart/form-data",  # no boundary
        "multipart/form-data; boundary=",
        f"multipart/form-data; boundary={BOUNDARY}\r\nX-Injected: 1",
        f"multipart/form-data; boundary={BOUNDARY}\nContent-Type: text/plain",
        "multipart/mixed; boundary=" + BOUNDARY,
        "text/plain",
        "application/x-www-form-urlencoded",
        "",
    ],
)
def test_a_content_type_that_is_not_a_form_or_json_is_refused(content_type: str) -> None:
    with pytest.raises(ValidationFailed):
        parse_notice(content_type, form(("amount", b"20000", None)))


def test_a_notice_is_read_from_a_form_or_from_json() -> None:
    assert parse_notice(FORM, form(("amount", b"20000", None))) == (20000, None)
    assert parse_notice(FORM, form(("amount", b" 20000 ", None), ("receipt", b"x", "a.jpg"))) == (20000, b"x")
    assert parse_notice("Multipart/Form-Data; boundary=" + BOUNDARY, form(("amount", b"7", None))) == (7, None)
    assert parse_notice("application/json", b'{"amount": 20000}') == (20000, None)
    assert parse_notice("application/json; charset=utf-8", b'{"amount": 20000}') == (20000, None)
    # Whatever JSON holds as the amount is passed on unchanged: the application decides what an amount is.
    assert parse_notice("application/json", b'{"amount": "20000"}') == ("20000", None)
    assert parse_notice("application/json", b'{"amount": 20000.5}') == (20000.5, None)


@pytest.mark.parametrize(
    "amount", [b"", b"-1", b"+5", b"1.0", b"1e3", b"1 000", b"0x10", b"\xd9\xa1\xd9\xa2", b"1" * 13]
)
def test_the_amount_field_of_a_form_is_digits_only(amount: bytes) -> None:
    with pytest.raises(ValidationFailed) as refused:
        parse_notice(FORM, form(("amount", amount, None)))
    assert "amount" in refused.value.fields


@pytest.mark.parametrize("body", [b"", b"{", b"[20000]", b"20000", b"null", b'{"amount": 1, "x": 2}', b"{}", b"\xff"])
def test_json_that_is_not_an_object_with_just_the_amount_is_refused(body: bytes) -> None:
    with pytest.raises(ValidationFailed):
        parse_notice("application/json", body)


class FakeRequest:
    """Just what `read_limited` uses of a request: the headers and the body as it arrives."""

    def __init__(self, chunks: list[bytes], headers: dict[str, str] | None = None) -> None:
        self.headers = headers or {}
        self.chunks = chunks
        self.read = 0

    async def stream(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            self.read += 1
            yield chunk


def test_a_body_up_to_the_limit_is_read_whole() -> None:
    request = FakeRequest([b"abc", b"", b"defg"], {"content-length": "7"})
    assert asyncio.run(read_limited(request, 7)) == b"abcdefg"  # type: ignore[arg-type]
    assert asyncio.run(read_limited(FakeRequest([]), 7)) == b""  # type: ignore[arg-type]


def test_a_body_announced_as_too_long_is_refused_without_reading_any_of_it() -> None:
    request = FakeRequest([b"x" * 8], {"content-length": "8"})
    with pytest.raises(ValidationFailed) as refused:
        asyncio.run(read_limited(request, 7))  # type: ignore[arg-type]
    assert refused.value.fields == {"receipt": "too_large"}
    assert request.read == 0


@pytest.mark.parametrize("declared", [None, "3", "not a number", "-1", ""])
def test_a_body_longer_than_announced_is_refused_at_the_byte_that_passes_the_limit(declared: str | None) -> None:
    headers = {} if declared is None else {"content-length": declared}
    request = FakeRequest([b"abc", b"defg", b"h", b"never read"], headers)
    with pytest.raises(ValidationFailed) as refused:
        asyncio.run(read_limited(request, 7))  # type: ignore[arg-type]
    assert refused.value.fields == {"receipt": "too_large"}
    assert request.read == 3, "reading stops with the chunk that crosses the limit"

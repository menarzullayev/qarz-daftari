"""The one larger allowance of the body limit: sending a payment notice, which may carry a receipt."""

import asyncio
import re
from typing import Any

import pytest

from qarz.domain.files import MAX_FILE_BYTES
from qarz.interface.body_limit import MAX_BODY, Allowance, BodyLimit
from qarz.interface.payment_notices_api import MAX_BODY_BYTES, RECEIPT_UPLOAD

LINK = "3f2b8c1e-5a4d-4e6f-9a7b-0c1d2e3f4a5b"
UPLOAD = f"/api/v1/me/accounts/{LINK}/payment-notices"


def answer(method: str, path: str, size: int, *, declared: bool, limit: Any = None) -> tuple[int, int]:
    """The status given to a body of `size` bytes, and how many of them reached the application."""
    reached = 0
    sent: list[dict[str, Any]] = []
    pieces = [b"x" * min(65536, size - offset) for offset in range(0, size, 65536)] or [b""]
    queue = [
        {"type": "http.request", "body": piece, "more_body": index < len(pieces) - 1}
        for index, piece in enumerate(pieces)
    ]

    async def inner(scope: Any, receive: Any, send: Any) -> None:
        nonlocal reached
        while True:
            message = await receive()
            reached += len(message.get("body", b""))
            if not message.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive() -> dict[str, Any]:
        return queue.pop(0)

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    app = limit(inner) if limit is not None else BodyLimit(inner, allowances=(RECEIPT_UPLOAD,))
    headers = [(b"content-length", str(size).encode())] if declared else []
    asyncio.run(app({"type": "http", "method": method, "path": path, "headers": headers}, receive, send))
    return sent[0]["status"], reached


def test_the_allowance_is_one_receipt_and_the_form_around_it() -> None:
    assert MAX_BODY == 1024 * 1024
    assert RECEIPT_UPLOAD.max_bytes == MAX_BODY_BYTES == MAX_FILE_BYTES + 16 * 1024
    assert RECEIPT_UPLOAD.method == "POST"


@pytest.mark.parametrize("declared", [True, False], ids=["declared", "streamed"])
def test_the_upload_route_takes_up_to_its_allowance_and_not_a_byte_more(declared: bool) -> None:
    assert answer("POST", UPLOAD, MAX_BODY + 1, declared=declared) == (200, MAX_BODY + 1)
    assert answer("POST", UPLOAD, MAX_BODY_BYTES, declared=declared) == (200, MAX_BODY_BYTES)
    assert answer("POST", UPLOAD, MAX_BODY_BYTES + 1, declared=declared) == (413, 0)


@pytest.mark.parametrize("declared", [True, False], ids=["declared", "streamed"])
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/v1/shops"),
        ("POST", f"/api/v1/me/accounts/{LINK}/disputes"),
        ("POST", f"/api/v1/me/accounts/{LINK}/payment-notices/"),
        ("POST", f"/api/v1/me/accounts/{LINK}/payment-notices/x"),
        ("POST", f"/prefix/api/v1/me/accounts/{LINK}/payment-notices"),
        ("POST", "/api/v1/me/accounts/anything/payment-notices"),
        ("POST", f"/api/v1/me/accounts/{LINK}/x/payment-notices"),
        ("POST", f"/api/v1/me/accounts/{LINK}%2Fx/payment-notices"),
        ("POST", f"/api/v1/shops/{LINK}/payment-notices/{LINK}/accept"),
        ("POST", "/tg/webhook"),
        ("PUT", UPLOAD),
        ("PATCH", UPLOAD),
        ("GET", UPLOAD),
        ("post", UPLOAD),
    ],
)
def test_every_other_request_keeps_the_general_limit(method: str, path: str, declared: bool) -> None:
    assert answer(method, path, MAX_BODY, declared=declared) == (200, MAX_BODY)
    assert answer(method, path, MAX_BODY + 1, declared=declared) == (413, 0)


def test_without_an_allowance_the_upload_route_is_limited_like_any_other() -> None:
    assert answer("POST", UPLOAD, MAX_BODY + 1, declared=True, limit=BodyLimit) == (413, 0)

    def narrow(inner: Any) -> BodyLimit:
        return BodyLimit(inner, 10, (Allowance("POST", re.compile("/a"), 20),))

    assert answer("POST", "/a", 20, declared=True, limit=narrow) == (200, 20)
    assert answer("POST", "/a", 21, declared=True, limit=narrow) == (413, 0)
    assert answer("POST", "/ab", 11, declared=True, limit=narrow) == (413, 0), "the path must match as a whole"
    assert answer("GET", "/a", 11, declared=True, limit=narrow) == (413, 0)

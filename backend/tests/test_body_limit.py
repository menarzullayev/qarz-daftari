"""The limit on the size of a request body, driven directly as an ASGI application."""

import asyncio
import json
from typing import Any

from qarz.interface.body_limit import MAX_BODY, BodyLimit


class Inner:
    """Stands for the application: reads the whole body and answers with its length."""

    def __init__(self) -> None:
        self.called = 0
        self.seen = b""

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        self.called += 1
        if scope["type"] == "http":
            while True:
                message = await receive()
                self.seen += message.get("body", b"")
                if not message.get("more_body", False):
                    break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": str(len(self.seen)).encode()})


def run(pieces: list[bytes], *, declared: int | None = None, limit: int = 10, kind: str = "http") -> tuple[Any, ...]:
    inner = Inner()
    app = BodyLimit(inner, limit)
    asked = 0
    sent: list[dict[str, Any]] = []
    queue = [
        {"type": "http.request", "body": piece, "more_body": index < len(pieces) - 1}
        for index, piece in enumerate(pieces)
    ] or [{"type": "http.request", "body": b"", "more_body": False}]

    async def receive() -> dict[str, Any]:
        nonlocal asked
        asked += 1
        return queue.pop(0) if queue else {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    headers = [] if declared is None else [(b"content-length", str(declared).encode())]
    asyncio.run(app({"type": kind, "headers": headers}, receive, send))
    return sent[0]["status"], sent[1]["body"], inner.called, asked, inner.seen


def test_a_body_within_the_limit_reaches_the_application_unchanged() -> None:
    assert run([b"hello"], declared=5) == (200, b"5", 1, 1, b"hello")
    assert run([b"01234", b"56789"]) == (200, b"10", 1, 2, b"0123456789"), "exactly the limit fits"
    assert run([]) == (200, b"0", 1, 1, b"")
    assert run([b"ab", b"", b"cd"]) == (200, b"4", 1, 3, b"abcd")


def test_a_declared_length_over_the_limit_is_refused_without_reading_anything() -> None:
    status, body, called, asked, _ = run([b"x" * 11], declared=11)
    assert (status, called, asked) == (413, 0, 0)
    assert json.loads(body)["error"]["code"] == "BODY_TOO_LARGE"
    assert run([b"x" * 10], declared=10)[0] == 200


def test_a_body_with_no_declared_length_is_given_up_on_once_too_large() -> None:
    status, _, called, asked, _ = run([b"0123456", b"789a", b"more", b"and more"])
    assert (status, called, asked) == (413, 0, 2), "nothing after the piece that crosses the limit is read"
    assert run([b"x" * 11])[:3] == (
        413,
        json.dumps(
            {"error": {"code": "BODY_TOO_LARGE", "message": "So'rov juda katta.", "fields": {}}}, ensure_ascii=False
        ).encode(),
        0,
    )
    # A length declared smaller than what is then sent does not get more through.
    assert run([b"012345", b"6789ab"], declared=3)[0] == 413


def test_what_is_not_an_http_request_is_passed_on() -> None:
    assert run([], kind="lifespan")[2] == 1


def test_the_limit_of_the_application_is_one_mebibyte() -> None:
    assert MAX_BODY == 1024 * 1024

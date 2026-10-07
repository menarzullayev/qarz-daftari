"""A limit on the size of a request body, applied before anything of the request is looked at.

Without it a body of any size is read and parsed before the caller is even asked who they are. The proxy
in front of the application is to have a limit of its own; this one holds whether or not it does.
"""

import json
import re
from collections.abc import Awaitable, Callable, MutableMapping, Sequence
from dataclasses import dataclass
from typing import Any

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
App = Callable[[Scope, Receive, Send], Awaitable[None]]

MAX_BODY = 1024 * 1024  # no request of the API is near this; uploads have their own, larger allowance

_TOO_LARGE = json.dumps(
    {"error": {"code": "BODY_TOO_LARGE", "message": "So'rov juda katta.", "fields": {}}}, ensure_ascii=False
).encode("utf-8")


@dataclass(frozen=True)
class Allowance:
    """One route that may carry a larger body: exactly this method, and a path that matches as a whole."""

    method: str
    path: re.Pattern[str]
    max_bytes: int


class BodyLimit:
    """ASGI middleware: answers 413 to a request whose body is larger than `max_bytes`.

    A declared length over the limit is refused without reading anything. A body with no declared length
    is read up to the limit and then given up on. A body within the limit is handed on unchanged.

    `allowances` name the few routes with a limit of their own; every other request keeps `max_bytes`.
    """

    def __init__(self, app: App, max_bytes: int = MAX_BODY, allowances: Sequence[Allowance] = ()) -> None:
        self._app = app
        self._max = max_bytes
        self._allowances = tuple(allowances)

    def _limit(self, scope: Scope) -> int:
        method, path = scope.get("method"), scope.get("path")
        for allowance in self._allowances:
            if method == allowance.method and isinstance(path, str) and allowance.path.fullmatch(path):
                return allowance.max_bytes
        return self._max

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        limit = self._limit(scope)
        declared = dict(scope.get("headers") or []).get(b"content-length", b"")
        if declared.isdigit() and int(declared) > limit:
            await self._refuse(send)
            return

        held: list[Message] = []
        size = 0
        while True:
            message = await receive()
            held.append(message)
            if message["type"] != "http.request":
                break
            size += len(message.get("body", b""))
            if size > limit:
                await self._refuse(send)
                return
            if not message.get("more_body", False):
                break

        async def replay() -> Message:
            return held.pop(0) if held else await receive()

        await self._app(scope, replay, send)

    @staticmethod
    async def _refuse(send: Send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(_TOO_LARGE)).encode()),
                    (b"connection", b"close"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": _TOO_LARGE})

"""Structured logs, a request identifier, and metrics (operations document: Monitoring, Logging).

Every request gets an identifier, returned in `X-Request-Id` and written with every log line about it, so
that a person reporting a failure can quote one short string. Logs are one JSON object a line and hold
identifiers only: never a name, a phone number, a message text, a token or a session identifier.

The metrics are what the alert rules in `deploy/monitoring` are written against. They live in this
process's memory and start from zero when it starts.
"""

import json
import logging
import re
import time
import traceback
from collections.abc import Awaitable, Callable, MutableMapping
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
App = Callable[[Scope, Receive, Send], Awaitable[None]]

log = logging.getLogger("qarz.request")
REQUEST_ID_HEADER = b"x-request-id"
_GIVEN_ID = re.compile(rb"[A-Za-z0-9_-]{8,64}")

# What a log line may carry besides its event name. Anything else given as `extra` is dropped, so that a
# careless call cannot put personal data into the logs.
LOG_FIELDS = (
    "request_id",
    "method",
    "route",
    "status",
    "ms",
    "user_id",
    "shop_id",
    "update_id",
    "job",
    "period",
    "channel",
    "count",
    "kind",
)

BUCKETS_MS = (25, 50, 100, 200, 300, 400, 500, 1000, 2500, 5000)

SECURITY_SHOP_NOT_MEMBER = "shop_not_member"
SECURITY_BAD_SIGN_IN = "bad_sign_in"
SECURITY_BAD_WEBHOOK_SECRET = "bad_webhook_secret"  # noqa: S105  (the name of an event, not a secret)
# An administrator's second-factor code was refused, or the factor is locked. A route reports it itself
# (`request.state.security_event`): the status alone does not tell it from a rate limit.
SECURITY_BAD_SECOND_FACTOR = "bad_second_factor"
# An administrator opened a support access: from now until it ends they can read one shop's data.
SECURITY_SUPPORT_ACCESS_OPENED = "support_access_opened"
# An administrator asked for a shop's data without an open support access, and was refused.
SECURITY_SUPPORT_ACCESS_REQUIRED = "admin_without_support_access"
# An administrator gave a shop to another person (runbook 7). Rare, and each one should be known about.
SECURITY_OWNER_REASSIGNED = "owner_reassigned"
EVERY_SECURITY_KIND = (
    SECURITY_SHOP_NOT_MEMBER,
    SECURITY_BAD_SIGN_IN,
    SECURITY_BAD_WEBHOOK_SECRET,
    SECURITY_BAD_SECOND_FACTOR,
    SECURITY_SUPPORT_ACCESS_OPENED,
    SECURITY_SUPPORT_ACCESS_REQUIRED,
    SECURITY_OWNER_REASSIGNED,
)

_INTERNAL_ERROR = "Xatolik yuz berdi."


class JsonFormatter(logging.Formatter):
    """One JSON object a line: time, level, logger, event, and the allowed fields that were given."""

    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "event": record.getMessage(),
        }
        for name in LOG_FIELDS:
            value = record.__dict__.get(name)
            if value is not None:
                line[name] = value if isinstance(value, int | float | bool) else str(value)
        if record.exc_info and record.exc_info[0] is not None:
            # The type and where it happened. The exception's own text is left out: it is written by
            # libraries and by the database, and nothing promises that it holds no personal data.
            line["error"] = record.exc_info[0].__name__
            line["trace"] = [
                f"{frame.filename}:{frame.lineno} {frame.name}" for frame in traceback.extract_tb(record.exc_info[2])
            ][-12:]
        return json.dumps(line, ensure_ascii=False)


def configure_logging(level: int = logging.INFO) -> None:
    """Send every log of the process to standard error as JSON lines. Calling it again adds nothing."""
    root = logging.getLogger()
    if not any(isinstance(handler.formatter, JsonFormatter) for handler in root.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        root.addHandler(handler)
    root.setLevel(level)
    # The server's own access log would repeat each request with its full path and query.
    logging.getLogger("uvicorn.access").disabled = True


class Metrics:
    """Counters and latency histograms of this process, rendered in the Prometheus text format."""

    def __init__(self) -> None:
        self._requests: dict[tuple[str, str, int], int] = {}
        self._buckets: dict[tuple[str, str], list[int]] = {}
        self._sums: dict[tuple[str, str], float] = {}
        # Every kind is exposed from the start, at zero: the alert rules read `increase(...)`, which sees
        # nothing in a series whose first sample is already 1, so the first event after a restart
        # would pass unnoticed.
        self._security: dict[str, int] = dict.fromkeys(EVERY_SECURITY_KIND, 0)

    def observe(self, method: str, route: str, status: int, ms: float) -> None:
        self._requests[(method, route, status)] = self._requests.get((method, route, status), 0) + 1
        counts = self._buckets.setdefault((method, route), [0] * (len(BUCKETS_MS) + 1))
        for index, edge in enumerate(BUCKETS_MS):
            if ms <= edge:
                counts[index] += 1
        counts[-1] += 1
        self._sums[(method, route)] = self._sums.get((method, route), 0.0) + ms

    def security_event(self, kind: str) -> None:
        self._security[kind] = self._security.get(kind, 0) + 1

    def render(self, gauges: dict[str, dict[str, float]] | None = None) -> str:
        lines = ["# TYPE qd_requests_total counter"]
        for (method, route, status), count in sorted(self._requests.items()):
            lines.append(f'qd_requests_total{{method="{method}",route="{route}",status="{status}"}} {count}')
        lines.append("# TYPE qd_request_duration_ms histogram")
        for (method, route), counts in sorted(self._buckets.items()):
            labels = f'method="{method}",route="{route}"'
            for edge, count in zip(BUCKETS_MS, counts, strict=False):
                lines.append(f'qd_request_duration_ms_bucket{{{labels},le="{edge}"}} {count}')
            lines.append(f'qd_request_duration_ms_bucket{{{labels},le="+Inf"}} {counts[-1]}')
            lines.append(f"qd_request_duration_ms_sum{{{labels}}} {self._sums[(method, route)]:.1f}")
            lines.append(f"qd_request_duration_ms_count{{{labels}}} {counts[-1]}")
        lines.append("# TYPE qd_security_events_total counter")
        for kind, count in sorted(self._security.items()):
            lines.append(f'qd_security_events_total{{kind="{kind}"}} {count}')
        for name, values in sorted((gauges or {}).items()):
            lines.append(f"# TYPE {name} gauge")
            label = "channel" if "outbox" in name else "status" if "receipts" in name or "sms" in name else "job"
            for key, value in sorted(values.items()):
                lines.append(f'{name}{{{label}="{key}"}} {value:.0f}')
        return "\n".join(lines) + "\n"


def security_kind(route: str, status: int, signed_in: bool) -> str | None:
    """Which security event, if any, an answered request is (operations document, Monitoring)."""
    if status == 404 and signed_in and "{shop_id}" in route:
        # A signed-in person asked about a shop they are not a member of.
        return SECURITY_SHOP_NOT_MEMBER
    if status == 401 and route.startswith("/api/v1/auth/telegram-"):
        return SECURITY_BAD_SIGN_IN
    if status == 403 and route == "/tg/webhook":
        return SECURITY_BAD_WEBHOOK_SECRET
    return None


# Answers of the API carry personal data: no cache may keep one, and no browser may guess its type.
_API_HEADERS = ((b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff"))


class Observe:
    """ASGI middleware: the request identifier, one log line and one measurement for each request.

    An exception nobody handled is logged with the identifier and answered with a JSON 500 that carries
    the same identifier and nothing about the failure itself.
    """

    def __init__(self, app: App, metrics: Metrics, clock: Callable[[], float] = time.perf_counter) -> None:
        self._app = app
        self._metrics = metrics
        self._clock = clock

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        given = dict(scope.get("headers") or []).get(REQUEST_ID_HEADER, b"")
        request_id = given.decode("ascii") if _GIVEN_ID.fullmatch(given) else uuid4().hex
        state = scope.setdefault("state", {})
        state["request_id"] = request_id
        protected = str(scope.get("path", "")).startswith("/api/")
        started = self._clock()
        status = 500
        answered = False

        async def send_with_id(message: Message) -> None:
            nonlocal status, answered
            if message["type"] == "http.response.start":
                status = int(message["status"])
                answered = True
                headers = [pair for pair in message.get("headers", []) if pair[0].lower() != REQUEST_ID_HEADER]
                if protected:
                    # Set here and not only in the proxy, so that it holds without one (security review,
                    # finding 12). A route that names its own caching rule keeps it.
                    named = {pair[0].lower() for pair in headers}
                    headers += [pair for pair in _API_HEADERS if pair[0] not in named]
                message = {**message, "headers": [*headers, (REQUEST_ID_HEADER, request_id.encode("ascii"))]}
            await send(message)

        failure: BaseException | None = None
        try:
            await self._app(scope, receive, send_with_id)
        except Exception as error:
            failure = error
            if answered:
                raise
            status = 500
            body = json.dumps(
                {"error": {"code": "ERROR", "message": _INTERNAL_ERROR, "fields": {}, "request_id": request_id}},
                ensure_ascii=False,
            ).encode("utf-8")
            await send_with_id(
                {
                    "type": "http.response.start",
                    "status": 500,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
                }
            )
            await send({"type": "http.response.body", "body": body})
        finally:
            self._record(scope, state, request_id, status, (self._clock() - started) * 1000.0, failure)

    def _record(
        self, scope: Scope, state: Any, request_id: str, status: int, ms: float, failure: BaseException | None
    ) -> None:
        template = getattr(scope.get("route"), "path", None)
        # A path nobody routes is one label, whatever was typed: paths are chosen by the caller.
        route = template if isinstance(template, str) else "unmatched"
        method = str(scope.get("method", ""))
        self._metrics.observe(method, route, status, ms)
        user_id = state.get("user_id") if isinstance(state, dict) else None
        fields = {
            "request_id": request_id,
            "method": method,
            "route": route,
            "status": status,
            "ms": round(ms, 1),
            "user_id": user_id,
            "shop_id": (scope.get("path_params") or {}).get("shop_id") if route != "unmatched" else None,
        }
        reported = state.get("security_event") if isinstance(state, dict) else None
        kind = reported or security_kind(route, status, signed_in=user_id is not None)
        if kind is not None:
            self._metrics.security_event(kind)
            log.warning("security", extra={**fields, "kind": kind})
        if failure is not None:
            log.error("request_failed", extra=fields, exc_info=failure)
        else:
            log.info("request", extra=fields)

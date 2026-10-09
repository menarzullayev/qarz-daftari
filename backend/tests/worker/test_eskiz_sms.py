"""The Eskiz SMS sender against a fake transport: nothing here opens a connection to anything.

Eskiz's document names no error answer, so the failure cases below state this code's own reading of an
HTTP status, not something Eskiz promised (runbook 12 lists it as unproven).
"""

import asyncio
import http.client
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from qarz.application.chat_texts import money
from qarz.application.ports import SendFailed, SendRejected
from qarz.application.reminders import reminder_text
from qarz.domain.languages import LANGUAGES, sms_language
from qarz.domain.reminders import Channel, ReminderKind, ReminderPlan
from qarz.infrastructure.eskiz_sms import (
    LOGIN_PATH,
    SEND_PATH,
    SIGN_IN_PAUSE,
    TOKEN_MAX_AGE,
    EskizSmsProvider,
    eskiz_number,
    wire_text,
)
from qarz.interface.observability import JsonFormatter

EMAIL = "owner@qarz-test.example"
PASSWORD = "eskiz-PASSWORD-9f2c"
SENDER = "4546"
PHONE = "+998901234567"
TEXT = "Shop A: Ali Valiyev, 70 000 so'm qarz muddati o'tgan. Iltimos, to'lab qo'ying."
ACCEPTED = (
    200,
    b'{"id": "59bf10a2-aba8-4694-8fd5-0be20102a580", "message": "Waiting for SMS provider", "status": "waiting"}',
)

Answer = tuple[int, bytes] | BaseException | Callable[[], Any]


def token_answer(token: str) -> tuple[int, bytes]:
    return 200, json.dumps({"message": "token_generated", "data": {"token": token}, "token_type": "bearer"}).encode()


@dataclass
class Clock:
    current: datetime = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current

    def advance(self, delta: timedelta) -> None:
        self.current += delta


@dataclass
class FakeEskiz:
    """Answers each path from its own queue, in order; a call with nothing queued fails the test."""

    logins: list[Answer] = field(default_factory=list)
    sends: list[Answer] = field(default_factory=list)
    calls: list[tuple[str, str, dict[str, str], dict[str, str]]] = field(default_factory=list)

    async def __call__(self, method: str, path: str, headers: dict[str, str], body: bytes | None) -> tuple[int, bytes]:
        self.calls.append((method, path, headers, form_fields(headers, body or b"")))
        queue = {LOGIN_PATH: self.logins, SEND_PATH: self.sends}[path]
        assert queue, f"an unexpected call to {path}"
        answer = queue.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        if callable(answer):
            return await answer()  # type: ignore[no-any-return]
        return answer

    def paths(self) -> list[str]:
        return [path for _, path, _, _ in self.calls]


def form_fields(headers: dict[str, str], body: bytes) -> dict[str, str]:
    boundary = headers["content-type"].split("boundary=", 1)[1]
    assert headers["content-type"].startswith("multipart/form-data; ") and body.endswith(f"--{boundary}--\r\n".encode())
    found = re.findall(rb'name="([a-z_]+)"\r\n\r\n(.*?)\r\n--' + boundary.encode(), body, flags=re.DOTALL)
    return {name.decode(): value.decode() for name, value in found}


def provider(eskiz: FakeEskiz, clock: Clock | None = None, **more: Any) -> EskizSmsProvider:
    return EskizSmsProvider(
        email=EMAIL, password=PASSWORD, sender=SENDER, transport=eskiz, now=(clock or Clock()).now, **more
    )


def send(sms: EskizSmsProvider, phone: str = PHONE, text: str = TEXT) -> str:
    return asyncio.run(sms.send(phone, text))


# --- the happy path -------------------------------------------------------------------------------------


def test_a_message_is_sent_as_the_document_describes() -> None:
    eskiz = FakeEskiz(logins=[token_answer("token-one")], sends=[ACCEPTED])
    assert send(provider(eskiz)) == "59bf10a2-aba8-4694-8fd5-0be20102a580"
    (_, login_path, login_headers, login), (method, send_path, headers, message) = eskiz.calls
    assert (login_path, login) == (LOGIN_PATH, {"email": EMAIL, "password": PASSWORD})
    assert "authorization" not in login_headers
    assert (method, send_path) == ("POST", SEND_PATH)
    assert headers["authorization"] == "Bearer token-one"
    # No callback address: delivery reports are not taken (see the pull request).
    assert message == {"mobile_phone": "998901234567", "message": TEXT, "from": SENDER}


def test_the_token_is_obtained_once_and_used_again() -> None:
    eskiz = FakeEskiz(logins=[token_answer("token-one")], sends=[ACCEPTED] * 3)
    sms = provider(eskiz)

    async def three() -> None:
        for _ in range(3):
            await sms.send(PHONE, TEXT)

    asyncio.run(three())
    assert eskiz.paths() == [LOGIN_PATH, SEND_PATH, SEND_PATH, SEND_PATH]
    assert {headers["authorization"] for _, path, headers, _ in eskiz.calls if path == SEND_PATH} == {
        "Bearer token-one"
    }


def test_nothing_is_asked_of_eskiz_before_the_first_message() -> None:
    eskiz = FakeEskiz()
    provider(eskiz)
    assert eskiz.calls == []


def test_a_token_near_the_end_of_its_life_is_replaced_before_it_is_refused() -> None:
    eskiz = FakeEskiz(logins=[token_answer("token-one"), token_answer("token-two")], sends=[ACCEPTED] * 3)
    clock = Clock()
    sms = provider(eskiz, clock)
    send(sms)
    clock.advance(TOKEN_MAX_AGE - timedelta(minutes=1))
    send(sms)
    clock.advance(timedelta(minutes=2))
    send(sms)
    assert eskiz.paths() == [LOGIN_PATH, SEND_PATH, SEND_PATH, LOGIN_PATH, SEND_PATH]
    assert eskiz.calls[-1][2]["authorization"] == "Bearer token-two"


# --- a token that is no longer accepted -----------------------------------------------------------------


def test_a_refused_token_is_replaced_once_and_the_message_sent_again_once() -> None:
    eskiz = FakeEskiz(
        logins=[token_answer("token-old"), token_answer("token-new")],
        sends=[(401, b'{"message": "Expired"}'), ACCEPTED, ACCEPTED],
    )
    sms = provider(eskiz)
    assert send(sms) == "59bf10a2-aba8-4694-8fd5-0be20102a580"
    assert eskiz.paths() == [LOGIN_PATH, SEND_PATH, LOGIN_PATH, SEND_PATH]
    assert [headers.get("authorization") for _, path, headers, _ in eskiz.calls if path == SEND_PATH] == [
        "Bearer token-old",
        "Bearer token-new",
    ]
    # The new token is the one kept.
    send(sms)
    assert eskiz.paths()[4:] == [SEND_PATH] and eskiz.calls[-1][2]["authorization"] == "Bearer token-new"


def test_a_token_refused_twice_is_a_failure_to_retry_later_and_not_a_loop() -> None:
    eskiz = FakeEskiz(
        logins=[token_answer("token-old"), token_answer("token-new"), token_answer("token-third")],
        sends=[(401, b"{}"), (401, b"{}"), ACCEPTED],
    )
    clock = Clock()
    sms = provider(eskiz, clock)
    with pytest.raises(SendFailed):
        send(sms)
    assert eskiz.paths() == [LOGIN_PATH, SEND_PATH, LOGIN_PATH, SEND_PATH], "one new sign-in, one more attempt"
    # The next messages do not sign in again at once: the account would be asked once a message.
    for _ in range(3):
        with pytest.raises(SendFailed):
            send(sms)
    assert len(eskiz.calls) == 4
    clock.advance(SIGN_IN_PAUSE + timedelta(seconds=1))
    send(sms)
    assert eskiz.paths()[4:] == [LOGIN_PATH, SEND_PATH]


# --- what each failure becomes --------------------------------------------------------------------------


@pytest.mark.parametrize("status", [400, 402, 403, 404, 422])
def test_a_request_eskiz_refuses_is_failed_for_good(status: int) -> None:
    """A number it will not send to, a text without an approved template, an empty balance."""
    eskiz = FakeEskiz(logins=[token_answer("t")], sends=[(status, b'{"status": "error", "message": "x"}')])
    with pytest.raises(SendRejected):
        send(provider(eskiz))
    assert eskiz.paths() == [LOGIN_PATH, SEND_PATH], "not sent a second time"


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504, 302])
def test_a_provider_that_is_busy_or_broken_is_tried_again_later(status: int) -> None:
    eskiz = FakeEskiz(logins=[token_answer("t")], sends=[(status, b"<html>busy</html>")])
    with pytest.raises(SendFailed):
        send(provider(eskiz))
    assert eskiz.paths() == [LOGIN_PATH, SEND_PATH], "the outbox retries, not this sender"


def test_a_refusal_and_a_failure_to_retry_are_different_outcomes() -> None:
    """The dispatcher tells them apart by type: neither may be a kind of the other."""
    assert not issubclass(SendRejected, SendFailed) and not issubclass(SendFailed, SendRejected)


def test_an_answer_that_says_error_under_a_good_status_is_a_refusal() -> None:
    eskiz = FakeEskiz(logins=[token_answer("t")], sends=[(200, b'{"status": "error", "message": "no"}')])
    with pytest.raises(SendRejected):
        send(provider(eskiz))


@pytest.mark.parametrize("body", [b"", b"not json", b"[]", b'{"id": 7}'])
def test_an_accepted_message_is_not_sent_twice_because_its_answer_was_unreadable(body: bytes) -> None:
    eskiz = FakeEskiz(logins=[token_answer("t")], sends=[(200, body)])
    assert send(provider(eskiz)) == ""


@pytest.mark.parametrize(
    "failure",
    [TimeoutError(), ConnectionResetError("reset"), OSError("unreachable"), http.client.RemoteDisconnected("gone")],
)
def test_a_network_failure_is_tried_again_later(failure: BaseException) -> None:
    eskiz = FakeEskiz(logins=[token_answer("t")], sends=[failure])
    with pytest.raises(SendFailed):
        send(provider(eskiz))


def test_a_call_that_does_not_answer_is_abandoned_at_its_deadline() -> None:
    async def never() -> tuple[int, bytes]:
        await asyncio.sleep(30)
        return ACCEPTED

    eskiz = FakeEskiz(logins=[token_answer("t")], sends=[never])
    started = asyncio.run(_timed(provider(eskiz, deadline=0.05)))
    assert started < 5, "the deadline, not the provider, ended the call"


async def _timed(sms: EskizSmsProvider) -> float:
    loop = asyncio.get_running_loop()
    began = loop.time()
    with pytest.raises(SendFailed) as raised:
        await sms.send(PHONE, TEXT)
    assert str(raised.value) == "timeout"
    return loop.time() - began


def test_the_sign_in_has_a_deadline_too() -> None:
    async def never() -> tuple[int, bytes]:
        await asyncio.sleep(30)
        return token_answer("t")

    eskiz = FakeEskiz(logins=[never])
    with pytest.raises(SendFailed) as raised:
        send(provider(eskiz, deadline=0.05))
    assert str(raised.value) == "timeout" and eskiz.paths() == [LOGIN_PATH]


# --- signing in -----------------------------------------------------------------------------------------


def test_a_refused_sign_in_sends_nothing_and_is_not_repeated_for_every_message() -> None:
    eskiz = FakeEskiz(logins=[(401, b'{"message": "Unauthorized"}'), token_answer("t")], sends=[ACCEPTED])
    clock = Clock()
    sms = provider(eskiz, clock)
    for _ in range(4):
        with pytest.raises(SendFailed):
            send(sms)
    assert eskiz.paths() == [LOGIN_PATH], "one wrong password, not four"
    clock.advance(SIGN_IN_PAUSE + timedelta(seconds=1))
    send(sms)
    assert eskiz.paths() == [LOGIN_PATH, LOGIN_PATH, SEND_PATH]


def test_a_sign_in_that_failed_for_a_passing_reason_is_tried_with_the_next_message() -> None:
    eskiz = FakeEskiz(logins=[(503, b""), OSError("down"), token_answer("t")], sends=[ACCEPTED])
    sms = provider(eskiz)
    for _ in range(2):
        with pytest.raises(SendFailed):
            send(sms)
    send(sms)
    assert eskiz.paths() == [LOGIN_PATH, LOGIN_PATH, LOGIN_PATH, SEND_PATH]


@pytest.mark.parametrize(
    "body", [b"{}", b'{"data": null}', b'{"data": {"token": ""}}', b'{"data": {"token": 5}}', b"x"]
)
def test_a_sign_in_without_a_token_sends_nothing(body: bytes) -> None:
    eskiz = FakeEskiz(logins=[(200, body)])
    with pytest.raises(SendFailed):
        send(provider(eskiz))
    assert eskiz.paths() == [LOGIN_PATH]


def test_an_account_that_is_not_complete_is_no_sender() -> None:
    for missing in ("email", "password", "sender"):
        parts = {"email": EMAIL, "password": PASSWORD, "sender": SENDER, missing: ""}
        with pytest.raises(ValueError, match="not fully configured") as raised:
            EskizSmsProvider(**parts)
        assert PASSWORD not in str(raised.value) and EMAIL not in str(raised.value)


# --- numbers --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("stored", "sent_as"),
    [
        ("+998901234567", "998901234567"),
        ("+998 90 123-45-67", "998901234567"),
        ("998901234567", "998901234567"),
        ("901234567", "998901234567"),
    ],
)
def test_an_uzbek_number_is_sent_as_digits_with_the_country_code(stored: str, sent_as: str) -> None:
    assert eskiz_number(stored) == sent_as


@pytest.mark.parametrize(
    "number",
    [
        "+79161234567",  # another country
        "+99890123456",  # one digit short
        "+9989012345678",  # one digit long
        "+998",
        "+99890123456a",
        "+٩٩٨٩٠١٢٣٤٥٦٧",  # digits, but not ASCII ones
        "12345",
        "",
        "not a number",
    ],
)
def test_any_other_number_is_refused_for_good_without_asking_eskiz(number: str) -> None:
    assert eskiz_number(number) is None
    eskiz = FakeEskiz()
    with pytest.raises(SendRejected):
        send(provider(eskiz), phone=number)
    assert eskiz.calls == [], "neither a sign-in nor a message"


# --- the text -------------------------------------------------------------------------------------------


def test_an_amount_travels_with_ordinary_spaces() -> None:
    """One no-break space would make a Latin SMS a Unicode one: 70 characters a part instead of 160."""
    plan = ReminderPlan(ReminderKind.OVERDUE, 1_250_000)
    for lang in LANGUAGES:
        text = reminder_text(lang, 1, plan, Channel.SMS, shop="Shop A", name="Ali")
        assert " " in text, "the chat form keeps its no-break spaces"
        sent = wire_text(text)
        assert " " not in sent and " " not in sent
        assert sent == text.replace(" ", " ") and money(sms_language(lang), 1_250_000).replace(" ", " ") in sent
    uzbek = wire_text(reminder_text("uz", 1, plan, Channel.SMS, shop="Shop A", name="Ali"))
    assert uzbek.isascii(), "the Uzbek text is one that fits the 160-character alphabet"


def test_the_text_sent_is_the_wire_form() -> None:
    eskiz = FakeEskiz(logins=[token_answer("t")], sends=[ACCEPTED])
    send(provider(eskiz), text="Shop A: Ali, bugun 70 000 so'm to'lash kuni. Rahmat.")
    assert eskiz.calls[-1][3]["message"] == "Shop A: Ali, bugun 70 000 so'm to'lash kuni. Rahmat."


# --- nothing secret or personal is logged or raised -----------------------------------------------------

TOKEN = "eyJ0eXAi-secret-token-7741"
PRIVATE = (EMAIL, PASSWORD, TOKEN, "Ali Valiyev", "70 000", "901234567", "998901234567", TEXT)


def leaks(record: logging.LogRecord) -> list[str]:
    """What of PRIVATE a log record carries: in the line as it is written, or anywhere on the record."""
    written = JsonFormatter().format(record)
    held = repr({key: value for key, value in vars(record).items() if key != "exc_info"})
    trace = logging.Formatter().formatException(record.exc_info) if record.exc_info else ""
    return [value for value in PRIVATE if value in written or value in held or value in trace]


def failing_sends() -> list[tuple[str, FakeEskiz, type[Exception]]]:
    echo = json.dumps({"status": "error", "message": f"{TEXT} to 998901234567 with {TOKEN}"}).encode()
    return [
        ("refused", FakeEskiz(logins=[token_answer(TOKEN)], sends=[(400, echo)]), SendRejected),
        ("busy", FakeEskiz(logins=[token_answer(TOKEN)], sends=[(503, echo)]), SendFailed),
        (
            "network",
            FakeEskiz(logins=[token_answer(TOKEN)], sends=[OSError(f"cannot reach with Bearer {TOKEN} for {PHONE}")]),
            SendFailed,
        ),
        ("unauthorized", FakeEskiz(logins=[token_answer(TOKEN)] * 2, sends=[(401, echo), (401, echo)]), SendFailed),
        ("sign-in refused", FakeEskiz(logins=[(401, f"{EMAIL} {PASSWORD}".encode())]), SendFailed),
        ("sign-in broken", FakeEskiz(logins=[OSError(f"{EMAIL}:{PASSWORD}")]), SendFailed),
    ]


@pytest.mark.parametrize(("case", "eskiz", "outcome"), failing_sends(), ids=[case for case, _, _ in failing_sends()])
def test_a_failed_send_logs_and_raises_nothing_secret_or_personal(
    case: str, eskiz: FakeEskiz, outcome: type[Exception], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    with pytest.raises(outcome) as raised:
        send(provider(eskiz))
    records = [record for record in caplog.records if record.name == "qarz.sms"]
    assert records, "a failure is logged"
    assert [leaks(record) for record in records] == [[]] * len(records)
    for record in records:
        line = json.loads(JsonFormatter().format(record))
        assert line["event"] in ("sms_rejected", "sms_retry") and line["channel"] == "sms"
        assert set(line) <= {"ts", "level", "logger", "event", "channel", "kind", "status"}
    # The exception: its own text, and nothing chained behind it that a traceback would print.
    error = raised.value
    assert not [value for value in PRIVATE if value in str(error) or value in repr(error)]
    assert error.__cause__ is None and error.__suppress_context__


def test_the_check_for_leaks_sees_one() -> None:
    """The counterpart: a log call that did carry the number or the text would fail the test above."""
    for value in (PHONE, TEXT, TOKEN, PASSWORD):
        careless = logging.LogRecord("qarz.sms", logging.WARNING, __file__, 1, "sms_retry to %s", (value,), None)
        assert leaks(careless)
    try:
        raise OSError(f"Bearer {TOKEN}")
    except OSError:
        import sys

        with_trace = logging.LogRecord("qarz.sms", logging.ERROR, __file__, 1, "sms_retry", None, sys.exc_info())
    assert leaks(with_trace) == [TOKEN]


def test_a_sent_message_is_logged_without_who_or_what(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    send(provider(FakeEskiz(logins=[token_answer(TOKEN)], sends=[ACCEPTED])))
    records = [record for record in caplog.records if record.name == "qarz.sms"]
    assert [record.getMessage() for record in records] == ["sms_sent"]
    assert leaks(records[0]) == []


def test_the_sender_does_not_print_its_secrets() -> None:
    sms = provider(FakeEskiz(logins=[token_answer(TOKEN)], sends=[ACCEPTED]))
    send(sms)
    assert not [value for value in PRIVATE if value in repr(sms) or value in str(sms)]

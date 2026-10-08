"""The SMS path end to end, from the outbox to a fake Eskiz, against the real database as the worker's role.

What decides whether an SMS leaves: the Eskiz account in the environment and the platform switch `sms_on`
at the moment of sending. With either missing nothing is sent, and the outbox records exactly what it
recorded when the sender was only a stand-in.
"""

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest

from qarz.application.dispatch import Dispatcher, DispatchResult
from qarz.application.ports import SendFailed
from qarz.domain import platform_settings
from qarz.infrastructure.db import Database
from qarz.infrastructure.eskiz_sms import LOGIN_PATH, SEND_PATH
from qarz.infrastructure.settings import Settings
from qarz.infrastructure.sms_sender import (
    ChannelSender,
    NoSmsProvider,
    SwitchedSmsProvider,
    build_sms_provider,
    eskiz_configured,
    platform_switch,
)

ACCOUNT = {"eskiz_email": "owner@qarz-test.example", "eskiz_password": "eskiz-PASSWORD-9f2c", "eskiz_sender": "4546"}
TEXT = "Shop A: Ali, 70 000 so'm qarz muddati o'tgan. Iltimos, to'lab qo'ying."
TOKEN = json.dumps({"data": {"token": "token-one"}}).encode()
ACCEPTED = b'{"id": "59bf10a2", "status": "waiting"}'


@dataclass
class FakeEskiz:
    """Accepts every sign-in and answers every message with `answer`; remembers the paths it was asked."""

    answer: tuple[int, bytes] = (200, ACCEPTED)
    paths: list[str] = field(default_factory=list)

    async def __call__(self, method: str, path: str, headers: dict[str, str], body: bytes | None) -> tuple[int, bytes]:
        self.paths.append(path)
        return (200, TOKEN) if path == LOGIN_PATH else self.answer

    @property
    def messages(self) -> int:
        return self.paths.count(SEND_PATH)


class NoTelegram:
    async def send(self, channel: str, recipient: str, payload: dict[str, Any]) -> None:
        raise AssertionError("no Telegram message is expected here")


def settings(**values: str) -> Settings:
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


async def on() -> bool:
    return True


# --- configured or not ----------------------------------------------------------------------------------


def test_the_account_comes_from_the_environment_and_is_empty_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    names = ("ESKIZ_EMAIL", "ESKIZ_PASSWORD", "ESKIZ_SENDER")
    for name in names:
        monkeypatch.delenv(f"QD_{name}", raising=False)
    empty = settings()
    assert [getattr(empty, name.lower()) for name in names] == ["", "", ""]
    assert not eskiz_configured(empty)
    for name in names:
        monkeypatch.setenv(f"QD_{name}", f"value-of-{name}")
    filled = settings()
    assert [getattr(filled, name.lower()) for name in names] == [f"value-of-{name}" for name in names]
    assert eskiz_configured(filled)
    # Printing the settings, as a start-up log or a traceback might, shows neither the e-mail nor the password.
    assert "value-of-ESKIZ_PASSWORD" not in repr(filled) and "value-of-ESKIZ_EMAIL" not in repr(filled)


@pytest.mark.parametrize("missing", sorted(ACCOUNT))
@pytest.mark.parametrize("blank", ["", "   "])
def test_with_any_part_of_the_account_missing_there_is_no_sender(missing: str, blank: str) -> None:
    partly = settings(**{**ACCOUNT, missing: blank})
    assert not eskiz_configured(partly)
    eskiz = FakeEskiz()
    provider = build_sms_provider(partly, switched_on=on, transport=eskiz)
    assert isinstance(provider, NoSmsProvider)
    with pytest.raises(SendFailed):
        asyncio.run(provider.send("+998901234567", TEXT))
    assert eskiz.paths == []


def test_a_partly_set_account_is_said_in_the_log_without_its_values(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    build_sms_provider(settings(**{**ACCOUNT, "eskiz_sender": ""}), switched_on=on, transport=FakeEskiz())
    build_sms_provider(settings(), switched_on=on, transport=FakeEskiz())
    events = [record for record in caplog.records if record.name == "qarz.sms"]
    assert [record.getMessage() for record in events] == ["sms_partly_configured"], "nothing set is not a warning"
    assert not [value for value in ACCOUNT.values() if value in repr(vars(events[0]))]


def test_a_configured_account_sends_only_while_the_switch_is_on() -> None:
    eskiz = FakeEskiz()
    state = {"on": False}

    async def switch() -> bool:
        return state["on"]

    provider = build_sms_provider(settings(**ACCOUNT), switched_on=switch, transport=eskiz)
    assert isinstance(provider, SwitchedSmsProvider)

    async def scenario() -> None:
        with pytest.raises(SendFailed):
            await provider.send("+998901234567", TEXT)
        assert eskiz.paths == [], "off: not even a sign-in"
        state["on"] = True
        await provider.send("+998901234567", TEXT)
        assert eskiz.messages == 1
        state["on"] = False  # read again for every message: off stops the very next one
        with pytest.raises(SendFailed):
            await provider.send("+998901234567", TEXT)
        assert eskiz.messages == 1

    asyncio.run(scenario())


def test_the_switch_is_off_unless_someone_turned_it_on() -> None:
    assert platform_settings.SETTINGS["sms_on"].default is False
    assert platform_settings.SETTINGS["sms_on"].needs_code, "turning it on asks the administrator's code again"
    assert platform_settings.SETTINGS["sms_monthly_quota"].default == 0


# --- against the database -------------------------------------------------------------------------------

db = pytest.mark.db
SetSwitch = Callable[[str | None], None]


@pytest.fixture
def sms_switch(owner: psycopg.Connection) -> Iterator[SetSwitch]:
    """Store a value for `sms_on` (JSON text), or remove the row with None. Removed again afterwards."""

    def store(value: str | None) -> None:
        owner.execute("DELETE FROM platform_setting WHERE key = 'sms_on'")
        if value is not None:
            owner.execute(
                "INSERT INTO platform_setting (key, value, updated_by) VALUES ('sms_on', %s::jsonb, 'test')", (value,)
            )

    owner.execute("UPDATE outbox_message SET status = 'sent' WHERE status = 'pending'")
    store(None)
    yield store
    store(None)


def run[T](worker_database_url: str, scenario: Callable[[Database], Awaitable[T]]) -> T:
    async def wrapper() -> T:
        database = Database(worker_database_url)
        try:
            return await scenario(database)
        finally:
            await database.dispose()

    return asyncio.run(wrapper())


async def queue_sms(database: Database, phone: str) -> None:
    async with database.platform() as session:
        await session.enqueue(channel="sms", recipient=phone, payload={"text": TEXT}, dedupe_key=uuid.uuid4().hex)


def rows(owner: psycopg.Connection, phone: str) -> list[tuple[str, int]]:
    return owner.execute(
        "SELECT status, attempts FROM outbox_message WHERE recipient = %s ORDER BY created_at, id", (phone,)
    ).fetchall()


def dispatch_one(
    worker_database_url: str, phone: str, configuration: Settings, eskiz: FakeEskiz
) -> tuple[DispatchResult, bool]:
    """Queue one SMS and run the dispatcher once, wired as the worker wires it. Also what the switch read."""

    async def scenario(database: Database) -> tuple[DispatchResult, bool]:
        switch = platform_switch(database)
        sms = build_sms_provider(configuration, switched_on=switch, transport=eskiz)
        await queue_sms(database, phone)
        now = datetime.now(UTC) + timedelta(seconds=2)
        result = await Dispatcher(database, ChannelSender(telegram=NoTelegram(), sms=sms), lambda: now).run_once()
        return result, await switch()

    return run(worker_database_url, scenario)


def read_switch(worker_database_url: str) -> bool:
    return run(worker_database_url, lambda database: platform_switch(database)())


@db
@pytest.mark.parametrize("stored", [None, "false", '"true"', "1", "null", '{"on": true}'])
def test_the_worker_reads_the_switch_as_off_unless_it_is_exactly_true(
    stored: str | None, worker_database_url: str, sms_switch: SetSwitch
) -> None:
    sms_switch(stored)
    assert read_switch(worker_database_url) is False
    sms_switch("true")
    assert read_switch(worker_database_url) is True


@db
def test_with_the_switch_off_a_queued_sms_is_not_sent_and_ends_as_it_did_with_the_stand_in(
    worker_database_url: str, owner: psycopg.Connection, sms_switch: SetSwitch
) -> None:
    eskiz = FakeEskiz()
    result, switched_on = dispatch_one(worker_database_url, "+998900000101", settings(**ACCOUNT), eskiz)
    assert switched_on is False and eskiz.paths == [], "nothing was asked of Eskiz"
    assert (result.sent, result.rescheduled, result.failed) == (0, 1, 0)

    # The stand-in, as the worker was wired before there was a provider.
    async def stand_in(database: Database) -> DispatchResult:
        await queue_sms(database, "+998900000102")
        now = datetime.now(UTC) + timedelta(seconds=2)
        sender = ChannelSender(telegram=NoTelegram(), sms=NoSmsProvider())
        return await Dispatcher(database, sender, lambda: now).run_once()

    before = run(worker_database_url, stand_in)
    assert result == before
    # Still waiting, with one counted attempt: retried with backoff and given up after a day.
    assert rows(owner, "+998900000101") == rows(owner, "+998900000102") == [("pending", 1)]


@db
def test_with_the_switch_on_and_no_account_a_queued_sms_is_not_sent(
    worker_database_url: str, owner: psycopg.Connection, sms_switch: SetSwitch
) -> None:
    sms_switch("true")
    eskiz = FakeEskiz()
    result, switched_on = dispatch_one(worker_database_url, "+998900000103", settings(), eskiz)
    assert switched_on is True and eskiz.paths == []
    assert (result.sent, result.rescheduled) == (0, 1)
    assert rows(owner, "+998900000103") == [("pending", 1)]


@db
def test_with_the_switch_on_and_an_account_a_queued_sms_is_sent_and_marked(
    worker_database_url: str, owner: psycopg.Connection, sms_switch: SetSwitch
) -> None:
    sms_switch("true")
    eskiz = FakeEskiz()
    result, _ = dispatch_one(worker_database_url, "+998900000104", settings(**ACCOUNT), eskiz)
    assert eskiz.paths == [LOGIN_PATH, SEND_PATH]
    assert (result.sent, result.rescheduled, result.failed) == (1, 0, 0)
    assert rows(owner, "+998900000104") == [("sent", 0)]


@db
def test_a_refused_sms_is_failed_at_once_and_one_to_try_later_is_kept(
    worker_database_url: str, owner: psycopg.Connection, sms_switch: SetSwitch
) -> None:
    sms_switch("true")
    refused, _ = dispatch_one(worker_database_url, "+998900000105", settings(**ACCOUNT), FakeEskiz((400, b"{}")))
    assert (refused.sent, refused.rescheduled, refused.failed) == (0, 0, 1)
    assert rows(owner, "+998900000105") == [("failed", 0)]
    later, _ = dispatch_one(worker_database_url, "+998900000106", settings(**ACCOUNT), FakeEskiz((503, b"")))
    assert (later.sent, later.rescheduled, later.failed) == (0, 1, 0)
    assert rows(owner, "+998900000106") == [("pending", 1)]


@db
def test_a_number_outside_uzbekistan_is_failed_at_once_without_asking_eskiz(
    worker_database_url: str, owner: psycopg.Connection, sms_switch: SetSwitch
) -> None:
    sms_switch("true")
    eskiz = FakeEskiz()
    result, _ = dispatch_one(worker_database_url, "+79160000107", settings(**ACCOUNT), eskiz)
    assert eskiz.paths == [] and result.failed == 1
    assert rows(owner, "+79160000107") == [("failed", 0)]

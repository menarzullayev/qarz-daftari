"""Telegram webhook: secret check, at-most-once processing, and replies queued in the same transaction."""

import itertools
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from qarz.application.chat import ChatService
from qarz.application.chat_texts import say
from qarz.application.shops import ShopService
from qarz.application.staff import StaffService
from qarz.application.telegram_updates import UpdateProcessor

from .conftest import WEBHOOK_SECRET

pytestmark = pytest.mark.db

SECRET = {"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET}
_ids = itertools.count(9_000_000_000)


def _update(chat_id: int = 4242, chat_type: str = "private", language: str = "uz") -> dict[str, Any]:
    return {
        "update_id": next(_ids),
        "message": {
            "message_id": 1,
            "chat": {"id": chat_id, "type": chat_type},
            "from": {"id": chat_id, "language_code": language},
            "text": "/start",
        },
    }


def _processor(database: Any) -> UpdateProcessor:
    return UpdateProcessor(database, ChatService(database, ShopService(database), StaffService(database)))


def _queued(owner: psycopg.Connection, update_id: int) -> list[tuple[str, str, str]]:
    return owner.execute(
        "SELECT channel, recipient, payload->>'text' FROM outbox_message WHERE dedupe_key = %s",
        (f"update:{update_id}:reply",),
    ).fetchall()


def test_a_valid_update_is_recorded_and_its_reply_queued(client: TestClient, owner: psycopg.Connection) -> None:
    update = _update(chat_id=1001)
    response = client.post("/tg/webhook", json=update, headers=SECRET)
    assert response.status_code == 200
    assert response.content == b""
    queued = _queued(owner, update["update_id"])
    assert len(queued) == 1
    assert queued[0][:2] == ("telegram", "1001")
    assert queued[0][2] == say("uz", "welcome_new")
    assert owner.execute(
        "SELECT count(*) FROM processed_update WHERE update_id = %s", (update["update_id"],)
    ).fetchone() == (1,)


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-Telegram-Bot-Api-Secret-Token": "wrong-secret-0123456789abcdef"},
        {"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET[:-1]},
        {"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET + "x"},
        {"X-Telegram-Bot-Api-Secret-Token": ""},
    ],
    ids=["missing", "wrong", "prefix", "longer", "empty"],
)
def test_a_wrong_secret_is_refused_and_nothing_is_processed(
    client: TestClient, owner: psycopg.Connection, headers: dict[str, str]
) -> None:
    update = _update()
    response = client.post("/tg/webhook", json=update, headers=headers)
    assert response.status_code == 403
    assert owner.execute(
        "SELECT count(*) FROM processed_update WHERE update_id = %s", (update["update_id"],)
    ).fetchone() == (0,)
    assert _queued(owner, update["update_id"]) == []


def test_a_redelivered_update_is_processed_once(client: TestClient, owner: psycopg.Connection) -> None:
    update = _update()
    for _ in range(3):
        assert client.post("/tg/webhook", json=update, headers=SECRET).status_code == 200
    assert len(_queued(owner, update["update_id"])) == 1


def test_messages_from_groups_are_ignored(client: TestClient, owner: psycopg.Connection) -> None:
    update = _update(chat_id=-100123, chat_type="supergroup")
    assert client.post("/tg/webhook", json=update, headers=SECRET).status_code == 200
    assert _queued(owner, update["update_id"]) == []
    # the update is still recorded, so Telegram does not redeliver it forever
    assert owner.execute(
        "SELECT count(*) FROM processed_update WHERE update_id = %s", (update["update_id"],)
    ).fetchone() == (1,)


def test_reply_language_follows_the_stored_choice_then_telegram(client: TestClient, owner: psycopg.Connection) -> None:
    russian_by_telegram = _update(chat_id=2001, language="ru-RU")
    client.post("/tg/webhook", json=russian_by_telegram, headers=SECRET)
    assert _queued(owner, russian_by_telegram["update_id"])[0][2] == say("ru", "welcome_new")

    # a stored choice of Uzbek wins over Telegram's Russian interface language
    owner.execute("INSERT INTO app_user (id, tg_id, lang) VALUES (gen_random_uuid(), 2002, 'uz')")
    stored_uzbek = _update(chat_id=2002, language="ru")
    client.post("/tg/webhook", json=stored_uzbek, headers=SECRET)
    assert _queued(owner, stored_uzbek["update_id"])[0][2] == say("uz", "welcome_new")


@pytest.mark.parametrize(
    "body", ["not json", "[]", '{"no_update_id": 1}', '{"update_id": "12"}', '{"update_id": true}']
)
def test_malformed_bodies_are_rejected(client: TestClient, body: str) -> None:
    response = client.post("/tg/webhook", content=body, headers={**SECRET, "Content-Type": "application/json"})
    assert response.status_code == 400


def test_a_failing_handler_returns_500_and_leaves_the_update_unrecorded(
    client: TestClient, owner: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """So that Telegram redelivers and the next attempt is processed, not skipped as a duplicate."""
    update = _update()
    real = UpdateProcessor.handle
    calls = {"n": 0}

    async def flaky(self: UpdateProcessor, incoming: dict[str, Any]) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            async with self._storage.platform() as session:
                await session.claim_update(incoming["update_id"])
                raise RuntimeError("database went away mid-update")
        return await real(self, incoming)

    monkeypatch.setattr(UpdateProcessor, "handle", flaky)

    assert client.post("/tg/webhook", json=update, headers=SECRET).status_code == 500
    assert owner.execute(
        "SELECT count(*) FROM processed_update WHERE update_id = %s", (update["update_id"],)
    ).fetchone() == (0,)
    assert _queued(owner, update["update_id"]) == []

    assert client.post("/tg/webhook", json=update, headers=SECRET).status_code == 200
    assert len(_queued(owner, update["update_id"])) == 1


def test_the_webhook_is_not_a_staff_route_and_needs_no_sign_in(client: TestClient) -> None:
    # reachable with the secret alone, and not listed under /api/
    assert client.post("/tg/webhook", json=_update(), headers=SECRET).status_code == 200
    assert client.get("/tg/webhook").status_code == 404


def test_the_processor_itself_reports_a_duplicate(app_database_url: str) -> None:
    """Independent of the outbox's own dedupe key: the second delivery must not run the handler at all."""
    import asyncio

    from qarz.infrastructure.db import Database

    update = _update()

    async def scenario() -> tuple[bool, bool, bool]:
        database = Database(app_database_url)
        try:
            processor = _processor(database)
            return (await processor.process(update), await processor.process(update), await processor.process(update))
        finally:
            await database.dispose()

    assert asyncio.run(scenario()) == (True, False, False)


def test_an_update_without_an_integer_id_is_refused_by_the_processor(app_database_url: str) -> None:
    import asyncio

    from qarz.infrastructure.db import Database

    async def scenario(update: dict[str, Any]) -> None:
        database = Database(app_database_url)
        try:
            await _processor(database).process(update)
        finally:
            await database.dispose()

    for bad in ({}, {"update_id": "7"}, {"update_id": True}):
        with pytest.raises(ValueError, match="update_id"):
            asyncio.run(scenario(bad))

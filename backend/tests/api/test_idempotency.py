"""Idempotent writes (ADR-006): a repeat returns the stored result and has no second effect."""

import threading
import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

from .conftest import World, as_user

pytestmark = pytest.mark.db


def _patch(client: TestClient, world: World, body: dict[str, object], key: str | None, user: uuid.UUID | None = None):  # type: ignore[no-untyped-def]
    headers = as_user(user or world.owner_a)
    if key is not None:
        headers["Idempotency-Key"] = key
    return client.patch(f"/api/v1/shops/{world.shop_a}", json=body, headers=headers)


def _activity_count(owner: psycopg.Connection, shop: uuid.UUID) -> int:
    row = owner.execute("SELECT count(*) FROM activity WHERE shop_id = %s", (shop,)).fetchone()
    assert row is not None
    return int(row[0])


def test_a_repeated_write_has_one_effect_and_the_same_answer(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    first = _patch(client, world, {"name": "Baraka"}, "key-00000001")
    second = _patch(client, world, {"name": "Baraka"}, "key-00000001")
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert _activity_count(owner, world.shop_a) == 1


def test_the_stored_answer_is_returned_even_after_the_data_moved_on(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    first = _patch(client, world, {"name": "First"}, "key-00000002")
    _patch(client, world, {"name": "Second"}, "key-00000003")
    replay = _patch(client, world, {"name": "First"}, "key-00000002")
    assert replay.json() == first.json()
    assert replay.json()["name"] == "First"
    # the replay did not write "First" again
    assert owner.execute("SELECT name FROM shop WHERE id = %s", (world.shop_a,)).fetchone() == ("Second",)
    assert _activity_count(owner, world.shop_a) == 2


def test_a_key_reused_for_a_different_request_is_refused(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    _patch(client, world, {"name": "Baraka"}, "key-00000004")
    reused = _patch(client, world, {"name": "Something else"}, "key-00000004")
    assert reused.status_code == 409
    assert reused.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert owner.execute("SELECT name FROM shop WHERE id = %s", (world.shop_a,)).fetchone() == ("Baraka",)
    assert _activity_count(owner, world.shop_a) == 1


@pytest.mark.parametrize("key", [None, "", "short", "has space 12345", "x" * 129, "ключ-кириллица-1"])
def test_a_write_without_a_valid_key_is_rejected(
    client: TestClient, world: World, owner: psycopg.Connection, key: str | None
) -> None:
    if key is not None and not key.isascii():
        # HTTP header values must be ASCII; the client library refuses to send this one at all.
        with pytest.raises(UnicodeEncodeError):
            _patch(client, world, {"name": "Baraka"}, key)
        return
    response = _patch(client, world, {"name": "Baraka"}, key)
    assert response.status_code == 422, response.text
    assert "Idempotency-Key" in response.json()["error"]["fields"]
    assert owner.execute("SELECT name FROM shop WHERE id = %s", (world.shop_a,)).fetchone() == ("Shop A",)


def test_a_failed_write_stores_nothing_so_the_key_can_be_retried(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    rejected = _patch(client, world, {"lang": "en"}, "key-00000005")
    assert rejected.status_code == 422
    assert owner.execute("SELECT count(*) FROM request_key WHERE key = 'key-00000005'").fetchone() == (0,)
    accepted = _patch(client, world, {"lang": "ru"}, "key-00000005")
    assert accepted.status_code == 200


def test_keys_are_scoped_to_the_shop(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    in_a = _patch(client, world, {"name": "A renamed"}, "key-00000006")
    in_b = client.patch(
        f"/api/v1/shops/{world.shop_b}",
        json={"name": "B renamed"},
        headers={**as_user(world.owner_b), "Idempotency-Key": "key-00000006"},
    )
    assert in_a.status_code == in_b.status_code == 200
    assert in_b.json()["name"] == "B renamed"
    rows = owner.execute("SELECT shop_id FROM request_key WHERE key = 'key-00000006'").fetchall()
    assert {row[0] for row in rows} == {world.shop_a, world.shop_b}


def test_an_outsider_cannot_probe_or_replay_a_key(client: TestClient, world: World) -> None:
    _patch(client, world, {"name": "Baraka"}, "key-00000007")
    replay = _patch(client, world, {"name": "Baraka"}, "key-00000007", user=world.owner_b)
    assert replay.status_code == 404
    # a member with too low a role does not get the stored answer either
    seller = _patch(client, world, {"name": "Baraka"}, "key-00000007", user=world.seller_a)
    assert seller.status_code == 403


def test_concurrent_requests_with_one_key_have_one_effect(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    results: list[int] = []

    def call() -> None:
        results.append(_patch(client, world, {"name": "Raced"}, "key-00000008").status_code)

    threads = [threading.Thread(target=call) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results == [200] * 8
    assert _activity_count(owner, world.shop_a) == 1

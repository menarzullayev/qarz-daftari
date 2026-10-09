"""Behavior of the shop operations beyond who may call them."""

import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

from .conftest import World, as_user

pytestmark = pytest.mark.db


def as_owner_writing(world: World) -> dict[str, str]:
    return {**as_user(world.owner_a), "Idempotency-Key": f"test-{uuid.uuid4().hex}"}


def test_owner_reads_the_shop(client: TestClient, world: World) -> None:
    response = client.get(f"/api/v1/shops/{world.shop_a}", headers=as_user(world.owner_a))
    assert response.status_code == 200
    assert response.json() == {"id": str(world.shop_a), "name": "Shop A", "lang": "uz", "default_promise_days": 30}


def test_owner_updates_settings_and_the_change_is_logged(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    response = client.patch(
        f"/api/v1/shops/{world.shop_a}",
        json={"name": "  Baraka do'koni  ", "lang": "ru", "default_promise_days": 14},
        headers=as_owner_writing(world),
    )
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Baraka do'koni"
    stored = owner.execute(
        "SELECT name, lang, default_promise_days FROM shop WHERE id = %s", (world.shop_a,)
    ).fetchone()
    assert stored == ("Baraka do'koni", "ru", 14)

    # REQ-035: the change records which staff member made it
    rows = owner.execute(
        "SELECT a.action, a.actor_kind, m.user_id, a.subject_type, a.subject_id "
        "FROM activity a JOIN membership m ON m.id = a.actor_id WHERE a.shop_id = %s",
        (world.shop_a,),
    ).fetchall()
    assert rows == [("shop.settings_changed", "staff", world.owner_a, "shop", world.shop_a)]

    # the other shop is untouched
    other = owner.execute("SELECT name FROM shop WHERE id = %s", (world.shop_b,)).fetchone()
    assert other == ("Shop B",)


def test_a_partial_update_keeps_the_other_settings(client: TestClient, world: World) -> None:
    response = client.patch(
        f"/api/v1/shops/{world.shop_a}", json={"default_promise_days": 7}, headers=as_owner_writing(world)
    )
    assert response.status_code == 200
    assert response.json() == {"id": str(world.shop_a), "name": "Shop A", "lang": "uz", "default_promise_days": 7}


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"name": ""}, "name"),
        ({"name": "   "}, "name"),
        ({"name": "x" * 81}, "name"),
        ({"lang": "de"}, "lang"),
        ({"lang": "kk"}, "lang"),
        ({"lang": "uz-cyrl"}, "lang"),
        ({"lang": "UZ"}, "lang"),
        ({"default_promise_days": 0}, "default_promise_days"),
        ({"default_promise_days": 366}, "default_promise_days"),
        ({"default_promise_days": "14"}, "default_promise_days"),
        ({"default_promise_days": 14.5}, "default_promise_days"),
        ({}, "_"),
        ({"reminder_hour": 3}, "reminder_hour"),
        ({"status": "erased"}, "status"),
        ({"id": "00000000-0000-0000-0000-000000000000"}, "id"),
    ],
)
def test_invalid_updates_are_rejected_and_change_nothing(
    client: TestClient, world: World, owner: psycopg.Connection, body: dict[str, object], field: str
) -> None:
    response = client.patch(f"/api/v1/shops/{world.shop_a}", json=body, headers=as_owner_writing(world))
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "VALIDATION"
    assert field in error["fields"]
    stored = owner.execute(
        "SELECT name, lang, default_promise_days, status FROM shop WHERE id = %s", (world.shop_a,)
    ).fetchone()
    assert stored == ("Shop A", "uz", 30, "active")
    assert owner.execute("SELECT count(*) FROM activity WHERE shop_id = %s", (world.shop_a,)).fetchone() == (0,)


def test_a_non_member_learns_nothing_from_an_invalid_request(client: TestClient, world: World) -> None:
    """Authorization comes before validation: an outsider gets NOT_FOUND, not a validation report."""
    response = client.patch(f"/api/v1/shops/{world.shop_a}", json={"lang": "de"}, headers=as_user(world.owner_b))
    assert response.status_code == 404
    assert response.json()["error"]["fields"] == {}


def test_error_messages_follow_the_callers_language(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    uz = client.get(f"/api/v1/shops/{world.shop_a}", headers=as_user(world.seller_a)).json()["error"]["message"]
    owner.execute("UPDATE app_user SET lang = 'ru' WHERE id = %s", (world.seller_a,))
    ru = client.get(f"/api/v1/shops/{world.shop_a}", headers=as_user(world.seller_a)).json()["error"]["message"]
    assert uz == "Bu amal uchun sizning rolingiz yetarli emas."
    assert ru == "Вашей роли недостаточно для этого действия."


def test_unknown_paths_and_methods_use_the_same_error_shape(client: TestClient, world: World) -> None:
    for response in (
        client.get("/api/v1/nothing-here", headers=as_user(world.owner_a)),
        client.delete(f"/api/v1/shops/{world.shop_a}", headers=as_user(world.owner_a)),
    ):
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"


def test_an_erased_shop_is_not_found_even_for_its_owner(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    owner.execute("UPDATE shop SET status = 'erased' WHERE id = %s", (world.shop_a,))
    response = client.get(f"/api/v1/shops/{world.shop_a}", headers=as_user(world.owner_a))
    assert response.status_code == 404

"""A customer's secret read-only link (the expansion of 2026-10-09, decision 12; module B).

Behind the platform switch `customer_links_on`. Each rule here has the case that must work and the case
that must be refused; who may call what, by role and as an outsider, is in the authorization suite.
"""

import hashlib
import uuid
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from qarz.domain.customer_share import SHARE_LIFETIME, TOKEN_LENGTH

from .conftest import AdminEnv, World, as_user
from .test_customer_account import attach_waiter, link_of
from .test_customers_ledger import key, record, shop

pytestmark = pytest.mark.db

PUBLIC = "/api/v1/customer-share"
HEADER = "X-Share-Token"
NOT_FOUND = {"error": {"code": "NOT_FOUND", "message": "Topilmadi.", "fields": {}}}


def switch(owner: psycopg.Connection, value: str = "true") -> None:
    """Store the switch as an administrator's change would (`value` is JSON). The row is signed with a
    user identifier, so the `admin_env` fixture behind `client` removes it after the test."""
    owner.execute(
        "INSERT INTO platform_setting (key, value, updated_by) VALUES ('customer_links_on', %s::jsonb, %s) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_by = EXCLUDED.updated_by",
        (value, str(uuid.uuid4())),
    )


@pytest.fixture
def on(client: TestClient, owner: psycopg.Connection) -> Iterator[None]:
    switch(owner)
    yield


def share_path(world: World, customer: Any) -> str:
    return f"{shop(world)}/customers/{customer}/share"


def make(client: TestClient, world: World, customer: Any, user: uuid.UUID | None = None) -> str:
    response = client.post(share_path(world, customer), headers={**as_user(user or world.manager_a), **key()})
    assert response.status_code == 201, response.text
    return str(response.json()["token"])


def view(client: TestClient, token: str | None) -> Any:
    return client.get(PUBLIC, headers={} if token is None else {HEADER: token})


def rows(owner: psycopg.Connection, customer: Any) -> list[tuple[Any, ...]]:
    return owner.execute(
        "SELECT token_hash, revoked_at IS NULL, expires_at - created_at, last_opened_at IS NOT NULL "
        "FROM customer_share WHERE customer_id = %s ORDER BY created_at, id",
        (customer,),
    ).fetchall()


def actions(owner: psycopg.Connection, world: World) -> list[tuple[str, str, Any]]:
    return [
        (str(row[0]), str(row[1]), row[2])
        for row in owner.execute(
            "SELECT action, actor_kind, subject_id FROM activity WHERE shop_id = %s "
            "AND (action LIKE 'customer.share%%' OR action LIKE 'shop.share%%') ORDER BY at, id",
            (world.shop_a,),
        ).fetchall()
    ]


def shares_of_the_world(owner: psycopg.Connection, world: World) -> int:
    row = owner.execute(
        "SELECT count(*) FROM customer_share WHERE shop_id IN (%s, %s)", (world.shop_a, world.shop_b)
    ).fetchone()
    assert row is not None
    return int(row[0])


def is_not_found(response: Any) -> bool:
    return bool(response.status_code == 404 and response.json() == NOT_FOUND)


# --- the switch ------------------------------------------------------------------------------------------


def _every_route(world: World) -> list[tuple[str, str, Any]]:
    return [
        ("GET", share_path(world, world.customer_a), None),
        ("POST", share_path(world, world.customer_a), None),
        ("DELETE", share_path(world, world.customer_a), None),
        ("GET", f"{shop(world)}/share-contact", None),
        ("PUT", f"{shop(world)}/share-contact", {"phone": "+998901234567"}),
        ("GET", PUBLIC, None),
    ]


@pytest.mark.parametrize("stored", [None, "false", '"true"', "1", "null"])
def test_with_the_switch_off_no_route_of_the_module_exists_for_anyone(
    client: TestClient, world: World, owner: psycopg.Connection, stored: str | None
) -> None:
    """Off is the default (no row), and only the JSON value `true` is on. The owner, a stranger and
    someone who is not signed in all get the answer of a route that was never there, and nothing is
    written."""
    if stored is not None:
        switch(owner, stored)
    for method, path, body in _every_route(world):
        for caller in (as_user(world.owner_a), as_user(world.stranger), {}):
            response = client.request(method, path, json=body, headers={**caller, **key(), HEADER: "A" * TOKEN_LENGTH})
            unknown = client.request(method, "/api/v1/no-such-route", json=body, headers={**caller, **key()})
            assert response.status_code == unknown.status_code == 404, (method, path, response.text)
            assert response.json() == unknown.json() == NOT_FOUND
    assert shares_of_the_world(owner, world) == 0
    assert actions(owner, world) == []
    assert owner.execute("SELECT share_phone FROM shop WHERE id = %s", (world.shop_a,)).fetchone() == (None,)


def test_the_check_above_would_notice_a_route_that_answered(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    """The counterpart: with the switch on the same requests are answered as themselves."""
    statuses = [
        client.request(method, path, json=body, headers={**as_user(world.owner_a), **key()}).status_code
        for method, path, body in _every_route(world)
    ]
    # No link yet, so there is none to end; and the read behind a link was given no secret.
    assert statuses == [200, 201, 200, 200, 200, 404]


def test_turning_the_switch_off_closes_the_links_already_handed_out(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    token = make(client, world, world.customer_a)
    assert view(client, token).status_code == 200
    switch(owner, "false")
    assert is_not_found(view(client, token))
    switch(owner)
    assert view(client, token).status_code == 200, "nothing was destroyed: the switch is the only thing that changed"


# --- making a link -----------------------------------------------------------------------------------------


def test_a_link_is_shown_once_and_only_its_hash_is_kept(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    headers = {**as_user(world.manager_a), **key()}
    made = client.post(share_path(world, world.customer_a), headers=headers)
    assert made.status_code == 201, made.text
    body = made.json()
    token = body["token"]
    # 32 random bytes: 256 bits, well over the 128 a secret needs.
    assert len(token) == TOKEN_LENGTH == 43 and set(body) == {"token", "created_at", "expires_at"}
    assert rows(owner, world.customer_a) == [(hashlib.sha256(token.encode()).digest(), True, SHARE_LIFETIME, False)]
    assert timedelta(days=90) == SHARE_LIFETIME

    # The same request again is answered from the record, which no longer holds the token.
    again = client.post(share_path(world, world.customer_a), headers=headers)
    assert again.status_code == 201 and again.json() == {**body, "token": None}
    assert len(rows(owner, world.customer_a)) == 1
    # Nothing the database keeps contains the token: not the link's row, not the stored answer, not the log.
    for table in ("customer_share", "request_key", "activity"):
        found = owner.execute(f"SELECT count(*) FROM {table} t WHERE t::text LIKE %s", (f"%{token}%",)).fetchone()
        assert found == (0,), table
    assert actions(owner, world) == [("customer.share_created", "staff", world.customer_a)]

    state = client.get(share_path(world, world.customer_a), headers=as_user(world.owner_a))
    assert state.status_code == 200
    assert state.json() == {
        "exists": True,
        "expired": False,
        "created_at": body["created_at"],
        "expires_at": body["expires_at"],
        "last_opened_at": None,
    }
    assert "token" not in state.text


def test_two_links_never_share_a_token(client: TestClient, world: World, on: None) -> None:
    tokens = {make(client, world, world.customer_a) for _ in range(5)}
    assert len(tokens) == 5


def test_a_new_link_ends_the_one_before_it_at_once(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    first = make(client, world, world.customer_a)
    assert view(client, first).status_code == 200
    second = make(client, world, world.customer_a, world.owner_a)
    assert first != second
    assert is_not_found(view(client, first))
    assert view(client, second).status_code == 200
    # One alive, the old one kept as ended.
    assert [alive for _, alive, _, _ in rows(owner, world.customer_a)] == [False, True]
    assert [action for action, _, _ in actions(owner, world)][:3] == [
        "customer.share_created",
        "customer.share_opened",
        "customer.share_replaced",
    ]


def test_a_link_is_not_made_for_an_archived_customer_or_one_of_another_shop(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    archived = client.post(share_path(world, world.archived_customer_a), headers={**as_user(world.owner_a), **key()})
    assert (archived.status_code, archived.json()["error"]["code"]) == (409, "CUSTOMER_ARCHIVED")
    other = uuid.uuid4()
    owner.execute(
        "INSERT INTO customer (id, shop_id, display_name, name_norm) VALUES (%s, %s, 'Begona', 'begona')",
        (other, world.shop_b),
    )
    foreign = client.post(share_path(world, other), headers={**as_user(world.owner_a), **key()})
    assert is_not_found(foreign)
    assert shares_of_the_world(owner, world) == 0


def test_a_seller_can_neither_make_nor_end_nor_look_up_a_link(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    make(client, world, world.customer_a)
    before = rows(owner, world.customer_a)
    for method in ("GET", "POST", "DELETE"):
        refused = client.request(
            method, share_path(world, world.customer_a), headers={**as_user(world.seller_a), **key()}
        )
        assert (refused.status_code, refused.json()["error"]["code"]) == (403, "FORBIDDEN_ROLE"), method
    assert rows(owner, world.customer_a) == before


# --- what the page says ------------------------------------------------------------------------------------


def test_the_page_shows_the_account_and_nothing_that_is_the_shops_own(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    owner.execute(
        "UPDATE customer SET display_name = 'Ali Valiyev, 5-uy qarzdor', phone = '+998901112233' WHERE id = %s",
        (world.customer_a,),
    )
    paid = record(client, world, world.customer_a, "payment", 20_000, note="Yashirin izoh: kech to'laydi")
    assert paid.status_code == 201, paid.text
    token = make(client, world, world.customer_a)

    response = view(client, token)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {
        "shop_name",
        "shop_phone",
        "first_name",
        "lang",
        "balance",
        "overdue",
        "expires_at",
        "entries",
        "entries_total",
    }
    assert (body["shop_name"], body["shop_phone"], body["first_name"]) == ("Shop A", None, "Ali")
    assert (body["balance"], body["entries_total"]) == (30_000, 2)
    assert body["overdue"] == {"amount": 0, "due_today": 0}
    assert [(entry["kind"], entry["amount"], entry["reversed"]) for entry in body["entries"]] == [
        ("payment", 20_000, False),
        ("credit", 50_000, False),
    ]
    for entry in body["entries"]:
        assert set(entry) == {"kind", "amount", "created_at", "promised_date", "reversed", "lines"}
    assert body["entries"][0]["promised_date"] is None and body["entries"][1]["promised_date"] is not None

    # Not the rest of the name, the note, the phone, any identifier, or anyone else.
    text = response.text
    for hidden in (
        "Valiyev",
        "qarzdor",
        "Yashirin",
        "998901112233",
        "note",
        "author",
        str(world.customer_a),
        str(world.shop_a),
        str(world.entry_a),
        str(world.seller_a_membership),
        'Vali"',
        "Sobir",
    ):
        assert hidden not in text, hidden
    # Never kept by a browser or a proxy, and never indexed.
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-robots-tag"] == "noindex, nofollow, noarchive"
    assert "set-cookie" not in response.headers


def test_goods_are_shown_without_their_catalog_identifiers(client: TestClient, world: World, on: None) -> None:
    sold = record(
        client,
        world,
        world.settled_customer_a,
        "credit",
        8_000,
        lines=[{"catalog_item_id": str(world.catalog_item_a), "qty": "2", "unit_price": 4_000}],
    )
    assert sold.status_code == 201, sold.text
    body = view(client, make(client, world, world.settled_customer_a)).json()
    assert body["entries"][0]["lines"] == [
        {"name": "Non", "qty": "2", "unit": "dona", "unit_price": 4_000, "line_total": 8_000}
    ]
    assert str(world.catalog_item_a) not in str(body)


def test_the_page_is_in_the_customers_language_or_else_the_shops(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    token = make(client, world, world.customer_a)
    assert view(client, token).json()["lang"] == "uz"
    owner.execute("UPDATE shop SET lang = 'ru' WHERE id = %s", (world.shop_a,))
    assert view(client, token).json()["lang"] == "ru"
    owner.execute("UPDATE customer SET lang = 'uz' WHERE id = %s", (world.customer_a,))
    assert view(client, token).json()["lang"] == "uz"


# --- every refusal is the same --------------------------------------------------------------------------------


def test_unknown_ended_and_expired_links_are_answered_in_exactly_the_same_way(
    client: TestClient, world: World, admin_env: AdminEnv, on: None
) -> None:
    ended = make(client, world, world.customer_a)
    gone = client.delete(share_path(world, world.customer_a), headers={**as_user(world.manager_a), **key()})
    assert gone.status_code == 200 and gone.json() == {"revoked": True}
    expired = make(client, world, world.settled_customer_a)
    admin_env.clock.offset += SHARE_LIFETIME + timedelta(seconds=1)

    answers = [
        view(client, token)
        for token in (ended, expired, "A" * TOKEN_LENGTH, "short", "", None, ended + "x", ended[:-1] + "=")
    ]
    assert all(is_not_found(answer) for answer in answers)
    assert len({answer.content for answer in answers}) == 1
    assert len({tuple(sorted(set(answer.headers) - {"x-request-id"})) for answer in answers}) == 1


def test_a_link_works_until_its_last_second_and_not_after(
    client: TestClient, world: World, admin_env: AdminEnv, on: None
) -> None:
    admin_env.clock.freeze()
    token = make(client, world, world.customer_a)
    admin_env.clock.offset += SHARE_LIFETIME - timedelta(seconds=1)
    assert view(client, token).status_code == 200
    admin_env.clock.offset += timedelta(seconds=1)
    assert is_not_found(view(client, token))
    # Staff are told it ran out, and may make another.
    state = client.get(share_path(world, world.customer_a), headers=as_user(world.manager_a)).json()
    assert (state["exists"], state["expired"]) == (True, True)
    assert view(client, make(client, world, world.customer_a)).status_code == 200


def test_the_secret_is_never_read_from_the_address(client: TestClient, world: World, on: None) -> None:
    """An address is logged by the proxy; the secret travels in a header only."""
    token = make(client, world, world.customer_a)
    for path in (f"{PUBLIC}/{token}", f"{PUBLIC}?token={token}", f"{PUBLIC}?{HEADER}={token}"):
        assert is_not_found(client.get(path)), path
    for method in ("POST", "PUT", "DELETE", "PATCH"):
        assert is_not_found(client.request(method, PUBLIC, headers={HEADER: token})), method


# --- ending a link -----------------------------------------------------------------------------------------


def test_ending_a_link_closes_it_and_is_recorded(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    token = make(client, world, world.customer_a)
    headers = {**as_user(world.owner_a), **key()}
    ended = client.delete(share_path(world, world.customer_a), headers=headers)
    assert ended.status_code == 200, ended.text
    assert is_not_found(view(client, token))
    assert client.delete(share_path(world, world.customer_a), headers=headers).json() == {"revoked": True}
    state = client.get(share_path(world, world.customer_a), headers=as_user(world.owner_a)).json()
    assert state == {"exists": False, "expired": False, "created_at": None, "expires_at": None, "last_opened_at": None}
    assert actions(owner, world) == [
        ("customer.share_created", "staff", world.customer_a),
        ("customer.share_revoked", "staff", world.customer_a),
    ]
    # There is nothing left to end.
    nothing = client.delete(share_path(world, world.customer_a), headers={**as_user(world.owner_a), **key()})
    assert is_not_found(nothing)
    assert len(actions(owner, world)) == 2


def test_ending_one_customers_link_leaves_anothers_alone(client: TestClient, world: World, on: None) -> None:
    kept = make(client, world, world.settled_customer_a)
    make(client, world, world.customer_a)
    client.delete(share_path(world, world.customer_a), headers={**as_user(world.manager_a), **key()})
    assert view(client, kept).json()["first_name"] == "Vali"


# --- openings are recorded, a day at a time ---------------------------------------------------------------------


def test_an_opening_is_put_in_the_activity_log_once_a_day(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv, on: None
) -> None:
    token = make(client, world, world.customer_a)
    opened = [action for action in actions(owner, world) if action[0] == "customer.share_opened"]
    assert opened == []
    for _ in range(4):
        assert view(client, token).status_code == 200
    opened = [action for action in actions(owner, world) if action[0] == "customer.share_opened"]
    # The customer is the actor, as for everything a customer does about their own record.
    assert opened == [("customer.share_opened", "customer", world.customer_a)]
    assert rows(owner, world.customer_a)[0][3] is True
    state = client.get(share_path(world, world.customer_a), headers=as_user(world.manager_a)).json()
    assert state["last_opened_at"] is not None

    admin_env.clock.offset += timedelta(days=1)
    view(client, token)
    view(client, token)
    assert len([action for action in actions(owner, world) if action[0] == "customer.share_opened"]) == 2


def test_a_refused_opening_writes_nothing(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    make(client, world, world.customer_a)
    before = (actions(owner, world), rows(owner, world.customer_a))
    for token in ("A" * TOKEN_LENGTH, "", None):
        view(client, token)
    assert (actions(owner, world), rows(owner, world.customer_a)) == before


# --- the customer's data is removed, the shop is deleted ----------------------------------------------------------


def test_removing_a_customers_data_ends_their_link(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    customer = world.settled_customer_a
    token = make(client, world, customer)
    assert view(client, token).status_code == 200
    attach_waiter(client, world, customer)
    done = client.post(f"/api/v1/me/accounts/{link_of(owner, customer)}/removal", headers=as_user(world.waiter))
    assert done.status_code == 200 and done.json()["removed"] is True, done.text

    assert is_not_found(view(client, token))
    assert [alive for _, alive, _, _ in rows(owner, customer)] == [False]
    # And none can be made or looked up for the anonymous record that is left.
    for method in ("GET", "POST", "DELETE"):
        refused = client.request(method, share_path(world, customer), headers={**as_user(world.owner_a), **key()})
        assert is_not_found(refused), method


def test_a_link_does_not_outlive_the_customers_data_even_if_it_was_not_ended(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    """The lookup refuses an anonymous record by itself: the page never shows one."""
    token = make(client, world, world.customer_a)
    owner.execute("UPDATE customer SET status = 'anonymized' WHERE id = %s", (world.customer_a,))
    assert is_not_found(view(client, token))
    owner.execute("UPDATE customer SET status = 'active' WHERE id = %s", (world.customer_a,))
    assert view(client, token).status_code == 200


def test_a_shop_that_is_being_deleted_shows_nothing_and_an_erased_one_keeps_nothing(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    token = make(client, world, world.customer_a)
    client.put(f"{shop(world)}/share-contact", json={"phone": "901234567"}, headers={**as_user(world.owner_a), **key()})
    asked = client.post(
        f"{shop(world)}/deletion", json={"confirm_name": "Shop A"}, headers={**as_user(world.owner_a), **key()}
    )
    assert asked.status_code == 201, asked.text
    assert is_not_found(view(client, token))

    # The owner changes their mind inside the waiting period: the link was not destroyed.
    cancelled = client.delete(f"{shop(world)}/deletion", headers={**as_user(world.owner_a), **key()})
    assert cancelled.status_code == 200, cancelled.text
    assert view(client, token).status_code == 200

    owner.execute(
        "UPDATE shop SET status = 'deletion_pending', deletion_due = now() - interval '1 second' WHERE id = %s",
        (world.shop_a,),
    )
    assert owner.execute("SELECT erase_shop(%s)", (world.shop_a,)).fetchone() == (True,)
    assert is_not_found(view(client, token))
    assert owner.execute("SELECT count(*) FROM customer_share WHERE shop_id = %s", (world.shop_a,)).fetchone() == (0,)
    assert owner.execute("SELECT share_phone FROM shop WHERE id = %s", (world.shop_a,)).fetchone() == (None,)


def test_a_link_of_one_shop_shows_nothing_of_another(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    other = uuid.uuid4()
    owner.execute(
        "INSERT INTO customer (id, shop_id, display_name, name_norm) VALUES (%s, %s, 'Ali Boshqa', 'ali boshqa')",
        (other, world.shop_b),
    )
    owner.execute(
        "INSERT INTO ledger_entry (id, shop_id, customer_id, seq, kind, amount, author_id) "
        "SELECT gen_random_uuid(), %s, %s, 1, 'opening', 777000, m.id FROM membership m WHERE m.shop_id = %s",
        (world.shop_b, other, world.shop_b),
    )
    body = view(client, make(client, world, world.customer_a)).json()
    assert (body["shop_name"], body["balance"]) == ("Shop A", 50_000)
    assert "777000" not in str(body) and "Shop B" not in str(body)


# --- the phone the shop shows ----------------------------------------------------------------------------------


def test_the_owner_sets_the_phone_customers_see_and_may_clear_it(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    contact = f"{shop(world)}/share-contact"
    token = make(client, world, world.customer_a)
    assert client.get(contact, headers=as_user(world.manager_a)).json() == {"phone": None}

    saved = client.put(contact, json={"phone": "90 123-45-67"}, headers={**as_user(world.owner_a), **key()})
    assert saved.status_code == 200 and saved.json() == {"phone": "+998901234567"}, saved.text
    assert client.get(contact, headers=as_user(world.manager_a)).json() == {"phone": "+998901234567"}
    assert view(client, token).json()["shop_phone"] == "+998901234567"

    for empty in (None, "  "):
        cleared = client.put(contact, json={"phone": empty}, headers={**as_user(world.owner_a), **key()})
        assert cleared.status_code == 200 and cleared.json() == {"phone": None}
    assert view(client, token).json()["shop_phone"] is None
    assert [action for action, _, _ in actions(owner, world) if action.startswith("shop.")] == [
        "shop.share_contact_changed"
    ] * 3


@pytest.mark.parametrize("body", [{"phone": "not a phone"}, {"phone": "12"}, {"phone": 998901234567}, {}, {"tel": "1"}])
def test_a_phone_that_is_not_one_is_refused_and_not_stored(
    client: TestClient, world: World, owner: psycopg.Connection, on: None, body: dict[str, Any]
) -> None:
    refused = client.put(f"{shop(world)}/share-contact", json=body, headers={**as_user(world.owner_a), **key()})
    assert (refused.status_code, refused.json()["error"]["code"]) == (422, "VALIDATION"), refused.text
    assert owner.execute("SELECT share_phone FROM shop WHERE id = %s", (world.shop_a,)).fetchone() == (None,)


def test_a_manager_reads_the_phone_but_only_the_owner_changes_it(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    refused = client.put(
        f"{shop(world)}/share-contact", json={"phone": "+998901234567"}, headers={**as_user(world.manager_a), **key()}
    )
    assert (refused.status_code, refused.json()["error"]["fields"]) == (403, {"needed_role": "owner"})
    assert owner.execute("SELECT share_phone FROM shop WHERE id = %s", (world.shop_a,)).fetchone() == (None,)


# --- with the switch off nothing that existed before changes shape ---------------------------------------------


def test_the_customers_own_page_and_the_staff_view_do_not_mention_the_link(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    detail = client.get(f"{shop(world)}/customers/{world.customer_a}", headers=as_user(world.owner_a))
    settings = client.get(shop(world), headers=as_user(world.owner_a))
    mine = client.get(f"/api/v1/me/accounts/{link_of(owner, world.customer_a)}", headers=as_user(world.customer_of_a))
    for response in (detail, settings, mine):
        assert response.status_code == 200, response.text
        assert "share" not in response.text

"""Routes with a response model answer with the same bytes as without one (qarz.interface.answers).

A response model checks an answer; it must not change it. For each typed route the test records what the
application service returned, sends that through a route with no model, and compares the bytes with what
the real route answered. The negative cases show that an answer the model does not describe is refused
outright and not trimmed, converted or filled in.
"""

import copy
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.exceptions import ResponseValidationError
from fastapi.testclient import TestClient

from qarz.application.account import AccountService
from qarz.application.customers import CustomerService
from qarz.application.ledger_service import LedgerService
from qarz.application.shops import ShopService

from .conftest import World, as_user
from .test_customers_ledger import read, record, reverse, seed_entry, shop, today, write
from .test_date_requests import ask_a, change
from .test_payment_notices import seed_notice

pytestmark = pytest.mark.db


def _recording(monkeypatch: pytest.MonkeyPatch, service: type, method: str) -> list[Any]:
    """Keeps a copy of everything the service method returns from now on."""
    original = getattr(service, method)
    returned: list[Any] = []

    async def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        result = await original(self, *args, **kwargs)
        returned.append(copy.deepcopy(result))
        return result

    monkeypatch.setattr(service, method, wrapper)
    return returned


def _replacing(monkeypatch: pytest.MonkeyPatch, service: type, method: str, change: Callable[[Any], Any]) -> None:
    """Makes the service method return what `change` makes of its real result."""
    original = getattr(service, method)

    async def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        return change(await original(self, *args, **kwargs))

    monkeypatch.setattr(service, method, wrapper)


def _without_a_model(body: Any) -> bytes:
    """The bytes the framework answers with when a route returns `body` and declares nothing."""
    plain = FastAPI()

    @plain.get("/")
    async def answer() -> Any:
        return body

    with TestClient(plain) as client:
        return client.get("/").content


def _a_full_account(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    """Gives customer_a every part an answer can carry: lines, a moved date, a request, a notice, history."""
    patch = {"phone": "+998901234567", "credit_limit": 900000}
    assert write(client, world.manager_a, "PATCH", f"{shop(world)}/customers/{world.customer_a}", patch).is_success
    # Fell due a month ago and was paid late: the payment history indicator has something to say.
    seed_entry(owner, world, world.customer_a, 2, "credit", 30000, promised=today() - timedelta(days=30), days_ago=40)
    lines = [
        {"catalog_item_id": str(world.catalog_item_a), "qty": "3", "unit_price": 4000},
        {"name": "Guruch", "qty": "1.5", "unit": "kg", "unit_price": 18000},
    ]
    sale = record(client, world, world.customer_a, "credit", None, lines=lines, note="Bozorlik")
    assert sale.status_code == 201, sale.text
    assert record(client, world, world.customer_a, "payment", 30000).status_code == 201
    mistake = record(client, world, world.customer_a, "credit", 7000)
    assert reverse(client, world, mistake.json()["entry"]["id"]).status_code == 201
    moved = change(
        client, world, world.manager_a, sale.json()["entry"]["id"], today() + timedelta(days=20), reason="Kelishildi"
    )
    assert moved.is_success, moved.text
    assert ask_a(client, world, owner, reason="Oylik kechikdi").status_code == 201
    seed_notice(owner, world, 20000)
    # A second debtor, so that a page of one has a next page.
    assert record(client, world, world.settled_customer_a, "credit", 12000).status_code == 201


def test_a_typed_route_answers_with_the_same_bytes_as_without_a_model(
    client: TestClient, world: World, owner: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _a_full_account(client, world, owner)
    base = shop(world)
    cases: list[tuple[type, str, str, dict[str, Any]]] = [
        (CustomerService, "list", f"{base}/customers", {}),
        (CustomerService, "list", f"{base}/customers", {"limit": 1}),
        (CustomerService, "list", f"{base}/customers", {"status": "archived"}),
        (LedgerService, "customer_detail", f"{base}/customers/{world.customer_a}", {}),
        (LedgerService, "customer_detail", f"{base}/customers/{world.archived_customer_a}", {}),
        (LedgerService, "overview", f"{base}/overview", {}),
        (LedgerService, "debtors", f"{base}/overview/debtors", {}),
        (LedgerService, "debtors", f"{base}/overview/debtors", {"limit": 1}),
        (LedgerService, "debtors", f"{base}/overview/debtors", {"overdue": "true"}),
        (ShopService, "read", base, {}),
        (AccountService, "my_shops", "/api/v1/me/shops", {}),
    ]
    for service, method, path, params in cases:
        with monkeypatch.context() as patch:
            returned = _recording(patch, service, method)
            response = read(client, world.manager_a, path, **params)
        assert response.status_code == 200, (path, response.text)
        assert len(returned) == 1, path
        assert response.content == _without_a_model(returned[0]), path
        assert response.headers["content-type"] == "application/json"

    # The account was full: the comparison covered every nested part and both forms of a page.
    detail = read(client, world.manager_a, f"{base}/customers/{world.customer_a}").json()
    assert detail["phone"] and detail["credit_limit"] and detail["payment_history"] and detail["payment_notices"]
    entries = detail["entries"]
    assert any(len(entry["lines"]) == 2 for entry in entries)
    assert any(len(entry["promises"]) == 2 for entry in entries)
    assert any(entry["date_request"] for entry in entries)
    assert any(entry["reverses_id"] for entry in entries) and any(entry["reversed"] for entry in entries)
    assert read(client, world.manager_a, f"{base}/customers", limit=1).json()["next_cursor"]
    assert read(client, world.manager_a, f"{base}/overview/debtors", limit=1).json()["next_cursor"]


def test_a_member_of_no_shop_gets_the_same_empty_answer(client: TestClient, world: World) -> None:
    response = client.get("/api/v1/me/shops", headers=as_user(world.stranger))
    assert response.content == _without_a_model({"items": [], "active_shop": None})


def _with_an_extra_field(body: dict[str, Any]) -> dict[str, Any]:
    return {**body, "margin": 5}


def _without_the_balance(body: dict[str, Any]) -> dict[str, Any]:
    return {name: value for name, value in body.items() if name != "balance"}


def _with_the_balance_as_text(body: dict[str, Any]) -> dict[str, Any]:
    return {**body, "balance": str(body["balance"])}


def _with_an_extra_field_in_an_entry(body: dict[str, Any]) -> dict[str, Any]:
    return {**body, "entries": [{**entry, "margin": 5} for entry in body["entries"]]}


@pytest.mark.parametrize(
    "change",
    [_with_an_extra_field, _without_the_balance, _with_the_balance_as_text, _with_an_extra_field_in_an_entry],
)
def test_an_answer_the_model_does_not_describe_is_refused_not_changed(
    client: TestClient,
    world: World,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    change: Callable[[Any], Any],
) -> None:
    """The negative cases: a field the model lacks is not dropped, a missing one is not filled in, and a
    number sent as text is not converted. Each is a server error with nothing of the answer in it, so the
    API tests notice the day it happens and no caller is given a silently different body."""
    path = f"{shop(world)}/customers/{world.customer_a}"
    assert read(client, world.manager_a, path).status_code == 200
    _replacing(monkeypatch, LedgerService, "customer_detail", change)
    response = read(client, world.manager_a, path)
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "ERROR"
    assert set(response.json()["error"]) == {"code", "message", "fields", "request_id"}
    assert "Ali" not in response.text and "margin" not in response.text
    assert any(record.exc_info and record.exc_info[0] is ResponseValidationError for record in caplog.records)


def test_a_refusal_is_still_the_usual_error_body(client: TestClient, world: World) -> None:
    """The model describes the successful answer only; errors pass by it."""
    missing = read(client, world.manager_a, f"{shop(world)}/customers/{uuid.uuid4()}")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "NOT_FOUND"
    assert read(client, world.stranger, f"{shop(world)}/overview").status_code == 404
    assert read(client, world.manager_a, f"{shop(world)}/customers", limit=0).json()["error"]["code"] == "VALIDATION"

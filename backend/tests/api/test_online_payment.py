"""Paying the subscription through Payme and Click (REQ-056, ADR-019).

The adapters are built and switched off. These tests turn the platform switch on in the test database only,
with keys that exist nowhere else, and play the provider's side of each protocol.
"""

import asyncio
import base64
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from qarz.application.auth import AuthService
from qarz.application.chat_texts import day as show_day
from qarz.application.chat_texts import money, say
from qarz.application.online_payment import OnlinePaymentService, PaymentKeys
from qarz.domain import online_payment as rules
from qarz.infrastructure.db import Database
from qarz.interface.http import create_app
from qarz.interface.online_payment_api import add_provider_routes

from .conftest import TEST_BOT_TOKEN, HeaderAuthenticator, World, as_user
from .test_subscription import owner_chat, set_subscription

pytestmark = pytest.mark.db

KEYS = PaymentKeys(
    payme_merchant_id="test-merchant",
    payme_key="payme-test-key-not-a-real-one",
    click_service_id="70001",
    click_merchant_id="50001",
    click_key="click-test-key-not-a-real-one",
)
START = datetime(2050, 3, 10, 7, 0, tzinfo=UTC)  # noon in Tashkent
TODAY = date(2050, 3, 10)
PRICE = 100_000


@dataclass
class Clock:
    now: datetime = START

    def __call__(self) -> datetime:
        return self.now


@dataclass
class Pay:
    """An application with the given provider keys, and the provider's side of the conversation."""

    client: TestClient
    clock: Clock
    keys: PaymentKeys
    _ids: Iterator[int] = field(default_factory=lambda: iter(range(1, 10_000)))

    def order(self, world: World, months: int = 1, user: uuid.UUID | None = None, key: str | None = None) -> Any:
        return self.client.post(
            f"/api/v1/shops/{world.shop_a}/subscription/online-orders",
            json={"months": months},
            headers={**as_user(user or world.owner_a), "Idempotency-Key": key or uuid.uuid4().hex},
        )

    def order_id(self, world: World, months: int = 1) -> str:
        response = self.order(world, months)
        assert response.status_code == 201, response.text
        return str(response.json()["id"])

    def payme(self, method: str, params: dict[str, Any], *, key: str | None = None, login: str = "Paycom") -> Any:
        token = base64.b64encode(f"{login}:{key or self.keys.payme_key}".encode()).decode()
        response = self.client.post(
            "/pay/payme",
            json={"jsonrpc": "2.0", "id": next(self._ids), "method": method, "params": params},
            headers={"Authorization": f"Basic {token}"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["jsonrpc"] == "2.0" and ("result" in body) != ("error" in body)
        return body

    def ms(self) -> int:
        return int(self.clock.now.timestamp() * 1000)

    def create(self, order: str, txn: str, amount: int = PRICE * 100, time: int | None = None) -> Any:
        return self.payme(
            "CreateTransaction",
            {"id": txn, "time": self.ms() if time is None else time, "amount": amount, "account": {"order_id": order}},
        )

    def click(self, action: int | str, order: str, txn: str, amount: str = str(PRICE), **changed: str) -> Any:
        form = {
            "click_trans_id": txn,
            "service_id": self.keys.click_service_id,
            "click_paydoc_id": "900",
            "merchant_trans_id": order,
            "amount": amount,
            "action": str(action),
            "error": "0",
            "error_note": "Success",
            "sign_time": "2050-03-10 12:00:00",
        }
        secret = changed.pop("secret", self.keys.click_key)
        signed_as = changed.pop("signed_as", None)
        form.update({name: value for name, value in changed.items() if name != "sign_string"})
        form["sign_string"] = changed.get("sign_string") or rules.click_sign(
            click_trans_id=form["click_trans_id"],
            service_id=form["service_id"],
            secret=secret,
            merchant_trans_id=form["merchant_trans_id"],
            merchant_prepare_id=form.get("merchant_prepare_id"),
            amount=form["amount"],
            action=signed_as or form["action"],
            sign_time=form["sign_time"],
        )
        response = self.client.post("/pay/click", data=form)
        assert response.status_code == 200, response.text
        return response.json()


def _app(app_database_url: str, keys: PaymentKeys) -> Iterator[Pay]:
    database, clock = Database(app_database_url), Clock()
    app = create_app(
        database.reachable,
        database,
        auth=AuthService(database, TEST_BOT_TOKEN),
        authenticator=HeaderAuthenticator(),
        now=clock,
        payment_keys=keys,
    )
    with TestClient(app) as client:
        yield Pay(client, clock, keys)
        client.portal.call(database.dispose)  # type: ignore[union-attr]


@pytest.fixture
def switch(owner: psycopg.Connection) -> Iterator[None]:
    """The platform switch on, for this test only."""
    owner.execute(
        "INSERT INTO platform_setting (key, value, updated_by) VALUES ('online_pay_on', 'true', 'test') "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
    )
    yield
    owner.execute("DELETE FROM platform_setting WHERE key = 'online_pay_on'")


@pytest.fixture
def off(app_database_url: str, owner: psycopg.Connection) -> Iterator[Pay]:
    """Keys configured, the switch as it is by default: off."""
    owner.execute("DELETE FROM platform_setting WHERE key = 'online_pay_on'")
    yield from _app(app_database_url, KEYS)


@pytest.fixture
def pay(app_database_url: str, switch: None, world: World, owner: psycopg.Connection) -> Iterator[Pay]:
    """Keys configured and the switch on; shop A's paid period has ended."""
    set_subscription(owner, world, "limited")
    # A provider's transaction identifier is unique across shops, and the tests reuse theirs.
    owner.execute("DELETE FROM online_payment")
    yield from _app(app_database_url, KEYS)


def orders(owner: psycopg.Connection, world: World) -> list[tuple[Any, ...]]:
    return owner.execute(
        "SELECT state, provider, provider_txn, cancel_reason, months, amount FROM online_payment "
        "WHERE shop_id = %s ORDER BY created_at, prepare_id",
        (world.shop_a,),
    ).fetchall()


def subscription(owner: psycopg.Connection, shop: uuid.UUID) -> Any:
    return owner.execute("SELECT state, paid_through FROM subscription WHERE shop_id = %s", (shop,)).fetchone()


def everything(owner: psycopg.Connection, world: World) -> tuple[Any, ...]:
    return (
        owner.execute("SELECT count(*) FROM online_payment").fetchone(),
        subscription(owner, world.shop_a),
        subscription(owner, world.shop_b),
        owner.execute("SELECT count(*) FROM outbox_message").fetchone(),
        owner.execute("SELECT count(*) FROM activity").fetchone(),
    )


def told(owner: psycopg.Connection, world: World) -> list[tuple[str, str]]:
    rows = owner.execute(
        "SELECT recipient, payload->>'text' FROM outbox_message WHERE shop_id = %s AND dedupe_key LIKE "
        "'sub:paid_online:%%' ORDER BY created_at, id",
        (world.shop_a,),
    ).fetchall()
    return [(str(row[0]), str(row[1])) for row in rows]


def code(answer: Any) -> int:
    return int(answer["error"]["code"])


# --- switched off --------------------------------------------------------------------------------


def test_by_default_nothing_of_online_payment_works(off: Pay, world: World, owner: psycopg.Connection) -> None:
    before = everything(owner, world)
    response = off.order(world)
    assert (response.status_code, response.json()["error"]["code"]) == (409, "ONLINE_PAY_OFF")

    token = base64.b64encode(f"Paycom:{KEYS.payme_key}".encode()).decode()
    payme = off.client.post(
        "/pay/payme",
        json={"id": 1, "method": "GetStatement", "params": {"from": 0, "to": 2**62}},
        headers={"Authorization": f"Basic {token}"},
    )
    click = off.client.post("/pay/click", data={"click_trans_id": "1"})
    for answer in (payme, click):
        assert (answer.status_code, answer.json()) == (503, {"status": "disabled"})
    # Nothing of a request is read while switched off: even one too large to be a provider's call.
    for path in ("/pay/payme", "/pay/click"):
        assert off.client.post(path, content=b" " * 20_000).status_code == 503
    assert everything(owner, world) == before


def test_the_adapters_themselves_answer_nothing_while_switched_off(
    app_database_url: str, world: World, owner: psycopg.Connection
) -> None:
    """Whoever calls them, not only the HTTP routes: the switch is read by the adapter itself."""
    owner.execute("DELETE FROM platform_setting WHERE key = 'online_pay_on'")
    before = everything(owner, world)

    async def scenario() -> tuple[Any, Any]:
        database = Database(app_database_url)
        try:
            service = OnlinePaymentService(database, KEYS)
            token = base64.b64encode(f"Paycom:{KEYS.payme_key}".encode()).decode()
            statement = {"id": 1, "method": "GetStatement", "params": {"from": 0, "to": 2**62}}
            return await service.payme(f"Basic {token}", statement), await service.click({})
        finally:
            await database.dispose()

    assert asyncio.run(scenario()) == (None, None)
    assert everything(owner, world) == before


@pytest.mark.parametrize("value", ['"true"', "1", "false", "null", '{"on": true}'])
def test_only_the_value_true_turns_the_switch_on(off: Pay, world: World, owner: psycopg.Connection, value: str) -> None:
    owner.execute(
        "INSERT INTO platform_setting (key, value, updated_by) VALUES ('online_pay_on', %s::jsonb, 'test')", (value,)
    )
    try:
        assert off.order(world).json()["error"]["code"] == "ONLINE_PAY_OFF"
        assert off.client.post("/pay/payme", json={}).status_code == 503
        assert off.client.post("/pay/click", data={}).status_code == 503
        assert orders(owner, world) == []
    finally:
        owner.execute("DELETE FROM platform_setting WHERE key = 'online_pay_on'")


def test_the_switch_alone_is_not_enough_without_keys(
    client: TestClient, switch: None, world: World, owner: psycopg.Connection
) -> None:
    """The ordinary application of the tests has no provider keys, as a deployment without contracts."""
    response = client.post(
        f"/api/v1/shops/{world.shop_a}/subscription/online-orders",
        json={"months": 1},
        headers={**as_user(world.owner_a), "Idempotency-Key": uuid.uuid4().hex},
    )
    assert (response.status_code, response.json()["error"]["code"]) == (409, "ONLINE_PAY_OFF")
    assert client.post("/pay/payme", json={}).status_code == 503
    assert client.post("/pay/click", data={}).status_code == 503
    assert orders(owner, world) == []


def test_a_provider_without_its_key_stays_disabled_while_the_other_works(
    app_database_url: str, switch: None, world: World
) -> None:
    only_payme = PaymentKeys(payme_merchant_id="m", payme_key="a-key-for-payme-only")
    for app in _app(app_database_url, only_payme):
        body = app.order(world).json()
        assert list(body["pay_urls"]) == ["payme"]
        assert app.client.post("/pay/click", data={}).status_code == 503
        assert code(app.payme("Nothing", {})) == rules.PAYME_NO_METHOD


def test_an_adapter_that_answers_nothing_is_reported_as_disabled() -> None:
    """The switch can be turned off between the route's look at it and the adapter's own."""

    class SwitchedOffMeanwhile:
        async def enabled(self, provider: str) -> bool:
            return True

        async def payme(self, authorization: str | None, body: Any) -> None:
            return None

        async def click(self, form: Any) -> None:
            return None

    app = FastAPI()
    add_provider_routes(app, SwitchedOffMeanwhile())  # type: ignore[arg-type]
    with TestClient(app) as client:
        for path in ("/pay/payme", "/pay/click"):
            answer = client.post(path, content=b"{}")
            assert (answer.status_code, answer.json()) == (503, {"status": "disabled"})


# --- the owner's order ---------------------------------------------------------------------------


def test_the_owner_orders_months_at_the_current_price(pay: Pay, world: World, owner: psycopg.Connection) -> None:
    owner.execute("INSERT INTO platform_setting (key, value, updated_by) VALUES ('price_uzs', '150000', 'test')")
    try:
        key = uuid.uuid4().hex
        response = pay.order(world, months=3, key=key)
        assert response.status_code == 201, response.text
        body = response.json()
        assert (body["months"], body["amount"]) == (3, 450_000)
        encoded = body["pay_urls"]["payme"].removeprefix("https://checkout.paycom.uz/")
        assert base64.b64decode(encoded).decode() == f"m=test-merchant;ac.order_id={body['id']};a=45000000"
        assert body["pay_urls"]["click"] == (
            "https://my.click.uz/services/pay?service_id=70001&merchant_id=50001&amount=450000"
            f"&transaction_param={body['id']}"
        )
        assert orders(owner, world) == [("created", None, None, None, 3, 450_000)]
        # The same request again is the same order, even after the price changed.
        owner.execute("UPDATE platform_setting SET value = '999' WHERE key = 'price_uzs'")
        assert pay.order(world, months=3, key=key).json() == body
        assert len(orders(owner, world)) == 1
    finally:
        owner.execute("DELETE FROM platform_setting WHERE key = 'price_uzs'")
    # Ordering gives nothing: the shop is still limited.
    assert subscription(owner, world.shop_a) == ("limited", None)


@pytest.mark.parametrize("stored", ['"150000"', "0", "-5", "true", "null", "1.5", "[150000]"])
def test_a_price_setting_that_is_not_a_positive_whole_number_means_the_initial_price(
    pay: Pay, world: World, owner: psycopg.Connection, stored: str
) -> None:
    owner.execute(
        "INSERT INTO platform_setting (key, value, updated_by) VALUES ('price_uzs', %s::jsonb, 'test')", (stored,)
    )
    try:
        assert pay.order(world, months=2).json()["amount"] == 200_000
    finally:
        owner.execute("DELETE FROM platform_setting WHERE key = 'price_uzs'")


def test_an_order_needs_the_owner_a_key_and_a_sensible_number_of_months(
    pay: Pay, world: World, owner: psycopg.Connection
) -> None:
    for other in (world.manager_a, world.seller_a):
        assert pay.order(world, user=other).status_code == 403
    assert pay.order(world, user=world.owner_b).status_code == 404
    for months in (0, 37, -1):
        refused = pay.order(world, months=months)
        assert (refused.status_code, refused.json()["error"]["fields"]) == (
            422,
            {"months": "must be between 1 and 36"},
        )
        # Who is asking is settled first: a manager is not told what a valid order looks like.
        assert pay.order(world, months=months, user=world.manager_a).status_code == 403
    assert pay.order(world, months=36).status_code == 201
    owner.execute("DELETE FROM online_payment")
    path = f"/api/v1/shops/{world.shop_a}/subscription/online-orders"
    assert pay.client.post(path, json={"months": 1}, headers=as_user(world.owner_a)).status_code == 422
    for body in ({"months": "1"}, {"months": 1.5}, {"months": True}, {"months": 1, "amount": 1}, {}):
        headers = {**as_user(world.owner_a), "Idempotency-Key": uuid.uuid4().hex}
        assert pay.client.post(path, json=body, headers=headers).status_code == 422, body
    assert orders(owner, world) == []


# --- Payme ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "Basic",
        "Basic not-base64!",
        "Bearer " + base64.b64encode(f"Paycom:{KEYS.payme_key}".encode()).decode(),
        "Basic " + base64.b64encode(f"Paycom:{KEYS.payme_key}x".encode()).decode(),
        "Basic " + base64.b64encode(f"paycom:{KEYS.payme_key}".encode()).decode(),
        "Basic " + base64.b64encode(f"Admin:{KEYS.payme_key}".encode()).decode(),
        "Basic " + base64.b64encode(KEYS.payme_key.encode()).decode(),
        "Basic " + base64.b64encode(b"Paycom:").decode(),
        "Basic " + base64.b64encode(f"Paycom:{KEYS.click_key}".encode()).decode(),
    ],
)
def test_payme_calls_without_our_key_are_refused_and_change_nothing(
    pay: Pay, world: World, owner: psycopg.Connection, header: str | None
) -> None:
    order = pay.order_id(world)
    before = everything(owner, world)
    for method, params in (
        ("CreateTransaction", {"id": "t1", "time": pay.ms(), "amount": PRICE * 100, "account": {"order_id": order}}),
        ("GetStatement", {"from": 0, "to": 2**62}),
    ):
        response = pay.client.post(
            "/pay/payme",
            json={"id": 7, "method": method, "params": params},
            headers={} if header is None else {"Authorization": header},
        )
        assert response.status_code == 200
        body = response.json()
        assert (body["id"], body["error"]["code"]) == (7, rules.PAYME_NO_ACCESS)
        assert "result" not in body
    assert everything(owner, world) == before


def test_a_payme_payment_from_check_to_perform(pay: Pay, world: World, owner: psycopg.Connection) -> None:
    order = pay.order_id(world, months=2)
    account = {"order_id": order}
    amount = 2 * PRICE * 100
    assert pay.payme("CheckPerformTransaction", {"amount": amount, "account": account})["result"] == {"allow": True}
    assert orders(owner, world)[0][0] == "created", "checking starts nothing"

    created = pay.create(order, "payme-1", amount)["result"]
    assert created == {"create_time": pay.ms(), "transaction": order, "state": 1}
    assert orders(owner, world) == [("pending", "payme", "payme-1", None, 2, 2 * PRICE)]
    assert subscription(owner, world.shop_a) == ("limited", None), "nothing is given before the money is taken"

    pay.clock.now += timedelta(minutes=5)
    assert pay.create(order, "payme-1", amount)["result"] == created, "a repeated call gets the same answer"

    performed = pay.payme("PerformTransaction", {"id": "payme-1"})["result"]
    assert performed == {"transaction": order, "perform_time": pay.ms(), "state": 2}
    # Two months from today, today being the first paid day (BR-27).
    assert subscription(owner, world.shop_a) == ("active", date(2050, 5, 9))
    text = say("uz", "sub_paid_online", shop="Shop A", amount=money("uz", 2 * PRICE), date=show_day(date(2050, 5, 9)))
    assert told(owner, world) == [(owner_chat(owner, world), text)]
    assert owner.execute(
        "SELECT actor_kind, action, subject_id::text FROM activity "
        "WHERE shop_id = %s AND action LIKE 'subscription.%%'",
        (world.shop_a,),
    ).fetchall() == [("system", "subscription.paid_online", order)]

    pay.clock.now += timedelta(days=3)
    assert pay.payme("PerformTransaction", {"id": "payme-1"})["result"] == performed
    assert subscription(owner, world.shop_a) == ("active", date(2050, 5, 9)), "performing twice pays once"
    assert len(told(owner, world)) == 1

    checked = pay.payme("CheckTransaction", {"id": "payme-1"})["result"]
    assert checked == {
        "create_time": created["create_time"],
        "perform_time": performed["perform_time"],
        "cancel_time": 0,
        "transaction": order,
        "state": 2,
        "reason": None,
    }
    # The shop of another owner got nothing.
    assert subscription(owner, world.shop_b) != ("active", date(2050, 5, 9))


def test_a_paid_payme_transaction_cannot_be_cancelled(pay: Pay, world: World, owner: psycopg.Connection) -> None:
    order = pay.order_id(world)
    pay.create(order, "payme-1")
    pay.payme("PerformTransaction", {"id": "payme-1"})
    paid = subscription(owner, world.shop_a)
    assert code(pay.payme("CancelTransaction", {"id": "payme-1", "reason": 5})) == rules.PAYME_CANNOT_CANCEL
    assert subscription(owner, world.shop_a) == paid
    assert orders(owner, world)[0][:4] == ("paid", "payme", "payme-1", None)


def test_a_pending_payme_transaction_is_cancelled_and_then_gives_nothing(
    pay: Pay, world: World, owner: psycopg.Connection
) -> None:
    order = pay.order_id(world)
    pay.create(order, "payme-1")
    pay.clock.now += timedelta(minutes=1)
    cancelled = pay.payme("CancelTransaction", {"id": "payme-1", "reason": 3})["result"]
    assert cancelled == {"transaction": order, "cancel_time": pay.ms(), "state": -1}
    assert orders(owner, world) == [("cancelled", "payme", "payme-1", 3, 1, PRICE)]

    pay.clock.now += timedelta(minutes=1)
    assert pay.payme("CancelTransaction", {"id": "payme-1", "reason": 5})["result"] == cancelled
    assert orders(owner, world)[0][3] == 3, "the first reason stays"
    assert code(pay.payme("PerformTransaction", {"id": "payme-1"})) == rules.PAYME_CANNOT_PERFORM
    assert code(pay.create(order, "payme-1")) == rules.PAYME_CANNOT_PERFORM
    assert code(pay.create(order, "payme-2")) == rules.PAYME_ORDER_BUSY, "a cancelled order is not paid again"
    assert pay.payme("CheckTransaction", {"id": "payme-1"})["result"]["state"] == -1
    assert pay.payme("CheckTransaction", {"id": "payme-1"})["result"]["reason"] == 3
    assert subscription(owner, world.shop_a) == ("limited", None)
    assert told(owner, world) == []


def test_payme_refuses_what_does_not_match_the_order(pay: Pay, world: World, owner: psycopg.Connection) -> None:
    order = pay.order_id(world)
    unknown = str(uuid.uuid4())
    for amount in (PRICE * 100 - 1, PRICE * 100 + 1, PRICE, 0, -PRICE * 100):
        check = pay.payme("CheckPerformTransaction", {"amount": amount, "account": {"order_id": order}})
        assert code(check) == rules.PAYME_WRONG_AMOUNT
        assert code(pay.create(order, f"t{amount}", amount)) == rules.PAYME_WRONG_AMOUNT
    for account in ({"order_id": unknown}, {"order_id": "not-an-id"}, {"order_id": None}, {}, {"order_id": 5}):
        check = pay.payme("CheckPerformTransaction", {"amount": PRICE * 100, "account": account})
        assert code(check) == rules.PAYME_NO_ORDER, account
        create = pay.payme(
            "CreateTransaction", {"id": "tx", "time": pay.ms(), "amount": PRICE * 100, "account": account}
        )
        assert code(create) == rules.PAYME_NO_ORDER, account
    assert orders(owner, world) == [("created", None, None, None, 1, PRICE)]

    pay.create(order, "payme-1")
    assert code(pay.create(order, "payme-2")) == rules.PAYME_ORDER_BUSY, "one transaction per order"
    busy = pay.payme("CheckPerformTransaction", {"amount": PRICE * 100, "account": {"order_id": order}})
    assert code(busy) == rules.PAYME_ORDER_BUSY
    assert orders(owner, world) == [("pending", "payme", "payme-1", None, 1, PRICE)]


def test_payme_cannot_pay_for_a_suspended_shop(pay: Pay, world: World, owner: psycopg.Connection) -> None:
    order = pay.order_id(world)
    set_subscription(owner, world, "suspended")
    check = pay.payme("CheckPerformTransaction", {"amount": PRICE * 100, "account": {"order_id": order}})
    assert code(check) == rules.PAYME_SHOP_BLOCKED
    assert code(pay.create(order, "payme-1")) == rules.PAYME_SHOP_BLOCKED
    assert orders(owner, world)[0][0] == "created"


def test_money_taken_for_a_shop_suspended_meanwhile_is_counted_but_does_not_lift_the_suspension(
    pay: Pay, world: World, owner: psycopg.Connection
) -> None:
    order = pay.order_id(world)
    pay.create(order, "payme-1")
    set_subscription(owner, world, "suspended")
    assert pay.payme("PerformTransaction", {"id": "payme-1"})["result"]["state"] == 2
    assert subscription(owner, world.shop_a) == ("suspended", date(2050, 4, 9))
    row = owner.execute("SELECT prior_state FROM subscription WHERE shop_id = %s", (world.shop_a,)).fetchone()
    assert row == ("active",), "when the suspension ends the shop is in its paid period"


@pytest.mark.parametrize(
    ("state", "dates", "paid_through"),
    [
        ("limited", {}, date(2050, 4, 9)),
        # A trial that still runs keeps its days: the month follows its last day.
        ("trial", {"trial_ends": date(2050, 3, 20)}, date(2050, 4, 20)),
        ("trial", {"trial_ends": date(2050, 3, 9)}, date(2050, 4, 9)),  # ended yesterday: from today
        ("active", {"paid_through": date(2050, 3, 25)}, date(2050, 4, 25)),
        ("active", {"paid_through": TODAY}, date(2050, 4, 10)),
        ("active", {"paid_through": date(2050, 3, 9)}, date(2050, 4, 9)),
    ],
)
def test_the_paid_period_runs_from_the_later_of_today_and_the_paid_through_date(
    pay: Pay, world: World, owner: psycopg.Connection, state: str, dates: dict[str, date], paid_through: date
) -> None:
    set_subscription(owner, world, state, **dates)
    order = pay.order_id(world)
    pay.create(order, "payme-1")
    pay.payme("PerformTransaction", {"id": "payme-1"})
    assert subscription(owner, world.shop_a) == ("active", paid_through)


def test_a_payme_transaction_not_performed_in_twelve_hours_is_cancelled(
    pay: Pay, world: World, owner: psycopg.Connection
) -> None:
    first, second = pay.order_id(world), pay.order_id(world)
    pay.create(first, "payme-1")
    pay.create(second, "payme-2")
    pay.clock.now += timedelta(hours=12)
    assert pay.payme("PerformTransaction", {"id": "payme-1"})["result"]["state"] == 2, "exactly 12 hours is in time"
    paid = subscription(owner, world.shop_a)

    pay.clock.now += timedelta(milliseconds=1)
    assert code(pay.payme("PerformTransaction", {"id": "payme-2"})) == rules.PAYME_CANNOT_PERFORM
    assert orders(owner, world)[1][:4] == ("cancelled", "payme", "payme-2", rules.PAYME_REASON_TIMEOUT)
    assert subscription(owner, world.shop_a) == paid
    assert pay.payme("CheckTransaction", {"id": "payme-2"})["result"]["state"] == -1

    third = pay.order_id(world)
    pay.create(third, "payme-3")
    pay.clock.now += timedelta(hours=12, milliseconds=1)
    assert code(pay.create(third, "payme-3")) == rules.PAYME_CANNOT_PERFORM, "a late repeat cancels too"
    assert orders(owner, world)[2][:4] == ("cancelled", "payme", "payme-3", rules.PAYME_REASON_TIMEOUT)


def test_payme_cannot_start_a_transaction_it_dated_more_than_twelve_hours_ago(
    pay: Pay, world: World, owner: psycopg.Connection
) -> None:
    order = pay.order_id(world)
    old = pay.ms() - rules.PAYME_TIMEOUT_MS
    assert code(pay.create(order, "payme-1", time=old - 1)) == rules.PAYME_CANNOT_PERFORM
    assert orders(owner, world)[0][0] == "created"
    assert pay.create(order, "payme-1", time=old)["result"]["state"] == 1


def test_payme_calls_that_make_no_sense_are_answered_in_its_protocol(
    pay: Pay, world: World, owner: psycopg.Connection
) -> None:
    order = pay.order_id(world)
    before = everything(owner, world)
    assert code(pay.payme("TakeEverything", {})) == rules.PAYME_NO_METHOD
    for method in ("PerformTransaction", "CancelTransaction", "CheckTransaction"):
        params = {"id": "nobody-knows-this", "reason": 1}
        assert code(pay.payme(method, params)) == rules.PAYME_NO_TRANSACTION, method
        assert code(pay.payme(method, {"reason": 1})) == rules.PAYME_BAD_REQUEST, method
        assert code(pay.payme(method, {"id": 5, "reason": 1})) == rules.PAYME_BAD_REQUEST, method
    bad = [
        ("CheckPerformTransaction", {"amount": "10000000", "account": {"order_id": order}}),
        ("CheckPerformTransaction", {"amount": True, "account": {"order_id": order}}),
        ("CheckPerformTransaction", {"amount": PRICE * 100}),
        ("CreateTransaction", {"id": "", "time": pay.ms(), "amount": PRICE * 100, "account": {"order_id": order}}),
        ("CreateTransaction", {"id": "t", "time": "now", "amount": PRICE * 100, "account": {"order_id": order}}),
        ("CreateTransaction", {"id": "t", "time": pay.ms(), "account": {"order_id": order}}),
        ("CancelTransaction", {"id": "t"}),
        ("CancelTransaction", {"id": "t", "reason": "3"}),
        ("CancelTransaction", {"id": "t", "reason": 10**9}),
        ("GetStatement", {"from": 0}),
        ("GetStatement", {"from": "0", "to": 1}),
    ]
    for method, params in bad:
        assert code(pay.payme(method, params)) == rules.PAYME_BAD_REQUEST, (method, params)

    token = base64.b64encode(f"Paycom:{KEYS.payme_key}".encode()).decode()
    headers = {"Authorization": f"Basic {token}", "Content-Type": "application/json"}
    for raw, expected in (
        (b"{not json", rules.PAYME_BAD_JSON),
        (b"[1, 2]", rules.PAYME_BAD_JSON),
        (b'"text"', rules.PAYME_BAD_JSON),
        (b'{"id": 1}', rules.PAYME_BAD_REQUEST),
        (b'{"id": 1, "method": 5, "params": {}}', rules.PAYME_BAD_REQUEST),
        (b'{"id": 1, "method": "CheckTransaction", "params": []}', rules.PAYME_BAD_REQUEST),
    ):
        response = pay.client.post("/pay/payme", content=raw, headers=headers)
        assert (response.status_code, response.json()["error"]["code"]) == (200, expected), raw
    too_large = pay.client.post("/pay/payme", content=b" " * 20_000, headers=headers)
    assert too_large.status_code == 413
    # Sent in pieces with no length declared: refused all the same.
    in_pieces = pay.client.post("/pay/payme", content=iter([b" " * 9_000] * 2), headers=headers)
    assert in_pieces.status_code == 413
    call = b'{"id": 1, "method": "Nothing", "params": {}}'
    just_fits = call + b" " * (16 * 1024 - len(call))
    assert len(just_fits) == 16 * 1024
    assert pay.client.post("/pay/payme", content=just_fits, headers=headers).status_code == 200
    assert pay.client.post("/pay/payme", content=just_fits + b" ", headers=headers).status_code == 413
    assert everything(owner, world) == before


def test_the_payme_statement_lists_payme_transactions_of_the_period_only(
    pay: Pay, world: World, owner: psycopg.Connection
) -> None:
    owner.execute("DELETE FROM online_payment")
    first, second, third, by_click = (pay.order_id(world) for _ in range(4))
    untouched = pay.order_id(world)
    start = pay.ms()
    pay.create(first, "payme-1")
    pay.payme("PerformTransaction", {"id": "payme-1"})
    pay.clock.now += timedelta(minutes=1)
    pay.create(second, "payme-2")
    pay.payme("CancelTransaction", {"id": "payme-2", "reason": 2})
    pay.clock.now += timedelta(minutes=1)
    pay.create(third, "payme-3")
    assert pay.click(0, by_click, "click-1")["error"] == 0

    listed = pay.payme("GetStatement", {"from": start, "to": start + 60_000})["result"]["transactions"]
    assert [(row["id"], row["state"], row["reason"]) for row in listed] == [("payme-1", 2, None), ("payme-2", -1, 2)]
    assert listed[0] == {
        "id": "payme-1",
        "time": start,
        "amount": PRICE * 100,
        "account": {"order_id": first},
        "create_time": start,
        "perform_time": start,
        "cancel_time": 0,
        "transaction": first,
        "state": 2,
        "reason": None,
    }
    everything_listed = pay.payme("GetStatement", {"from": 0, "to": 2**62})["result"]["transactions"]
    assert [row["id"] for row in everything_listed] == ["payme-1", "payme-2", "payme-3"]
    assert untouched not in str(everything_listed) and by_click not in str(everything_listed)
    assert pay.payme("GetStatement", {"from": start + 120_001, "to": 2**62})["result"] == {"transactions": []}


# --- Click ---------------------------------------------------------------------------------------


def test_a_click_payment_from_prepare_to_complete(pay: Pay, world: World, owner: psycopg.Connection) -> None:
    order = pay.order_id(world)
    prepare_id = owner.execute("SELECT prepare_id FROM online_payment WHERE id = %s", (order,)).fetchone()[0]  # type: ignore[index]

    prepared = pay.click(0, order, "click-1")
    assert prepared == {
        "click_trans_id": "click-1",
        "merchant_trans_id": order,
        "merchant_prepare_id": prepare_id,
        "error": 0,
        "error_note": "Success",
    }
    assert orders(owner, world) == [("pending", "click", "click-1", None, 1, PRICE)]
    assert subscription(owner, world.shop_a) == ("limited", None)
    assert pay.click(0, order, "click-1") == prepared, "a repeated call gets the same answer"

    completed = pay.click(1, order, "click-1", "100000.00", merchant_prepare_id=str(prepare_id))
    assert completed == {
        "click_trans_id": "click-1",
        "merchant_trans_id": order,
        "merchant_confirm_id": prepare_id,
        "error": 0,
        "error_note": "Success",
    }
    assert subscription(owner, world.shop_a) == ("active", date(2050, 4, 9))
    assert len(told(owner, world)) == 1

    pay.clock.now += timedelta(days=40)
    again = pay.click(1, order, "click-1", merchant_prepare_id=str(prepare_id))
    assert (again["error"], again["error_note"]) == (rules.CLICK_ALREADY_PAID, "Already paid")
    assert pay.click(0, order, "click-1")["error"] == rules.CLICK_ALREADY_PAID
    assert subscription(owner, world.shop_a) == ("active", date(2050, 4, 9)), "completing twice pays once"
    assert len(told(owner, world)) == 1


def test_click_calls_with_a_wrong_signature_change_nothing(pay: Pay, world: World, owner: psycopg.Connection) -> None:
    order = pay.order_id(world)
    before = everything(owner, world)
    assert pay.click(0, order, "click-1", secret="another-secret")["error"] == rules.CLICK_BAD_SIGN
    assert pay.click(0, order, "click-1", secret="")["error"] == rules.CLICK_BAD_SIGN
    assert pay.click(0, order, "click-1", sign_string="0" * 32)["error"] == rules.CLICK_BAD_SIGN
    assert pay.click(0, order, "click-1", sign_string=" ")["error"] in (rules.CLICK_BAD_SIGN, rules.CLICK_BAD_REQUEST)
    assert everything(owner, world) == before

    # Each signed field is covered: what was signed for one request does not pass for another.
    good = rules.click_sign(
        click_trans_id="click-1",
        service_id=KEYS.click_service_id,
        secret=KEYS.click_key,
        merchant_trans_id=order,
        merchant_prepare_id=None,
        amount=str(PRICE),
        action="0",
        sign_time="2050-03-10 12:00:00",
    )
    other_order = pay.order_id(world)
    assert pay.click(0, other_order, "click-1", sign_string=good)["error"] == rules.CLICK_BAD_SIGN
    assert pay.click(0, order, "click-2", sign_string=good)["error"] == rules.CLICK_BAD_SIGN
    assert pay.click(0, order, "click-1", "1", sign_string=good)["error"] == rules.CLICK_BAD_SIGN
    assert pay.click(0, order, "click-1", sign_time="2050-03-10 12:00:01", sign_string=good)["error"] == -1
    assert pay.click(0, order, "click-1", service_id="70002", sign_string=good)["error"] == rules.CLICK_BAD_SIGN
    assert {row[0] for row in orders(owner, world)} == {"created"}

    # A prepare signature does not complete: completing also signs the identifier we gave.
    assert pay.click(0, order, "click-1", sign_string=good)["error"] == 0
    prepare_id = str(owner.execute("SELECT prepare_id FROM online_payment WHERE id = %s", (order,)).fetchone()[0])  # type: ignore[index]
    replay = pay.click(1, order, "click-1", merchant_prepare_id=prepare_id, sign_string=good)
    assert replay["error"] == rules.CLICK_BAD_SIGN
    as_prepare = pay.click(1, order, "click-1", merchant_prepare_id=prepare_id, signed_as="0")
    assert as_prepare["error"] == rules.CLICK_BAD_SIGN
    assert subscription(owner, world.shop_a) == ("limited", None)


def test_click_refuses_what_does_not_match_the_order(pay: Pay, world: World, owner: psycopg.Connection) -> None:
    order = pay.order_id(world)
    for amount in ("99999", "100000.01", "1000", "0", "-100000", "abc", "NaN", "sNaN", "Infinity", "1e5x"):
        assert pay.click(0, order, "click-1", amount)["error"] == rules.CLICK_WRONG_AMOUNT, amount
    assert pay.click(0, str(uuid.uuid4()), "click-1")["error"] == rules.CLICK_NO_ORDER
    assert pay.click(0, "not-an-id", "click-1")["error"] == rules.CLICK_NO_ORDER
    assert pay.click(2, order, "click-1")["error"] == rules.CLICK_NO_ACTION
    assert pay.click(0, order, "click-1", service_id="70002")["error"] == rules.CLICK_BAD_REQUEST
    assert orders(owner, world) == [("created", None, None, None, 1, PRICE)]
    for missing in ("click_trans_id", "service_id", "merchant_trans_id", "amount", "action", "sign_time"):
        given: dict[str, Any] = {"action": 0, "amount": str(PRICE)}
        blank = {missing: ""}
        if missing in given:
            given.update(blank)
            blank = {}
        answer = pay.click(given["action"], order, "click-1", given["amount"], **blank)
        assert answer["error"] == rules.CLICK_BAD_REQUEST, missing
    assert pay.client.post("/pay/click", data={}).json()["error"] == rules.CLICK_BAD_REQUEST
    assert pay.client.post("/pay/click", content=b"\xff\xfe").json()["error"] == rules.CLICK_BAD_REQUEST
    assert pay.client.post("/pay/click", content=b"a=1&" * 5_000).status_code == 413
    assert orders(owner, world) == [("created", None, None, None, 1, PRICE)]

    set_subscription(owner, world, "suspended")
    assert pay.click(0, order, "click-1")["error"] == rules.CLICK_NO_ORDER, "a suspended shop cannot pay"
    assert orders(owner, world)[0][0] == "created"


def test_click_completes_only_the_transaction_it_prepared(pay: Pay, world: World, owner: psycopg.Connection) -> None:
    order, other = pay.order_id(world), pay.order_id(world)
    ids = dict(owner.execute("SELECT id::text, prepare_id FROM online_payment").fetchall())
    assert pay.click(1, order, "click-1", merchant_prepare_id=str(ids[order]))["error"] == rules.CLICK_NO_TRANSACTION
    pay.click(0, order, "click-1")
    pay.click(0, other, "click-2")
    for txn, prepare in (("click-1", ids[other]), ("click-2", ids[order]), ("click-9", ids[order]), ("click-1", "")):
        answer = pay.click(1, order, txn, merchant_prepare_id=str(prepare))
        assert answer["error"] in (rules.CLICK_NO_TRANSACTION, rules.CLICK_BAD_REQUEST), (txn, prepare)
        assert "merchant_confirm_id" not in answer
    assert pay.click(0, order, "click-3")["error"] == rules.CLICK_BAD_REQUEST, "one transaction per order"
    assert subscription(owner, world.shop_a) == ("limited", None)
    assert [row[0] for row in orders(owner, world)] == ["pending", "pending"]


def test_a_click_payment_that_failed_on_its_side_is_cancelled(
    pay: Pay, world: World, owner: psycopg.Connection
) -> None:
    order = pay.order_id(world)
    prepare_id = str(owner.execute("SELECT prepare_id FROM online_payment WHERE id = %s", (order,)).fetchone()[0])  # type: ignore[index]
    pay.click(0, order, "click-1")
    assert pay.click(1, order, "click-1", merchant_prepare_id=prepare_id, error="x")["error"] == -8
    failed = pay.click(1, order, "click-1", merchant_prepare_id=prepare_id, error="-5017")
    assert (failed["error"], failed["error_note"]) == (rules.CLICK_CANCELLED, "Transaction cancelled")
    assert orders(owner, world) == [("cancelled", "click", "click-1", None, 1, PRICE)]
    assert pay.click(1, order, "click-1", merchant_prepare_id=prepare_id)["error"] == rules.CLICK_CANCELLED
    assert pay.click(0, order, "click-1")["error"] == rules.CLICK_CANCELLED
    assert subscription(owner, world.shop_a) == ("limited", None)
    assert told(owner, world) == []


def test_an_order_being_paid_through_one_provider_is_closed_to_the_other(
    pay: Pay, world: World, owner: psycopg.Connection
) -> None:
    by_payme, by_click = pay.order_id(world), pay.order_id(world)
    pay.create(by_payme, "same-id")
    pay.click(0, by_click, "same-id")
    ids = dict(owner.execute("SELECT id::text, prepare_id FROM online_payment").fetchall())

    assert pay.click(0, by_payme, "click-1")["error"] == rules.CLICK_BAD_REQUEST
    answer = pay.click(1, by_payme, "same-id", merchant_prepare_id=str(ids[by_payme]))
    assert answer["error"] == rules.CLICK_NO_TRANSACTION, "Payme's transaction is not Click's, whatever its name"
    assert code(pay.create(by_click, "payme-2")) == rules.PAYME_ORDER_BUSY
    # The same identifier at both providers names two different transactions, whichever came first.
    assert pay.payme("CheckTransaction", {"id": "same-id"})["result"]["transaction"] == by_payme
    click_first, payme_second = pay.order_id(world), pay.order_id(world)
    pay.click(0, click_first, "same-2")
    pay.create(payme_second, "same-2")
    assert pay.payme("CheckTransaction", {"id": "same-2"})["result"]["transaction"] == payme_second
    assert pay.payme("PerformTransaction", {"id": "same-2"})["result"]["transaction"] == payme_second
    assert pay.payme("CancelTransaction", {"id": "same-id", "reason": 1})["result"]["transaction"] == by_payme
    states = dict(owner.execute("SELECT id::text, state FROM online_payment").fetchall())
    assert (states[by_payme], states[by_click], states[click_first], states[payme_second]) == (
        "cancelled",
        "pending",
        "pending",
        "paid",
    )


def test_a_payment_reaches_only_the_shop_of_the_order(pay: Pay, world: World, owner: psycopg.Connection) -> None:
    set_subscription(owner, world, "limited")
    owner.execute(
        "UPDATE subscription SET state = 'limited', trial_ends = NULL, paid_through = NULL WHERE shop_id = %s",
        (world.shop_b,),
    )
    order = pay.order_id(world)
    pay.create(order, "payme-1")
    pay.payme("PerformTransaction", {"id": "payme-1"})
    assert subscription(owner, world.shop_a) == ("active", date(2050, 4, 9))
    assert subscription(owner, world.shop_b) == ("limited", None)
    assert owner.execute("SELECT count(*) FROM online_payment WHERE shop_id = %s", (world.shop_b,)).fetchone() == (0,)
    assert owner.execute(
        "SELECT count(*) FROM outbox_message WHERE shop_id = %s AND dedupe_key LIKE 'sub:paid_online:%%'",
        (world.shop_b,),
    ).fetchone() == (0,)

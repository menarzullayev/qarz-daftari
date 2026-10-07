"""The online payment table and its functions, used directly as the application role (migration 0019).

A provider names an order or its own transaction, never a shop, so three functions cross the tenant
boundary on purpose. Each must give no more than it says: which shop, or Payme's own transactions.
"""

import uuid
from typing import Any

import psycopg
import pytest

from ..conftest import AppSession, Shop

pytestmark = pytest.mark.db

FUNCTIONS = [
    "online_payment_shop(uuid)",
    "online_payment_shop_by_txn(text, text)",
    "payme_statement(bigint, bigint)",
]


def _order(owner: psycopg.Connection, shop: Shop, **columns: Any) -> uuid.UUID:
    order_id = uuid.uuid4()
    values = {"id": order_id, "shop_id": shop.shop_id, "months": 1, "amount": 100_000, **columns}
    names = ", ".join(values)
    marks = ", ".join(["%s"] * len(values))
    owner.execute(f"INSERT INTO online_payment ({names}) VALUES ({marks})", tuple(values.values()))
    return order_id


def _pending(owner: psycopg.Connection, shop: Shop, provider: str, txn: str, time: int | None = None) -> uuid.UUID:
    return _order(owner, shop, state="pending", provider=provider, provider_txn=txn, provider_time=time)


@pytest.mark.parametrize("function", FUNCTIONS)
def test_only_the_application_role_may_call_them(owner: psycopg.Connection, function: str) -> None:
    row = owner.execute(
        "SELECT has_function_privilege('qd_app', %s, 'EXECUTE'), has_function_privilege('public', %s, 'EXECUTE'), "
        "(SELECT prosecdef FROM pg_proc WHERE oid = %s::regprocedure)",
        (function, function, function),
    ).fetchone()
    assert row == (True, False, True)


def test_orders_are_seen_only_inside_their_shop(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop, shop_b: Shop
) -> None:
    mine, theirs = _order(owner, shop_a), _order(owner, shop_b)
    with as_app(None) as app:
        assert app.execute("SELECT count(*) FROM online_payment").fetchone() == (0,)
    with as_app(shop_a.shop_id) as app:
        assert app.execute("SELECT id FROM online_payment").fetchall() == [(mine,)]
        changed = app.execute("UPDATE online_payment SET amount = 1 WHERE id = %s", (theirs,))
        assert changed.rowcount == 0
    assert owner.execute("SELECT amount FROM online_payment WHERE id = %s", (theirs,)).fetchone() == (100_000,)


def test_the_application_cannot_put_an_order_into_another_shop(as_app: AppSession, shop_a: Shop, shop_b: Shop) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege), as_app(shop_a.shop_id) as app:
        app.execute(
            "INSERT INTO online_payment (id, shop_id, months, amount) VALUES (gen_random_uuid(), %s, 1, 100000)",
            (shop_b.shop_id,),
        )


def test_the_application_cannot_delete_an_order(owner: psycopg.Connection, as_app: AppSession, shop_a: Shop) -> None:
    order = _order(owner, shop_a)
    with pytest.raises(psycopg.errors.InsufficientPrivilege), as_app(shop_a.shop_id) as app:
        app.execute("DELETE FROM online_payment WHERE id = %s", (order,))


@pytest.mark.parametrize(
    "columns",
    [
        {"months": 0},
        {"months": 37},
        {"amount": 0},
        {"amount": -5},
        {"state": "refunded"},
        {"provider": "stripe", "state": "pending"},
        {"state": "pending"},  # being paid, but through nobody
        {"provider": "payme", "provider_txn": "t"},  # a provider on an order nobody started paying
        {"state": "paid", "provider": "payme", "provider_txn": "t"},  # paid without the moment it was paid
        {"state": "pending", "provider": "payme", "provider_txn": "t", "paid_at": "2050-01-01"},
    ],
)
def test_an_order_that_makes_no_sense_cannot_be_stored(
    owner: psycopg.Connection, shop_a: Shop, columns: dict[str, Any]
) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        _order(owner, shop_a, **columns)


def test_a_provider_transaction_belongs_to_one_order(owner: psycopg.Connection, shop_a: Shop, shop_b: Shop) -> None:
    _pending(owner, shop_a, "payme", "same")
    _pending(owner, shop_b, "click", "same")  # another provider's identifier is another transaction
    with pytest.raises(psycopg.errors.UniqueViolation):
        _pending(owner, shop_b, "payme", "same")


def test_the_lookups_say_which_shop_and_nothing_else(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop, shop_b: Shop
) -> None:
    created = _order(owner, shop_a)
    by_payme = _pending(owner, shop_b, "payme", f"p-{uuid.uuid4()}")
    txn = owner.execute("SELECT provider_txn FROM online_payment WHERE id = %s", (by_payme,)).fetchone()[0]  # type: ignore[index]
    with as_app(None) as app:
        assert app.execute("SELECT online_payment_shop(%s)", (created,)).fetchone() == (shop_a.shop_id,)
        assert app.execute("SELECT online_payment_shop(%s)", (by_payme,)).fetchone() == (shop_b.shop_id,)
        assert app.execute("SELECT online_payment_shop(%s)", (uuid.uuid4(),)).fetchone() == (None,)
        assert app.execute("SELECT online_payment_shop_by_txn('payme', %s)", (txn,)).fetchone() == (shop_b.shop_id,)
        assert app.execute("SELECT online_payment_shop_by_txn('click', %s)", (txn,)).fetchone() == (None,)
        assert app.execute("SELECT online_payment_shop_by_txn('payme', 'nobody')").fetchone() == (None,)
        assert app.execute("SELECT online_payment_shop_by_txn(NULL, NULL)").fetchone() == (None,)
        # Knowing the shop opens nothing: the rows stay behind the tenant boundary.
        assert app.execute("SELECT count(*) FROM online_payment").fetchone() == (0,)


def test_the_statement_holds_payme_transactions_of_the_period_from_every_shop(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop, shop_b: Shop
) -> None:
    base = (uuid.uuid4().int % 10**9) * 10_000 + 10**15  # a period of its own for this test
    first = _pending(owner, shop_a, "payme", f"a-{base}", base)
    second = _pending(owner, shop_b, "payme", f"b-{base}", base + 500)
    edge = _pending(owner, shop_a, "payme", f"c-{base}", base + 1000)
    _pending(owner, shop_a, "payme", f"d-{base}", base + 1001)  # after the period
    _pending(owner, shop_a, "payme", f"e-{base}", base - 1)  # before it
    _pending(owner, shop_b, "click", f"f-{base}", base + 10)  # Click's, though it carries a time
    _order(owner, shop_a)  # nobody started paying
    with as_app(None) as app:
        rows = app.execute("SELECT id, provider_txn, provider_time FROM payme_statement(%s, %s)", (base, base + 1000))
        assert rows.fetchall() == [
            (first, f"a-{base}", base),
            (second, f"b-{base}", base + 500),
            (edge, f"c-{base}", base + 1000),
        ]
        columns = [column.name for column in app.execute("SELECT * FROM payme_statement(0, 0)").description or []]
    # What Payme sent or was told, and no shop.
    assert columns == [
        "id",
        "provider_txn",
        "provider_time",
        "amount",
        "state",
        "cancel_reason",
        "started_at",
        "paid_at",
        "cancelled_at",
    ]

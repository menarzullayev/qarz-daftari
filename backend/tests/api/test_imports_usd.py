"""The import of a shop that works in dollars: an optional currency column (expansion module F; BR-43).

A row is so'm unless its currency cell says `USD`; a file without the column is so'm throughout. The
column exists only while the platform switch `usd_on` and the shop's own setting are both on: with either
off the template, the reading of a file, the preview and every answer are what they were before dollars,
and a file with a currency column is refused as an unknown column, as it always was.

In `world`, Ali (`customer_a`) owes 50 000 so'm and is linked to a Telegram account.
"""

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import timedelta
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from qarz.application.chat_texts import both, day, money, say
from qarz.application.imports import template
from qarz.domain import imports
from qarz.domain.money import Currency
from qarz.infrastructure.db import Database

from .conftest import World, as_user
from .test_customers_ledger import _subscription, key, shop, today, write
from .test_disputes import staff_notices, tg
from .test_imports import (
    _active,
    act,
    applied,
    apply,
    customer_id,
    customers,
    nothing_saved,
    plan_of,
    state,
    told,
    undone,
    upload,
    uploaded,
    work,
    worker,  # noqa: F401  (a fixture)
)
from .test_usd import dollars, keys_named, platform_on  # noqa: F401  (fixtures)

pytestmark = pytest.mark.db

USD = Currency.USD
HEAD = "Ism,Telefon,Qarz summasi,Valyuta,To'lash muddati,Izoh"


def table(*lines: str) -> bytes:
    return "\n".join([HEAD, *lines]).encode("utf-8")


def ledger(owner: psycopg.Connection, world: World, batch: Any) -> list[tuple[Any, ...]]:
    """Name, entry number, kind, amount and currency of what an import wrote, and of what reverses it."""
    return owner.execute(
        "SELECT c.display_name, e.seq, e.kind, e.amount, e.currency FROM ledger_entry e "
        "JOIN customer c ON c.id = e.customer_id LEFT JOIN ledger_entry o ON o.id = e.reverses_id "
        "WHERE e.shop_id = %s AND %s::uuid IN (e.import_batch_id, o.import_batch_id) ORDER BY c.display_name, e.seq",
        (world.shop_a, batch),
    ).fetchall()


def measured(owner: psycopg.Connection) -> dict[str, tuple[int, int]]:
    """How many opening balances were measured in each currency, and their sum."""
    rows = owner.execute(
        "SELECT m.currency, count(*), coalesce(sum(m.amount), 0) FROM measure.event m WHERE m.kind = 'opening' "
        "GROUP BY m.currency"
    ).fetchall()
    found = {str(currency): (int(number), int(total)) for currency, number, total in rows}
    return {currency: found.get(currency, (0, 0)) for currency in ("UZS", "USD")}


def mismatches(owner: psycopg.Connection, world: World) -> list[tuple[Any, ...]]:
    return owner.execute("SELECT * FROM open_debt_mismatches(%s)", (world.shop_a,)).fetchall()


def owed(client: TestClient, world: World, customer: Any) -> tuple[int, int]:
    """What a customer owes in so'm and in cents, as the API says it."""
    answer = client.get(f"{shop(world)}/customers/{customer}", headers=as_user(world.manager_a))
    assert answer.status_code == 200, answer.text
    return int(answer.json()["balance"]), int(answer.json()["usd"]["balance"])


def turn_dollars(client: TestClient, world: World, on: bool) -> None:
    answer = write(client, world.owner_a, "PATCH", shop(world), {"usd_on": on})
    assert answer.status_code == 200, answer.text


# --- off: the import is what it was ---------------------------------------------------------------------


def test_without_dollars_the_template_and_every_answer_of_an_import_are_what_they_were(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    """Neither switch is on. The template is the five-column file byte for byte; nothing has a dollar key."""
    got = client.get(f"{shop(world)}/imports/template", headers=as_user(world.manager_a))
    assert got.content == template("uz") and imports.read_xlsx(got.content)[0][1] == list(
        imports.TEMPLATE_HEADERS["uz"]
    )

    batch = uploaded(client, world, b"Ism,Telefon,Qarz summasi,To'lash muddati,Izoh\nKarim,,250000,,\nali,,70000,,")
    body = state(client, world, batch)
    assert body["preview"]["counts"] == {"new_customers": 1, "existing_customers": 1, "entries": 2, "amount": 320000}
    assert set(body["preview"]["rows"][0]) == {
        "row", "name", "phone", "amount", "promised_date", "note", "action", "matched_by", "same_as_row", "customer",
    }  # fmt: skip
    assert body["preview"]["rows"][1]["customer"] == {
        "id": str(world.customer_a),
        "display_name": "Ali",
        "phone": None,
        "balance": 50000,
    }
    assert apply(client, world, batch).status_code == 202
    assert work() == 1
    done = state(client, world, batch)
    assert done["applied"] == {"new_customers": 1, "existing_customers": 1, "entries": 2, "amount": 320000}
    listed = client.get(f"{shop(world)}/imports", headers=as_user(world.manager_a)).json()
    assert set(listed) == {"items"}, "the list is the list alone"
    assert keys_named([body, done, listed, undone(client, world, batch)]) == set()
    assert all("jami " + money("uz", 320000) + "." in text for _, text in told(owner, batch, "s_import_applied"))


@pytest.mark.parametrize("switches", ["neither", "platform only", "shop only"])
def test_a_currency_column_is_an_unknown_column_unless_both_switches_are_on(
    client: TestClient, world: World, owner: psycopg.Connection, switches: str
) -> None:
    """Today's behaviour exactly: the file is rejected whole, with the code it always had, and nothing is kept."""
    if switches == "platform only":
        owner.execute("INSERT INTO platform_setting (key, value, updated_by) VALUES ('usd_on', 'true', 'test')")
    if switches == "shop only":
        owner.execute("UPDATE shop SET usd_on = true WHERE id = %s", (world.shop_a,))
    try:
        before = nothing_saved(owner, world)
        batch = uploaded(client, world, table("Karim,,250000,UZS,,", "Lola,,12.50,USD,,"))
        body = state(client, world, batch)
        assert (body["status"], body["file_problem"], body["preview"]) == ("rejected", "unknown_column", None)
        assert keys_named(body) == set() and nothing_saved(owner, world) == before
        got = client.get(f"{shop(world)}/imports/template", headers=as_user(world.manager_a))
        assert got.content == template("uz"), "and the template has no currency column"
    finally:
        owner.execute("DELETE FROM platform_setting WHERE key = 'usd_on'")
        owner.execute("UPDATE shop SET usd_on = false WHERE id = %s", (world.shop_a,))


# --- on: the template, the check and the preview ---------------------------------------------------------


def test_a_dollar_shop_gets_the_template_with_the_currency_column_in_its_readers_language(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    dollars: None,  # noqa: F811
) -> None:
    got = client.get(f"{shop(world)}/imports/template", headers=as_user(world.manager_a))
    assert imports.read_xlsx(got.content) == [(1, HEAD.split(","))]
    listed = client.get(f"{shop(world)}/imports", headers=as_user(world.manager_a)).json()
    assert listed == {"items": [], "currency_column": True}, "the screen is told that the column exists"
    for lang in ("ru", "tg", "kaa", "en", "uz-Cyrl"):
        owner.execute("UPDATE app_user SET lang = %s WHERE id = %s", (lang, world.owner_a))
        own = client.get(f"{shop(world)}/imports/template", headers=as_user(world.owner_a)).content
        assert own == template(lang, dollars=True) != template(lang)
        assert imports.read_xlsx(own)[0][1][3] == imports.CURRENCY_TITLES[lang]
        # Whatever language it was downloaded in, it is read back.
        assert state(client, world, uploaded(client, world, own))["file_problem"] == "no_rows", lang


def test_the_preview_shows_each_rows_currency_and_one_total_for_each_currency(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    dollars: None,  # noqa: F811
) -> None:
    sold = write(
        client,
        world.manager_a,
        "POST",
        f"{shop(world)}/customers/{world.customer_a}/entries",
        {"kind": "credit", "amount": 700, "currency": "USD"},
    )
    assert sold.status_code == 201, sold.text
    before = nothing_saved(owner, world)
    batch = uploaded(
        client,
        world,
        table(
            "Karim,,250000,,,",  # an empty cell is so'm
            "Karim,,12.50,USD,,",  # the same new customer, in dollars
            "ali,,70000,uzs,,",
            'Ali,,"1 250,5",usd,,',
            "Lola,,0.01,USD,,",
        ),
    )
    body = state(client, world, batch)
    seen = body["preview"]
    assert (body["status"], seen["errors"]) == ("validated", [])
    # Never one figure of the two: 320 000 so'm, and 1 263.01 $ in cents.
    assert seen["counts"] == {
        "new_customers": 2,
        "existing_customers": 1,
        "entries": 5,
        "amount": 320000,
        "usd": {"amount": 1250 + 125050 + 1},
    }
    assert [(r["row"], r["amount"], r.get("currency"), r["action"], r["same_as_row"]) for r in seen["rows"]] == [
        (2, 250000, None, "create", None),
        (3, 1250, "USD", "same_as_row", 2),
        (4, 70000, None, "existing", None),
        (5, 125050, "USD", "existing", None),
        (6, 1, "USD", "create", None),
    ]
    assert "currency" not in seen["rows"][0], "so'm is the absence of the key, as everywhere"
    assert seen["rows"][3]["customer"] == {
        "id": str(world.customer_a),
        "display_name": "Ali",
        "phone": None,
        "balance": 50000,
        "usd": {"balance": 700},
    }
    assert nothing_saved(owner, world) == before


def test_the_problems_of_a_currency_and_of_a_dollar_amount_are_said_row_by_row_and_block_the_import(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    dollars: None,  # noqa: F811
) -> None:
    before = nothing_saved(owner, world)
    batch = uploaded(
        client,
        world,
        table(
            "Karim,,250000,EUR,,",
            "Lola,,12.505,USD,,",
            "Nodir,,10000.01,USD,,",
            "Olim,,0,USD,,",
            "Pari,,12.5,UZS,,",
            "Qodir,,45000,USD,,",  # a fine amount of so'm, far above the dollar bound
            "Rano,,12.50,USD,,",
        ),
    )
    body = state(client, world, batch)
    assert (body["status"], body["rows"], body["file_problem"]) == ("rejected", 7, None)
    assert body["errors"] == [
        {"row": 2, "column": "currency", "code": "currency_unknown"},
        {"row": 3, "column": "amount", "code": "amount_too_precise"},
        {"row": 4, "column": "amount", "code": "amount_too_large"},
        {"row": 5, "column": "amount", "code": "amount_too_small"},
        {"row": 6, "column": "amount", "code": "amount_not_whole"},
        {"row": 7, "column": "amount", "code": "amount_too_large"},
    ]
    assert body["preview"]["plan"] is None, "a file with a problem cannot be applied"
    assert act(client, world, batch, "apply", {"plan": "0" * 32}).status_code == 409
    assert nothing_saved(owner, world) == before


# --- on: applying and undoing ---------------------------------------------------------------------------


def test_applying_writes_each_opening_balance_in_its_rows_currency(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    dollars: None,  # noqa: F811
) -> None:
    due = today() + timedelta(days=5)
    measured_before = measured(owner)
    batch = applied(
        client,
        world,
        table(
            "Karim,,250000,,,",
            f"Karim,,12.50,USD,{due.strftime('%d.%m.%Y')},eski qarz",
            "ali,,70000,,,",
            "Ali,,8,USD,,",
        ),
    )
    # One customer appearing with two currencies has two opening entries, one in each book.
    assert ledger(owner, world, batch) == [
        ("Ali", 2, "opening", 70000, "UZS"),
        ("Ali", 3, "opening", 800, "USD"),
        ("Karim", 1, "opening", 250000, "UZS"),
        ("Karim", 2, "opening", 1250, "USD"),
    ]
    assert owed(client, world, world.customer_a) == (120000, 800)
    assert owed(client, world, customer_id(owner, world, "Karim")) == (250000, 1250)
    promise = owner.execute(
        "SELECT p.promised_date, p.actor FROM promise p JOIN ledger_entry e ON e.id = p.entry_id "
        "WHERE e.import_batch_id = %s AND e.currency = 'USD' AND e.amount = 1250",
        (batch,),
    ).fetchone()
    assert promise == (due, "staff")
    after = measured(owner)
    assert {currency: tuple(now - was for now, was in zip(after[currency], measured_before[currency], strict=True))
            for currency in ("USD", "UZS")} == {"USD": (2, 2050), "UZS": (2, 320000)}  # fmt: skip
    assert mismatches(owner, world) == [], "each book adds up by itself"

    done = state(client, world, batch)
    assert done["applied"] == {
        "new_customers": 1,
        "existing_customers": 1,
        "entries": 4,
        "amount": 320000,
        "usd": {"amount": 2050},
    }
    # Staff are told each currency's total by itself.
    said = both("uz", 320000, 2050)
    assert said == f"{money('uz', 320000)} va {money('uz', 2050, USD)}"
    assert {text for _, text in told(owner, batch, "s_import_applied")} == {
        f"📥 Shop A\nImport qo'llandi: 4 ta qarz yozuvi, jami {said}. "
        "Yangi mijozlar: 1 ta.\n24 soat ichida butunlay bekor qilish mumkin."
    }
    # The linked customer is told of each entry with the balance of its own currency.
    written = owner.execute("SELECT id FROM ledger_entry WHERE import_batch_id = %s", (batch,)).fetchall()
    texts = {payload["text"] for (entry,) in written for _, payload in staff_notices(owner, f"entry:{entry}:notify")}
    assert texts == {
        say(
            "uz",
            "n_opening",
            shop="Shop A",
            name="Ali",
            amount=money("uz", amount, currency),
            date=day(today() + timedelta(days=30)),
            balance=money("uz", balance, currency),
        )
        for amount, balance, currency in ((70000, 120000, Currency.UZS), (800, 800, USD))
    }


def test_an_undo_takes_back_each_currency_in_its_own_book(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    dollars: None,  # noqa: F811
) -> None:
    batch = applied(client, world, table("Karim,,250000,,,", "Karim,,12.50,USD,,", "ali,,70000,,,", "Ali,,8,USD,,"))
    body = undone(client, world, batch, world.owner_a)
    assert (body["status"], body["undone"]) == ("undone", {"reversed": 4, "archived": 1})
    assert [row for row in ledger(owner, world, batch) if row[2] == "reversal"] == [
        ("Ali", 4, "reversal", 70000, "UZS"),
        ("Ali", 5, "reversal", 800, "USD"),
        ("Karim", 3, "reversal", 250000, "UZS"),
        ("Karim", 4, "reversal", 1250, "USD"),
    ]
    assert owed(client, world, world.customer_a) == (50000, 0)
    assert ("Karim", "karim", None, "archived") in customers(owner, world)
    assert mismatches(owner, world) == []
    assert told(owner, batch, "s_import_undone") == [
        (
            tg(owner, world.owner_a),
            f"↩️ Shop A\nImport bekor qilindi: 4 ta yozuv qaytarildi. Import summasi: {both('uz', 320000, 2050)}.",
        )
    ]
    to_ali = owner.execute(
        "SELECT payload->>'text' FROM outbox_message WHERE shop_id = %s AND recipient = %s ORDER BY created_at, id",
        (world.shop_a, tg(owner, world.customer_of_a)),
    ).fetchall()
    assert {text for (text,) in to_ali[-2:]} == {
        say(
            "uz", "n_reversed_credit", shop="Shop A", name="Ali", amount=money("uz", 70000), balance=money("uz", 50000)
        ),
        say(
            "uz",
            "n_reversed_credit",
            shop="Shop A",
            name="Ali",
            amount=money("uz", 800, USD),
            balance=money("uz", 0, USD),
        ),
    }


def test_a_dollar_balance_that_was_paid_against_blocks_the_undo_whatever_the_sum_balance_is(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    dollars: None,  # noqa: F811
) -> None:
    """The so'm book alone would allow it: Ali owes far more so'm than the import gave him dollars."""
    batch = applied(client, world, table("Ali,,8,USD,,"))
    paid = write(
        client,
        world.manager_a,
        "POST",
        f"{shop(world)}/customers/{world.customer_a}/entries",
        {"kind": "payment", "amount": 300, "currency": "USD"},
    )
    assert paid.status_code == 201, paid.text
    body = undone(client, world, batch)
    assert (body["status"], body["refused"]) == ("applied", {"step": "undo", "reason": "balance_used"})
    assert [row for row in ledger(owner, world, batch) if row[2] == "reversal"] == []
    assert owed(client, world, world.customer_a) == (50000, 500)


def test_dollar_rows_are_not_applied_once_the_shop_has_stopped_working_in_dollars(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    dollars: None,  # noqa: F811
) -> None:
    batch = uploaded(client, world, table("Karim,,250000,,,", "Lola,,12.50,USD,,"))
    plan = plan_of(client, world, batch)
    before = nothing_saved(owner, world)
    turn_dollars(client, world, False)
    assert apply(client, world, batch, plan).status_code == 202
    assert work() == 1
    body = state(client, world, batch)
    assert (body["status"], body["refused"]) == ("validated", {"step": "apply", "reason": "usd_off"})
    assert nothing_saved(owner, world) == before, "not the so'm row either: an import is whole or not at all"
    assert ledger(owner, world, batch) == []
    # Working in dollars again, the same import with the same preview goes through.
    turn_dollars(client, world, True)
    assert apply(client, world, batch, plan).status_code == 202
    assert work() == 1
    assert state(client, world, batch)["status"] == "applied"
    assert [row[3:] for row in ledger(owner, world, batch)] == [(250000, "UZS"), (1250, "USD")]


def test_the_free_plan_counts_the_customers_of_a_dollar_import_like_any_other(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    dollars: None,  # noqa: F811
    free_plan: Any,
) -> None:
    _subscription(owner, world, "state = 'limited', trial_ends = NULL, paid_through = NULL")
    held = _active(owner, world) + 1
    free_plan(held)
    batch = uploaded(client, world, table("Karim,,12.50,USD,,", "Lola,,8,USD,,"))
    plan = plan_of(client, world, batch)
    refused = apply(client, world, batch, plan)
    assert (refused.status_code, refused.json()["error"]["code"]) == (402, "FREE_PLAN_FULL")
    free_plan(held + 1)
    assert apply(client, world, batch, plan).status_code == 202
    assert work() == 1
    assert state(client, world, batch)["status"] == "applied"
    assert _active(owner, world) == held + 1


def test_asking_twice_with_one_key_applies_a_dollar_import_once(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    dollars: None,  # noqa: F811
) -> None:
    data = table("Lola,,12.50,USD,,")
    once = key()
    first = upload(client, world, data, headers=once)
    again = upload(client, world, data, headers=once)
    assert first.status_code == again.status_code == 201 and first.json()["id"] == again.json()["id"]
    assert work() == 1
    batch = first.json()["id"]
    plan = plan_of(client, world, batch)
    asked = key()
    assert apply(client, world, batch, plan, headers=asked).status_code == 202
    assert apply(client, world, batch, plan, headers=asked).status_code == 202
    assert work() == 1 and work() == 0
    assert ledger(owner, world, batch) == [("Lola", 1, "opening", 1250, "USD")]


# --- the setting and an import being applied, at the same moment ---------------------------------------------


def waiting_for_a_lock(owner: psycopg.Connection, at_least: int = 1) -> None:
    """Return once that many sessions of this database wait for a lock; fail if they never do."""
    for _ in range(200):
        row = owner.execute(
            "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND wait_event_type = 'Lock'"
        ).fetchone()
        assert row is not None
        if row[0] >= at_least:
            return
        time.sleep(0.05)
    raise AssertionError("nothing came to wait for a lock")


def test_dollars_are_not_turned_off_under_an_import_that_is_writing_dollar_rows(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    database_url: str,
    dollars: None,  # noqa: F811
) -> None:
    """The owner turns the shop's dollars off in the very moment their own import with a dollar row is
    being applied. The worker cannot hold the shop's row as a request does (its role only reads it), so it
    holds the shop's lock for the setting, and turning dollars off waits for it. Here the worker is stopped
    in the middle, after it read the setting and before it wrote, by holding the customer it is about to
    lock: without the lock the setting is turned off at once and the import then writes a dollar debt into
    a shop without dollars."""
    batch = uploaded(client, world, table("Ali,,8,USD,,"))
    assert apply(client, world, batch).status_code == 202
    with psycopg.connect(database_url) as gate, ThreadPoolExecutor(max_workers=2) as pool:
        try:
            gate.execute("SELECT 1 FROM customer WHERE id = %s FOR UPDATE", (world.customer_a,))
            applying = pool.submit(work)
            waiting_for_a_lock(owner)  # the worker read "dollars are on" and now waits for Ali
            turning = pool.submit(write, client, world.owner_a, "PATCH", shop(world), {"usd_on": False})
            try:
                early = turning.result(timeout=2).status_code
            except FutureTimeout:
                early = None  # the owner's change waits for the worker, as it must
        finally:
            gate.rollback()  # let Ali go, whatever happened: the worker goes on
        assert applying.result(timeout=30) == 1
        answer = turning.result(timeout=30)
    assert early is None, f"dollars were turned off ({early}) while an import was writing a dollar row"
    # The import went first and wrote the dollar row; the change then found a dollar debt and was refused.
    assert state(client, world, batch)["status"] == "applied"
    assert ledger(owner, world, batch) == [("Ali", 2, "opening", 800, "USD")]
    assert (answer.status_code, answer.json()["error"]["code"]) == (409, "USD_BALANCE_OPEN")
    assert owner.execute("SELECT usd_on FROM shop WHERE id = %s", (world.shop_a,)).fetchone() == (True,)


def test_an_import_waits_for_a_change_of_the_setting_that_is_being_made_and_then_sees_it(
    world: World,
    owner: psycopg.Connection,
    app_database_url: str,
    worker_database_url: str,
    dollars: None,  # noqa: F811
) -> None:
    """The other order, at the storage: while a transaction that turned dollars off is still open, a writer
    that holds the setting the worker's way waits; when it is committed the writer reads "off". And two
    such writers do not wait for each other, nor does one that only reads."""

    async def run() -> tuple[bool, bool, bool]:
        api, worker_side = Database(app_database_url), Database(worker_database_url)
        try:
            async with worker_side.tenant(world.shop_a) as one, worker_side.tenant(world.shop_a) as two:
                together = (
                    await asyncio.wait_for(one.hold_dollars_setting(), 5),
                    await asyncio.wait_for(two.hold_dollars_setting(), 5),
                )
            assert together == (True, True)
            async with api.tenant(world.shop_a) as changing:
                await changing.set_dollars_setting(False)
                async with worker_side.tenant(world.shop_a) as reading:
                    assert await asyncio.wait_for(reading.dollars_setting(), 5) is True  # a reader never waits

                async def held() -> bool:
                    async with worker_side.tenant(world.shop_a) as writing:
                        return await writing.hold_dollars_setting()

                writer = asyncio.create_task(held())
                await asyncio.sleep(0.5)
                waited = not writer.done()
            return together[0], waited, await asyncio.wait_for(writer, 10)
        finally:
            await api.dispose()
            await worker_side.dispose()

    assert asyncio.run(run()) == (True, True, False)
    owner.execute("UPDATE shop SET usd_on = true WHERE id = %s", (world.shop_a,))

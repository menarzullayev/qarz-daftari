"""Reports for managers and owners: a period of Tashkent days, and overdue debt by age (story S13.1, REQ-046).

"Today" is always the Tashkent date worked out in Python, never the database server's `current_date`:
between 19:00 and 24:00 UTC the two differ.
"""

import random
import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from qarz.domain import ledger
from qarz.domain.ledger import Entry, EntryKind
from qarz.domain.promise import TASHKENT, tashkent_date

from .conftest import World, as_user
from .test_customers_ledger import _random_account, read, seed_customer, shop, today

pytestmark = pytest.mark.db

DAY = timedelta(days=1)
MICRO = timedelta(microseconds=1)
INVALID, AFTER_TO, FUTURE, TOO_LONG = "DATE_INVALID", "FROM_AFTER_TO", "IN_FUTURE", "PERIOD_TOO_LONG"


def local(day: date, hour: int = 12, minute: int = 0, second: int = 0, microsecond: int = 0) -> datetime:
    """An instant given as Tashkent wall time."""
    return datetime.combine(day, time(hour, minute, second, microsecond), tzinfo=TASHKENT)


def customer_at(owner: psycopg.Connection, shop_id: uuid.UUID, name: str, when: datetime) -> uuid.UUID:
    customer_id = uuid.uuid4()
    owner.execute(
        "INSERT INTO customer (id, shop_id, display_name, name_norm, created_at) VALUES (%s, %s, %s, %s, %s)",
        (customer_id, shop_id, name, name.lower(), when),
    )
    return customer_id


def promise_at(owner: psycopg.Connection, shop_id: uuid.UUID, entry: uuid.UUID, promised: date, when: datetime) -> None:
    owner.execute(
        "INSERT INTO promise (id, shop_id, entry_id, promised_date, actor, created_at) "
        "VALUES (%s, %s, %s, %s, 'staff', %s)",
        (uuid.uuid4(), shop_id, entry, promised, when),
    )


def entry_at(
    owner: psycopg.Connection,
    world: World,
    customer: uuid.UUID,
    seq: int,
    kind: str,
    amount: int,
    when: datetime,
    *,
    promised: date | None = None,
    reverses: uuid.UUID | None = None,
    author: uuid.UUID | None = None,
    shop_id: uuid.UUID | None = None,
) -> uuid.UUID:
    entry_id = uuid.uuid4()
    owner.execute(
        "INSERT INTO ledger_entry (id, shop_id, customer_id, seq, kind, amount, reverses_id, author_id, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            entry_id,
            shop_id or world.shop_a,
            customer,
            seq,
            kind,
            amount,
            reverses,
            author or world.seller_a_membership,
            when,
        ),
    )
    if kind in ("credit", "opening"):
        promise_at(owner, shop_id or world.shop_a, entry_id, promised or tashkent_date(when) + 30 * DAY, when)
    return entry_id


def dispute_at(owner: psycopg.Connection, shop_id: uuid.UUID, entry: uuid.UUID, when: datetime) -> None:
    owner.execute(
        "INSERT INTO dispute (id, shop_id, entry_id, reason, created_at) VALUES (%s, %s, %s, 'Men olmaganman', %s)",
        (uuid.uuid4(), shop_id, entry, when),
    )


def period(client: TestClient, user: uuid.UUID, shop_id: uuid.UUID, first: date, last: date) -> dict[str, Any]:
    response = client.get(
        f"/api/v1/shops/{shop_id}/reports/period",
        params={"from": first.isoformat(), "to": last.isoformat()},
        headers=as_user(user),
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def overdue(client: TestClient, user: uuid.UUID, shop_id: uuid.UUID) -> dict[str, Any]:
    response = client.get(f"/api/v1/shops/{shop_id}/reports/overdue", headers=as_user(user))
    assert response.status_code == 200, response.text
    return dict(response.json())


def reconciles(report: dict[str, Any]) -> bool:
    movement = report["credit"]["amount"] + report["opening"]["amount"] - report["payments"]["amount"]
    return bool(
        report["outstanding"]["start"] + movement == report["outstanding"]["end"]
        and report["net_change"] == movement
        and sum(day["credit"] for day in report["days"]) == report["credit"]["amount"]
        and sum(day["payments"] for day in report["days"]) == report["payments"]["amount"]
    )


def tally(amount: int, count: int) -> dict[str, int]:
    return {"amount": amount, "count": count}


# --- the period report ----------------------------------------------------------------------------------


def test_the_period_report_of_a_small_shop(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    base = today() - 20 * DAY
    seller, manager, boss = world.seller_a_membership, world.manager_a_membership, world.owner_a_membership
    karim = customer_at(owner, world.shop_a, "Karim", local(base - 5 * DAY))
    lola = customer_at(owner, world.shop_a, "Lola", local(base + DAY, 10))
    murod = customer_at(owner, world.shop_a, "Murod", local(base + 3 * DAY))

    # Before the period. The promise was first set outside the period and then moved to its first day:
    # the newest promise is the one that counts.
    old = entry_at(owner, world, karim, 1, "credit", 100_000, local(base - 3 * DAY), promised=base - 2 * DAY)
    promise_at(owner, world.shop_a, old, base, local(base - 3 * DAY, 13))
    # In the period.
    entry_at(owner, world, karim, 2, "payment", 30_000, local(base, 9), author=manager)
    disputed = entry_at(owner, world, karim, 3, "credit", 20_000, local(base, 10), promised=base + 10 * DAY)
    entry_at(owner, world, lola, 1, "credit", 50_000, local(base + DAY, 11), promised=base + 2 * DAY, author=manager)
    mistake = entry_at(
        owner, world, lola, 2, "credit", 7_000, local(base + DAY, 12), promised=base + 2 * DAY, author=boss
    )
    entry_at(owner, world, lola, 3, "reversal", 7_000, local(base + DAY, 13), reverses=mistake, author=manager)
    entry_at(owner, world, lola, 4, "payment", 50_000, local(base + 2 * DAY, 15))
    entry_at(owner, world, karim, 4, "payment", 10_000, local(base + 2 * DAY, 16))
    dispute_at(owner, world.shop_a, disputed, local(base + DAY, 18))
    # After the period.
    later = entry_at(owner, world, murod, 1, "credit", 5_000, local(base + 3 * DAY), promised=base + 4 * DAY)
    entry_at(owner, world, karim, 5, "payment", 60_000, local(base + 4 * DAY))
    dispute_at(owner, world.shop_a, later, local(base + 3 * DAY, 18))

    writes_before = owner.execute(
        "SELECT (SELECT count(*) FROM activity WHERE shop_id = %(s)s), (SELECT count(*) FROM measure.event)",
        {"s": world.shop_a},
    ).fetchone()
    report = period(client, world.manager_a, world.shop_a, base, base + 2 * DAY)
    assert report == {
        "from": base.isoformat(),
        "to": (base + 2 * DAY).isoformat(),
        "outstanding": {"start": 100_000, "end": 80_000},
        "credit": {"amount": 70_000, "count": 2, "customers": 2},
        "payments": {"amount": 90_000, "count": 3, "customers": 2},
        "opening": {"amount": 0, "count": 0},
        "net_change": -20_000,
        "reversals": {"amount": 7_000, "count": 1},
        "new_customers": 1,
        "disputes_opened": 1,
        # Karim's 100 000 fell due on the first day: 30 000 was paid by then, 10 000 two days late.
        # Lola's 50 000 fell due on the last day and was paid that day. The reversed 7 000 is no debt.
        "on_time": {"due_amount": 150_000, "on_time_amount": 80_000, "percent": 53},
        "days": [
            {"date": base.isoformat(), "credit": 20_000, "payments": 30_000},
            {"date": (base + DAY).isoformat(), "credit": 50_000, "payments": 0},
            {"date": (base + 2 * DAY).isoformat(), "credit": 0, "payments": 60_000},
        ],
        "top_debtors": [{"customer_id": str(karim), "display_name": "Karim", "balance": 80_000}],
        # The owner recorded only the sale that was reversed, so the owner recorded nothing that counts.
        "staff": [
            {
                "membership_id": str(manager),
                "role": "manager",
                "credit": tally(50_000, 1),
                "payments": tally(30_000, 1),
            },
            {"membership_id": str(seller), "role": "seller", "credit": tally(20_000, 1), "payments": tally(60_000, 2)},
        ],
    }
    assert reconciles(report)
    # Only debt promised within the period has fallen due in it: each of the two alone, then neither.
    shares = {
        (first, last): period(client, world.manager_a, world.shop_a, base + first * DAY, base + last * DAY)["on_time"]
        for first, last in ((0, 1), (1, 2), (1, 1))
    }
    assert shares == {
        (0, 1): {"due_amount": 100_000, "on_time_amount": 30_000, "percent": 30},
        (1, 2): {"due_amount": 50_000, "on_time_amount": 50_000, "percent": 100},
        (1, 1): {"due_amount": 0, "on_time_amount": 0, "percent": None},
    }
    # The owner sees the same; looking at a report writes nothing.
    assert period(client, world.owner_a, world.shop_a, base, base + 2 * DAY) == report
    writes_after = owner.execute(
        "SELECT (SELECT count(*) FROM activity WHERE shop_id = %(s)s), (SELECT count(*) FROM measure.event)",
        {"s": world.shop_a},
    ).fetchone()
    assert writes_after == writes_before


def test_a_day_ends_at_midnight_in_tashkent(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    day = today() - 9 * DAY
    last_instant, first_instant = local(day, 23, 59, 59, 999_999), local(day + DAY, 0, 0, 0)
    assert first_instant - last_instant == MICRO
    customer = customer_at(owner, world.shop_a, "Yarim tun", local(day - 30 * DAY))
    before = entry_at(owner, world, customer, 1, "credit", 11_000, last_instant)
    after = entry_at(owner, world, customer, 2, "credit", 22_000, first_instant)
    # 23:30 UTC on `day` is 04:30 on the next day in Tashkent.
    entry_at(owner, world, customer, 3, "payment", 5_000, datetime(day.year, day.month, day.day, 23, 30, tzinfo=UTC))
    customer_at(owner, world.shop_a, "Kech kelgan", last_instant)
    customer_at(owner, world.shop_a, "Erta kelgan", first_instant)
    dispute_at(owner, world.shop_a, before, last_instant)
    dispute_at(owner, world.shop_a, after, first_instant)
    undone = customer_at(owner, world.shop_a, "Bekor", local(day - 30 * DAY))
    late = entry_at(owner, world, undone, 1, "credit", 4_000, local(day - 5 * DAY))
    early = entry_at(owner, world, undone, 2, "credit", 3_000, local(day - 5 * DAY, 13))
    entry_at(owner, world, undone, 3, "reversal", 4_000, last_instant, reverses=late)
    entry_at(owner, world, undone, 4, "reversal", 3_000, first_instant, reverses=early)

    def figures(report: dict[str, Any]) -> tuple[Any, ...]:
        assert reconciles(report)
        return (
            report["outstanding"],
            report["credit"],
            report["payments"],
            report["reversals"],
            report["new_customers"],
            report["disputes_opened"],
            report["days"],
            [(member["role"], member["credit"], member["payments"]) for member in report["staff"]],
        )

    def one_day(which: date, credit: int, payments: int) -> list[dict[str, Any]]:
        return [{"date": which.isoformat(), "credit": credit, "payments": payments}]

    nothing = {"amount": 0, "count": 0, "customers": 0}
    assert figures(period(client, world.manager_a, world.shop_a, day - DAY, day - DAY)) == (
        {"start": 0, "end": 0},
        nothing,
        nothing,
        tally(0, 0),
        0,
        0,
        one_day(day - DAY, 0, 0),
        [],
    )
    assert figures(period(client, world.manager_a, world.shop_a, day, day)) == (
        {"start": 0, "end": 11_000},
        {"amount": 11_000, "count": 1, "customers": 1},
        nothing,
        tally(4_000, 1),
        1,
        1,
        one_day(day, 11_000, 0),
        [("seller", tally(11_000, 1), tally(0, 0))],
    )
    assert figures(period(client, world.manager_a, world.shop_a, day + DAY, day + DAY)) == (
        {"start": 11_000, "end": 28_000},
        {"amount": 22_000, "count": 1, "customers": 1},
        {"amount": 5_000, "count": 1, "customers": 1},
        tally(3_000, 1),
        1,
        1,
        one_day(day + DAY, 22_000, 5_000),
        [("seller", tally(22_000, 1), tally(5_000, 1))],
    )
    both = period(client, world.manager_a, world.shop_a, day, day + DAY)
    assert both["days"] == one_day(day, 11_000, 0) + one_day(day + DAY, 22_000, 5_000)
    assert (both["outstanding"], both["new_customers"]) == ({"start": 0, "end": 28_000}, 2)


def test_an_entry_and_its_reversal_cancel_out_wherever_each_falls(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    """A sale made in the period and reversed after it is not credit given in the period.

    The report shows the ledger as it is known now. The reversal itself is counted, once, in the period
    it was recorded in, so nothing disappears silently.
    """
    day = today() - 15 * DAY
    customer = customer_at(owner, world.shop_a, "Xato yozuv", local(day - 30 * DAY))
    wrong = entry_at(owner, world, customer, 1, "credit", 40_000, local(day, 10))
    entry_at(owner, world, customer, 2, "credit", 25_000, local(day, 11))
    paid = entry_at(owner, world, customer, 3, "payment", 10_000, local(day, 12))
    entry_at(owner, world, customer, 4, "reversal", 10_000, local(day + DAY, 10), reverses=paid)
    entry_at(owner, world, customer, 5, "reversal", 40_000, local(day + DAY, 11), reverses=wrong)

    first = period(client, world.manager_a, world.shop_a, day, day)
    assert first["outstanding"] == {"start": 0, "end": 25_000}
    assert first["credit"] == {"amount": 25_000, "count": 1, "customers": 1}
    assert first["payments"] == {"amount": 0, "count": 0, "customers": 0}
    assert first["reversals"] == tally(0, 0)
    assert first["days"] == [{"date": day.isoformat(), "credit": 25_000, "payments": 0}]
    assert first["staff"] == [
        {
            "membership_id": str(world.seller_a_membership),
            "role": "seller",
            "credit": tally(25_000, 1),
            "payments": tally(0, 0),
        }
    ]
    assert first["top_debtors"] == [{"customer_id": str(customer), "display_name": "Xato yozuv", "balance": 25_000}]

    second = period(client, world.manager_a, world.shop_a, day + DAY, day + DAY)
    assert second["outstanding"] == {"start": 25_000, "end": 25_000}
    assert (second["credit"]["amount"], second["payments"]["amount"]) == (0, 0)
    assert second["reversals"] == tally(50_000, 2)
    assert second["staff"] == []

    both = period(client, world.manager_a, world.shop_a, day, day + DAY)
    assert (both["credit"]["amount"], both["payments"]["amount"], both["reversals"]) == (25_000, 0, tally(50_000, 2))
    assert reconciles(first) and reconciles(second) and reconciles(both)


def test_a_payment_that_outlives_the_sale_it_paid_counts_from_the_day_it_was_made(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    """The one case where the balance of a past day, read with today's knowledge, is below what was owed.

    A sale is paid, a second sale follows, and the first sale is then reversed: the payment now covers the
    second sale (BR-3), although it was made before it. Between the payment and the second sale the
    corrected ledger holds a payment and no debt.
    """
    day = today() - 12 * DAY
    customer = customer_at(owner, world.shop_a, "Oldindan", local(day - 30 * DAY))
    first_sale = entry_at(owner, world, customer, 1, "credit", 10_000, local(day - 2 * DAY))
    entry_at(owner, world, customer, 2, "payment", 10_000, local(day - DAY))
    entry_at(owner, world, customer, 3, "credit", 10_000, local(day))
    entry_at(owner, world, customer, 4, "reversal", 10_000, local(day + DAY), reverses=first_sale)

    report = period(client, world.manager_a, world.shop_a, day, day)
    assert report["outstanding"] == {"start": -10_000, "end": 0}
    assert (report["credit"]["amount"], report["payments"]["amount"], report["net_change"]) == (10_000, 0, 10_000)
    assert report["top_debtors"] == []
    assert reconciles(report)


def test_an_opening_balance_is_shown_apart_from_credit_given(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    day = today() - 8 * DAY
    customer = customer_at(owner, world.shop_a, "Daftardan", local(day - 30 * DAY))
    entry_at(owner, world, customer, 1, "opening", 15_000, local(day - DAY), author=world.owner_a_membership)
    entry_at(owner, world, customer, 2, "opening", 40_000, local(day), promised=day, author=world.owner_a_membership)

    report = period(client, world.owner_a, world.shop_a, day, day)
    assert report["outstanding"] == {"start": 15_000, "end": 55_000}
    assert report["opening"] == tally(40_000, 1)
    assert report["credit"] == {"amount": 0, "count": 0, "customers": 0}
    assert (report["net_change"], report["staff"]) == (40_000, [])
    assert report["days"] == [{"date": day.isoformat(), "credit": 0, "payments": 0}]
    # An opening balance is debt like any other once its promised date has passed.
    assert report["on_time"] == {"due_amount": 40_000, "on_time_amount": 0, "percent": 0}
    assert reconciles(report)


def test_debt_promised_for_today_is_not_yet_counted_as_fallen_due(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    yesterday = today() - DAY
    customer = customer_at(owner, world.shop_a, "Muddatli", local(yesterday - 30 * DAY))
    entry_at(owner, world, customer, 1, "credit", 10_000, local(yesterday - 5 * DAY), promised=yesterday)
    entry_at(owner, world, customer, 2, "credit", 20_000, local(yesterday - 4 * DAY), promised=today())
    entry_at(owner, world, customer, 3, "payment", 4_000, local(yesterday, 23, 59, 59))
    entry_at(owner, world, customer, 4, "payment", 1_000, local(today(), 0, 0, 0))

    assert period(client, world.manager_a, world.shop_a, yesterday, today())["on_time"] == {
        "due_amount": 10_000,
        "on_time_amount": 4_000,
        "percent": 40,
    }
    assert period(client, world.manager_a, world.shop_a, today(), today())["on_time"] == {
        "due_amount": 0,
        "on_time_amount": 0,
        "percent": None,
    }


def test_the_largest_debtors_at_the_end_of_the_period(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    day = today() - 6 * DAY
    created = local(day - 30 * DAY)
    debtors = []
    for index in range(12):
        customer = customer_at(owner, world.shop_a, f"Qarzdor {index:02d}", created)
        entry_at(owner, world, customer, 1, "credit", (index + 1) * 10_000, local(day, 10))
        debtors.append(customer)
    # Paid in full the next day: still the largest debtor at the end of `day`.
    entry_at(owner, world, debtors[11], 2, "payment", 120_000, local(day + DAY, 9))
    settled = customer_at(owner, world.shop_a, "Uzgan", created)
    entry_at(owner, world, settled, 1, "credit", 900_000, local(day, 10))
    entry_at(owner, world, settled, 2, "payment", 900_000, local(day, 11))
    newcomer = customer_at(owner, world.shop_a, "Keyin", created)
    entry_at(owner, world, newcomer, 1, "credit", 500_000, local(day + DAY, 0, 0, 0))
    # Two who owe the same are listed in a fixed order, the one the debtors list uses.
    equal = sorted((customer_at(owner, world.shop_a, "Teng", created) for _ in range(2)), reverse=True)
    for customer in equal:
        entry_at(owner, world, customer, 1, "credit", 55_000, local(day, 10))
    removed = customer_at(owner, world.shop_a, "Mijoz 1", created)
    entry_at(owner, world, removed, 1, "credit", 800_000, local(day, 10))
    owner.execute("UPDATE customer SET status = 'anonymized' WHERE id = %s", (removed,))

    def listed(report: dict[str, Any]) -> list[tuple[str, int]]:
        assert all(set(item) == {"customer_id", "display_name", "balance"} for item in report["top_debtors"])
        return [(item["display_name"], item["balance"]) for item in report["top_debtors"]]

    at_the_end_of_day = period(client, world.manager_a, world.shop_a, day, day)
    tied = [("Teng", 55_000), ("Teng", 55_000)]
    assert listed(at_the_end_of_day) == (
        [(f"Qarzdor {index:02d}", (index + 1) * 10_000) for index in range(11, 4, -1)] + tied + [("Qarzdor 04", 50_000)]
    )
    assert at_the_end_of_day["top_debtors"][0]["customer_id"] == str(debtors[11])
    assert [item["customer_id"] for item in at_the_end_of_day["top_debtors"][7:9]] == [str(c) for c in equal]
    # The anonymized record is no longer named, but what it owes stays in the total (REQ-029).
    assert at_the_end_of_day["outstanding"]["end"] == sum(range(10_000, 130_000, 10_000)) + 110_000 + 800_000

    a_day_later = period(client, world.manager_a, world.shop_a, day, day + DAY)
    assert listed(a_day_later) == (
        [("Keyin", 500_000)]
        + [(f"Qarzdor {index:02d}", (index + 1) * 10_000) for index in range(10, 4, -1)]
        + tied
        + [("Qarzdor 04", 50_000)]
    )


# --- generated ledgers: the report against `qarz.domain.ledger` -----------------------------------------


def _instant(day: date) -> datetime:
    """Midnight in Tashkent, written out by hand: the day before at 19:00 UTC."""
    return datetime(day.year, day.month, day.day, tzinfo=UTC) - timedelta(hours=5)


def _balance_as_of(account: list[Entry], bound: datetime) -> int:
    """What the corrected ledger shows just before `bound`.

    The domain gives the balance as it was recorded then; a later reversal takes its target back out.
    """
    recorded = [entry for entry in account if entry.created_at < bound]
    by_id = {entry.id: entry for entry in recorded}
    result = ledger.balance(recorded)
    for entry in account:
        if entry.created_at >= bound and entry.reverses_id in by_id:
            target = by_id[entry.reverses_id]
            result += target.amount if target.kind is EntryKind.PAYMENT else -target.amount
    return result


def _seed_generated(
    owner: psycopg.Connection, world: World, rng: random.Random, count: int
) -> tuple[dict[uuid.UUID, list[Entry]], dict[uuid.UUID, uuid.UUID], dict[uuid.UUID, str]]:
    """Generated accounts in shop A. Returns them with the seeded sale of the world, entry authors, names."""
    members = [world.seller_a_membership, world.manager_a_membership, world.owner_a_membership]
    accounts: dict[uuid.UUID, list[Entry]] = {}
    authors: dict[uuid.UUID, uuid.UUID] = {}
    names: dict[uuid.UUID, str] = {}
    for index in range(count):
        name = f"Tasodifiy {index:02d}"
        customer = seed_customer(owner, world.shop_a, name)
        account = _random_account(rng, today() - rng.randint(0, 120) * DAY)
        for entry in account:
            authors[entry.id] = rng.choice(members)
            owner.execute(
                "INSERT INTO ledger_entry"
                " (id, shop_id, customer_id, seq, kind, amount, reverses_id, author_id, created_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    entry.id,
                    world.shop_a,
                    customer,
                    entry.seq,
                    entry.kind.value,
                    entry.amount,
                    entry.reverses_id,
                    authors[entry.id],
                    entry.created_at,
                ),
            )
            if entry.promised_date is not None:
                promise_at(owner, world.shop_a, entry.id, entry.promised_date, entry.created_at)
        accounts[customer] = account
        names[customer] = name
    # The sale every test world starts with.
    row = owner.execute(
        "SELECT e.created_at, p.promised_date FROM ledger_entry e JOIN promise p ON p.entry_id = e.id WHERE e.id = %s",
        (world.entry_a,),
    ).fetchone()
    assert row is not None
    accounts[world.customer_a] = [Entry(world.entry_a, 1, EntryKind.CREDIT, 50_000, row[0], None, row[1])]
    authors[world.entry_a] = world.seller_a_membership
    names[world.customer_a] = "Ali"
    return accounts, authors, names


def test_the_period_report_agrees_with_the_domain_rules_on_generated_ledgers(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    rng = random.Random(20261007)  # noqa: S311 - a fixed seed for repeatable test data, not a secret
    accounts, authors, names = _seed_generated(owner, world, rng, 40)
    customers_today = 40 + 3  # the generated ones and the three of the world were all created just now
    roles = {
        world.owner_a_membership: (0, "owner"),
        world.manager_a_membership: (1, "manager"),
        world.seller_a_membership: (2, "seller"),
    }

    periods = [(today() - 70 * DAY, today()), (today(), today()), (today() - 366 * DAY, today() - DAY)]
    for _ in range(25):
        first = today() - rng.randint(0, 65) * DAY
        periods.append((first, min(today(), first + rng.randint(0, 40) * DAY)))

    seen = {"reversed_later": 0, "on_time": 0, "late": 0, "start": 0}
    for first, last in periods:
        start, end = _instant(first), _instant(last + DAY)
        credit = payments = reversal_amount = reversal_count = on_time = due = on_time_again = due_again = 0
        credit_count = payment_count = 0
        borrowers: set[uuid.UUID] = set()
        payers: set[uuid.UUID] = set()
        by_day: dict[date, list[int]] = {first + offset * DAY: [0, 0] for offset in range((last - first).days + 1)}
        by_author: dict[uuid.UUID, list[int]] = {}
        balances_start = balances_end = 0
        at_end: list[tuple[int, uuid.UUID]] = []
        for customer, account in accounts.items():
            reversed_ids = {entry.reverses_id for entry in account if entry.reverses_id is not None}
            allocation = ledger.allocate(account)
            # The domain and this test agree on which sales still count.
            assert {a.entry_id for a in allocation} == {
                e.id for e in account if e.kind is EntryKind.CREDIT and e.id not in reversed_ids
            }
            for entry in account:
                if not start <= entry.created_at < end:
                    continue
                if entry.kind is EntryKind.REVERSAL:
                    reversal_amount += entry.amount
                    reversal_count += 1
                    continue
                if entry.id in reversed_ids:
                    reversed_later = next(e for e in account if e.reverses_id == entry.id)
                    seen["reversed_later"] += reversed_later.created_at >= end
                    continue
                is_credit = entry.kind is EntryKind.CREDIT
                credit += entry.amount if is_credit else 0
                payments += 0 if is_credit else entry.amount
                credit_count += is_credit
                payment_count += not is_credit
                (borrowers if is_credit else payers).add(customer)
                by_day[tashkent_date(entry.created_at)][0 if is_credit else 1] += entry.amount
                mine = by_author.setdefault(authors[entry.id], [0, 0, 0, 0])
                mine[0 if is_credit else 2] += entry.amount
                mine[1 if is_credit else 3] += 1
            # BR-9 as the domain states it: everything promised before a day, so a period is the
            # difference of two such days. Debt promised for today has not fallen due yet.
            until = min(last + DAY, today())
            if until > first:
                for history, sign in (
                    (ledger.payment_history(account, until), 1),
                    (ledger.payment_history(account, first), -1),
                ):
                    if history is not None:
                        on_time += sign * history.on_time_amount
                        due += sign * history.due_amount
            # The same read straight from the allocation, entry by entry.
            for a in allocation:
                if first <= a.promised_date <= last and a.promised_date < today():
                    due_again += a.amount
                    on_time_again += sum(p.amount for p in a.parts if tashkent_date(p.paid_at) <= a.promised_date)
            balances_start += _balance_as_of(account, start)
            balance_end = _balance_as_of(account, end)
            balances_end += balance_end
            if balance_end > 0:
                at_end.append((balance_end, customer))
        assert (on_time, due) == (on_time_again, due_again)
        seen["on_time"] += on_time
        seen["late"] += due - on_time
        seen["start"] += balances_start

        report = period(client, world.manager_a, world.shop_a, first, last)
        assert report == {
            "from": first.isoformat(),
            "to": last.isoformat(),
            "outstanding": {"start": balances_start, "end": balances_end},
            "credit": {"amount": credit, "count": credit_count, "customers": len(borrowers)},
            "payments": {"amount": payments, "count": payment_count, "customers": len(payers)},
            "opening": {"amount": 0, "count": 0},
            "net_change": credit - payments,
            "reversals": {"amount": reversal_amount, "count": reversal_count},
            "new_customers": customers_today if last == today() else 0,
            "disputes_opened": 0,
            "on_time": {
                "due_amount": due,
                "on_time_amount": on_time,
                "percent": None if due == 0 else (200 * on_time + due) // (2 * due),
            },
            "days": [{"date": d.isoformat(), "credit": c, "payments": p} for d, (c, p) in sorted(by_day.items())],
            "top_debtors": [
                {"customer_id": str(customer), "display_name": names[customer], "balance": balance}
                for balance, customer in sorted(at_end, reverse=True)[:10]
            ],
            "staff": [
                {
                    "membership_id": str(member),
                    "role": roles[member][1],
                    "credit": tally(figures[0], figures[1]),
                    "payments": tally(figures[2], figures[3]),
                }
                for member, figures in sorted(by_author.items(), key=lambda item: (roles[item[0]][0], item[0]))
            ],
        }, (first, last)
        assert reconciles(report), (first, last)

    assert seen["reversed_later"] > 0, "some sale or payment must be reversed after the period it was made in"
    assert seen["on_time"] > 0 and seen["late"] > 0 and seen["start"] > 0

    # A period that ends today ends where the overview stands.
    until_today = period(client, world.manager_a, world.shop_a, today() - 70 * DAY, today())
    overview = read(client, world.manager_a, f"{shop(world)}/overview").json()
    assert until_today["outstanding"]["end"] == overview["outstanding"]
    assert until_today["outstanding"]["end"] == sum(ledger.balance(account) for account in accounts.values())
    largest = read(client, world.manager_a, f"{shop(world)}/overview/debtors", limit=10).json()["items"]
    assert [(i["customer_id"], i["balance"]) for i in until_today["top_debtors"]] == [
        (i["id"], i["balance"]) for i in largest
    ]


# --- overdue by age -------------------------------------------------------------------------------------


def _band(band: str, first: int, last: int | None, amount: int, customers: int) -> dict[str, Any]:
    return {"band": band, "from_days": first, "to_days": last, "amount": amount, "customers": customers}


def test_overdue_debt_is_grouped_by_age_after_oldest_first_allocation(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    now, created = datetime.now(UTC), datetime.now(UTC) - 300 * DAY

    def ago(days: int) -> date:
        return today() - days * DAY

    partly_paid = customer_at(owner, world.shop_a, "Qisman", created)
    entry_at(owner, world, partly_paid, 1, "credit", 60_000, now - 200 * DAY, promised=ago(91))
    entry_at(owner, world, partly_paid, 2, "credit", 30_000, now - 199 * DAY, promised=ago(90))
    entry_at(owner, world, partly_paid, 3, "credit", 20_000, now - 198 * DAY, promised=ago(31))
    # Pays the oldest sale and a third of the next: 20 000 and 20 000 stay, both 31 to 90 days late.
    entry_at(owner, world, partly_paid, 4, "payment", 70_000, now - 10 * DAY)

    two_bands = customer_at(owner, world.shop_a, "Ikki guruh", created)
    for seq, (amount, days) in enumerate(
        [(5_000, 30), (6_000, 8), (7_000, 7), (8_000, 1), (9_000, 0), (1_000, -3)], start=1
    ):
        entry_at(owner, world, two_bands, seq, "credit", amount, now - (100 - seq) * DAY, promised=ago(days))

    disputing = customer_at(owner, world.shop_a, "E'tirozli", created)
    reversed_sale = entry_at(owner, world, disputing, 1, "credit", 99_000, now - 150 * DAY, promised=ago(100))
    entry_at(owner, world, disputing, 2, "reversal", 99_000, now - 149 * DAY, reverses=reversed_sale)
    disputed = entry_at(owner, world, disputing, 3, "credit", 11_000, now - 148 * DAY, promised=ago(91))
    dispute_at(owner, world.shop_a, disputed, now - 2 * DAY)

    settled = customer_at(owner, world.shop_a, "To'lagan", created)
    entry_at(owner, world, settled, 1, "credit", 4_000, now - 250 * DAY, promised=ago(200))
    entry_at(owner, world, settled, 2, "payment", 4_000, now - 5 * DAY)

    # A sale that somehow has no promised date cannot be late; the overview leaves it out the same way.
    no_promise = customer_at(owner, world.shop_a, "Va'dasiz", created)
    owner.execute(
        "INSERT INTO ledger_entry (id, shop_id, customer_id, seq, kind, amount, author_id, created_at) "
        "VALUES (%s, %s, %s, 1, 'credit', 13000, %s, %s)",
        (uuid.uuid4(), world.shop_a, no_promise, world.seller_a_membership, now - 120 * DAY),
    )

    report = overdue(client, world.manager_a, world.shop_a)
    assert report == {
        "as_of": today().isoformat(),
        "total": {"amount": 77_000, "customers": 3},
        "bands": [
            _band("1_7", 1, 7, 15_000, 1),
            _band("8_30", 8, 30, 11_000, 1),
            _band("31_90", 31, 90, 40_000, 1),
            _band("over_90", 91, None, 11_000, 1),  # under dispute, and counted as in the overview (BR-13)
        ],
    }
    assert overdue(client, world.owner_a, world.shop_a) == report
    assert read(client, world.manager_a, f"{shop(world)}/overview").json()["overdue"] == report["total"]


def test_the_overdue_report_agrees_with_the_domain_rules_on_generated_ledgers(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    rng = random.Random(7102026)  # noqa: S311 - a fixed seed for repeatable test data, not a secret
    accounts, _, _ = _seed_generated(owner, world, rng, 60)
    limits = [("1_7", 1, 7), ("8_30", 8, 30), ("31_90", 31, 90), ("over_90", 91, None)]
    amounts = {name: 0 for name, _, _ in limits}
    who: dict[str, set[uuid.UUID]] = {name: set() for name, _, _ in limits}
    for customer, account in accounts.items():
        for a in ledger.allocate(account):
            days = (today() - a.promised_date).days
            if a.remaining == 0 or days < 1:
                continue
            (name,) = [n for n, low, high in limits if low <= days and (high is None or days <= high)]
            amounts[name] += a.remaining
            who[name].add(customer)
        status = ledger.overdue(account, today())
        assert status.overdue_amount == sum(a.remaining for a in ledger.allocate(account) if a.promised_date < today())

    report = overdue(client, world.manager_a, world.shop_a)
    assert report["bands"] == [_band(name, low, high, amounts[name], len(who[name])) for name, low, high in limits]
    assert all(amounts[name] > 0 for name, _, _ in limits), "the generator must reach every band"
    everyone = set().union(*who.values())
    assert len(everyone) < sum(len(group) for group in who.values()), "someone must be in two bands at once"
    assert report["total"] == {"amount": sum(amounts.values()), "customers": len(everyone)}
    assert report["total"]["amount"] == sum(ledger.overdue(a, today()).overdue_amount for a in accounts.values())
    assert read(client, world.manager_a, f"{shop(world)}/overview").json()["overdue"] == report["total"]


# --- one shop never sees another's figures --------------------------------------------------------------


def test_a_report_shows_nothing_of_another_shop(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    day = today() - 4 * DAY
    mine = customer_at(owner, world.shop_a, "Bizniki", local(day - 60 * DAY))
    entry_at(owner, world, mine, 1, "credit", 30_000, local(day, 10), promised=day)
    before = (
        period(client, world.owner_a, world.shop_a, day - DAY, today()),
        overdue(client, world.owner_a, world.shop_a),
    )

    row = owner.execute("SELECT id FROM membership WHERE shop_id = %s", (world.shop_b,)).fetchone()
    assert row is not None
    author_b = row[0]
    theirs = customer_at(owner, world.shop_b, "Begona", local(day, 8))
    in_b = {"author": author_b, "shop_id": world.shop_b}
    sale = entry_at(owner, world, theirs, 1, "credit", 700_000, local(day, 10), promised=day, **in_b)
    entry_at(owner, world, theirs, 2, "payment", 200_000, local(day, 11), **in_b)
    undone = entry_at(owner, world, theirs, 3, "credit", 90_000, local(day, 12), promised=day, **in_b)
    entry_at(owner, world, theirs, 4, "reversal", 90_000, local(day, 13), reverses=undone, **in_b)
    dispute_at(owner, world.shop_b, sale, local(day, 14))

    assert (
        period(client, world.owner_a, world.shop_a, day - DAY, today()),
        overdue(client, world.owner_a, world.shop_a),
    ) == before
    assert "Begona" not in str(before) and str(author_b) not in str(before)

    report_b = period(client, world.owner_b, world.shop_b, day, day)
    assert report_b["outstanding"] == {"start": 0, "end": 500_000}
    assert (report_b["credit"], report_b["payments"]) == (
        {"amount": 700_000, "count": 1, "customers": 1},
        {"amount": 200_000, "count": 1, "customers": 1},
    )
    assert (report_b["reversals"], report_b["new_customers"], report_b["disputes_opened"]) == (tally(90_000, 1), 1, 1)
    assert report_b["on_time"] == {"due_amount": 700_000, "on_time_amount": 200_000, "percent": 29}
    assert report_b["top_debtors"] == [{"customer_id": str(theirs), "display_name": "Begona", "balance": 500_000}]
    assert [member["membership_id"] for member in report_b["staff"]] == [str(author_b)]
    overdue_b = overdue(client, world.owner_b, world.shop_b)
    assert overdue_b["total"] == {"amount": 500_000, "customers": 1}
    assert [band["amount"] for band in overdue_b["bands"]] == [500_000, 0, 0, 0]


# --- who may ask, and for what --------------------------------------------------------------------------


def _ask(client: TestClient, user: uuid.UUID, shop_id: uuid.UUID, params: dict[str, str]) -> Any:
    return client.get(f"/api/v1/shops/{shop_id}/reports/period", params=params, headers=as_user(user))


def _iso(offset: int) -> str:
    return (today() + offset * DAY).isoformat()


@pytest.mark.parametrize(
    ("offsets", "fields"),
    [
        ((None, None), {"from": INVALID, "to": INVALID}),
        ((-3, None), {"to": INVALID}),
        ((None, -3), {"from": INVALID}),
        ((-2, -3), {"from": AFTER_TO}),
        ((0, 1), {"to": FUTURE}),
        ((1, 1), {"to": FUTURE}),
        ((-366, 0), {"to": TOO_LONG}),
        ((-400, -30), {"to": TOO_LONG}),
    ],
)
def test_a_period_that_breaks_a_rule_is_refused(
    client: TestClient, world: World, offsets: tuple[int | None, int | None], fields: dict[str, str]
) -> None:
    params = {name: _iso(offset) for name, offset in zip(("from", "to"), offsets, strict=True) if offset is not None}
    response = _ask(client, world.owner_a, world.shop_a, params)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "VALIDATION"
    assert response.json()["error"]["fields"] == fields


@pytest.mark.parametrize(
    "written",
    ["", "yesterday", "2026-1-5", "20260105", "2026-02-30", "05.01.2026", "2026-01-05T00:00:00", "2026-01-05 "],
)
def test_a_date_must_be_written_as_year_month_day(client: TestClient, world: World, written: str) -> None:
    good = _iso(-3)
    for params, field in (({"from": written, "to": good}, "from"), ({"from": good, "to": written}, "to")):
        response = _ask(client, world.owner_a, world.shop_a, params)
        assert response.status_code == 422, response.text
        assert response.json()["error"]["fields"] == {field: INVALID}


@pytest.mark.parametrize("offsets", [(0, 0), (-1, 0), (-5, -5), (-365, 0), (-400, -35)])
def test_a_period_at_the_limits_is_accepted(client: TestClient, world: World, offsets: tuple[int, int]) -> None:
    """One day, today as the last day, and 366 days with both ends counted."""
    response = _ask(client, world.owner_a, world.shop_a, {"from": _iso(offsets[0]), "to": _iso(offsets[1])})
    assert response.status_code == 200, response.text
    assert len(response.json()["days"]) == offsets[1] - offsets[0] + 1


def test_who_is_asking_is_settled_before_the_period_is_looked_at(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    bad = {"from": "not-a-date", "to": _iso(5)}
    outsider = _ask(client, world.owner_b, world.shop_a, bad)
    assert (outsider.status_code, outsider.json()["error"]["code"]) == (404, "NOT_FOUND")
    assert outsider.json() == _ask(client, world.owner_b, uuid.uuid4(), bad).json()
    seller = _ask(client, world.seller_a, world.shop_a, bad)
    assert (seller.status_code, seller.json()["error"]["code"]) == (403, "FORBIDDEN_ROLE")
    assert seller.json()["error"]["fields"] == {"needed_role": "manager"}
    assert _ask(client, world.manager_a, world.shop_a, bad).status_code == 422

    # BR-30: in a suspended shop only the owner may look, and that too is settled before the dates.
    owner.execute("UPDATE subscription SET state = 'suspended' WHERE shop_id = %s", (world.shop_a,))
    good = {"from": _iso(-7), "to": _iso(0)}
    for params in (bad, good):
        manager = _ask(client, world.manager_a, world.shop_a, params)
        assert (manager.status_code, manager.json()["error"]["code"]) == (403, "SHOP_SUSPENDED")
    assert _ask(client, world.owner_a, world.shop_a, bad).status_code == 422
    assert _ask(client, world.owner_a, world.shop_a, good).status_code == 200

    path = f"{shop(world)}/reports/overdue"
    refused = read(client, world.manager_a, path)
    assert (refused.status_code, refused.json()["error"]["code"]) == (403, "SHOP_SUSPENDED")
    assert read(client, world.owner_a, path).status_code == 200
    assert read(client, world.seller_a, path).status_code == 403


def test_staff_of_one_role_are_listed_in_a_fixed_order(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    day = today() - 3 * DAY
    sellers = [world.seller_a_membership]
    for _ in range(2):
        user_id, membership_id = uuid.uuid4(), uuid.uuid4()
        owner.execute("INSERT INTO app_user (id, tg_id) VALUES (%s, %s)", (user_id, uuid.uuid4().int % 10**15))
        owner.execute(
            "INSERT INTO membership (id, shop_id, user_id, role) VALUES (%s, %s, %s, 'seller')",
            (membership_id, world.shop_a, user_id),
        )
        sellers.append(membership_id)
    customer = customer_at(owner, world.shop_a, "Uch sotuvchi", local(day - 30 * DAY))
    for seq, seller in enumerate(sellers, start=1):
        entry_at(owner, world, customer, seq, "credit", seq * 1_000, local(day, 9 + seq), author=seller)

    staff = period(client, world.owner_a, world.shop_a, day, day)["staff"]
    assert [member["membership_id"] for member in staff] == [str(seller) for seller in sorted(sellers)]
    assert {member["membership_id"]: member["credit"] for member in staff} == {
        str(seller): tally(seq * 1_000, 1) for seq, seller in enumerate(sellers, start=1)
    }

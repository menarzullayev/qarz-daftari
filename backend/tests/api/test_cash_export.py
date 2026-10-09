"""One period of the cash book as a workbook (expansion module H): `POST .../cash/export`.

Two sheets, read back here with the suite's own reader: the summary (balances by method, what stands by
category) and every entry of the period, cancelled ones marked. Each rule has the case that works and
the case that is refused. With the switch off the route does not exist (test_cash_book.py lists it among
the module's routes); who may call it by role is in the authorization suite.

In `world`, Ali (`customer_a`) owes 50 000 so'm.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from qarz.domain import cash

from .. import xlsx_reader
from .conftest import World, as_user, set_overrides, stored_objects, switch_permissions_on
from .test_cash_book import actions, added, base, cancel, refused, summary, switch
from .test_customers_ledger import _subscription, key, record, reverse, shop, today, write
from .test_usd import dollars, platform_on  # noqa: F401  (fixtures)

pytestmark = pytest.mark.db

SUMMARY, ENTRIES = "Hisobot", "Kassa"
HEADER = [
    "Kun",
    "Yozilgan vaqt",
    "Yo'nalish",
    "To'lov usuli",
    "Valyuta",
    "Summa",
    "Toifa",
    "Izoh",
    "Mijoz",
    "Manba",
    "Bekor qilinganmi",
    "Bekor qilish sababi",
    "Bekor qilingan vaqt",
    "Xodim ID",
    "Yozuv ID",
]
WITHOUT_NAMES = [title for title in HEADER if title != "Mijoz"]


@pytest.fixture
def on(client: TestClient, owner: psycopg.Connection) -> None:
    switch(owner)


def ask(client: TestClient, world: World, first: Any, last: Any, user: uuid.UUID | None = None, **headers: str) -> Any:
    body = {"from": str(first), "to": str(last)}
    sent = {**as_user(user or world.manager_a), **(headers or key())}
    return client.post(f"{base(world)}/export", json=body, headers=sent)


def book(client: TestClient, world: World, first: date, last: date, user: uuid.UUID | None = None) -> dict[str, Any]:
    """The workbook of a period, fetched the way a front end does: ask, then follow the link with no
    credentials at all."""
    answer = ask(client, world, first, last, user)
    assert answer.status_code == 201, answer.text
    assert set(answer.json()) == {"from", "to", "entries", "url", "expires_at"}
    assert answer.json()["url"].startswith("/files/")
    served = client.get(answer.json()["url"])
    assert served.status_code == 200, served.text
    assert served.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert served.headers["content-disposition"].startswith('attachment; filename="export-')
    sheets = xlsx_reader.read(served.content)
    assert list(sheets) == [SUMMARY, ENTRIES]
    assert len(sheets[ENTRIES]) - 1 == answer.json()["entries"]
    return sheets


def cells(row: list[Any], width: int) -> list[Any]:
    """A row with its empty last cells, which the file does not carry."""
    return [*row, *([None] * (width - len(row)))]


def listed(sheets: dict[str, Any], *columns: str) -> list[tuple[Any, ...]]:
    """The entries sheet, by the titles of the columns asked for."""
    titles = sheets[ENTRIES][0]
    picked = [titles.index(column) for column in columns]
    return [tuple(cells(row, len(titles))[index] for index in picked) for row in sheets[ENTRIES][1:]]


def section(sheets: dict[str, Any], title: str) -> list[list[Any]]:
    """The rows of the summary under a title, without their header, up to the next empty row."""
    rows = sheets[SUMMARY]
    start = next(index for index, row in enumerate(rows) if row and row[0] == title and len(row) == 1)
    found: list[list[Any]] = []
    for row in rows[start + 2 :]:
        if not row or all(cell is None for cell in row):
            break
        found.append(row)
    return found


def days_back(*offsets: int) -> list[date]:
    return [today() - timedelta(days=offset) for offset in offsets]


# --- what the workbook holds ---------------------------------------------------------------------------


def test_a_period_is_two_sheets_the_entries_and_what_they_come_to(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    before, first, last, after = days_back(3, 2, 1, 0)
    added(client, world, "income", 1_000_000, name="Boshlang'ich qoldiq", day=before.isoformat())
    sale = added(client, world, "income", 500_000, day=first.isoformat(), note="Ertalabki savdo")
    added(client, world, "income", 200_000, method="card", day=first.isoformat())
    added(client, world, "expense", 300_000, name="Ijara", method="transfer", day=last.isoformat())
    added(client, world, "expense", 50_000, name="Transport", day=last.isoformat())
    wrong = added(client, world, "expense", 999_000, name="Ish haqi", day=last.isoformat())
    assert cancel(client, world, wrong["id"], "Ikki marta yozilgan").status_code == 201
    added(client, world, "income", 100_000, day=after.isoformat())

    sheets = book(client, world, first, last)
    assert sheets[ENTRIES][0] == HEADER
    assert listed(sheets, "Kun", "Yo'nalish", "To'lov usuli", "Valyuta", "Summa", "Toifa", "Manba") == [
        (first.isoformat(), "Kirim", "Naqd", "UZS", 500_000, "Savdo", "Qo'lda yozilgan"),
        (first.isoformat(), "Kirim", "Karta", "UZS", 200_000, "Savdo", "Qo'lda yozilgan"),
        (last.isoformat(), "Chiqim", "O'tkazma", "UZS", 300_000, "Ijara", "Qo'lda yozilgan"),
        (last.isoformat(), "Chiqim", "Naqd", "UZS", 50_000, "Transport", "Qo'lda yozilgan"),
        (last.isoformat(), "Chiqim", "Naqd", "UZS", 999_000, "Ish haqi", "Qo'lda yozilgan"),
    ]
    assert listed(sheets, "Izoh", "Yozuv ID", "Xodim ID")[0] == (
        "Ertalabki savdo",
        sale["id"],
        str(world.manager_a_membership),
    )

    head = {row[0]: row[1] for row in sheets[SUMMARY][:5]}
    assert head["Do'kon"] == "Shop A"
    assert (head["Davr boshi"], head["Davr oxiri"]) == (first.isoformat(), last.isoformat())
    assert head["Yozuvlar soni (bekor qilinganlari bilan)"] == 5
    # What each method opened the period with, took in, paid out and closed it with: the entry of the day
    # before is the opening balance, the cancelled one is in no figure, the day after is not there at all.
    assert section(sheets, "Qoldiqlar") == [
        ["UZS", "Naqd", 1_000_000, 500_000, 50_000, 1_450_000, 2],
        ["UZS", "Karta", 0, 200_000, 0, 200_000, 1],
        ["UZS", "O'tkazma", 0, 0, 300_000, -300_000, 1],
        ["UZS", "Jami", 1_000_000, 700_000, 350_000, 1_350_000, 4],
    ]
    assert section(sheets, "Toifalar bo'yicha") == [
        ["UZS", "Kirim", "Savdo", 700_000, 2],
        ["UZS", "Chiqim", "Ijara", 300_000, 1],
        ["UZS", "Chiqim", "Transport", 50_000, 1],
    ]
    # The file says what the screen says for the same period.
    report = summary(client, world, first, last)
    assert [
        [line["currency"], line["opening"], line["income"], line["expense"], line["closing"], line["count"]]
        for line in report["balances"]
    ] == [[row[0], *row[2:]] for row in section(sheets, "Qoldiqlar")[:3]]


def test_a_cancelled_entry_is_listed_and_marked_with_its_reason(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    kept = added(client, world, "income", 40_000)
    wrong = added(client, world, "expense", 70_000)
    assert cancel(client, world, wrong["id"], "Summasi xato").status_code == 201
    paid = record(client, world, world.customer_a, "payment", 20_000)
    assert paid.status_code == 201, paid.text
    assert reverse(client, world, paid.json()["entry"]["id"]).status_code == 201

    sheets = book(client, world, today(), today())
    marks = {row[0]: row[1:] for row in listed(sheets, "Yozuv ID", "Bekor qilinganmi", "Bekor qilish sababi", "Manba")}
    assert marks[kept["id"]] == ("Yo'q", None, "Qo'lda yozilgan")
    assert marks[wrong["id"]] == ("Ha", "Summasi xato", "Qo'lda yozilgan")
    # The ledger cancels its entry without a reason: the file says what happened instead.
    (of_ledger,) = [mark for entry, mark in marks.items() if entry not in (kept["id"], wrong["id"])]
    assert of_ledger == ("Ha", "Mijozning to'lovi bekor qilindi", "Mijoz to'lovi")
    when = dict(listed(sheets, "Yozuv ID", "Bekor qilingan vaqt"))
    assert when[kept["id"]] is None
    assert datetime.strptime(when[wrong["id"]], "%Y-%m-%d %H:%M").date() in (today(), today() - timedelta(days=1))
    # Only what stands is added up.
    assert [row[2:] for row in section(sheets, "Qoldiqlar")] == [
        [0, 40_000, 0, 40_000, 1],
        [0, 0, 0, 0, 0],
        [0, 0, 0, 0, 0],
        [0, 40_000, 0, 40_000, 1],
    ]
    assert section(sheets, "Toifalar bo'yicha") == [["UZS", "Kirim", "Savdo", 40_000, 1]]


def test_the_counterpart_an_entry_that_is_not_cancelled_would_change_the_figures(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    """The check above is not satisfied by a sheet that simply leaves expenses out."""
    added(client, world, "income", 40_000)
    added(client, world, "expense", 70_000)
    sheets = book(client, world, today(), today())
    assert section(sheets, "Qoldiqlar")[0] == ["UZS", "Naqd", 0, 40_000, 70_000, -30_000, 2]
    assert set(listed(sheets, "Bekor qilinganmi")) == {("Yo'q",)}


def test_so_m_and_dollars_are_never_added_together(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    on: None,
    dollars: None,  # noqa: F811
) -> None:
    added(client, world, "income", 500_000)
    added(client, world, "income", 125_050, currency="USD", method="card")
    added(client, world, "expense", 2_000, name="Ijara", currency="USD", method="card")
    added(client, world, "expense", 100_000, name="Ijara")

    sheets = book(client, world, today(), today())
    assert listed(sheets, "Valyuta", "Summa") == [
        ("UZS", 500_000),
        ("USD", Decimal("1250.50")),
        ("USD", Decimal("20.00")),
        ("UZS", 100_000),
    ]
    assert section(sheets, "Qoldiqlar") == [
        ["UZS", "Naqd", 0, 500_000, 100_000, 400_000, 2],
        ["UZS", "Karta", 0, 0, 0, 0, 0],
        ["UZS", "O'tkazma", 0, 0, 0, 0, 0],
        ["UZS", "Jami", 0, 500_000, 100_000, 400_000, 2],
        ["USD", "Naqd", 0, 0, 0, 0, 0],
        ["USD", "Karta", 0, Decimal("1250.50"), Decimal("20.00"), Decimal("1230.50"), 2],
        ["USD", "O'tkazma", 0, 0, 0, 0, 0],
        ["USD", "Jami", 0, Decimal("1250.50"), Decimal("20.00"), Decimal("1230.50"), 2],
    ]
    assert section(sheets, "Toifalar bo'yicha") == [
        ["UZS", "Kirim", "Savdo", 500_000, 1],
        ["UZS", "Chiqim", "Ijara", 100_000, 1],
        ["USD", "Kirim", "Savdo", Decimal("1250.50"), 1],
        ["USD", "Chiqim", "Ijara", Decimal("20.00"), 1],
    ]
    # No cell anywhere holds a figure that so'm and dollars would add up to.
    mixed = {500_000 + 125_050, 400_000 + 123_050, 625_050, Decimal("501250.50"), Decimal("401230.50")}
    assert not mixed & {cell for rows in sheets.values() for row in rows for cell in row}


def test_a_shop_without_dollars_has_no_dollar_line(client: TestClient, world: World, on: None) -> None:
    added(client, world, "income", 500_000)
    sheets = book(client, world, today(), today())
    assert {row[0] for row in section(sheets, "Qoldiqlar")} == {"UZS"}


# --- the period ------------------------------------------------------------------------------------------


def test_only_the_days_of_the_period_are_in_the_file(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    """An entry dated the day before the period or the day after it is absent; the first and the last
    day themselves are in."""
    before, first, last, after = days_back(4, 3, 1, 0)
    for day, amount in ((before, 1_000), (first, 2_000), (last, 3_000), (after, 4_000)):
        added(client, world, "income", amount, day=day.isoformat())
    assert listed(book(client, world, first, last), "Kun", "Summa") == [
        (first.isoformat(), 2_000),
        (last.isoformat(), 3_000),
    ]
    assert listed(book(client, world, before, before), "Summa") == [(1_000,)]
    assert listed(book(client, world, after, after), "Summa") == [(4_000,)]
    assert listed(book(client, world, before, after), "Summa") == [(1_000,), (2_000,), (3_000,), (4_000,)]
    # A period nothing is dated in is a workbook with its headers and no entry.
    empty = book(client, world, first + timedelta(days=1), first + timedelta(days=1))
    assert empty[ENTRIES] == [HEADER]
    assert section(empty, "Toifalar bo'yicha") == []
    assert section(empty, "Qoldiqlar")[0] == ["UZS", "Naqd", 3_000, 0, 0, 3_000, 0]


@pytest.mark.parametrize(
    ("first", "last", "fields"),
    [
        ("yesterday", "2026-01-10", {"from": "DATE_INVALID"}),
        ("2026-01-10", "10.01.2026", {"to": "DATE_INVALID"}),
        ("2026-02-01", "2026-01-01", {"from": "FROM_AFTER_TO"}),
        ("2026-01-01", "2999-01-01", {"to": "IN_FUTURE"}),
        ("2024-12-31", "2026-01-01", {"to": "PERIOD_TOO_LONG"}),
    ],
)
def test_a_period_that_is_not_valid_is_refused_and_nothing_is_kept(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    file_root: Path,
    on: None,
    first: str,
    last: str,
    fields: dict[str, str],
) -> None:
    assert refused(ask(client, world, first, last), 422, "VALIDATION") == fields
    assert stored_objects(file_root) == []
    assert actions(owner, world) == []


def test_the_longest_period_is_366_days(client: TestClient, world: World, on: None) -> None:
    last = today()
    assert ask(client, world, last - timedelta(days=365), last).status_code == 201
    assert refused(ask(client, world, last - timedelta(days=366), last), 422, "VALIDATION") == {"to": "PERIOD_TOO_LONG"}


@pytest.mark.parametrize("body", [{}, {"from": "2026-01-01"}, {"from": "2026-01-01", "to": "2026-01-02", "x": 1}])
def test_a_request_that_does_not_name_exactly_a_period_is_refused(
    client: TestClient, world: World, on: None, body: dict[str, Any]
) -> None:
    answer = client.post(f"{base(world)}/export", json=body, headers={**as_user(world.manager_a), **key()})
    assert (answer.status_code, answer.json()["error"]["code"]) == (422, "VALIDATION")


def test_a_period_with_more_entries_than_a_workbook_takes_is_refused_and_a_shorter_one_is_written(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    file_root: Path,
    on: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    yesterday, now = days_back(1, 0)
    for day in (yesterday, yesterday, now, now):
        added(client, world, "income", 1_000, day=day.isoformat())
    monkeypatch.setattr(cash, "MAX_EXPORT_ENTRIES", 3)
    assert refused(ask(client, world, yesterday, now), 422, "VALIDATION") == {"to": "TOO_MANY_ENTRIES"}
    assert stored_objects(file_root) == []
    assert len(listed(book(client, world, now, now), "Summa")) == 2
    monkeypatch.setattr(cash, "MAX_EXPORT_ENTRIES", 4)
    assert len(listed(book(client, world, yesterday, now), "Summa")) == 4


def test_a_period_longer_than_one_page_is_whole_and_in_order(
    client: TestClient, world: World, owner: psycopg.Connection, on: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from qarz.application import cash_export

    monkeypatch.setattr(cash_export, "PAGE", 2)
    amounts = [1_000, 2_000, 3_000, 4_000, 5_000]
    for amount in amounts:
        added(client, world, "income", amount)
    sheets = book(client, world, today(), today())
    assert [row[0] for row in listed(sheets, "Summa")] == amounts
    assert section(sheets, "Qoldiqlar")[0] == ["UZS", "Naqd", 0, 15_000, 0, 15_000, 5]


# --- who may, and what they are shown ------------------------------------------------------------------


def test_a_seller_may_not_export_the_book_unless_given_the_right_to_read_it(
    client: TestClient, world: World, owner: psycopg.Connection, file_root: Path, on: None
) -> None:
    added(client, world, "income", 40_000)
    denied = ask(client, world, today(), today(), world.seller_a)
    assert (denied.status_code, denied.json()["error"]["code"]) == (403, "FORBIDDEN_ROLE")
    assert stored_objects(file_root) == []
    assert "cash.exported" not in actions(owner, world)
    # The right to read the book is the right to take a period of it away: nothing more is asked.
    switch_permissions_on(owner)
    set_overrides(owner, world.seller_a_membership, granted=["cash.view"])
    assert listed(book(client, world, today(), today(), world.seller_a), "Summa") == [(40_000,)]
    # And without it nobody exports, whatever else they hold.
    set_overrides(owner, world.manager_a_membership, denied=["cash.view"])
    refused_ = ask(client, world, today(), today(), world.manager_a)
    assert (refused_.status_code, refused_.json()["error"]["code"]) == (403, "FORBIDDEN_PERMISSION")


def test_whose_payment_it_is_is_written_only_for_someone_who_may_see_the_customers(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    added(client, world, "income", 40_000)
    assert record(client, world, world.customer_a, "payment", 20_000).status_code == 201

    seen = book(client, world, today(), today())
    assert seen[ENTRIES][0] == HEADER
    assert listed(seen, "Summa", "Mijoz", "Manba") == [
        (40_000, None, "Qo'lda yozilgan"),
        (20_000, "Ali", "Mijoz to'lovi"),
    ]
    switch_permissions_on(owner)
    set_overrides(owner, world.manager_a_membership, denied=["ledger.view"])
    hidden = book(client, world, today(), today())
    assert hidden[ENTRIES][0] == WITHOUT_NAMES
    assert listed(hidden, "Summa", "Manba") == [(40_000, "Qo'lda yozilgan"), (20_000, "Mijoz to'lovi")]
    assert "Ali" not in {cell for rows in hidden.values() for row in rows for cell in row}


def test_one_shops_file_never_holds_another_shops_entries(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    added(client, world, "income", 40_000, note="A do'koniniki")
    other = uuid.uuid4()
    owner.execute(
        "INSERT INTO cash_category (id, shop_id, direction, name, name_norm) "
        "VALUES (%s, %s, 'income', 'Savdo', 'savdo')",
        (other, world.shop_b),
    )
    owner_b = owner.execute(
        "SELECT id FROM membership WHERE shop_id = %s AND user_id = %s", (world.shop_b, world.owner_b)
    ).fetchone()
    assert owner_b is not None
    owner.execute(
        "INSERT INTO cash_entry (id, shop_id, direction, method, amount, category_id, note, day, author_id) "
        "VALUES (gen_random_uuid(), %s, 'income', 'cash', 777000, %s, 'B do''koniniki', %s, %s)",
        (world.shop_b, other, today(), owner_b[0]),
    )
    mine = book(client, world, today(), today())
    assert listed(mine, "Summa", "Izoh") == [(40_000, "A do'koniniki")]
    assert section(mine, "Qoldiqlar")[0] == ["UZS", "Naqd", 0, 40_000, 0, 40_000, 1]
    # Shop B's owner gets B's entries from B, and nothing from A: for them shop A does not exist.
    theirs = client.post(
        f"/api/v1/shops/{world.shop_b}/cash/export",
        json={"from": str(today()), "to": str(today())},
        headers={**as_user(world.owner_b), **key()},
    )
    assert theirs.status_code == 201, theirs.text
    read_back = xlsx_reader.read(client.get(theirs.json()["url"]).content)
    assert listed(read_back, "Summa", "Izoh") == [(777_000, "B do'koniniki")]
    outside = ask(client, world, today(), today(), world.owner_b)
    assert (outside.status_code, outside.json()["error"]["code"]) == (404, "NOT_FOUND")
    for user in (world.stranger, world.customer_of_a):
        assert ask(client, world, today(), today(), user).status_code == 404


def test_in_a_suspended_shop_only_the_owner_still_takes_the_book_away(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    added(client, world, "income", 40_000)
    _subscription(owner, world, "state = 'suspended', prior_state = 'trial'")
    late = ask(client, world, today(), today(), world.manager_a)
    assert (late.status_code, late.json()["error"]["code"]) == (403, "SHOP_SUSPENDED")
    assert listed(book(client, world, today(), today(), world.owner_a), "Summa") == [(40_000,)]


# --- the file and the request ----------------------------------------------------------------------------


def test_the_file_is_kept_an_hour_as_an_export_and_the_request_is_in_the_activity_log(
    client: TestClient, world: World, owner: psycopg.Connection, file_root: Path, on: None
) -> None:
    added(client, world, "income", 40_000)
    answer = ask(client, world, today(), today())
    assert answer.status_code == 201, answer.text
    (kept,) = owner.execute(
        "SELECT purpose, mime, delete_after FROM stored_file WHERE shop_id = %s", (world.shop_a,)
    ).fetchall()
    assert kept[:2] == ("export", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert abs(kept[2] - (datetime.now(UTC) + timedelta(hours=1))) < timedelta(minutes=1)
    assert len(stored_objects(file_root)) == 1
    # The link is the download's: five minutes.
    expires = datetime.fromisoformat(answer.json()["expires_at"])
    assert abs(expires - (datetime.now(UTC) + timedelta(minutes=5))) < timedelta(minutes=1)
    logged = owner.execute(
        "SELECT actor_id, subject_type, detail FROM activity WHERE shop_id = %s AND action = 'cash.exported'",
        (world.shop_a,),
    ).fetchall()
    assert logged == [(world.manager_a_membership, "shop", {"from": str(today()), "to": str(today()), "entries": 1})]
    # No job of the shop's own export is made: that list is what it was.
    assert owner.execute("SELECT count(*) FROM export_job WHERE shop_id = %s", (world.shop_a,)).fetchone() == (0,)


def test_a_repeated_request_keeps_one_file_and_a_key_names_one_period(
    client: TestClient, world: World, owner: psycopg.Connection, file_root: Path, on: None
) -> None:
    added(client, world, "income", 40_000)
    request_key = key()
    first = ask(client, world, today(), today(), **request_key)
    again = ask(client, world, today(), today(), **request_key)
    assert (first.status_code, again.status_code) == (201, 201)
    assert first.json() == again.json()
    assert len(stored_objects(file_root)) == 1
    assert actions(owner, world).count("cash.exported") == 1
    other = ask(client, world, today() - timedelta(days=1), today(), **request_key)
    assert (other.status_code, other.json()["error"]["code"]) == (409, "IDEMPOTENCY_KEY_REUSED")
    assert len(stored_objects(file_root)) == 1
    missing = client.post(
        f"{base(world)}/export", json={"from": str(today()), "to": str(today())}, headers=as_user(world.manager_a)
    )
    assert "Idempotency-Key" in refused(missing, 422, "VALIDATION")


def test_the_workbook_is_in_the_shops_language(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    added(client, world, "income", 40_000)
    assert write(client, world.owner_a, "PATCH", shop(world), {"lang": "ru"}).status_code == 200
    answer = ask(client, world, today(), today())
    sheets = xlsx_reader.read(client.get(answer.json()["url"]).content)
    assert list(sheets) == ["Отчёт", "Касса"]
    assert sheets["Касса"][0][:6] == ["День", "Время записи", "Направление", "Способ оплаты", "Валюта", "Сумма"]
    assert sheets["Касса"][1][2:6] == ["Приход", "Наличные", "UZS", 40_000]

"""Exports as worker jobs with signed downloads (REQ-028; BR-25, BR-29, BR-30; ADR-020)."""

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from qarz.application import exports as exports_module
from qarz.application.chat_texts import say
from qarz.application.errors import StorageTimeout
from qarz.application.exports import ExportService
from qarz.application.files import FileService
from qarz.application.payment_notices import PaymentNoticeService
from qarz.application.ports import FileStoreError
from qarz.application.reminders import ReminderService
from qarz.application.scheduler import Scheduler
from qarz.application.shop_deletion import ShopDeletionService
from qarz.infrastructure.db import Database
from qarz.infrastructure.file_store import FilesystemFileStore

from .. import xlsx_reader
from .conftest import World, as_user, stored_objects
from .test_customers_ledger import _subscription, key, record, reverse, seed_customer, seed_entry, shop, today
from .test_disputes import tg

pytestmark = pytest.mark.db

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture(autouse=True)
def no_jobs_left_by_earlier_tests(owner: psycopg.Connection) -> None:
    """The worker takes waiting jobs of any shop, and the test database is shared by the whole run: jobs
    that other tests left waiting are closed here, so that each test meets only its own."""
    owner.execute(
        "UPDATE export_job SET status = 'failed', error = 'interrupted' WHERE status IN ('queued', 'running')"
    )


def ask(client: TestClient, world: World, user: uuid.UUID | None = None, **headers: str) -> Any:
    sent = {**as_user(user or world.owner_a), **(headers or key())}
    return client.post(f"{shop(world)}/exports", headers=sent)


def listed(client: TestClient, world: World, user: uuid.UUID | None = None) -> list[dict[str, Any]]:
    response = client.get(f"{shop(world)}/exports", headers=as_user(user or world.owner_a))
    assert response.status_code == 200, response.text
    return list(response.json()["items"])


def link(client: TestClient, world: World, job: Any, user: uuid.UUID | None = None) -> Any:
    return client.get(f"{shop(world)}/exports/{job}/download", headers=as_user(user or world.owner_a))


def jobs(owner: psycopg.Connection, shop_id: uuid.UUID) -> list[tuple[Any, ...]]:
    return owner.execute(
        "SELECT status, error, attempts, file_id IS NOT NULL, row_count FROM export_job WHERE shop_id = %s "
        "ORDER BY created_at, id",
        (shop_id,),
    ).fetchall()


def export_files(owner: psycopg.Connection, shop_id: uuid.UUID) -> list[tuple[Any, ...]]:
    return owner.execute(
        "SELECT id, mime, delete_after FROM stored_file WHERE shop_id = %s AND purpose = 'export' ORDER BY created_at",
        (shop_id,),
    ).fetchall()


def told(owner: psycopg.Connection, shop_id: uuid.UUID) -> list[tuple[str, str]]:
    return [
        (str(row[0]), str(row[1]))
        for row in owner.execute(
            "SELECT recipient, payload->>'text' FROM outbox_message WHERE shop_id = %s AND dedupe_key LIKE 'export:%%' "
            "ORDER BY created_at, dedupe_key",
            (shop_id,),
        ).fetchall()
    ]


def work(worker_database_url: str, file_root: Path, *, store: Any = "files", now: Any = None, limit: int = 3) -> int:
    """One pass of the worker over the waiting exports."""

    async def run() -> int:
        database = Database(worker_database_url)
        try:
            kept = FilesystemFileStore(file_root) if store == "files" else store
            return await ExportService(database, FileService(database, kept), now).run_pending(limit)
        finally:
            await database.dispose()

    return asyncio.run(run())


def workbook(client: TestClient, world: World, job: Any) -> dict[str, list[list[Any]]]:
    given = link(client, world, job)
    assert given.status_code == 200, given.text
    served = client.get(given.json()["url"])
    assert served.status_code == 200, served.text
    return xlsx_reader.read(served.content)


def column(rows: list[list[Any]], index: int) -> list[Any]:
    return [row[index] if index < len(row) else None for row in rows[1:]]


# --- asking, the job, the file -------------------------------------------------------------------------


def test_an_owner_asks_the_worker_writes_and_the_file_comes_through_a_signed_link(
    client: TestClient, world: World, owner: psycopg.Connection, worker_database_url: str, file_root: Path
) -> None:
    asked = ask(client, world)
    assert asked.status_code == 201, asked.text
    body = asked.json()
    assert (body["status"], body["error"], body["available"], body["available_until"], body["rows"]) == (
        "queued",
        None,
        False,
        None,
        None,
    )
    assert body["requested_by"] == str(world.owner_a_membership) and body["finished_at"] is None
    assert jobs(owner, world.shop_a) == [("queued", None, 0, False, None)]
    assert [item["id"] for item in listed(client, world)] == [body["id"]]
    logged = owner.execute(
        "SELECT actor_id FROM activity WHERE shop_id = %s AND action = 'export.requested'", (world.shop_a,)
    ).fetchall()
    assert logged == [(world.owner_a_membership,)]
    # Nothing is written and nobody is told until the worker has done it.
    assert stored_objects(file_root) == [] and told(owner, world.shop_a) == []
    early = link(client, world, body["id"])
    assert (early.status_code, early.json()["error"]["code"]) == (409, "EXPORT_NOT_READY")
    assert early.json()["error"]["fields"] == {"status": "queued"}

    assert work(worker_database_url, file_root) == 1
    assert jobs(owner, world.shop_a) == [("done", None, 1, True, 1)]
    ((file_id, mime, delete_after),) = export_files(owner, world.shop_a)
    assert mime == XLSX
    assert abs(delete_after - (datetime.now(UTC) + timedelta(days=7))) < timedelta(minutes=2)
    assert len(stored_objects(file_root)) == 1
    text = say("uz", "export_ready", shop="Shop A")
    assert text == (
        "✅ «Shop A» do'konining eksporti tayyor. Uni ilovaning eksport bo'limidan yuklab oling; fayl 7 kun saqlanadi."
    )
    assert told(owner, world.shop_a) == [(tg(owner, world.owner_a), text)]

    (item,) = listed(client, world)
    assert (item["status"], item["available"], item["rows"]) == ("done", True, 1)
    assert datetime.fromisoformat(item["available_until"]) == delete_after

    given = link(client, world, body["id"])
    assert given.status_code == 200 and set(given.json()) == {"url", "expires_at"}
    assert given.json()["url"].startswith("/files/")
    served = client.get(given.json()["url"])  # no session: the link is the authorization
    assert served.status_code == 200
    assert served.headers["content-type"] == XLSX
    assert served.headers["content-disposition"] == f'attachment; filename="export-{file_id.hex[:8]}.xlsx"'
    assert served.headers["x-content-type-options"] == "nosniff"
    assert served.headers["cache-control"] == "private, no-store"
    assert list(xlsx_reader.read(served.content)) == [
        "Hisobot",
        "Mijozlar",
        "Daftar",
        "Muddatlar tarixi",
        "Mahsulotlar",
    ]
    # A second pass finds nothing to do.
    assert work(worker_database_url, file_root) == 0


def test_the_workbook_holds_the_customers_the_whole_ledger_the_promise_history_and_the_goods(
    client: TestClient, world: World, owner: psycopg.Connection, worker_database_url: str, file_root: Path
) -> None:
    owner.execute(
        "UPDATE customer SET phone = '+998901234567', credit_limit = 900000 WHERE id = %s", (world.customer_a,)
    )
    lines = [
        {"name": "Guruch", "qty": "2.5", "unit": "kg", "unit_price": 20000},
        {"name": "Non", "qty": "3", "unit": "dona", "unit_price": 4000},
    ]
    sale = record(client, world, world.customer_a, "credit", None, lines=lines, note="=SUM(1;2) bayramga").json()
    payment = record(client, world, world.customer_a, "payment", 12000).json()["entry"]["id"]
    assert reverse(client, world, payment).status_code == 201
    moved = today() + timedelta(days=20)
    changed = client.post(
        f"{shop(world)}/entries/{world.entry_a}/promise",
        json={"promised_date": moved.isoformat(), "reason": "Oylikdan keyin"},
        headers={**as_user(world.manager_a), **key()},
    )
    assert changed.status_code == 200, changed.text
    assert record(client, world, world.settled_customer_a, "credit", 7000).status_code == 201
    assert record(client, world, world.settled_customer_a, "payment", 7000).status_code == 201

    job = ask(client, world, world.manager_a).json()["id"]
    assert work(worker_database_url, file_root) == 1
    book = workbook(client, world, job)

    customers = book["Mijozlar"]
    assert customers[0] == ["Mijoz", "Telefon", "Holati", "Nasiya limiti", "Qarzi", "Qo'shilgan sana", "Mijoz ID"]
    by_name = {row[0]: row for row in customers[1:]}
    assert set(by_name) == {"Ali", "Vali", "Sobir"}
    assert by_name["Ali"][1:5] == ["+998901234567", "Faol", 900000, 50000 + 62000]
    assert by_name["Vali"][1:5] == [None, "Faol", None, 0]
    assert by_name["Sobir"][2] == "Arxivda" and by_name["Sobir"][6] == str(world.archived_customer_a)
    assert [row[0] for row in customers[1:]] == ["Ali", "Sobir", "Vali"], "in name order"

    ledger = book["Daftar"]
    assert len(ledger[0]) == 14 and ledger[0][:5] == ["Sana va vaqt", "Mijoz", "Turi", "Summa", "Qarzga ta'siri"]
    stored = owner.execute(
        "SELECT id, kind, amount FROM ledger_entry WHERE shop_id = %s ORDER BY created_at, id", (world.shop_a,)
    ).fetchall()
    assert [(row[12], row[3]) for row in ledger[1:]] == [(str(entry), amount) for entry, _, amount in stored]
    assert column(ledger, 2) == ["Nasiya", "Nasiya", "To'lov", "Bekor qilish", "Nasiya", "To'lov"]
    assert column(ledger, 4) == [50000, 62000, -12000, 12000, 7000, -7000]
    assert column(ledger, 7) == ["Yo'q", "Yo'q", "Ha", "Yo'q", "Yo'q", "Yo'q"]
    assert column(ledger, 8)[3] == payment and column(ledger, 8)[:3] == [None, None, None]
    assert column(ledger, 5)[1] == "=SUM(1;2) bayramga", "a note that looks like a formula is text"
    assert column(ledger, 6)[0] == moved.isoformat(), "the current promised date"
    assert column(ledger, 9)[:4] == ["Sotuvchi", "Sotuvchi", "Sotuvchi", "Menejer"]
    assert column(ledger, 10)[3] == str(world.manager_a_membership)
    assert column(ledger, 11) == [1, 2, 3, 4, 1, 2]
    # Each customer's debt on the first sheet is the sum of their rows on this one.
    for name, row in by_name.items():
        assert row[4] == sum(line[4] for line in ledger[1:] if line[13] == row[6]), name

    promises = book["Muddatlar tarixi"]
    of_first = [row for row in promises[1:] if row[0] == str(world.entry_a)]
    assert [(row[2], row[4], row[5] if len(row) > 5 else None) for row in of_first] == [
        ((today() + timedelta(days=7)).isoformat(), "Do'kon bo'yicha odatiy muddat", None),
        (moved.isoformat(), "Xodim", "Oylikdan keyin"),
    ]
    assert {row[1] for row in promises[1:]} == {"Ali", "Vali"}

    goods = book["Mahsulotlar"]
    assert [row[3:] for row in goods[1:]] == [
        [1, "Guruch", Decimal("2.5"), "kg", 20000, 50000],
        [2, "Non", 3, "dona", 4000, 12000],
    ]
    assert {row[0] for row in goods[1:]} == {sale["entry"]["id"]}

    summary = book["Hisobot"]
    facts = {row[0]: row[1] for row in summary if len(row) == 2}
    assert facts["Do'kon"] == "Shop A"
    assert (facts["Mijozlar soni"], facts["Qarzdor mijozlar soni"], facts["Jami qarz"]) == (3, 1, 112000)
    assert facts["Daftardagi yozuvlar soni"] == 6
    month = datetime.now(UTC).astimezone(exports_module.TASHKENT).strftime("%Y-%m")
    by_month = {row[0]: row[1:] for row in summary if len(row) == 7 and row[0] != "Oy"}
    # The reversed payment and its reversal are not counted; the reversed one is counted as such.
    assert by_month[month] == [50000 + 62000 + 7000, 3, 0, 7000, 1, 1]


def test_an_export_never_carries_another_shops_rows_or_anyones_telegram_identity(
    client: TestClient, world: World, owner: psycopg.Connection, worker_database_url: str, file_root: Path
) -> None:
    stranger = seed_customer(owner, world.shop_b, "Begona Maxfiy")
    owner.execute("UPDATE customer SET phone = '+998977777777' WHERE id = %s", (stranger,))
    author_b = owner.execute("SELECT id FROM membership WHERE shop_id = %s", (world.shop_b,)).fetchone()
    assert author_b is not None
    foreign = seed_entry(
        owner, world, stranger, 1, "credit", 31337, promised=today(), shop_id=world.shop_b, author=author_b[0]
    )
    job = ask(client, world).json()["id"]
    asked_b = client.post(f"/api/v1/shops/{world.shop_b}/exports", headers={**as_user(world.owner_b), **key()})
    assert asked_b.status_code == 201
    assert work(worker_database_url, file_root) == 2

    content = client.get(link(client, world, job).json()["url"]).content
    everything = b"".join(xlsx_reader.parts(content).values()).decode()
    for secret in ("Begona", "+998977777777", "31337", str(stranger), str(foreign), str(world.shop_b), "Shop B"):
        assert secret not in everything, secret
    for user in (world.customer_of_a, world.owner_a, world.seller_a, world.waiter):
        assert tg(owner, user) not in everything, "no Telegram identifier of anyone"
    assert str(world.customer_of_a) not in everything, "nor the user behind a customer"
    assert "Ali" in everything and str(world.customer_a) in everything

    # Shop B's own export is B's own, and A cannot ask for its link.
    other = client.get(
        f"/api/v1/shops/{world.shop_b}/exports/{asked_b.json()['id']}/download", headers=as_user(world.owner_b)
    )
    book_b = xlsx_reader.read(client.get(other.json()["url"]).content)
    assert [row[0] for row in book_b["Mijozlar"][1:]] == ["Begona Maxfiy"]
    assert link(client, world, asked_b.json()["id"]).status_code == 404
    through_b = client.get(f"/api/v1/shops/{world.shop_b}/exports/{job}/download", headers=as_user(world.owner_b))
    assert through_b.status_code == 404


def test_a_customer_whose_data_was_removed_appears_only_as_the_label_the_shop_sees(
    client: TestClient, world: World, owner: psycopg.Connection, worker_database_url: str, file_root: Path
) -> None:
    owner.execute("UPDATE customer SET phone = '+998935554433' WHERE id = %s", (world.customer_a,))
    assert record(client, world, world.customer_a, "payment", 50000).status_code == 201
    link_id = owner.execute("SELECT id FROM customer_link WHERE customer_id = %s", (world.customer_a,)).fetchone()
    assert link_id is not None
    removed = client.post(f"/api/v1/me/accounts/{link_id[0]}/removal", headers=as_user(world.customer_of_a))
    assert removed.json()["removed"] is True

    job = ask(client, world).json()["id"]
    work(worker_database_url, file_root)
    book = workbook(client, world, job)
    label = f"Anonim {world.customer_a.hex[:6].upper()}"
    row = next(row for row in book["Mijozlar"][1:] if row[6] == str(world.customer_a))
    assert row[:5] == [label, None, "Ma'lumotlari o'chirilgan", None, 0]
    flat = str(book)
    assert "+998935554433" not in flat and "'Ali'" not in flat
    assert {line[1] for line in book["Daftar"][1:]} == {label}, "the amounts stay, under the label"


def test_the_workbook_is_in_the_shops_language(
    client: TestClient, world: World, owner: psycopg.Connection, worker_database_url: str, file_root: Path
) -> None:
    owner.execute("UPDATE shop SET lang = 'ru' WHERE id = %s", (world.shop_a,))
    owner.execute("UPDATE app_user SET lang = 'ru' WHERE id = %s", (world.owner_a,))
    job = ask(client, world).json()["id"]
    work(worker_database_url, file_root)
    book = workbook(client, world, job)
    assert list(book) == ["Отчёт", "Клиенты", "Книга", "История сроков", "Товары"]
    assert book["Книга"][1][2] == "Продажа в долг" and book["Клиенты"][0][0] == "Клиент"
    assert told(owner, world.shop_a)[0][1] == say("ru", "export_ready", shop="Shop A")


def test_the_ledger_is_read_a_page_at_a_time_and_nothing_is_lost_or_doubled(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    worker_database_url: str,
    file_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(exports_module, "PAGE", 3)
    same_moment = datetime.now(UTC) - timedelta(days=2)
    for seq in range(2, 12):
        if seq % 2 == 0:
            seed_entry(owner, world, world.customer_a, seq, "credit", 1000 + seq, promised=today())
            continue
        # Several entries at exactly the same instant: the position in the ledger is the pair (time, id).
        entry = uuid.uuid4()
        owner.execute(
            "INSERT INTO ledger_entry (id, shop_id, customer_id, seq, kind, amount, author_id, created_at) "
            "VALUES (%s, %s, %s, %s, 'credit', %s, %s, %s)",
            (entry, world.shop_a, world.customer_a, seq, 1000 + seq, world.seller_a_membership, same_moment),
        )
        owner.execute(
            "INSERT INTO promise (id, shop_id, entry_id, promised_date, actor, created_at) "
            "VALUES (gen_random_uuid(), %s, %s, %s, 'staff', %s)",
            (world.shop_a, entry, today(), same_moment),
        )
    for n in range(7):
        seed_customer(owner, world.shop_a, f"Mijoz {n}")
    job = ask(client, world).json()["id"]
    work(worker_database_url, file_root)
    book = workbook(client, world, job)
    stored = owner.execute(
        "SELECT id FROM ledger_entry WHERE shop_id = %s ORDER BY created_at, id", (world.shop_a,)
    ).fetchall()
    assert column(book["Daftar"], 12) == [str(row[0]) for row in stored]
    assert len(stored) == 11
    assert len(book["Mijozlar"]) - 1 == 3 + 7
    assert len({row[6] for row in book["Mijozlar"][1:]}) == 10
    assert len(book["Muddatlar tarixi"]) - 1 == 11
    assert jobs(owner, world.shop_a) == [("done", None, 1, True, 11)]


def test_the_file_is_the_ledger_as_it_stood_when_the_worker_took_the_job(
    client: TestClient, world: World, owner: psycopg.Connection, worker_database_url: str, file_root: Path
) -> None:
    seed_entry(owner, world, world.customer_a, 2, "credit", 8000, promised=today(), days_ago=2)
    earlier = seed_entry(owner, world, world.customer_a, 3, "payment", 5000, days_ago=1)
    job = ask(client, world).json()["id"]
    an_hour_ago = datetime.now(UTC) - timedelta(hours=1)
    # Recorded after that instant: the seeded sale of 50 000, a new sale, and the reversal of the payment.
    assert record(client, world, world.customer_a, "credit", 9999).status_code == 201
    assert reverse(client, world, earlier).status_code == 201
    work(worker_database_url, file_root, now=lambda: an_hour_ago)
    book = workbook(client, world, job)
    assert column(book["Daftar"], 3) == [8000, 5000]
    assert column(book["Daftar"], 7) == ["Yo'q", "Yo'q"], "not shown as reversed by a reversal that is not in the file"
    ali = next(row for row in book["Mijozlar"][1:] if row[0] == "Ali")
    assert ali[4] == 3000 == sum(column(book["Daftar"], 4))


# --- who may, and when ---------------------------------------------------------------------------------


def test_one_export_at_a_time_per_shop(
    client: TestClient, world: World, owner: psycopg.Connection, worker_database_url: str, file_root: Path
) -> None:
    first = ask(client, world)
    again = ask(client, world, world.manager_a)
    assert (again.status_code, again.json()["error"]["code"]) == (409, "EXPORT_NOT_ALLOWED")
    assert again.json()["error"]["fields"] == {"reason": "in_progress"}
    owner.execute(
        "UPDATE export_job SET status = 'running', started_at = now(), attempts = 1 WHERE shop_id = %s", (world.shop_a,)
    )
    assert ask(client, world).json()["error"]["fields"] == {"reason": "in_progress"}
    assert len(jobs(owner, world.shop_a)) == 1
    # Another shop is not held up by this one.
    assert (
        client.post(f"/api/v1/shops/{world.shop_b}/exports", headers={**as_user(world.owner_b), **key()}).status_code
        == 201
    )

    owner.execute(
        "UPDATE export_job SET status = 'queued', started_at = NULL, attempts = 0 WHERE shop_id = %s", (world.shop_a,)
    )
    work(worker_database_url, file_root)
    assert ask(client, world).status_code == 201
    assert first.json()["id"] != listed(client, world)[0]["id"]
    with pytest.raises(psycopg.errors.UniqueViolation):
        owner.execute(
            "INSERT INTO export_job (id, shop_id, requested_by) VALUES (gen_random_uuid(), %s, %s)",
            (world.shop_a, world.owner_a_membership),
        )


def test_five_exports_a_day_per_shop_failed_ones_and_other_days_not_counted(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    start = datetime.combine(today(), datetime.min.time(), tzinfo=exports_module.TASHKENT)

    def seed(status: str, created_at: datetime, error: str | None = None) -> None:
        owner.execute(
            "INSERT INTO export_job (id, shop_id, requested_by, status, error, created_at) "
            "VALUES (gen_random_uuid(), %s, %s, %s, %s, %s)",
            (world.shop_a, world.owner_a_membership, status, error, created_at),
        )

    for _ in range(4):
        seed("done", start)
    for _ in range(3):
        seed("failed", start + timedelta(seconds=1), "internal")
        seed("done", start - timedelta(seconds=1))  # the last second of yesterday in Tashkent
    fifth = ask(client, world)
    assert fifth.status_code == 201, fifth.text
    owner.execute("UPDATE export_job SET status = 'done' WHERE id = %s", (fifth.json()["id"],))
    sixth = ask(client, world)
    assert (sixth.status_code, sixth.json()["error"]["code"]) == (409, "EXPORT_NOT_ALLOWED")
    assert sixth.json()["error"]["fields"] == {"reason": "daily_limit"}
    assert len(jobs(owner, world.shop_a)) == 4 + 6 + 1
    assert len(listed(client, world)) == 11


def test_a_repeated_request_makes_one_job(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    request_key = key()
    first, again = ask(client, world, **request_key), ask(client, world, **request_key)
    assert (first.status_code, again.status_code) == (201, 201) and first.json() == again.json()
    assert len(jobs(owner, world.shop_a)) == 1
    assert client.post(f"{shop(world)}/exports", headers=as_user(world.owner_a)).status_code == 422


def test_in_a_suspended_shop_only_the_owner_exports_and_in_a_limited_one_managers_too(
    client: TestClient, world: World, owner: psycopg.Connection, worker_database_url: str, file_root: Path
) -> None:
    _subscription(owner, world, "state = 'limited'")
    limited = ask(client, world, world.manager_a)
    assert limited.status_code == 201, limited.text
    work(worker_database_url, file_root)

    _subscription(owner, world, "state = 'suspended'")
    for response in (ask(client, world, world.manager_a), link(client, world, limited.json()["id"], world.manager_a)):
        assert (response.status_code, response.json()["error"]["code"]) == (403, "SHOP_SUSPENDED")
    refused = client.get(f"{shop(world)}/exports", headers=as_user(world.manager_a))
    assert refused.status_code == 403
    assert ask(client, world, world.seller_a).status_code == 403
    assert len(jobs(owner, world.shop_a)) == 1

    asked = ask(client, world, world.owner_a)
    assert asked.status_code == 201, asked.text
    assert work(worker_database_url, file_root) == 1, "the worker writes it although the shop is suspended"
    assert "Hisobot" in workbook(client, world, asked.json()["id"])
    assert len(listed(client, world, world.owner_a)) == 2


def test_a_shop_waiting_to_be_deleted_can_still_be_exported(
    client: TestClient, world: World, owner: psycopg.Connection, worker_database_url: str, file_root: Path
) -> None:
    owner.execute(
        "UPDATE shop SET status = 'deletion_pending', deletion_due = now() + interval '29 days' WHERE id = %s",
        (world.shop_a,),
    )
    asked = ask(client, world)
    assert asked.status_code == 201, asked.text
    assert work(worker_database_url, file_root) == 1
    assert workbook(client, world, asked.json()["id"])["Mijozlar"][1][0] == "Ali"


def test_only_a_manager_or_owner_of_the_shop_gets_a_link(
    client: TestClient, world: World, owner: psycopg.Connection, worker_database_url: str, file_root: Path
) -> None:
    job = ask(client, world).json()["id"]
    work(worker_database_url, file_root)
    assert link(client, world, job, world.manager_a).status_code == 200
    assert link(client, world, job, world.seller_a).status_code == 403
    for user in (world.owner_b, world.customer_of_a, world.stranger, world.suspended_a):
        assert link(client, world, job, user).status_code == 404
    assert client.get(f"{shop(world)}/exports/{job}/download").status_code == 401
    assert link(client, world, uuid.uuid4()).status_code == 404
    assert link(client, world, "not-a-uuid").status_code == 404


# --- the worker ----------------------------------------------------------------------------------------


def test_two_workers_at_once_write_each_job_once(
    client: TestClient, world: World, owner: psycopg.Connection, worker_database_url: str, file_root: Path
) -> None:
    ask(client, world)
    assert (
        client.post(f"/api/v1/shops/{world.shop_b}/exports", headers={**as_user(world.owner_b), **key()}).status_code
        == 201
    )

    async def both() -> list[int]:
        first, second = Database(worker_database_url), Database(worker_database_url)
        try:
            store = FilesystemFileStore(file_root)
            return list(
                await asyncio.gather(
                    ExportService(first, FileService(first, store)).run_pending(),
                    ExportService(second, FileService(second, store)).run_pending(),
                )
            )
        finally:
            await first.dispose()
            await second.dispose()

    assert sum(asyncio.run(both())) == 2
    assert jobs(owner, world.shop_a) == [("done", None, 1, True, 1)]
    assert jobs(owner, world.shop_b) == [("done", None, 1, True, 0)]
    assert len(stored_objects(file_root)) == 2
    assert len(told(owner, world.shop_a)) == 1 and len(told(owner, world.shop_b)) == 1


def test_a_job_whose_worker_died_is_taken_again_and_given_up_after_three_starts(
    client: TestClient, world: World, owner: psycopg.Connection, worker_database_url: str, file_root: Path
) -> None:
    ask(client, world)

    def running(minutes_ago: float, attempts: int) -> None:
        owner.execute(
            "UPDATE export_job SET status = 'running', started_at = %s, attempts = %s WHERE shop_id = %s",
            (datetime.now(UTC) - timedelta(minutes=minutes_ago), attempts, world.shop_a),
        )

    running(14, 1)
    assert work(worker_database_url, file_root) == 0, "its worker may still be writing"
    assert jobs(owner, world.shop_a) == [("running", None, 1, False, None)]

    running(16, 2)
    assert work(worker_database_url, file_root) == 1
    assert jobs(owner, world.shop_a) == [("done", None, 3, True, 1)], "the third start is still made"

    assert ask(client, world).status_code == 201
    owner.execute(
        "UPDATE export_job SET status = 'running', started_at = %s, attempts = 3 "
        "WHERE shop_id = %s AND status = 'queued'",
        (datetime.now(UTC) - timedelta(minutes=16), world.shop_a),
    )
    assert work(worker_database_url, file_root) == 1
    assert jobs(owner, world.shop_a)[1] == ("failed", "interrupted", 4, False, None)
    assert told(owner, world.shop_a)[-1][1] == say("uz", "export_failed", shop="Shop A")
    assert len(stored_objects(file_root)) == 1
    # A failed export does not stand in the way of the next.
    assert ask(client, world).status_code == 201


class BrokenStore:
    def __init__(self, inner: FilesystemFileStore) -> None:
        self.inner = inner

    async def put(self, key: str, data: bytes, mime: str) -> None:
        raise FileStoreError("ConnectionRefusedError")

    async def get(self, key: str) -> bytes:
        return await self.inner.get(key)

    async def delete(self, key: str) -> None:
        await self.inner.delete(key)


def test_a_failed_job_says_what_kind_of_failure_it_was_and_nothing_else(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    worker_database_url: str,
    file_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    first = ask(client, world).json()["id"]
    assert work(worker_database_url, file_root, store=BrokenStore(FilesystemFileStore(file_root))) == 1
    assert jobs(owner, world.shop_a) == [("failed", "file_store", 1, False, None)]
    assert export_files(owner, world.shop_a) == [] and stored_objects(file_root) == []
    text = say("uz", "export_failed", shop="Shop A")
    assert text == "«Shop A» do'konining eksportini tayyorlab bo'lmadi. Birozdan keyin qaytadan so'rang."
    assert told(owner, world.shop_a) == [(tg(owner, world.owner_a), text)]
    not_ready = link(client, world, first)
    assert (not_ready.status_code, not_ready.json()["error"]["fields"]) == (409, {"status": "failed"})
    assert listed(client, world)[0]["error"] == "file_store"

    async def crash(self: Any, *arguments: Any) -> int:
        raise RuntimeError("Ali owes 50000 and lives at Chilonzor 5")

    async def too_slow(self: Any, *arguments: Any) -> int:
        raise StorageTimeout()

    for failure, kind in ((crash, "internal"), (too_slow, "timeout")):
        monkeypatch.setattr(ExportService, "_write", failure)
        ask(client, world)
        with caplog.at_level(logging.ERROR, logger="qarz.exports"):
            assert work(worker_database_url, file_root) == 1
        assert jobs(owner, world.shop_a)[-1] == ("failed", kind, 1, False, None)
    everything = (
        caplog.text + str(owner.execute("SELECT * FROM export_job").fetchall()) + str(told(owner, world.shop_a))
    )
    assert "Chilonzor" not in everything and "50000" not in everything
    assert "RuntimeError" in caplog.text, "the kind of failure is logged"
    assert stored_objects(file_root) == [] and len(told(owner, world.shop_a)) == 3


def test_a_worker_whose_job_was_closed_meanwhile_keeps_no_file_and_tells_nobody_again(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    worker_database_url: str,
    file_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ask(client, world)
    real = FileService.stage

    async def stage_while_another_worker_gives_up(self: FileService, checked: Any) -> Any:
        staged = await real(self, checked)
        owner.execute(
            "UPDATE export_job SET status = 'failed', error = 'interrupted', finished_at = now() WHERE shop_id = %s",
            (world.shop_a,),
        )
        return staged

    monkeypatch.setattr(FileService, "stage", stage_while_another_worker_gives_up)
    assert work(worker_database_url, file_root) == 1
    assert jobs(owner, world.shop_a) == [("failed", "interrupted", 1, False, None)]
    assert export_files(owner, world.shop_a) == [] and stored_objects(file_root) == []
    assert told(owner, world.shop_a) == []


def test_the_scheduler_writes_waiting_exports_at_every_tick(
    client: TestClient, world: World, owner: psycopg.Connection, worker_database_url: str, file_root: Path
) -> None:
    moment = datetime(2084, 4, 4, 21, 30, tzinfo=UTC)  # night in Tashkent: no other job has work

    def tick() -> None:
        async def run() -> None:
            database = Database(worker_database_url)
            try:
                clock = lambda: moment  # noqa: E731
                service = ExportService(database, FileService(database, FilesystemFileStore(file_root)), clock)
                await Scheduler(database, ReminderService(database, clock), clock, exports=service).tick()
            finally:
                await database.dispose()

        asyncio.run(run())

    ask(client, world)
    tick()
    assert jobs(owner, world.shop_a) == [("done", None, 1, True, 1)]
    ask(client, world)
    tick()  # the same minute: not once an hour, but whenever something waits
    assert [row[0] for row in jobs(owner, world.shop_a)] == ["done", "done"]


# --- how long the file is kept -------------------------------------------------------------------------


def test_the_workbook_is_deleted_after_seven_days_by_the_hourly_cleanup(
    client: TestClient, world: World, owner: psycopg.Connection, worker_database_url: str, file_root: Path
) -> None:
    job = ask(client, world).json()["id"]
    work(worker_database_url, file_root)
    url = link(client, world, job).json()["url"]
    ((file_id, _, _),) = export_files(owner, world.shop_a)

    def cleanup(after: timedelta) -> tuple[int, int]:
        async def run() -> tuple[int, int]:
            database = Database(worker_database_url)
            try:
                files = FileService(database, FilesystemFileStore(file_root))
                return await PaymentNoticeService(database, files, lambda: datetime.now(UTC) + after).run_hourly()
            finally:
                await database.dispose()

        return asyncio.run(run())

    cleanup(timedelta(days=6, hours=23))
    assert len(stored_objects(file_root)) == 1 and client.get(url).status_code == 200

    # Past its time but not yet swept: already not served and no link is given.
    owner.execute("UPDATE stored_file SET delete_after = %s WHERE id = %s", (datetime.now(UTC), file_id))
    assert client.get(url).status_code == 404
    expired = link(client, world, job)
    assert (expired.status_code, expired.json()["error"]["code"]) == (409, "EXPORT_NOT_READY")
    assert expired.json()["error"]["fields"] == {"status": "expired"}
    assert listed(client, world)[0]["available"] is False

    assert cleanup(timedelta(minutes=1))[1] >= 1
    assert stored_objects(file_root) == [] and export_files(owner, world.shop_a) == []
    assert jobs(owner, world.shop_a) == [("done", None, 1, False, 1)], "the job stays in the history, without its file"
    (item,) = listed(client, world)
    assert (item["status"], item["available"], item["available_until"]) == ("done", False, None)
    gone = link(client, world, job)
    assert (gone.status_code, gone.json()["error"]["fields"]) == (409, {"status": "expired"})


def test_erasing_a_shop_takes_its_export_jobs_and_workbooks_with_it(
    client: TestClient, world: World, owner: psycopg.Connection, worker_database_url: str, file_root: Path
) -> None:
    ask(client, world)
    assert (
        client.post(f"/api/v1/shops/{world.shop_b}/exports", headers={**as_user(world.owner_b), **key()}).status_code
        == 201
    )
    work(worker_database_url, file_root)
    assert len(stored_objects(file_root)) == 2
    owner.execute(
        "UPDATE shop SET status = 'deletion_pending', deletion_due = now() - interval '1 second' WHERE id = %s",
        (world.shop_a,),
    )

    async def erase() -> None:
        database = Database(worker_database_url)
        try:
            await ShopDeletionService(database, files=FilesystemFileStore(file_root)).erase_due()
        finally:
            await database.dispose()

    asyncio.run(erase())
    assert owner.execute("SELECT status FROM shop WHERE id = %s", (world.shop_a,)).fetchone() == ("erased",)
    assert jobs(owner, world.shop_a) == [] and export_files(owner, world.shop_a) == []
    assert len(jobs(owner, world.shop_b)) == 1 and len(stored_objects(file_root)) == 1


def test_a_workbook_larger_than_the_store_would_hand_back_is_not_kept(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    worker_database_url: str,
    file_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ask(client, world)
    monkeypatch.setattr(exports_module, "MAX_EXPORT_BYTES", 1000)
    assert work(worker_database_url, file_root) == 1
    assert jobs(owner, world.shop_a) == [("failed", "file_store", 1, False, None)]
    assert stored_objects(file_root) == [] and export_files(owner, world.shop_a) == []
    # At exactly the limit it is kept.
    ask(client, world)
    probe: list[int] = []
    real = FileService.stage

    async def measure(self: FileService, checked: Any) -> Any:
        probe.append(len(checked.content))
        return await real(self, checked)

    monkeypatch.setattr(FileService, "stage", measure)
    monkeypatch.setattr(exports_module, "MAX_EXPORT_BYTES", 10**9)
    work(worker_database_url, file_root)
    ask(client, world)
    monkeypatch.setattr(exports_module, "MAX_EXPORT_BYTES", probe[0])
    work(worker_database_url, file_root)
    assert [row[0] for row in jobs(owner, world.shop_a)] == ["failed", "done", "done"]


def test_a_finished_export_is_measured_by_its_size_and_carries_no_identity(
    client: TestClient, world: World, owner: psycopg.Connection, worker_database_url: str, file_root: Path
) -> None:
    def measured() -> list[tuple[Any, ...]]:
        return owner.execute(
            "SELECT kind, amount FROM measure.event WHERE kind LIKE 'export%' ORDER BY at, kind"
        ).fetchall()

    before = measured()
    job = ask(client, world).json()["id"]
    assert measured() == before, "asking is recorded in the shop's activity log, not measured"
    work(worker_database_url, file_root)
    assert measured()[len(before) :] == [("export_done", 1)]
    refs = owner.execute("SELECT shop_ref, entry_ref FROM measure.event WHERE kind = 'export_done'").fetchall()
    assert all(str(world.shop_a) != str(shop_ref) and job != str(entry_ref) for shop_ref, entry_ref in refs)
    ask(client, world)
    work(worker_database_url, file_root, store=BrokenStore(FilesystemFileStore(file_root)))
    assert measured()[len(before) + 1 :] == [("export_failed", 0)]


def test_someone_who_is_no_longer_staff_is_not_told_about_the_export_they_asked_for(
    client: TestClient, world: World, owner: psycopg.Connection, worker_database_url: str, file_root: Path
) -> None:
    job = ask(client, world, world.manager_a).json()["id"]
    owner.execute("UPDATE membership SET status = 'suspended' WHERE id = %s", (world.manager_a_membership,))
    assert work(worker_database_url, file_root) == 1
    assert jobs(owner, world.shop_a) == [("done", None, 1, True, 1)], "the work is done all the same"
    assert told(owner, world.shop_a) == []
    assert link(client, world, job, world.manager_a).status_code == 404
    assert link(client, world, job, world.owner_a).status_code == 200

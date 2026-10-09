"""Import of customers with opening balances: template, upload, check, preview, apply, undo (story S14.1).

REQ-062, REQ-063; domain rule BR-24; BR-29 and BR-30 for the subscription states; REQ-N07 for the ledger,
which stays insert-only through an undo. The API records what is asked; the worker does the steps
(architecture, "Import"), so every test here drives the worker itself with `work()`.
"""

import asyncio
import threading
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from qarz.application.chat_texts import day, money, say
from qarz.application.files import FileService
from qarz.application.imports import ImportService, apply_in, check_in, undo_in
from qarz.application.reminders import ReminderService
from qarz.application.scheduler import Scheduler
from qarz.application.xlsx import MIME as XLSX_MIME
from qarz.application.xlsx import Workbook
from qarz.domain import imports
from qarz.domain.files import MAX_FILE_BYTES
from qarz.infrastructure.db import Database
from qarz.infrastructure.file_store import FilesystemFileStore

from .conftest import World, as_user, stored_objects
from .test_customer_account import ME, link_of
from .test_customers_ledger import _subscription, detail, key, record, reverse, shop, today
from .test_disputes import staff_notices, tg
from .test_payment_notices import with_services

pytestmark = pytest.mark.db

HEAD = "Ism,Telefon,Qarz summasi,To'lash muddati,Izoh"
_WORKER: dict[str, Any] = {}


@pytest.fixture(autouse=True)
def worker(owner: psycopg.Connection, worker_database_url: str, file_root: Path) -> Iterator[None]:
    """Where `work()` finds the database and the file store; and no step left waiting by an earlier test,
    since a worker takes the oldest waiting batch of any shop."""
    owner.execute("UPDATE import_batch SET status = 'discarded' WHERE status IN ('uploaded', 'applying', 'undoing')")
    _WORKER.update(url=worker_database_url, root=file_root)
    yield
    _WORKER.clear()


def work(limit: int = 10, now: Any = None) -> int:
    """One pass of the worker over the import steps that wait. Returns how many it took."""

    async def run() -> int:
        database = Database(_WORKER["url"])
        try:
            files = FileService(database, FilesystemFileStore(_WORKER["root"]))
            return await ImportService(database, files, now).run_pending(limit)
        finally:
            await database.dispose()

    return asyncio.run(run())


def table(*lines: str) -> bytes:
    return "\n".join([HEAD, *lines]).encode("utf-8")


def typed(value: date) -> str:
    return value.strftime("%d.%m.%Y")


def upload(client: TestClient, world: World, data: bytes, user: uuid.UUID | None = None, headers: Any = None) -> Any:
    return client.post(
        f"{shop(world)}/imports", content=data, headers={**as_user(user or world.manager_a), **(headers or key())}
    )


def uploaded(client: TestClient, world: World, data: bytes) -> str:
    """Upload a file and let the worker check it. Returns the batch, whatever the check found."""
    response = upload(client, world, data)
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "uploaded"
    assert work() == 1
    return str(response.json()["id"])


def read(client: TestClient, world: World, batch: Any, user: uuid.UUID | None = None) -> Any:
    return client.get(f"{shop(world)}/imports/{batch}", headers=as_user(user or world.manager_a))


def state(client: TestClient, world: World, batch: Any) -> dict[str, Any]:
    response = read(client, world, batch)
    assert response.status_code == 200, response.text
    return dict(response.json())


def plan_of(client: TestClient, world: World, batch: Any) -> str:
    body = state(client, world, batch)
    assert body["status"] == "validated" and body["preview"]["plan"] is not None, body
    return str(body["preview"]["plan"])


def act(client: TestClient, world: World, batch: Any, action: str, body: Any = None, **options: Any) -> Any:
    user = options.get("user") or world.manager_a
    headers = {**as_user(user), **(options.get("headers") or key())}
    return client.post(f"{shop(world)}/imports/{batch}/{action}", json=body, headers=headers)


def apply(client: TestClient, world: World, batch: Any, plan: str | None = None, **options: Any) -> Any:
    return act(client, world, batch, "apply", {"plan": plan or plan_of(client, world, batch)}, **options)


def applied(client: TestClient, world: World, data: bytes, user: uuid.UUID | None = None) -> str:
    batch = uploaded(client, world, data)
    response = apply(client, world, batch, user=user)
    assert (response.status_code, response.json()["status"]) == (202, "applying"), response.text
    assert work() == 1
    assert state(client, world, batch)["status"] == "applied"
    return batch


def undone(client: TestClient, world: World, batch: Any, user: uuid.UUID | None = None) -> dict[str, Any]:
    response = act(client, world, batch, "undo", user=user)
    assert (response.status_code, response.json()["status"]) == (202, "undoing"), response.text
    assert work() == 1
    return state(client, world, batch)


def batches(owner: psycopg.Connection, world: World) -> list[tuple[Any, ...]]:
    return owner.execute(
        "SELECT status, summary, author_id, applied_at IS NOT NULL FROM import_batch WHERE shop_id = %s "
        "ORDER BY created_at, id",
        (world.shop_a,),
    ).fetchall()


def statuses(owner: psycopg.Connection, world: World) -> list[str]:
    return [str(row[0]) for row in batches(owner, world)]


def customers(owner: psycopg.Connection, world: World) -> list[tuple[Any, ...]]:
    return owner.execute(
        "SELECT display_name, name_norm, phone, status FROM customer WHERE shop_id = %s ORDER BY display_name",
        (world.shop_a,),
    ).fetchall()


def entries(owner: psycopg.Connection, world: World, batch: Any = None) -> list[tuple[Any, ...]]:
    """Name, entry number, kind, amount, note and promise of the shop's entries, or of one import's."""
    return owner.execute(
        "SELECT c.display_name, e.seq, e.kind, e.amount, e.note, "
        "(SELECT p.promised_date FROM promise p WHERE p.entry_id = e.id), "
        "(SELECT p.actor FROM promise p WHERE p.entry_id = e.id) "
        "FROM ledger_entry e JOIN customer c ON c.id = e.customer_id "
        "WHERE e.shop_id = %s AND (%s::uuid IS NULL OR e.import_batch_id = %s::uuid) ORDER BY c.display_name, e.seq",
        (world.shop_a, batch, batch),
    ).fetchall()


def count(owner: psycopg.Connection, world: World, name: str) -> int:
    row = owner.execute(f"SELECT count(*) FROM {name} WHERE shop_id = %s", (world.shop_a,)).fetchone()
    assert row is not None
    return int(row[0])


def nothing_saved(owner: psycopg.Connection, world: World) -> tuple[int, ...]:
    """What an import must not touch before it is applied."""
    return tuple(count(owner, world, name) for name in ("customer", "ledger_entry", "promise"))


def balance(owner: psycopg.Connection, world: World, name: str) -> int:
    row = owner.execute(
        "SELECT coalesce(sum(CASE WHEN e.kind IN ('credit', 'opening') THEN e.amount ELSE -e.amount END), 0) "
        "FROM ledger_entry e JOIN customer c ON c.id = e.customer_id "
        "WHERE c.shop_id = %s AND c.display_name = %s AND e.kind <> 'reversal' "
        "AND NOT EXISTS (SELECT 1 FROM ledger_entry r WHERE r.reverses_id = e.id)",
        (world.shop_a, name),
    ).fetchone()
    assert row is not None
    return int(row[0])


def customer_id(owner: psycopg.Connection, world: World, name: str) -> uuid.UUID:
    row = owner.execute(
        "SELECT id FROM customer WHERE shop_id = %s AND display_name = %s", (world.shop_a, name)
    ).fetchone()
    assert row is not None
    return uuid.UUID(str(row[0]))


def import_file(owner: psycopg.Connection, world: World) -> list[tuple[Any, ...]]:
    return owner.execute(
        "SELECT purpose, mime, size_bytes, delete_after FROM stored_file WHERE shop_id = %s AND purpose = 'import' "
        "ORDER BY created_at, id",
        (world.shop_a,),
    ).fetchall()


def activity(owner: psycopg.Connection, world: World) -> list[tuple[Any, ...]]:
    return owner.execute(
        "SELECT action, subject_type, actor_id FROM activity WHERE shop_id = %s AND action LIKE 'import.%%' "
        "ORDER BY at, id",
        (world.shop_a,),
    ).fetchall()


def told(owner: psycopg.Connection, batch: Any, text_key: str) -> list[tuple[str, str]]:
    """Who was told `text_key` about the batch, and in what words."""
    return [(recipient, payload["text"]) for recipient, payload in staff_notices(owner, f"import:{batch}:{text_key}:")]


def measured(owner: psycopg.Connection, kind: str) -> int:
    row = owner.execute("SELECT count(*) FROM measure.event WHERE kind = %s", (kind,)).fetchone()
    assert row is not None
    return int(row[0])


# --- the template ---------------------------------------------------------------------------------------


def test_a_manager_downloads_the_template_in_their_language(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    response = client.get(f"{shop(world)}/imports/template", headers=as_user(world.manager_a))
    assert response.status_code == 200
    assert response.headers["content-type"] == XLSX_MIME == imports.XLSX_MIME
    assert response.headers["content-disposition"] == 'attachment; filename="qarz-daftari-import.xlsx"'
    assert imports.read_xlsx(response.content) == [(1, HEAD.split(","))]

    owner.execute("UPDATE app_user SET lang = 'ru' WHERE id = %s", (world.owner_a,))
    russian = client.get(f"{shop(world)}/imports/template", headers=as_user(world.owner_a))
    assert imports.read_xlsx(russian.content)[0][1][0] == "Имя"
    assert nothing_saved(owner, world) == (3, 1, 1)
    assert batches(owner, world) == []


def test_the_filled_template_is_imported_end_to_end(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    """A workbook, with number cells for the amount and the date and text cells, from upload to the ledger."""
    due = today() + timedelta(days=12)
    book = Workbook()
    sheet = book.sheet("Import", HEAD.split(","))
    sheet.append(["Karim aka", "+998 90 123-45-67", 250000, (due - date(1899, 12, 30)).days, "eski daftardan"])
    batch = applied(client, world, book.finish())
    assert entries(owner, world, batch) == [("Karim aka", 1, "opening", 250000, "eski daftardan", due, "staff")]
    assert ("Karim aka", "karim aka", "+998901234567", "active") in customers(owner, world)
    assert import_file(owner, world)[0][1] == imports.XLSX_MIME


# --- upload: kept at once, checked by the worker --------------------------------------------------------


def test_an_upload_answers_at_once_and_the_worker_checks_the_file(
    client: TestClient, world: World, owner: psycopg.Connection, file_root: Path
) -> None:
    data = table("Karim,901234567,250000,,", "Lola,,80000,,qo'shni")
    before = nothing_saved(owner, world)
    response = upload(client, world, data)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body == {
        "id": body["id"],
        "status": "uploaded",
        "format": "csv",
        "author_id": str(world.manager_a_membership),
        "created_at": body["created_at"],
        "applied_at": None,
        "undo_until": None,
        "rows": 0,
        "file_problem": None,
        "errors": [],
        "applied": None,
        "undone": None,
        "refused": None,
    }
    # Nothing has been read yet: the batch waits, without a preview, and nobody has been told anything.
    assert state(client, world, body["id"])["preview"] is None
    assert told(owner, body["id"], "import_checked") == []
    kept = import_file(owner, world)
    assert [(row[0], row[1], row[2]) for row in kept] == [("import", "text/csv", len(data))]
    month = datetime.now(UTC) + timedelta(days=30)
    assert abs(kept[0][3] - month) < timedelta(minutes=5), "an import file is kept for thirty days at most"
    assert [path.read_bytes() for path in stored_objects(file_root)] == [data]
    assert activity(owner, world) == [("import.uploaded", "import", world.manager_a_membership)]

    assert work() == 1
    assert work() == 0, "a checked batch waits for nothing"
    checked = state(client, world, body["id"])
    assert (checked["status"], checked["rows"], checked["errors"], checked["file_problem"]) == (
        "validated",
        2,
        [],
        None,
    )
    assert [row["name"] for row in checked["preview"]["rows"]] == ["Karim", "Lola"]
    assert nothing_saved(owner, world) == before, "BR-24: nothing is saved before the import is applied"
    assert told(owner, body["id"], "import_checked") == [
        (
            tg(owner, world.manager_a),
            "📥 Shop A\nImport fayli tekshirildi: 2 ta qator, xatosiz. Ilovada ko'rib chiqib, qo'llashingiz mumkin.",
        )
    ]
    # The batch's summary holds counts and codes; no name, phone or amount of a row.
    stored = owner.execute("SELECT summary::text, plan FROM import_batch WHERE id = %s", (body["id"],)).fetchone()
    assert stored is not None
    assert "Karim" not in stored[0] and "901234567" not in stored[0]
    assert stored[1] == checked["preview"]["plan"]


def test_rows_with_problems_are_listed_with_row_numbers_and_codes_and_block_the_import(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    data = table(
        "Karim,901234567,250000,,",
        ",12345,abc,31.02.2026,",
        "Lola,,50,,",
        "Sobir,,1000,,",  # an archived customer of the shop
        "Nodir,,1000," + typed(today() + timedelta(days=366)) + ",",
    )
    before = nothing_saved(owner, world)
    batch = uploaded(client, world, data)
    body = state(client, world, batch)
    assert (body["status"], body["rows"], body["file_problem"]) == ("rejected", 5, None)
    assert body["errors"] == [
        {"row": 3, "column": "amount", "code": "amount_invalid"},
        {"row": 3, "column": "name", "code": "name_missing"},
        {"row": 3, "column": "phone", "code": "phone_invalid"},
        {"row": 3, "column": "promised_date", "code": "date_invalid"},
        {"row": 4, "column": "amount", "code": "amount_too_small"},
        {"row": 5, "column": "name", "code": "customer_archived"},
        {"row": 6, "column": "promised_date", "code": "date_too_far"},
    ]
    assert "abc" not in str(body["errors"]) and "12345" not in str(body["errors"]), "a code, never the cell"
    seen = body["preview"]
    assert (seen["plan"], seen["errors"]) == (None, body["errors"])
    assert [row["row"] for row in seen["rows"]] == [2], "the row without a problem is still shown"
    assert told(owner, batch, "import_rejected") == [
        (
            tg(owner, world.manager_a),
            "📥 Shop A\nImport faylining 4 ta qatorida xato topildi. Ilovada ro'yxatini ko'rib, "
            "tuzatilgan faylni qayta yuklang.",
        )
    ]
    assert told(owner, batch, "import_checked") == []

    for plan in ("0" * 32, imports.plan_token([])):
        refused = apply(client, world, batch, plan)
        assert (refused.status_code, refused.json()["error"]["code"]) == (409, "IMPORT_NOT_APPLICABLE")
        assert refused.json()["error"]["fields"] == {"reason": "rejected"}
    assert work() == 0
    assert nothing_saved(owner, world) == before
    assert statuses(owner, world) == ["rejected"]


@pytest.mark.parametrize(
    ("data", "problem"),
    [
        (b"", "empty"),
        (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR", "not_a_spreadsheet"),
        (b"Ism,Qarz\nAli\x00,1\n", "not_a_spreadsheet"),
    ],
    ids=["empty", "image", "binary"],
)
def test_what_is_plainly_not_a_table_is_refused_at_upload_and_nothing_of_it_is_kept(
    client: TestClient, world: World, owner: psycopg.Connection, file_root: Path, data: bytes, problem: str
) -> None:
    response = upload(client, world, data)
    assert (response.status_code, response.json()["error"]["code"]) == (422, "VALIDATION"), response.text
    assert response.json()["error"]["fields"] == {"file": problem}
    assert batches(owner, world) == []
    assert import_file(owner, world) == []
    assert stored_objects(file_root) == []
    assert activity(owner, world) == []
    assert work() == 0


@pytest.mark.parametrize(
    ("data", "problem"),
    [
        ("Имя;Долг\nАли;45000\n".encode("cp1251"), "encoding"),
        (b"PK\x03\x04" + b"\x01" * 100, "malformed"),
        (b"Ism,Telefon\nAli,901234567\n", "missing_column"),
        (b"Ism,Qarz,Manzil\nAli,45000,Chilonzor\n", "unknown_column"),
        (b"Ism,Qarz,Summa\nAli,45000,45000\n", "duplicate_column"),
        (b"Ism,Qarz\n", "no_rows"),
        (b"\n\n", "no_header"),
        ("\n".join(["Ism,Qarz", *(f"Mijoz {n},1000" for n in range(2001))]).encode(), "too_many_rows"),
        (b'{"file": "Ism,Qarz"}', "unknown_column"),
    ],
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_a_file_the_worker_cannot_read_as_an_import_is_rejected_with_a_code(
    client: TestClient, world: World, owner: psycopg.Connection, data: bytes, problem: str
) -> None:
    """What needs the sheet to be read is found by the worker, not by the upload."""
    batch = uploaded(client, world, data)
    body = state(client, world, batch)
    assert (body["status"], body["file_problem"], body["errors"], body["preview"]) == ("rejected", problem, [], None)
    assert told(owner, batch, "import_unreadable") == [
        (
            tg(owner, world.manager_a),
            "📥 Shop A\nImport faylini jadval sifatida o'qib bo'lmadi. Sababi ilovada ko'rsatilgan.",
        )
    ]
    assert import_file(owner, world)[0][3] <= datetime.now(UTC), "a file of no use is not kept"
    refused = apply(client, world, batch, "0" * 32)
    assert (refused.status_code, refused.json()["error"]["fields"]) == (409, {"reason": "rejected"})
    assert nothing_saved(owner, world) == (3, 1, 1)


def test_an_upload_may_be_larger_than_other_requests_but_not_than_a_file_may_be(
    client: TestClient, world: World, owner: psycopg.Connection, file_root: Path
) -> None:
    line = "Mijoz {n},,1000,," + "x" * 900
    big = table(*(line.format(n=n) for n in range(1500)))
    assert 1024 * 1024 < len(big) < MAX_FILE_BYTES
    batch = uploaded(client, world, big)
    checked = state(client, world, batch)
    assert checked["status"] == "rejected"
    assert {error["code"] for error in checked["errors"]} == {"note_too_long"}

    too_big = upload(client, world, b"Ism,Qarz\n" + b"\n" * MAX_FILE_BYTES)
    assert (too_big.status_code, too_big.json()["error"]["code"]) == (413, "BODY_TOO_LARGE")
    # The allowance is for the upload alone: another route of the import keeps the general limit.
    elsewhere = client.post(
        f"{shop(world)}/imports/{batch}/apply", content=big, headers={**as_user(world.manager_a), **key()}
    )
    assert elsewhere.status_code == 413
    assert len(batches(owner, world)) == 1
    assert len(stored_objects(file_root)) == 1


def test_uploading_twice_with_the_same_key_keeps_one_batch_and_one_file(
    client: TestClient, world: World, owner: psycopg.Connection, file_root: Path
) -> None:
    data, same = table("Karim,,250000,,"), key()
    first = upload(client, world, data, headers=same)
    repeat = upload(client, world, data, headers=same)
    assert (first.status_code, repeat.status_code) == (201, 201)
    assert repeat.json() == first.json()
    other = upload(client, world, table("Lola,,1000,,"), headers=same)
    assert (other.status_code, other.json()["error"]["code"]) == (409, "IDEMPOTENCY_KEY_REUSED")
    assert len(batches(owner, world)) == 1
    assert [path.read_bytes() for path in stored_objects(file_root)] == [data], "no copy without a batch is left"
    assert len(activity(owner, world)) == 1
    assert work() == 1, "one batch waits, once"


def test_a_file_store_that_is_down_refuses_the_upload_and_saves_nothing(
    client: TestClient, world: World, owner: psycopg.Connection, file_root: Path
) -> None:
    file_root.parent.mkdir(parents=True, exist_ok=True)
    file_root.write_text("a file where the store's directory should be")
    failed = upload(client, world, table("Karim,,250000,,"))
    assert failed.status_code == 500
    assert batches(owner, world) == []
    assert import_file(owner, world) == []


def test_a_stranger_learns_nothing_from_the_file_they_send(
    client: TestClient, world: World, owner: psycopg.Connection, file_root: Path
) -> None:
    """Who may import is decided before the file is looked at: a bad file and a good one get the same answer."""
    for data in (table("Karim,,250000,,"), b"\x89PNG\x00 not a table", b""):
        for user, status in ((world.owner_b, 404), (world.seller_a, 403), (world.stranger, 404)):
            assert upload(client, world, data, user).status_code == status
        missing_key = client.post(f"{shop(world)}/imports", content=data, headers=as_user(world.manager_a))
        assert missing_key.status_code == 422
        assert "Idempotency-Key" in missing_key.json()["error"]["fields"]
    assert batches(owner, world) == []
    assert stored_objects(file_root) == []


# --- the preview ----------------------------------------------------------------------------------------


def test_the_preview_says_what_each_row_would_do_and_merges_nothing_silently(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    owner.execute("UPDATE customer SET phone = '+998907777777' WHERE id = %s", (world.settled_customer_a,))
    due = today() + timedelta(days=5)
    batch = uploaded(
        client,
        world,
        table(
            f"Karim,901234567,250000,{typed(due)},eski qarz",  # nobody known: a new customer
            "ali,,70000,,",  # the shop's Ali, by name
            "Boshqa ism,907777777,5000,,",  # the shop's Vali, by phone
            "KARIM,,30000,,",  # the Karim of row 2
            "Yangi,901234567,1000,,",  # the Karim of row 2 again, by phone
        ),
    )
    before = nothing_saved(owner, world)
    body = state(client, world, batch)
    seen = body["preview"]
    assert (body["status"], seen["errors"]) == ("validated", [])
    assert seen["counts"] == {"new_customers": 1, "existing_customers": 2, "entries": 5, "amount": 356000}
    assert seen["rows"][0] == {
        "row": 2,
        "name": "Karim",
        "phone": "+998901234567",
        "amount": 250000,
        "promised_date": due.isoformat(),
        "note": "eski qarz",
        "action": "create",
        "matched_by": None,
        "same_as_row": None,
        "customer": None,
    }
    assert [(r["row"], r["action"], r["matched_by"], r["same_as_row"]) for r in seen["rows"]] == [
        (2, "create", None, None),
        (3, "existing", "name", None),
        (4, "existing", "phone", None),
        (5, "same_as_row", "name", 2),
        (6, "same_as_row", "phone", 2),
    ]
    assert seen["rows"][1]["customer"] == {
        "id": str(world.customer_a),
        "display_name": "Ali",
        "phone": None,
        "balance": 50000,
    }
    assert seen["rows"][2]["customer"]["display_name"] == "Vali"
    assert (seen["rows"][2]["customer"]["balance"], seen["rows"][2]["promised_date"]) == (0, None)
    assert len(seen["plan"]) == 32
    assert seen == state(client, world, batch)["preview"] == read(client, world, batch, world.owner_a).json()["preview"]
    assert nothing_saved(owner, world) == before, "looking changes nothing"


def test_a_row_that_could_mean_two_customers_is_an_error(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    owner.execute(
        "INSERT INTO customer (id, shop_id, display_name, name_norm) VALUES (%s, %s, 'Vali', 'vali')",
        (uuid.uuid4(), world.shop_a),
    )
    body = state(client, world, uploaded(client, world, table("Vali,,5000,,", "Karim,,1000,,")))
    assert body["status"] == "rejected"
    assert body["errors"] == [{"row": 2, "column": "name", "code": "ambiguous_customer"}]


def test_two_rows_for_one_customer_of_the_shop_count_that_customer_once(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    batch = uploaded(client, world, table("Ali,,1000,,", "ali,,2000,,", "Karim,,3000,,"))
    seen = state(client, world, batch)["preview"]
    assert seen["counts"] == {"new_customers": 1, "existing_customers": 1, "entries": 3, "amount": 6000}
    assert apply(client, world, batch, seen["plan"]).status_code == 202
    work()
    assert state(client, world, batch)["applied"] == seen["counts"]
    assert [row[:4] for row in entries(owner, world, batch)] == [
        ("Ali", 2, "opening", 1000),
        ("Ali", 3, "opening", 2000),
        ("Karim", 1, "opening", 3000),
    ]


# --- apply ------------------------------------------------------------------------------------------------


def test_applying_is_asked_for_and_then_done_by_the_worker_one_opening_balance_per_row(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    due = today() - timedelta(days=10)  # an old debt that is overdue already
    batch = uploaded(
        client,
        world,
        table(f"Karim,901234567,250000,{typed(due)},eski qarz", "ali,,70000,,", "Lola,,80000,,", "karim,,30000,,"),
    )
    before = nothing_saved(owner, world)
    response = apply(client, world, batch, user=world.owner_a)
    assert response.status_code == 202, response.text
    assert (response.json()["status"], response.json()["applied"]) == ("applying", None)
    assert nothing_saved(owner, world) == before, "the request itself writes nothing to the ledger"

    assert work() == 1
    body = state(client, world, batch)
    counts = {"new_customers": 2, "existing_customers": 1, "entries": 4, "amount": 430000}
    assert (body["status"], body["applied"], body["undone"], body["refused"]) == ("applied", counts, None, None)
    assert body["preview"] is None, "the rows are in the ledger; the batch keeps them no longer"
    undo_until = datetime.fromisoformat(body["undo_until"]) - datetime.fromisoformat(body["applied_at"])
    assert undo_until == timedelta(hours=24)

    default = today() + timedelta(days=30)
    assert entries(owner, world, batch) == [
        ("Ali", 2, "opening", 70000, None, default, "default"),
        ("Karim", 1, "opening", 250000, "eski qarz", due, "staff"),
        ("Karim", 2, "opening", 30000, None, default, "default"),
        ("Lola", 1, "opening", 80000, None, default, "default"),
    ]
    assert customers(owner, world) == [
        ("Ali", "ali", None, "active"),
        ("Karim", "karim", "+998901234567", "active"),
        ("Lola", "lola", None, "active"),
        ("Sobir", "sobir", None, "archived"),
        ("Vali", "vali", None, "active"),
    ]
    assert [balance(owner, world, name) for name in ("Ali", "Karim", "Lola")] == [120000, 280000, 80000]
    # REQ-063: marked as imported, with author and time. The author is who asked for the import to be applied.
    marks = owner.execute(
        "SELECT DISTINCT import_batch_id, author_id, created_at FROM ledger_entry WHERE import_batch_id = %s", (batch,)
    ).fetchall()
    assert [(row[0], row[1]) for row in marks] == [(uuid.UUID(batch), world.owner_a_membership)]
    assert marks[0][2] == datetime.fromisoformat(body["applied_at"])

    stored = batches(owner, world)[0]
    assert (stored[0], stored[3], stored[1]["applied"]) == ("applied", True, counts)
    assert len(stored[1]["created_customers"]) == 2
    kept = owner.execute(
        "SELECT preview, plan, attempts, started_at FROM import_batch WHERE id = %s", (batch,)
    ).fetchone()
    assert kept == (None, None, 0, None)
    assert activity(owner, world) == [
        ("import.uploaded", "import", world.manager_a_membership),
        ("import.apply_requested", "import", world.owner_a_membership),
        ("import.applied", "import", world.owner_a_membership),
    ]
    # The rows are in the ledger: the file is due for deletion at once.
    assert import_file(owner, world)[0][3] <= datetime.now(UTC)
    # Overdue status follows the imported date like any other.
    assert detail(client, world, world.customer_a)["balance"] == 120000
    karim = detail(client, world, customer_id(owner, world, "Karim"))
    assert (karim["overdue"]["amount"], karim["overdue"]["since"]) == (250000, due.isoformat())
    assert [(e["kind"], e["import_id"]) for e in karim["entries"]] == [("opening", batch), ("opening", batch)]
    assert detail(client, world, world.customer_a)["entries"][1]["import_id"] is None


def test_applying_tells_the_owner_whoever_asked_and_a_linked_customer_and_measures_each_entry(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    before = measured(owner, "opening")
    batch = applied(client, world, table("Ali,,70000,,", "Lola,,80000,,"))  # asked for by the manager
    text = (
        f"📥 Shop A\nImport qo'llandi: 2 ta qarz yozuvi, jami {money('uz', 150000)}. "
        "Yangi mijozlar: 1 ta.\n24 soat ichida butunlay bekor qilish mumkin."
    )
    assert sorted(told(owner, batch, "s_import_applied")) == sorted(
        [(tg(owner, world.owner_a), text), (tg(owner, world.manager_a), text)]
    )
    ali_entry = owner.execute(
        "SELECT e.id FROM ledger_entry e WHERE e.import_batch_id = %s AND e.customer_id = %s", (batch, world.customer_a)
    ).fetchone()
    assert ali_entry is not None
    notes = staff_notices(owner, f"entry:{ali_entry[0]}:notify")
    assert [recipient for recipient, _ in notes] == [tg(owner, world.customer_of_a)]
    assert notes[0][1]["text"] == say(
        "uz",
        "n_opening",
        shop="Shop A",
        name="Ali",
        amount=money("uz", 70000),
        date=day(today() + timedelta(days=30)),
        balance=money("uz", 120000),
    )
    assert notes[0][1]["text"].startswith("Shop A\nAli, daftarga oldingi qarzingiz kiritildi:")
    assert [row[0]["callback_data"][:7] for row in notes[0][1]["reply_markup"]["inline_keyboard"]] == [
        "v2:dsp:",
        "v2:dmv:",
    ]
    # Lola is new and linked to nobody: one message to a customer in all.
    to_customers = owner.execute(
        "SELECT count(*) FROM outbox_message WHERE shop_id = %s AND dedupe_key LIKE 'entry:%%'", (world.shop_a,)
    ).fetchone()
    assert to_customers == (1,)
    assert measured(owner, "opening") - before == 2
    # A linked customer can object to an imported balance like to any other debt.
    disputed = client.post(
        f"{ME}/{link_of(owner, world.customer_a)}/disputes",
        json={"entry_id": str(ali_entry[0]), "reason": "Bu qarz to'langan"},
        headers=as_user(world.customer_of_a),
    )
    assert disputed.status_code == 201, disputed.text

    # Asked for by the owner: one person, told once.
    other = applied(client, world, table("Nodir,,5000,,"), user=world.owner_a)
    assert [who for who, _ in told(owner, other, "s_import_applied")] == [tg(owner, world.owner_a)]


def test_asking_twice_with_the_same_key_queues_one_step_and_a_second_request_waits_its_turn(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    batch = uploaded(client, world, table("Karim,,250000,,", "Lola,,80000,,"))
    plan, same = plan_of(client, world, batch), key()
    first = apply(client, world, batch, plan, headers=same)
    repeat = apply(client, world, batch, plan, headers=same)
    assert (first.status_code, repeat.status_code) == (202, 202)
    assert repeat.json() == first.json()
    assert [row[0] for row in activity(owner, world)].count("import.apply_requested") == 1
    again = apply(client, world, batch, plan)
    assert (again.status_code, again.json()["error"]["fields"]) == (409, {"reason": "applying"})
    for other in ("undo", "discard"):
        assert act(client, world, batch, other).status_code == 409

    assert work() == 1
    assert work() == 0
    assert len(entries(owner, world, batch)) == 2
    assert len(told(owner, batch, "s_import_applied")) == 2
    assert apply(client, world, batch, plan, headers=same).json() == first.json(), "the stored answer, nothing new"
    done = apply(client, world, batch, plan)
    assert (done.status_code, done.json()["error"]["fields"]) == (409, {"reason": "applied"})
    reused = apply(client, world, batch, "f" * 32, headers=same)
    assert (reused.status_code, reused.json()["error"]["code"]) == (409, "IDEMPOTENCY_KEY_REUSED")
    assert work() == 0
    assert len(entries(owner, world, batch)) == 2
    assert count(owner, world, "customer") == 5


def test_only_the_plan_that_was_shown_can_be_asked_for(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    batch = uploaded(client, world, table("Karim,,250000,,"))
    shown = plan_of(client, world, batch)
    for wrong in ("0" * 32, shown[:-1] + ("0" if shown[-1] != "0" else "1"), shown.upper() + "x"):
        refused = apply(client, world, batch, wrong)
        assert (refused.status_code, refused.json()["error"]["code"]) == (409, "IMPORT_NOT_APPLICABLE")
        assert refused.json()["error"]["fields"] == {"reason": "stale"}
    for malformed in ({}, {"plan": ""}, {"plan": 5}, {"plan": shown, "force": True}, None):
        assert act(client, world, batch, "apply", malformed).status_code == 422
    assert statuses(owner, world) == ["validated"]
    assert work() == 0, "nothing was queued"


def test_a_plan_that_is_no_longer_true_is_not_applied_and_the_batch_comes_back_with_a_fresh_preview(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    batch = uploaded(client, world, table("Karim,,250000,,"))
    shown = plan_of(client, world, batch)
    # Someone adds a customer called Karim after the check: the row would now go to that customer.
    added = client.post(
        f"{shop(world)}/customers", json={"display_name": "Karim"}, headers={**as_user(world.seller_a), **key()}
    )
    assert added.status_code == 201
    before = nothing_saved(owner, world)
    assert apply(client, world, batch, shown).status_code == 202  # the request cannot know yet
    assert work() == 1

    body = state(client, world, batch)
    assert (body["status"], body["refused"]) == ("validated", {"step": "apply", "reason": "stale"})
    assert nothing_saved(owner, world) == before, "nothing is applied on a preview nobody saw"
    seen = body["preview"]
    assert (seen["rows"][0]["action"], seen["rows"][0]["customer"]["id"]) == ("existing", added.json()["id"])
    assert seen["plan"] != shown
    assert told(owner, batch, "import_refused") == [
        (
            tg(owner, world.manager_a),
            "📥 Shop A\nImport qo'llanmadi: tekshiruvdan keyin ma'lumotlar o'zgargan. Ilovada qayta ko'rib chiqing.",
        )
    ]
    assert told(owner, batch, "s_import_applied") == []
    old = apply(client, world, batch, shown)
    assert (old.status_code, old.json()["error"]["fields"]) == (409, {"reason": "stale"})

    # Shown again, the merge is no longer unseen, and can be applied.
    assert apply(client, world, batch, seen["plan"]).status_code == 202
    work()
    done = state(client, world, batch)
    assert (done["status"], done["refused"]) == ("applied", None)
    assert entries(owner, world, batch) == [
        ("Karim", 1, "opening", 250000, None, today() + timedelta(days=30), "default")
    ]
    assert count(owner, world, "customer") == before[0]


def test_an_import_whose_rows_have_gone_wrong_since_the_check_is_not_applied(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    batch = uploaded(client, world, table("Vali,,5000,,", "Karim,,1000,,"))
    shown = plan_of(client, world, batch)
    owner.execute("UPDATE customer SET status = 'archived' WHERE id = %s", (world.settled_customer_a,))
    before = nothing_saved(owner, world)
    assert apply(client, world, batch, shown).status_code == 202
    work()
    body = state(client, world, batch)
    assert (body["status"], body["refused"]) == ("rejected", {"step": "apply", "reason": "errors"})
    assert body["errors"] == [{"row": 2, "column": "name", "code": "customer_archived"}]
    assert body["preview"]["plan"] is None
    assert nothing_saved(owner, world) == before, "the row without a problem is not applied either"


def test_an_import_is_applied_whole_or_not_at_all(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    """The ledger refuses the very last of 620 rows, after several chunks have been written."""
    lines = [f"Mijoz {number},,{1000 + number},," for number in range(619)] + ["Oxirgi,,666666,,"]
    batch = uploaded(client, world, table(*lines))
    plan = plan_of(client, world, batch)
    before = nothing_saved(owner, world)
    owner.execute(
        "CREATE FUNCTION test_refuse_666666() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
        "IF NEW.amount = 666666 THEN RAISE EXCEPTION 'refused by the test'; END IF; RETURN NEW; END $$"
    )
    owner.execute(
        "CREATE TRIGGER test_refuse_666666 BEFORE INSERT ON ledger_entry "
        "FOR EACH ROW EXECUTE FUNCTION test_refuse_666666()"
    )
    try:
        assert apply(client, world, batch, plan).status_code == 202
        assert work() == 1
    finally:
        owner.execute("DROP TRIGGER test_refuse_666666 ON ledger_entry")
        owner.execute("DROP FUNCTION test_refuse_666666()")
    body = state(client, world, batch)
    assert (body["status"], body["refused"]) == ("validated", {"step": "apply", "reason": "internal"})
    assert nothing_saved(owner, world) == before
    assert import_file(owner, world)[0][3] > datetime.now(UTC) + timedelta(days=29), "the file is still kept"
    assert told(owner, batch, "s_import_applied") == []
    assert [text for _, text in told(owner, batch, "import_failed")] == [
        "📥 Shop A\nImport bo'yicha so'ralgan amal bajarilmadi. Hech narsa o'zgarmadi; qayta urinib ko'ring."
    ]
    assert work() == 0, "a step that failed is not tried again by itself"
    # With the obstacle gone the same import, with the same preview, goes through.
    assert apply(client, world, batch, plan).status_code == 202
    work()
    assert state(client, world, batch)["status"] == "applied"
    assert len(entries(owner, world, batch)) == 620


def test_the_rows_of_one_new_customer_become_one_customer_with_several_entries(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    batch = applied(client, world, table("Karim,901234567,1000,,", "Karim,,2000,,", "Yangi ism,901234567,3000,,"))
    assert entries(owner, world, batch) == [
        ("Karim", 1, "opening", 1000, None, today() + timedelta(days=30), "default"),
        ("Karim", 2, "opening", 2000, None, today() + timedelta(days=30), "default"),
        ("Karim", 3, "opening", 3000, None, today() + timedelta(days=30), "default"),
    ]
    assert count(owner, world, "customer") == 4


def test_imports_are_refused_in_limited_mode_and_in_a_suspended_shop(
    client: TestClient, world: World, owner: psycopg.Connection, file_root: Path
) -> None:
    batch = uploaded(client, world, table("Karim,,250000,,"))
    plan = plan_of(client, world, batch)
    applied_batch = applied(client, world, table("Lola,,80000,,"))
    before = (nothing_saved(owner, world), batches(owner, world), len(stored_objects(file_root)))

    _subscription(owner, world, "state = 'limited'")
    for refused in (upload(client, world, table("Nodir,,1000,,")), apply(client, world, batch, plan)):
        assert (refused.status_code, refused.json()["error"]["code"]) == (402, "SUBSCRIPTION_LIMITED")
    assert (nothing_saved(owner, world), batches(owner, world), len(stored_objects(file_root))) == before
    assert read(client, world, batch).status_code == 200, "looking goes on (BR-29)"

    _subscription(owner, world, "state = 'suspended'")
    writes = [
        upload(client, world, table("Nodir,,1000,,"), world.owner_a),
        apply(client, world, batch, plan, user=world.owner_a),
        act(client, world, batch, "discard", user=world.owner_a),
        act(client, world, applied_batch, "undo", user=world.owner_a),
        read(client, world, batch, world.manager_a),
        client.get(f"{shop(world)}/imports", headers=as_user(world.manager_a)),
        client.get(f"{shop(world)}/imports/template", headers=as_user(world.manager_a)),
    ]
    for refused in writes:
        assert (refused.status_code, refused.json()["error"]["code"]) == (403, "SHOP_SUSPENDED")
    assert (nothing_saved(owner, world), batches(owner, world), len(stored_objects(file_root))) == before
    assert work() == 0
    # BR-30: the owner may still look.
    assert read(client, world, batch, world.owner_a).status_code == 200
    assert len(client.get(f"{shop(world)}/imports", headers=as_user(world.owner_a)).json()["items"]) == 2

    # A mistake can still be taken back in limited mode: an undo is reversals, which BR-29 allows.
    _subscription(owner, world, "state = 'limited'")
    assert undone(client, world, applied_batch)["status"] == "undone"
    assert act(client, world, batch, "discard").status_code == 200


def test_an_import_the_free_plan_cannot_hold_is_refused_and_one_that_fits_is_applied(
    client: TestClient, world: World, owner: psycopg.Connection, free_plan: Any
) -> None:
    """BR-34 for an import: the customers it would add need places like any other."""
    _subscription(owner, world, "state = 'limited', trial_ends = NULL, paid_through = NULL")
    held = _active(owner, world) + 1
    free_plan(held)
    batch = uploaded(client, world, table("Karim,,250000,,", "Lola,,80000,,"))  # a free shop may import
    plan = plan_of(client, world, batch)
    before = nothing_saved(owner, world)

    refused = apply(client, world, batch, plan)
    assert (refused.status_code, refused.json()["error"]["code"]) == (402, "FREE_PLAN_FULL")
    assert refused.json()["error"]["fields"] == {"limit": str(held)}
    assert (nothing_saved(owner, world), state(client, world, batch)["status"]) == (before, "validated")
    assert work() == 0, "nothing was queued"

    free_plan(held + 1)
    assert apply(client, world, batch, plan).status_code == 202
    assert work() == 1
    assert state(client, world, batch)["status"] == "applied"
    assert _active(owner, world) == held + 1


def test_the_worker_asks_again_whether_the_customers_of_an_import_fit(
    client: TestClient, world: World, owner: psycopg.Connection, free_plan: Any
) -> None:
    _subscription(owner, world, "state = 'limited', trial_ends = NULL, paid_through = NULL")
    held = _active(owner, world) + 1
    free_plan(held)
    batch = uploaded(client, world, table("Karim,,250000,,"))
    shown = plan_of(client, world, batch)
    assert apply(client, world, batch, shown).status_code == 202  # it fits when it is asked for
    # The last place is taken before the worker comes to the batch.
    taken = client.post(
        f"{shop(world)}/customers", json={"display_name": "Oldinroq"}, headers={**as_user(world.seller_a), **key()}
    )
    assert taken.status_code == 201
    before = nothing_saved(owner, world)
    assert work() == 1

    body = state(client, world, batch)
    assert (body["status"], body["refused"]) == ("validated", {"step": "apply", "reason": "free_plan_full"})
    assert nothing_saved(owner, world) == before, "all of it or nothing (BR-24)"
    assert told(owner, batch, "import_refused_free_plan") == [
        (tg(owner, world.manager_a), say("uz", "import_refused_free_plan", shop="Shop A", limit=held))
    ]
    assert told(owner, batch, "s_import_applied") == []

    # Once the shop pays, the same preview is applied.
    _subscription(owner, world, f"state = 'active', paid_through = '{today() + timedelta(days=30)}'")
    assert apply(client, world, batch, body["preview"]["plan"]).status_code == 202
    assert work() == 1
    assert state(client, world, batch)["status"] == "applied"


def _active(owner: psycopg.Connection, world: World) -> int:
    row = owner.execute(
        "SELECT count(*) FROM customer WHERE shop_id = %s AND status = 'active'", (world.shop_a,)
    ).fetchone()
    assert row is not None
    return int(row[0])


# --- undo -------------------------------------------------------------------------------------------------


def test_an_undo_is_asked_for_and_the_worker_reverses_every_entry_and_archives_the_new_customers(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    batch = applied(client, world, table("Karim,,250000,,", "ali,,70000,,", "Lola,,80000,,", "karim,,30000,,"))
    rows_before = count(owner, world, "ledger_entry")
    reversal_activity = owner.execute(
        "SELECT count(*) FROM activity WHERE shop_id = %s AND action = 'ledger.entry_reversed'", (world.shop_a,)
    ).fetchone()
    reversals_measured = measured(owner, "reversal")

    response = act(client, world, batch, "undo", user=world.owner_a)
    assert (response.status_code, response.json()["status"]) == (202, "undoing")
    assert count(owner, world, "ledger_entry") == rows_before, "the request itself reverses nothing"
    assert work() == 1
    body = state(client, world, batch)
    assert (body["status"], body["undone"], body["undo_until"]) == ("undone", {"reversed": 4, "archived": 2}, None)
    assert body["applied_at"] is not None, "when it was applied stays on record"
    assert body["applied"]["entries"] == 4

    # REQ-N07: nothing is deleted or changed; four reversals are added.
    assert count(owner, world, "ledger_entry") == rows_before + 4
    assert len(entries(owner, world, batch)) == 4
    reversals = owner.execute(
        "SELECT r.amount, r.author_id, r.kind, r.import_batch_id, c.display_name, r.seq FROM ledger_entry r "
        "JOIN ledger_entry e ON e.id = r.reverses_id JOIN customer c ON c.id = r.customer_id "
        "WHERE e.import_batch_id = %s ORDER BY r.amount",
        (batch,),
    ).fetchall()
    who = world.owner_a_membership
    assert reversals == [
        (30000, who, "reversal", None, "Karim", 4),
        (70000, who, "reversal", None, "Ali", 3),
        (80000, who, "reversal", None, "Lola", 2),
        (250000, who, "reversal", None, "Karim", 3),
    ]
    assert [balance(owner, world, name) for name in ("Ali", "Karim", "Lola")] == [50000, 0, 0]
    assert customers(owner, world) == [
        ("Ali", "ali", None, "active"),  # was there before the import, and still owes
        ("Karim", "karim", None, "archived"),
        ("Lola", "lola", None, "archived"),
        ("Sobir", "sobir", None, "archived"),
        ("Vali", "vali", None, "active"),
    ]
    # Everything a reversal means, for each of the four: its activity row and its measurement row.
    after_activity = owner.execute(
        "SELECT count(*) FROM activity WHERE shop_id = %s AND action = 'ledger.entry_reversed' AND actor_id = %s",
        (world.shop_a, who),
    ).fetchone()
    assert reversal_activity is not None and after_activity is not None
    assert after_activity[0] - reversal_activity[0] == 4
    assert measured(owner, "reversal") - reversals_measured == 4
    assert statuses(owner, world) == ["undone"]
    assert activity(owner, world)[-2:] == [("import.undo_requested", "import", who), ("import.undone", "import", who)]
    assert told(owner, batch, "s_import_undone") == [
        (
            tg(owner, world.owner_a),
            f"↩️ Shop A\nImport bekor qilindi: 4 ta yozuv qaytarildi. Import summasi: {money('uz', 430000)}.",
        )
    ]
    # The linked customer is told that the imported balance was taken back.
    to_ali = owner.execute(
        "SELECT payload->>'text' FROM outbox_message WHERE shop_id = %s AND recipient = %s ORDER BY created_at, id",
        (world.shop_a, tg(owner, world.customer_of_a)),
    ).fetchall()
    assert to_ali[-1][0] == say(
        "uz", "n_reversed_credit", shop="Shop A", name="Ali", amount=money("uz", 70000), balance=money("uz", 50000)
    )
    assert len(to_ali) == 2, "the imported balance, and its reversal"
    # The ledger the domain reads is whole: the detail of each account still adds up.
    assert detail(client, world, world.customer_a)["balance"] == 50000
    assert detail(client, world, customer_id(owner, world, "Karim"))["balance"] == 0

    again = act(client, world, batch, "undo")
    assert again.json()["error"] == {
        "code": "IMPORT_UNDO_REFUSED",
        "message": "Bu importni bekor qilib bo'lmaydi: muddat o'tgan yoki yozuvlarga to'lov qilingan.",
        "fields": {"reason": "not_applied"},
    }
    assert apply(client, world, batch, "0" * 32).status_code == 409
    assert work() == 0
    assert count(owner, world, "ledger_entry") == rows_before + 4


def test_asking_for_an_undo_twice_with_the_same_key_reverses_once(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    batch = applied(client, world, table("Karim,,250000,,"))
    same = key()
    first = act(client, world, batch, "undo", headers=same)
    repeat = act(client, world, batch, "undo", headers=same)
    assert (first.status_code, repeat.status_code, repeat.json()) == (202, 202, first.json())
    second = act(client, world, batch, "undo")
    assert (second.status_code, second.json()["error"]["fields"]) == (409, {"reason": "not_applied"})
    assert work() == 1
    assert work() == 0
    assert count(owner, world, "ledger_entry") == 3
    assert len(told(owner, batch, "s_import_undone")) == 2


def test_an_undo_can_be_asked_for_during_twenty_four_hours_and_no_longer(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    late = applied(client, world, table("Karim,,250000,,"))
    in_time = applied(client, world, table("Lola,,80000,,"))
    owner.execute("UPDATE import_batch SET applied_at = now() - interval '24 hours 1 minute' WHERE id = %s", (late,))
    owner.execute(
        "UPDATE import_batch SET applied_at = now() - interval '23 hours 59 minutes' WHERE id = %s", (in_time,)
    )
    before = count(owner, world, "ledger_entry")
    refused = act(client, world, late, "undo")
    assert (refused.status_code, refused.json()["error"]["code"]) == (409, "IMPORT_UNDO_REFUSED")
    assert refused.json()["error"]["fields"] == {"reason": "too_late"}
    assert work() == 0
    assert count(owner, world, "ledger_entry") == before

    # Asked for in time, it is done even if the worker gets to it after the twenty-fourth hour.
    assert act(client, world, in_time, "undo").status_code == 202
    owner.execute("UPDATE import_batch SET applied_at = now() - interval '25 hours' WHERE id = %s", (in_time,))
    assert work() == 1
    assert statuses(owner, world) == ["applied", "undone"]


@pytest.mark.parametrize("status", ["uploaded", "validated", "rejected", "applying", "undoing", "discarded", "failed"])
def test_only_an_applied_import_can_be_undone(
    client: TestClient, world: World, owner: psycopg.Connection, status: str
) -> None:
    batch = uploaded(client, world, table("Karim,,250000,,"))
    owner.execute(
        "UPDATE import_batch SET status = %s, applied_at = now(), started_at = now() WHERE id = %s", (status, batch)
    )
    refused = act(client, world, batch, "undo")
    assert (refused.status_code, refused.json()["error"]["fields"]) == (409, {"reason": "not_applied"})
    assert count(owner, world, "ledger_entry") == 1


def test_an_import_whose_balance_has_been_paid_against_is_not_undone_at_all(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    batch = applied(client, world, table("Karim,,250000,,", "Lola,,80000,,"))
    lola = customer_id(owner, world, "Lola")
    payment = record(client, world, lola, "payment", 30000)
    assert payment.status_code == 201
    before = (count(owner, world, "ledger_entry"), customers(owner, world))

    assert act(client, world, batch, "undo").status_code == 202  # the request cannot know yet
    assert work() == 1
    body = state(client, world, batch)
    assert (body["status"], body["refused"], body["undone"]) == (
        "applied",
        {"step": "undo", "reason": "balance_used"},
        None,
    )
    # Undone whole or not at all: Karim's entry, which could have been reversed, is not.
    assert (count(owner, world, "ledger_entry"), customers(owner, world)) == before
    assert balance(owner, world, "Karim") == 250000
    assert told(owner, batch, "s_import_undone") == []
    assert told(owner, batch, "import_undo_refused") == [
        (
            tg(owner, world.manager_a),
            "↩️ Shop A\nImportni bekor qilib bo'lmadi: uning yozuvlariga to'lov qilingan. Hech narsa o'zgarmadi.",
        )
    ]
    # With the payment taken back, the undo that is asked for again goes through.
    assert reverse(client, world, payment.json()["entry"]["id"]).status_code == 201
    again = undone(client, world, batch)
    assert (again["status"], again["refused"], again["undone"]) == ("undone", None, {"reversed": 2, "archived": 2})


def test_a_payment_of_exactly_what_was_owed_before_the_import_does_not_block_the_undo(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    batch = applied(client, world, table("Ali,,70000,,"))
    assert record(client, world, world.customer_a, "payment", 50000).status_code == 201  # all of the old debt
    assert undone(client, world, batch)["undone"] == {"reversed": 1, "archived": 0}
    assert balance(owner, world, "Ali") == 0
    assert record(client, world, world.customer_a, "credit", 1000).status_code == 201, "the account is still sound"


def test_an_undo_skips_what_was_reversed_by_hand_and_keeps_a_customer_who_owes_for_something_else(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    batch = applied(client, world, table("Karim,,250000,,", "Lola,,80000,,", "Nodir,,5000,,"))
    ids = dict(
        owner.execute(
            "SELECT c.display_name, e.id FROM ledger_entry e JOIN customer c ON c.id = e.customer_id "
            "WHERE e.import_batch_id = %s",
            (batch,),
        ).fetchall()
    )
    assert reverse(client, world, ids["Karim"]).status_code == 201
    assert record(client, world, customer_id(owner, world, "Lola"), "credit", 12000).status_code == 201

    body = undone(client, world, batch)
    assert body["undone"] == {"reversed": 2, "archived": 2}
    assert [balance(owner, world, name) for name in ("Karim", "Lola", "Nodir")] == [0, 12000, 0]
    states = {name: status for name, _, _, status in customers(owner, world)}
    assert (states["Karim"], states["Lola"], states["Nodir"]) == ("archived", "active", "archived")
    once = owner.execute("SELECT count(*) FROM ledger_entry r WHERE r.reverses_id = %s", (ids["Karim"],)).fetchone()
    assert once == (1,)


def test_an_undo_does_for_disputes_and_date_requests_what_any_reversal_does(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    """A dispute on a reversed entry ends; a date request ends on a reversed entry and on one that the
    freed payment now covers in full, and stays open on one that is still owed."""
    batch = applied(client, world, table("Ali,,70000,,"))
    imported = owner.execute("SELECT id FROM ledger_entry WHERE import_batch_id = %s", (batch,)).fetchone()
    assert imported is not None
    link = link_of(owner, world.customer_a)
    paid_later = record(client, world, world.customer_a, "credit", 30000).json()["entry"]["id"]
    still_owed = record(client, world, world.customer_a, "credit", 9000).json()["entry"]["id"]
    for entry in (imported[0], paid_later, still_owed):
        asked = client.post(
            f"{ME}/{link}/date-requests",
            json={"entry_id": str(entry), "requested_date": (today() + timedelta(days=45)).isoformat()},
            headers=as_user(world.customer_of_a),
        )
        assert asked.status_code == 201, asked.text
    disputed = client.post(
        f"{ME}/{link}/disputes",
        json={"entry_id": str(imported[0]), "reason": "Bu qarz to'langan"},
        headers=as_user(world.customer_of_a),
    )
    assert disputed.status_code == 201
    # 80 000 paid: the old 50 000 and 30 000 of the imported 70 000. Without the import it covers the old
    # debt and the 30 000 sale in full.
    assert record(client, world, world.customer_a, "payment", 80000).status_code == 201

    assert undone(client, world, batch)["status"] == "undone"
    dispute = owner.execute("SELECT status, decided_by FROM dispute WHERE entry_id = %s", (imported[0],)).fetchone()
    assert dispute == ("reversed", world.manager_a_membership)
    requests = dict(
        owner.execute(
            "SELECT entry_id::text, status FROM date_change_request WHERE shop_id = %s", (world.shop_a,)
        ).fetchall()
    )
    assert requests == {str(imported[0]): "expired", paid_later: "expired", still_owed: "open"}
    assert balance(owner, world, "Ali") == 9000


def test_an_undo_carries_out_a_removal_request_that_waited_for_the_balance(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    batch = applied(client, world, table("Karim,,250000,,", "Lola,,80000,,"))
    karim = customer_id(owner, world, "Karim")
    owner.execute(
        "INSERT INTO removal_request (id, shop_id, customer_id, status) VALUES (%s, %s, %s, 'waiting')",
        (uuid.uuid4(), world.shop_a, karim),
    )
    assert undone(client, world, batch)["undone"] == {"reversed": 2, "archived": 1}
    row = owner.execute("SELECT status, display_name FROM customer WHERE id = %s", (karim,)).fetchone()
    assert row is not None
    assert row[0] == "anonymized" and "Karim" not in row[1], "removed, as asked; not merely archived"
    assert owner.execute("SELECT status FROM removal_request WHERE customer_id = %s", (karim,)).fetchone() == (
        "completed",
    )


def test_a_customer_whose_data_was_removed_is_never_matched_and_never_archived_by_an_undo(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    owner.execute(
        "INSERT INTO customer (id, shop_id, display_name, name_norm, status) "
        "VALUES (%s, %s, 'Anonim ABC123', 'karim', 'anonymized')",
        (uuid.uuid4(), world.shop_a),
    )
    batch = uploaded(client, world, table("Karim,,250000,,", "Lola,,80000,,"))
    seen = state(client, world, batch)["preview"]
    assert [row["action"] for row in seen["rows"]] == ["create", "create"]
    assert apply(client, world, batch, seen["plan"]).status_code == 202
    work()

    # Lola asks for her data to be removed; then the import is undone.
    owner.execute(
        "UPDATE customer SET status = 'anonymized', display_name = 'Anonim LOLA01' "
        "WHERE shop_id = %s AND display_name = 'Lola'",
        (world.shop_a,),
    )
    assert undone(client, world, batch)["undone"] == {"reversed": 2, "archived": 1}
    states = {name: status for name, _, _, status in customers(owner, world)}
    assert (states["Karim"], states["Anonim LOLA01"]) == ("archived", "anonymized")


# --- discard, the list, and other shops -----------------------------------------------------------------


def test_a_batch_that_is_not_wanted_is_discarded_and_its_file_and_preview_go(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    batch = uploaded(client, world, table("Karim,,250000,,"))
    plan = plan_of(client, world, batch)
    response = act(client, world, batch, "discard")
    assert (response.status_code, response.json()["status"]) == (200, "discarded")
    assert import_file(owner, world)[0][3] <= datetime.now(UTC)
    assert activity(owner, world)[-1] == ("import.discarded", "import", world.manager_a_membership)
    assert state(client, world, batch)["preview"] is None
    assert owner.execute("SELECT preview, plan FROM import_batch WHERE id = %s", (batch,)).fetchone() == (None, None)

    for again in (act(client, world, batch, "discard"), apply(client, world, batch, plan)):
        assert (again.status_code, again.json()["error"]["code"]) == (409, "IMPORT_NOT_APPLICABLE")
        assert again.json()["error"]["fields"] == {"reason": "discarded"}
    assert nothing_saved(owner, world) == (3, 1, 1)

    done = applied(client, world, table("Lola,,80000,,"))
    refused = act(client, world, done, "discard")
    assert (refused.status_code, refused.json()["error"]["fields"]) == (409, {"reason": "applied"})
    with_errors = uploaded(client, world, table("Lola,,abc,,"))
    assert act(client, world, with_errors, "discard").json()["status"] == "discarded"
    # One that is discarded before the worker gets to it is never checked.
    waiting = upload(client, world, table("Nodir,,1000,,")).json()["id"]
    assert act(client, world, waiting, "discard").json()["status"] == "discarded"
    assert work() == 0
    assert state(client, world, waiting)["status"] == "discarded"


def test_discarding_twice_with_the_same_key_is_one_discard(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    batch = uploaded(client, world, table("Karim,,250000,,"))
    same = key()
    first = act(client, world, batch, "discard", headers=same)
    repeat = act(client, world, batch, "discard", headers=same)
    assert (first.status_code, repeat.status_code, repeat.json()) == (200, 200, first.json())
    assert [row[0] for row in activity(owner, world)] == ["import.uploaded", "import.discarded"]


def test_the_list_shows_the_shops_own_imports_newest_first(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    first = uploaded(client, world, table("Karim,,250000,,"))
    second = applied(client, world, table("Lola,,80000,,"))
    owner.execute("UPDATE import_batch SET created_at = created_at - interval '1 hour' WHERE id = %s", (first,))
    listed = client.get(f"{shop(world)}/imports", headers=as_user(world.owner_a)).json()["items"]
    assert [(item["id"], item["status"]) for item in listed] == [(second, "applied"), (first, "validated")]
    assert set(listed[0]) == {
        "id", "status", "format", "author_id", "created_at", "applied_at", "undo_until", "rows", "file_problem",
        "errors", "applied", "undone", "refused",
    }  # fmt: skip
    theirs = client.get(f"/api/v1/shops/{world.shop_b}/imports", headers=as_user(world.owner_b))
    assert theirs.json() == {"items": []}

    for number in range(21):
        owner.execute(
            "INSERT INTO import_batch (id, shop_id, status, author_id, created_at) "
            "VALUES (gen_random_uuid(), %s, 'discarded', %s, now() + make_interval(mins => %s))",
            (world.shop_a, world.owner_a_membership, number + 1),
        )
    listed = client.get(f"{shop(world)}/imports", headers=as_user(world.owner_a)).json()["items"]
    assert len(listed) == 20
    assert {item["status"] for item in listed} == {"discarded"}


def test_an_import_of_another_shop_does_not_exist(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    batch = uploaded(client, world, table("Karim,,250000,,"))
    plan = plan_of(client, world, batch)
    other = f"/api/v1/shops/{world.shop_b}/imports"
    answers = [
        client.get(f"{other}/{batch}", headers=as_user(world.owner_b)),
        client.post(f"{other}/{batch}/apply", json={"plan": plan}, headers={**as_user(world.owner_b), **key()}),
        client.post(f"{other}/{batch}/undo", headers={**as_user(world.owner_b), **key()}),
        client.post(f"{other}/{batch}/discard", headers={**as_user(world.owner_b), **key()}),
        read(client, world, uuid.uuid4()),
        apply(client, world, uuid.uuid4(), plan),
        act(client, world, uuid.uuid4(), "undo"),
        act(client, world, uuid.uuid4(), "discard"),
        read(client, world, "template-not"),
    ]
    for response in answers:
        assert (response.status_code, response.json()["error"]["code"]) == (404, "NOT_FOUND")
    assert statuses(owner, world) == ["validated"]
    assert nothing_saved(owner, world) == (3, 1, 1)
    # Shop B's own import is checked against shop B's customers only: its "Ali" is a new customer there,
    # and applying it adds nothing to shop A.
    theirs = client.post(other, content=table("Ali,,1000,,"), headers={**as_user(world.owner_b), **key()}).json()
    assert work() == 1
    seen = client.get(f"{other}/{theirs['id']}", headers=as_user(world.owner_b)).json()["preview"]
    assert (seen["rows"][0]["action"], seen["rows"][0]["customer"]) == ("create", None)
    asked = client.post(
        f"{other}/{theirs['id']}/apply", json={"plan": seen["plan"]}, headers={**as_user(world.owner_b), **key()}
    )
    assert asked.status_code == 202
    assert work() == 1
    assert nothing_saved(owner, world) == (3, 1, 1)
    in_b = owner.execute(
        "SELECT c.display_name, e.amount FROM ledger_entry e JOIN customer c ON c.id = e.customer_id "
        "WHERE e.shop_id = %s",
        (world.shop_b,),
    ).fetchall()
    assert in_b == [("Ali", 1000)]


# --- the file is kept only as long as it is needed ------------------------------------------------------


def test_the_file_of_an_applied_import_is_deleted_by_the_hourly_job_and_the_batch_stays(
    client: TestClient, world: World, owner: psycopg.Connection, worker_database_url: str, file_root: Path
) -> None:
    done = applied(client, world, table("Karim,,250000,,"))
    waiting = uploaded(client, world, table("Lola,,80000,,"))
    assert len(stored_objects(file_root)) == 2

    with_services(worker_database_url, file_root, lambda notices, files: notices.run_hourly())
    assert [path.read_bytes() for path in stored_objects(file_root)] == [table("Lola,,80000,,")]
    assert len(import_file(owner, world)) == 1
    kept = owner.execute("SELECT id, file_id IS NULL FROM import_batch WHERE shop_id = %s", (world.shop_a,)).fetchall()
    assert sorted(kept) == sorted([(uuid.UUID(done), True), (uuid.UUID(waiting), False)])
    assert state(client, world, done)["status"] == "applied"
    assert undone(client, world, done)["status"] == "undone", "an undo does not need the file"

    # Thirty days later the batch that was never applied loses its file and, with it, the rows it showed.
    plan = plan_of(client, world, waiting)
    owner.execute(
        "UPDATE stored_file SET delete_after = now() - interval '1 minute' WHERE shop_id = %s", (world.shop_a,)
    )
    with_services(worker_database_url, file_root, lambda notices, files: notices.run_hourly())
    assert stored_objects(file_root) == []
    assert import_file(owner, world) == []
    gone = state(client, world, waiting)
    assert (gone["status"], gone["preview"]) == ("validated", None)
    assert apply(client, world, waiting, plan).status_code == 202
    work()
    refused = state(client, world, waiting)
    assert (refused["status"], refused["refused"]) == ("validated", {"step": "apply", "reason": "file_gone"})
    assert count(owner, world, "customer") == 4


def test_a_file_that_has_vanished_from_the_store_cannot_be_checked_or_applied(
    client: TestClient, world: World, owner: psycopg.Connection, file_root: Path
) -> None:
    checked = uploaded(client, world, table("Karim,,250000,,"))
    plan = plan_of(client, world, checked)
    unchecked = upload(client, world, table("Lola,,80000,,")).json()["id"]
    for path in stored_objects(file_root):
        path.unlink()
    assert apply(client, world, checked, plan).status_code == 202
    assert work() == 2
    first, second = state(client, world, checked), state(client, world, unchecked)
    assert (first["status"], first["refused"]) == ("validated", {"step": "apply", "reason": "file_gone"})
    assert (second["status"], second["file_problem"], second["preview"]) == ("rejected", "file_gone", None)
    assert nothing_saved(owner, world) == (3, 1, 1)


# --- the worker -------------------------------------------------------------------------------------------


def test_two_workers_at_once_do_each_step_once(
    client: TestClient, world: World, owner: psycopg.Connection, worker_database_url: str, file_root: Path
) -> None:
    waiting = [uploaded(client, world, table(f"{name},,1000,,")) for name in ("Karim", "Lola", "Nodir")]
    for batch in waiting:
        assert apply(client, world, batch).status_code == 202
    unchecked = upload(client, world, table("Zafar,,2000,,")).json()["id"]

    async def both() -> list[int]:
        first, second = Database(worker_database_url), Database(worker_database_url)
        try:
            store = FilesystemFileStore(file_root)
            return list(
                await asyncio.gather(
                    ImportService(first, FileService(first, store)).run_pending(10),
                    ImportService(second, FileService(second, store)).run_pending(10),
                )
            )
        finally:
            await first.dispose()
            await second.dispose()

    assert sum(asyncio.run(both())) == 4, "four steps waited; each was taken by exactly one worker"
    assert statuses(owner, world) == ["applied", "applied", "applied", "validated"]
    assert [row[:4] for row in entries(owner, world)] == [
        ("Ali", 1, "credit", 50000),
        ("Karim", 1, "opening", 1000),
        ("Lola", 1, "opening", 1000),
        ("Nodir", 1, "opening", 1000),
    ]
    for batch in waiting:
        assert len(told(owner, batch, "s_import_applied")) == 2, "each person told once"
    assert len(told(owner, unchecked, "import_checked")) == 1


def test_a_worker_that_takes_a_step_another_has_done_does_nothing(
    client: TestClient, world: World, owner: psycopg.Connection, worker_database_url: str, file_root: Path
) -> None:
    """A worker that was silent too long and wakes up after another has taken over its batch."""
    batch = uploaded(client, world, table("Karim,,250000,,"))
    assert apply(client, world, batch).status_code == 202

    async def twice(step: str) -> None:
        database = Database(worker_database_url)
        try:
            service = ImportService(database, FileService(database, FilesystemFileStore(file_root)))
            await service.do_step(world.shop_a, uuid.UUID(batch), step)
            await service.do_step(world.shop_a, uuid.UUID(batch), step)
        finally:
            await database.dispose()

    asyncio.run(twice("applying"))
    assert len(entries(owner, world, batch)) == 1
    assert len(told(owner, batch, "s_import_applied")) == 2
    asyncio.run(twice("uploaded"))  # and a check that is no longer wanted changes nothing
    assert state(client, world, batch)["status"] == "applied"

    assert act(client, world, batch, "undo").status_code == 202
    asyncio.run(twice("undoing"))
    assert count(owner, world, "ledger_entry") == 3, "one entry of the world, one imported, one reversal"
    assert state(client, world, batch)["undone"] == {"reversed": 1, "archived": 1}


def test_a_step_whose_worker_died_is_taken_again_and_given_up_after_three_starts(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    batch = uploaded(client, world, table("Karim,,250000,,"))
    assert apply(client, world, batch).status_code == 202

    def claim(minutes_ago: int, attempts: int) -> None:
        owner.execute(
            "UPDATE import_batch SET started_at = now() - make_interval(mins => %s), attempts = %s WHERE id = %s",
            (minutes_ago, attempts, batch),
        )

    claim(14, 1)  # a worker took it fourteen minutes ago and may still be at it
    assert work() == 0
    assert nothing_saved(owner, world) == (3, 1, 1), "what a dead worker left is exactly what it found"
    claim(16, 3)  # the third worker too has been silent for more than fifteen minutes
    assert work() == 1
    body = state(client, world, batch)
    assert (body["status"], body["refused"]) == ("validated", {"step": "apply", "reason": "interrupted"})
    assert nothing_saved(owner, world) == (3, 1, 1)
    assert len(told(owner, batch, "import_failed")) == 1
    assert owner.execute("SELECT attempts, started_at FROM import_batch WHERE id = %s", (batch,)).fetchone() == (
        0,
        None,
    )

    # Asked for again, it starts from nothing; a worker silent for sixteen minutes is replaced and the
    # second start does the work.
    assert apply(client, world, batch).status_code == 202
    claim(16, 1)
    assert work() == 1
    assert state(client, world, batch)["status"] == "applied"

    # The same for a check and for an undo: given up, the batch is left where the person can act on it.
    unchecked = upload(client, world, table("Lola,,80000,,")).json()["id"]
    owner.execute(
        "UPDATE import_batch SET started_at = now() - interval '16 minutes', attempts = 3 WHERE id = %s", (unchecked,)
    )
    assert act(client, world, batch, "undo").status_code == 202
    claim(16, 3)
    assert work() == 2
    failed, kept = state(client, world, unchecked), state(client, world, batch)
    assert (failed["status"], failed["refused"]) == ("failed", {"step": "check", "reason": "interrupted"})
    assert (kept["status"], kept["refused"]) == ("applied", {"step": "undo", "reason": "interrupted"})
    assert act(client, world, unchecked, "discard").json()["status"] == "discarded"
    assert undone(client, world, batch)["status"] == "undone"


def test_an_apply_waits_for_a_payment_being_recorded_on_a_matched_customer(
    client: TestClient, world: World, owner: psycopg.Connection, database_url: str
) -> None:
    """Like every write to an account: the customer row is locked before the account is read, so the
    import's entry is numbered after the payment and never beside it."""
    batch = uploaded(client, world, table("Ali,,70000,,", "Karim,,1000,,"))
    assert apply(client, world, batch).status_code == 202
    finished = threading.Event()

    def run() -> None:
        work()
        finished.set()

    with psycopg.connect(database_url) as cashier:
        # A payment in progress: the customer row is locked, as the application locks it.
        cashier.execute("SELECT id FROM customer WHERE id = %s FOR UPDATE", (world.customer_a,))
        thread = threading.Thread(target=run)
        thread.start()
        assert not finished.wait(1.5), "the worker waits for the account"
        assert nothing_saved(owner, world) == (3, 1, 1), "and has written nothing meanwhile"
        cashier.execute(
            "INSERT INTO ledger_entry (id, shop_id, customer_id, seq, kind, amount, author_id) "
            "VALUES (%s, %s, %s, 2, 'payment', 20000, %s)",
            (uuid.uuid4(), world.shop_a, world.customer_a, world.seller_a_membership),
        )
        cashier.commit()
    thread.join(timeout=30)
    assert finished.is_set()
    assert state(client, world, batch)["status"] == "applied"
    assert [row[:4] for row in entries(owner, world) if row[0] == "Ali"] == [
        ("Ali", 1, "credit", 50000),
        ("Ali", 2, "payment", 20000),
        ("Ali", 3, "opening", 70000),
    ]
    assert detail(client, world, world.customer_a)["balance"] == 100000


def test_an_undo_waits_for_a_payment_being_recorded_and_then_sees_it(
    client: TestClient, world: World, owner: psycopg.Connection, database_url: str
) -> None:
    batch = applied(client, world, table("Ali,,70000,,"))
    assert act(client, world, batch, "undo").status_code == 202
    finished = threading.Event()

    def run() -> None:
        work()
        finished.set()

    with psycopg.connect(database_url) as cashier:
        cashier.execute("SELECT id FROM customer WHERE id = %s FOR UPDATE", (world.customer_a,))
        thread = threading.Thread(target=run)
        thread.start()
        assert not finished.wait(1.5), "the worker waits for the account"
        cashier.execute(
            "INSERT INTO ledger_entry (id, shop_id, customer_id, seq, kind, amount, author_id) "
            "VALUES (%s, %s, %s, 3, 'payment', 60000, %s)",
            (uuid.uuid4(), world.shop_a, world.customer_a, world.seller_a_membership),
        )
        cashier.commit()
    thread.join(timeout=30)
    assert finished.is_set()
    # 120 000 owed, 60 000 just paid: reversing the imported 70 000 would take the balance below zero.
    body = state(client, world, batch)
    assert (body["status"], body["refused"]) == ("applied", {"step": "undo", "reason": "balance_used"})
    assert count(owner, world, "ledger_entry") == 3
    assert detail(client, world, world.customer_a)["balance"] == 60000


def test_the_scheduler_does_the_import_steps_at_every_tick(
    client: TestClient, world: World, owner: psycopg.Connection, worker_database_url: str, file_root: Path
) -> None:
    batch = upload(client, world, table("Karim,,250000,,")).json()["id"]
    # At night in Tashkent and far in the future: no other job of the scheduler has work then.
    moment = datetime(2084, 4, 4, 21, 30, tzinfo=UTC)

    def tick() -> None:
        async def run() -> None:
            database = Database(worker_database_url)
            try:
                clock = lambda: moment  # noqa: E731
                service = ImportService(database, FileService(database, FilesystemFileStore(file_root)), clock)
                await Scheduler(database, ReminderService(database, clock), clock, imports=service).tick()
            finally:
                await database.dispose()

        asyncio.run(run())

    tick()
    assert state(client, world, batch)["status"] == "validated"
    assert apply(client, world, batch).status_code == 202
    tick()
    assert state(client, world, batch)["status"] == "applied"
    tick()
    assert len(entries(owner, world, batch)) == 1


# --- two thousand rows ----------------------------------------------------------------------------------


def test_two_thousand_rows_no_request_runs_long_and_the_worker_does_each_step_in_reasonable_time(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    lines = [f"Mijoz {number:04d},9{number:08d},{50000 + number},," for number in range(2000)]
    timings: dict[str, float] = {}

    def timed(name: str, call: Any) -> Any:
        started = time.perf_counter()
        result = call()
        timings[name] = time.perf_counter() - started
        return result

    batch = timed("upload", lambda: upload(client, world, table(*lines))).json()["id"]
    assert timed("check (worker)", work) == 1
    seen = timed("read", lambda: state(client, world, batch))["preview"]
    assert seen["counts"] == {
        "new_customers": 2000,
        "existing_customers": 0,
        "entries": 2000,
        "amount": sum(50000 + number for number in range(2000)),
    }
    assert timed("apply", lambda: apply(client, world, batch, seen["plan"])).status_code == 202
    assert timed("apply (worker)", work) == 1
    assert count(owner, world, "customer") == 2003
    assert len(entries(owner, world, batch)) == 2000
    assert timed("undo", lambda: act(client, world, batch, "undo")).status_code == 202
    assert timed("undo (worker)", work) == 1
    assert state(client, world, batch)["undone"] == {"reversed": 2000, "archived": 2000}
    assert count(owner, world, "ledger_entry") == 4001
    print("\nimport of 2000 rows, seconds: " + ", ".join(f"{name} {spent:.2f}" for name, spent in timings.items()))
    # No request does the work. Generous bounds: the point is the order of magnitude.
    for request in ("upload", "read", "apply", "undo"):
        assert timings[request] < 3, request
    for step in ("check (worker)", "apply (worker)", "undo (worker)"):
        assert timings[step] < 20, step


# --- found by breaking the rules one at a time ----------------------------------------------------------


def test_each_step_looks_at_the_state_again_once_it_holds_the_batch(
    client: TestClient, world: World, owner: psycopg.Connection, worker_database_url: str, file_root: Path
) -> None:
    """Between a worker reading a batch and locking it another may finish the step: asked directly, with
    the rows in hand, a check or an apply of a batch that waits for neither changes nothing; and a step
    is not given up once it is done."""
    data = table("Karim,,250000,,")
    batch = uuid.UUID(applied(client, world, data))
    parsed = imports.parse(data, today())
    assert isinstance(parsed, imports.ParsedFile)
    before = (batches(owner, world), entries(owner, world), count(owner, world, "customer"))

    async def late() -> None:
        database = Database(worker_database_url)
        try:
            now = datetime.now(UTC)
            async with database.tenant(world.shop_a) as session:
                await check_in(session, batch, parsed, now)
            async with database.tenant(world.shop_a) as session:
                await apply_in(session, batch, parsed, now)
            async with database.tenant(world.shop_a) as session:
                await undo_in(session, batch, now)
            service = ImportService(database, FileService(database, FilesystemFileStore(file_root)))
            for step in ("uploaded", "applying", "undoing"):
                await service._give_up(world.shop_a, batch, step, "internal")
        finally:
            await database.dispose()

    asyncio.run(late())
    assert (batches(owner, world), entries(owner, world), count(owner, world, "customer")) == before
    assert state(client, world, batch)["refused"] is None
    assert told(owner, batch, "import_failed") == []


def test_an_undo_leaves_a_dispute_that_was_already_decided_as_it_was(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    batch = applied(client, world, table("Ali,,70000,,"))
    imported = owner.execute("SELECT id FROM ledger_entry WHERE import_batch_id = %s", (batch,)).fetchone()
    assert imported is not None
    disputed = client.post(
        f"{ME}/{link_of(owner, world.customer_a)}/disputes",
        json={"entry_id": str(imported[0]), "reason": "Bu qarz to'langan"},
        headers=as_user(world.customer_of_a),
    )
    declined = client.post(
        f"{shop(world)}/disputes/{disputed.json()['id']}/decline",
        json={"reason": "Daftarda bor"},
        headers={**as_user(world.owner_a), **key()},
    )
    assert declined.status_code == 200
    kept = owner.execute(
        "SELECT status, decided_by, closed_at FROM dispute WHERE entry_id = %s", (imported[0],)
    ).fetchone()
    assert undone(client, world, batch)["status"] == "undone"
    assert (
        owner.execute(
            "SELECT status, decided_by, closed_at FROM dispute WHERE entry_id = %s", (imported[0],)
        ).fetchone()
        == kept
    )

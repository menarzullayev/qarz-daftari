"""Import of customers with opening balances: template, upload, preview, apply, undo (story S14.1).

REQ-062, REQ-063; domain rule BR-24; BR-29 and BR-30 for the subscription states; REQ-N07 for the ledger,
which stays insert-only through an undo.
"""

import time
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from qarz.application.chat_texts import day, money, say
from qarz.domain import imports
from qarz.domain.files import MAX_FILE_BYTES

from .conftest import World, as_user, stored_objects
from .test_customer_account import ME, link_of
from .test_customers_ledger import _subscription, detail, key, record, reverse, shop, today
from .test_disputes import staff_notices, tg
from .test_payment_notices import with_services

pytestmark = pytest.mark.db

HEAD = "Ism,Telefon,Qarz summasi,To'lash muddati,Izoh"


def table(*lines: str) -> bytes:
    return "\n".join([HEAD, *lines]).encode("utf-8")


def typed(value: date) -> str:
    return value.strftime("%d.%m.%Y")


def upload(client: TestClient, world: World, data: bytes, user: uuid.UUID | None = None, headers: Any = None) -> Any:
    return client.post(
        f"{shop(world)}/imports", content=data, headers={**as_user(user or world.manager_a), **(headers or key())}
    )


def uploaded(client: TestClient, world: World, data: bytes) -> str:
    response = upload(client, world, data)
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def preview(client: TestClient, world: World, batch: Any, user: uuid.UUID | None = None) -> Any:
    return client.get(f"{shop(world)}/imports/{batch}", headers=as_user(user or world.manager_a))


def plan_of(client: TestClient, world: World, batch: Any) -> str:
    body = preview(client, world, batch).json()
    assert body["preview"]["plan"] is not None, body
    return str(body["preview"]["plan"])


def act(client: TestClient, world: World, batch: Any, action: str, body: Any = None, **options: Any) -> Any:
    user = options.get("user") or world.manager_a
    headers = {**as_user(user), **(options.get("headers") or key())}
    return client.post(f"{shop(world)}/imports/{batch}/{action}", json=body, headers=headers)


def apply(client: TestClient, world: World, batch: Any, plan: str | None = None, **options: Any) -> Any:
    return act(client, world, batch, "apply", {"plan": plan or plan_of(client, world, batch)}, **options)


def applied(client: TestClient, world: World, data: bytes) -> str:
    batch = uploaded(client, world, data)
    response = apply(client, world, batch)
    assert response.status_code == 200, response.text
    return batch


def batches(owner: psycopg.Connection, world: World) -> list[tuple[Any, ...]]:
    return owner.execute(
        "SELECT status, summary, author_id, applied_at IS NOT NULL FROM import_batch WHERE shop_id = %s "
        "ORDER BY created_at, id",
        (world.shop_a,),
    ).fetchall()


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


# --- the template ---------------------------------------------------------------------------------------


def test_a_manager_downloads_the_template_in_their_language(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    response = client.get(f"{shop(world)}/imports/template", headers=as_user(world.manager_a))
    assert response.status_code == 200
    assert response.headers["content-type"] == imports.XLSX_MIME
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
    """A workbook, with a number cell, a date cell and text cells, from upload to the ledger."""
    due = today() + timedelta(days=12)
    serial = (due - date(1899, 12, 30)).days
    filled = imports.write_xlsx(
        [HEAD.split(","), ["Karim aka", "+998 90 123-45-67", "250000", str(serial), "eski daftardan"]]
    )
    batch = applied(client, world, filled)
    assert entries(owner, world, batch) == [("Karim aka", 1, "opening", 250000, "eski daftardan", due, "staff")]
    assert ("Karim aka", "karim aka", "+998901234567", "active") in customers(owner, world)
    assert import_file(owner, world)[0][1] == imports.XLSX_MIME


# --- upload and validation ------------------------------------------------------------------------------


def test_an_upload_is_validated_and_kept_but_nothing_is_saved_to_the_ledger(
    client: TestClient, world: World, owner: psycopg.Connection, file_root: Path
) -> None:
    data = table("Karim,901234567,250000,,", "Lola,,80000,,qo'shni")
    before = nothing_saved(owner, world)
    response = upload(client, world, data)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body == {
        "id": body["id"],
        "status": "validated",
        "format": "csv",
        "author_id": str(world.manager_a_membership),
        "created_at": body["created_at"],
        "applied_at": None,
        "undo_until": None,
        "rows": 2,
        "errors": [],
        "applied": None,
        "undone": None,
    }
    assert batches(owner, world) == [
        ("validated", {"format": "csv", "rows": 2, "errors": []}, world.manager_a_membership, False)
    ]
    assert nothing_saved(owner, world) == before, "BR-24: nothing is saved before the import is applied"

    kept = import_file(owner, world)
    assert [(row[0], row[1], row[2]) for row in kept] == [("import", "text/csv", len(data))]
    month = datetime.now(UTC) + timedelta(days=30)
    assert abs(kept[0][3] - month) < timedelta(minutes=5), "an import file is kept for thirty days at most"
    assert [path.read_bytes() for path in stored_objects(file_root)] == [data]
    assert activity(owner, world) == [("import.uploaded", "import", world.manager_a_membership)]
    # The batch holds counts and codes; no name, phone or amount of a row.
    stored = owner.execute("SELECT summary::text FROM import_batch WHERE id = %s", (body["id"],)).fetchone()
    assert stored is not None
    assert "Karim" not in stored[0]
    assert "901234567" not in stored[0]


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
    response = upload(client, world, data)
    assert response.status_code == 201
    body = response.json()
    assert (body["status"], body["rows"]) == ("uploaded", 5)
    assert body["errors"] == [
        {"row": 3, "column": "amount", "code": "amount_invalid"},
        {"row": 3, "column": "name", "code": "name_missing"},
        {"row": 3, "column": "phone", "code": "phone_invalid"},
        {"row": 3, "column": "promised_date", "code": "date_invalid"},
        {"row": 4, "column": "amount", "code": "amount_too_small"},
        {"row": 5, "column": "name", "code": "customer_archived"},
        {"row": 6, "column": "promised_date", "code": "date_too_far"},
    ]
    assert "abc" not in response.text and "12345" not in response.text, "a problem is a code, never the cell"

    seen = preview(client, world, body["id"]).json()["preview"]
    assert seen["plan"] is None
    assert seen["errors"] == body["errors"]
    assert [row["row"] for row in seen["rows"]] == [2], "the row without a problem is still shown"

    for plan in ("0" * 32, imports.plan_token([])):
        refused = apply(client, world, body["id"], plan)
        assert (refused.status_code, refused.json()["error"]["code"]) == (409, "IMPORT_NOT_APPLICABLE")
        assert refused.json()["error"]["fields"] == {"reason": "uploaded"}
    assert nothing_saved(owner, world) == before
    assert batches(owner, world)[0][0] == "uploaded"


@pytest.mark.parametrize(
    ("data", "problem"),
    [
        (b"", "empty"),
        (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR", "not_a_spreadsheet"),
        ("Имя;Долг\nАли;45000\n".encode("cp1251"), "encoding"),
        (b"PK\x03\x04" + b"\x00" * 100, "malformed"),
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
def test_a_file_that_cannot_be_read_is_refused_and_nothing_of_it_is_kept(
    client: TestClient, world: World, owner: psycopg.Connection, file_root: Path, data: bytes, problem: str
) -> None:
    response = upload(client, world, data)
    assert (response.status_code, response.json()["error"]["code"]) == (422, "VALIDATION"), response.text
    assert response.json()["error"]["fields"] == {"file": problem}
    assert batches(owner, world) == []
    assert import_file(owner, world) == []
    assert stored_objects(file_root) == []
    assert activity(owner, world) == []


def test_an_upload_may_be_larger_than_other_requests_but_not_than_a_file_may_be(
    client: TestClient, world: World, owner: psycopg.Connection, file_root: Path
) -> None:
    line = "Mijoz {n},,1000,," + "x" * 900
    big = table(*(line.format(n=n) for n in range(1500)))
    assert 1024 * 1024 < len(big) < MAX_FILE_BYTES
    accepted = upload(client, world, big)
    assert (accepted.status_code, accepted.json()["status"]) == (201, "uploaded")
    assert {error["code"] for error in accepted.json()["errors"]} == {"note_too_long"}

    too_big = upload(client, world, b"Ism,Qarz\n" + b"\n" * MAX_FILE_BYTES)
    assert (too_big.status_code, too_big.json()["error"]["code"]) == (413, "BODY_TOO_LARGE")
    # The allowance is for the upload alone: another route of the import keeps the general limit.
    elsewhere = client.post(
        f"{shop(world)}/imports/{accepted.json()['id']}/apply",
        content=big,
        headers={**as_user(world.manager_a), **key()},
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


def test_a_file_store_that_is_down_refuses_the_upload_and_saves_nothing(
    client: TestClient, world: World, owner: psycopg.Connection, file_root: Path
) -> None:
    file_root.parent.mkdir(parents=True, exist_ok=True)
    file_root.write_text("a file where the store's directory should be")
    failed = upload(client, world, table("Karim,,250000,,"))
    assert failed.status_code == 500
    assert batches(owner, world) == []
    assert import_file(owner, world) == []


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
    body = preview(client, world, batch).json()
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
    assert seen["plan"] == preview(client, world, batch, world.owner_a).json()["preview"]["plan"]
    assert nothing_saved(owner, world) == before, "looking changes nothing"


def test_a_row_that_could_mean_two_customers_is_an_error(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    owner.execute(
        "INSERT INTO customer (id, shop_id, display_name, name_norm) VALUES (%s, %s, 'Vali', 'vali')",
        (uuid.uuid4(), world.shop_a),
    )
    response = upload(client, world, table("Vali,,5000,,", "Karim,,1000,,"))
    assert response.json()["status"] == "uploaded"
    assert response.json()["errors"] == [{"row": 2, "column": "name", "code": "ambiguous_customer"}]


def test_the_preview_follows_the_shops_customers_as_they_are_now(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    batch = uploaded(client, world, table("Karim,,250000,,"))
    first = preview(client, world, batch).json()["preview"]
    assert first["rows"][0]["action"] == "create"
    karim = uuid.uuid4()
    owner.execute(
        "INSERT INTO customer (id, shop_id, display_name, name_norm) VALUES (%s, %s, 'Karim', 'karim')",
        (karim, world.shop_a),
    )
    second = preview(client, world, batch).json()["preview"]
    assert (second["rows"][0]["action"], second["rows"][0]["customer"]["id"]) == ("existing", str(karim))
    assert second["plan"] != first["plan"]
    owner.execute("UPDATE customer SET status = 'archived' WHERE id = %s", (karim,))
    third = preview(client, world, batch).json()["preview"]
    assert (third["plan"], third["rows"]) == (None, [])
    assert third["errors"] == [{"row": 2, "column": "name", "code": "customer_archived"}]


# --- apply ------------------------------------------------------------------------------------------------


def test_applying_creates_the_customers_and_one_opening_balance_per_row(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    due = today() - timedelta(days=10)  # an old debt that is overdue already
    batch = uploaded(
        client,
        world,
        table(f"Karim,901234567,250000,{typed(due)},eski qarz", "ali,,70000,,", "Lola,,80000,,", "karim,,30000,,"),
    )
    response = apply(client, world, batch, user=world.owner_a)
    assert response.status_code == 200, response.text
    body = response.json()
    counts = {"new_customers": 2, "existing_customers": 1, "entries": 4, "amount": 430000}
    assert (body["status"], body["applied"], body["undone"]) == ("applied", counts, None)
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
    # REQ-063: marked as imported, with author and time.
    marks = owner.execute(
        "SELECT DISTINCT import_batch_id, author_id, created_at FROM ledger_entry WHERE import_batch_id = %s", (batch,)
    ).fetchall()
    assert [(row[0], row[1]) for row in marks] == [(uuid.UUID(batch), world.owner_a_membership)]
    assert marks[0][2] == datetime.fromisoformat(body["applied_at"])

    stored = batches(owner, world)[0]
    assert (stored[0], stored[3], stored[1]["applied"]) == ("applied", True, counts)
    assert len(stored[1]["created_customers"]) == 2
    assert activity(owner, world) == [
        ("import.uploaded", "import", world.manager_a_membership),
        ("import.applied", "import", world.owner_a_membership),
    ]
    # The rows are in the ledger: the file is due for deletion at once.
    assert import_file(owner, world)[0][3] <= datetime.now(UTC)
    # Overdue status follows the imported date like any other.
    assert detail(client, world, world.customer_a)["balance"] == 120000
    seen = client.get(f"{shop(world)}/customers", params={"q": "Karim"}, headers=as_user(world.seller_a)).json()
    karim = detail(client, world, seen["items"][0]["id"])
    assert (karim["overdue"]["amount"], karim["overdue"]["since"]) == (250000, due.isoformat())
    assert [(e["kind"], e["import_id"]) for e in karim["entries"]] == [("opening", batch), ("opening", batch)]
    assert detail(client, world, world.customer_a)["entries"][1]["import_id"] is None


def test_applying_tells_the_owner_and_a_linked_customer_and_measures_each_entry(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    measured = owner.execute("SELECT count(*) FROM measure.event WHERE kind = 'opening'").fetchone()
    batch = applied(client, world, table("Ali,,70000,,", "Lola,,80000,,"))
    assert staff_notices(owner, f"import:{batch}:s_import_applied") == [
        (
            tg(owner, world.owner_a),
            {
                "text": f"📥 Shop A\nImport qo'llandi: 2 ta qarz yozuvi, jami {money('uz', 150000)}. "
                "Yangi mijozlar: 1 ta.\n24 soat ichida butunlay bekor qilish mumkin."
            },
        )
    ]
    ali_entry = owner.execute(
        "SELECT e.id FROM ledger_entry e WHERE e.import_batch_id = %s AND e.customer_id = %s", (batch, world.customer_a)
    ).fetchone()
    assert ali_entry is not None
    told = staff_notices(owner, f"entry:{ali_entry[0]}:notify")
    assert [recipient for recipient, _ in told] == [tg(owner, world.customer_of_a)]
    assert told[0][1]["text"] == say(
        "uz",
        "n_opening",
        shop="Shop A",
        name="Ali",
        amount=money("uz", 70000),
        date=day(today() + timedelta(days=30)),
        balance=money("uz", 120000),
    )
    assert told[0][1]["text"].startswith("Shop A\nAli, daftarga oldingi qarzingiz kiritildi:")
    assert [row[0]["callback_data"][:7] for row in told[0][1]["reply_markup"]["inline_keyboard"]] == [
        "v2:dsp:",
        "v2:dmv:",
    ]
    # Lola is new and linked to nobody: one message to a customer in all.
    notes = owner.execute(
        "SELECT count(*) FROM outbox_message WHERE shop_id = %s AND dedupe_key LIKE 'entry:%%'", (world.shop_a,)
    ).fetchone()
    assert notes == (1,)
    after = owner.execute("SELECT count(*) FROM measure.event WHERE kind = 'opening'").fetchone()
    assert measured is not None and after is not None
    assert after[0] - measured[0] == 2
    # A linked customer can object to an imported balance like to any other debt.
    link = link_of(owner, world.customer_a)
    disputed = client.post(
        f"{ME}/{link}/disputes",
        json={"entry_id": str(ali_entry[0]), "reason": "Bu qarz to'langan"},
        headers=as_user(world.customer_of_a),
    )
    assert disputed.status_code == 201, disputed.text


def test_applying_twice_with_the_same_key_has_no_second_effect(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    batch = uploaded(client, world, table("Karim,,250000,,", "Lola,,80000,,"))
    plan, same = plan_of(client, world, batch), key()
    first = apply(client, world, batch, plan, headers=same)
    repeat = apply(client, world, batch, plan, headers=same)
    assert (first.status_code, repeat.status_code) == (200, 200)
    assert repeat.json() == first.json()
    assert len(entries(owner, world, batch)) == 2
    assert len(staff_notices(owner, f"import:{batch}:s_import_applied")) == 1

    again = apply(client, world, batch, plan)
    assert (again.status_code, again.json()["error"]["fields"]) == (409, {"reason": "applied"})
    reused = apply(client, world, batch, "f" * 32, headers=same)
    assert (reused.status_code, reused.json()["error"]["code"]) == (409, "IDEMPOTENCY_KEY_REUSED")
    assert len(entries(owner, world, batch)) == 2
    assert count(owner, world, "customer") == 5


def test_only_the_plan_that_was_shown_can_be_applied(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    batch = uploaded(client, world, table("Karim,,250000,,"))
    shown = plan_of(client, world, batch)
    before = nothing_saved(owner, world)
    for wrong in ("0" * 32, shown[:-1] + ("0" if shown[-1] != "0" else "1"), shown.upper() + "x"):
        refused = apply(client, world, batch, wrong)
        assert (refused.status_code, refused.json()["error"]["code"]) == (409, "IMPORT_NOT_APPLICABLE")
        assert refused.json()["error"]["fields"] == {"reason": "stale"}
    for malformed in ({}, {"plan": ""}, {"plan": 5}, {"plan": shown, "force": True}, None):
        assert act(client, world, batch, "apply", malformed).status_code == 422

    # Someone adds a customer called Karim after the preview: the row would now go to that customer.
    record_karim = client.post(
        f"{shop(world)}/customers", json={"display_name": "Karim"}, headers={**as_user(world.seller_a), **key()}
    )
    assert record_karim.status_code == 201
    stale = apply(client, world, batch, shown)
    assert (stale.status_code, stale.json()["error"]["fields"]) == (409, {"reason": "stale"})
    assert nothing_saved(owner, world) == (before[0] + 1, before[1], before[2])
    assert batches(owner, world)[0][0] == "validated"

    # Shown again, the merge is no longer unseen, and can be applied.
    seen = preview(client, world, batch).json()["preview"]
    assert (seen["rows"][0]["action"], seen["rows"][0]["customer"]["id"]) == ("existing", record_karim.json()["id"])
    assert apply(client, world, batch, seen["plan"]).status_code == 200
    assert entries(owner, world, batch) == [
        ("Karim", 1, "opening", 250000, None, today() + timedelta(days=30), "default")
    ]
    assert count(owner, world, "customer") == before[0] + 1


def test_an_import_whose_rows_have_gone_wrong_since_the_upload_is_not_applied(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    batch = uploaded(client, world, table("Vali,,5000,,", "Karim,,1000,,"))
    shown = plan_of(client, world, batch)
    owner.execute("UPDATE customer SET status = 'archived' WHERE id = %s", (world.settled_customer_a,))
    before = nothing_saved(owner, world)
    refused = apply(client, world, batch, shown)
    assert (refused.status_code, refused.json()["error"]["fields"]) == (409, {"reason": "errors"})
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
        failed = apply(client, world, batch, plan)
    finally:
        owner.execute("DROP TRIGGER test_refuse_666666 ON ledger_entry")
        owner.execute("DROP FUNCTION test_refuse_666666()")
    assert failed.status_code == 500
    assert nothing_saved(owner, world) == before
    assert batches(owner, world)[0][0] == "validated"
    assert import_file(owner, world)[0][3] > datetime.now(UTC) + timedelta(days=29), "the file is still kept"
    assert staff_notices(owner, f"import:{batch}") == []
    # With the obstacle gone the same import goes through.
    assert apply(client, world, batch, plan).status_code == 200
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
    assert preview(client, world, batch).status_code == 200, "looking goes on (BR-29)"

    _subscription(owner, world, "state = 'suspended'")
    writes = [
        upload(client, world, table("Nodir,,1000,,"), world.owner_a),
        apply(client, world, batch, plan, user=world.owner_a),
        act(client, world, batch, "discard", user=world.owner_a),
        act(client, world, applied_batch, "undo", user=world.owner_a),
        preview(client, world, batch, world.manager_a),
        client.get(f"{shop(world)}/imports", headers=as_user(world.manager_a)),
        client.get(f"{shop(world)}/imports/template", headers=as_user(world.manager_a)),
    ]
    for refused in writes:
        assert (refused.status_code, refused.json()["error"]["code"]) == (403, "SHOP_SUSPENDED")
    assert (nothing_saved(owner, world), batches(owner, world), len(stored_objects(file_root))) == before
    # BR-30: the owner may still look.
    assert preview(client, world, batch, world.owner_a).status_code == 200
    assert len(client.get(f"{shop(world)}/imports", headers=as_user(world.owner_a)).json()["items"]) == 2

    # A mistake can still be taken back in limited mode: an undo is reversals, which BR-29 allows.
    _subscription(owner, world, "state = 'limited'")
    assert act(client, world, applied_batch, "undo").status_code == 200
    assert act(client, world, batch, "discard").status_code == 200


# --- undo -------------------------------------------------------------------------------------------------


def test_an_undo_reverses_every_entry_and_archives_the_customers_the_import_created(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    batch = applied(client, world, table("Karim,,250000,,", "ali,,70000,,", "Lola,,80000,,", "karim,,30000,,"))
    rows_before = count(owner, world, "ledger_entry")
    response = act(client, world, batch, "undo", user=world.owner_a)
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["status"], body["undone"], body["undo_until"]) == ("undone", {"reversed": 4, "archived": 2}, None)
    assert body["applied"]["entries"] == 4

    # REQ-N07: nothing is deleted or changed; four reversals are added.
    assert count(owner, world, "ledger_entry") == rows_before + 4
    assert len(entries(owner, world, batch)) == 4
    reversals = owner.execute(
        "SELECT r.amount, r.author_id FROM ledger_entry r JOIN ledger_entry e ON e.id = r.reverses_id "
        "WHERE e.import_batch_id = %s ORDER BY r.amount",
        (batch,),
    ).fetchall()
    assert reversals == [(amount, world.owner_a_membership) for amount in (30000, 70000, 80000, 250000)]
    assert [balance(owner, world, name) for name in ("Ali", "Karim", "Lola")] == [50000, 0, 0]
    assert customers(owner, world) == [
        ("Ali", "ali", None, "active"),  # was there before the import, and still owes
        ("Karim", "karim", None, "archived"),
        ("Lola", "lola", None, "archived"),
        ("Sobir", "sobir", None, "archived"),
        ("Vali", "vali", None, "active"),
    ]
    assert batches(owner, world)[0][0] == "undone"
    assert activity(owner, world)[-1] == ("import.undone", "import", world.owner_a_membership)
    assert staff_notices(owner, f"import:{batch}:s_import_undone") == [
        (
            tg(owner, world.owner_a),
            {"text": f"↩️ Shop A\nImport bekor qilindi: 4 ta yozuv qaytarildi. Import summasi: {money('uz', 430000)}."},
        )
    ]
    # The linked customer is told that the imported balance was taken back.
    told = owner.execute(
        "SELECT payload->>'text' FROM outbox_message WHERE shop_id = %s AND recipient = %s ORDER BY created_at, id",
        (world.shop_a, tg(owner, world.customer_of_a)),
    ).fetchall()
    assert told[-1][0] == say(
        "uz", "n_reversed_credit", shop="Shop A", name="Ali", amount=money("uz", 70000), balance=money("uz", 50000)
    )

    for again in (act(client, world, batch, "undo"), apply(client, world, batch, "0" * 32)):
        assert again.status_code == 409
    assert act(client, world, batch, "undo").json()["error"] == {
        "code": "IMPORT_UNDO_REFUSED",
        "message": "Bu importni bekor qilib bo'lmaydi: muddat o'tgan yoki yozuvlarga to'lov qilingan.",
        "fields": {"reason": "not_applied"},
    }
    assert count(owner, world, "ledger_entry") == rows_before + 4


def test_undoing_twice_with_the_same_key_reverses_once(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    batch = applied(client, world, table("Karim,,250000,,"))
    same = key()
    first = act(client, world, batch, "undo", headers=same)
    repeat = act(client, world, batch, "undo", headers=same)
    assert (first.status_code, repeat.status_code) == (200, 200)
    assert repeat.json() == first.json()
    assert count(owner, world, "ledger_entry") == 3
    assert len(staff_notices(owner, f"import:{batch}:s_import_undone")) == 1


def test_an_import_can_be_undone_for_twenty_four_hours_and_no_longer(
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
    assert count(owner, world, "ledger_entry") == before
    assert act(client, world, in_time, "undo").status_code == 200
    assert [row[0] for row in batches(owner, world)] == ["applied", "undone"]


@pytest.mark.parametrize("status", ["uploaded", "validated", "discarded"])
def test_only_an_applied_import_can_be_undone(
    client: TestClient, world: World, owner: psycopg.Connection, status: str
) -> None:
    batch = uploaded(client, world, table("Karim,,250000,,"))
    owner.execute("UPDATE import_batch SET status = %s WHERE id = %s", (status, batch))
    refused = act(client, world, batch, "undo")
    assert (refused.status_code, refused.json()["error"]["fields"]) == (409, {"reason": "not_applied"})
    assert count(owner, world, "ledger_entry") == 1


def test_an_import_whose_balance_has_been_paid_against_is_not_undone_at_all(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    batch = applied(client, world, table("Karim,,250000,,", "Lola,,80000,,"))
    lola = owner.execute(
        "SELECT id FROM customer WHERE shop_id = %s AND display_name = 'Lola'", (world.shop_a,)
    ).fetchone()
    assert lola is not None
    assert record(client, world, lola[0], "payment", 30000).status_code == 201
    before = (count(owner, world, "ledger_entry"), customers(owner, world))
    refused = act(client, world, batch, "undo")
    assert (refused.status_code, refused.json()["error"]["code"]) == (409, "IMPORT_UNDO_REFUSED")
    assert refused.json()["error"]["fields"] == {"reason": "balance_used"}
    # Undone whole or not at all: Karim's entry, which could have been reversed, is not.
    assert (count(owner, world, "ledger_entry"), customers(owner, world)) == before
    assert balance(owner, world, "Karim") == 250000
    assert batches(owner, world)[0][0] == "applied"
    assert staff_notices(owner, f"import:{batch}:s_import_undone") == []


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
    lola = owner.execute("SELECT customer_id FROM ledger_entry WHERE id = %s", (ids["Lola"],)).fetchone()
    assert lola is not None
    assert record(client, world, lola[0], "credit", 12000).status_code == 201

    response = act(client, world, batch, "undo")
    assert response.status_code == 200, response.text
    assert response.json()["undone"] == {"reversed": 2, "archived": 2}
    assert [balance(owner, world, name) for name in ("Karim", "Lola", "Nodir")] == [0, 12000, 0]
    states = {name: status for name, _, _, status in customers(owner, world)}
    assert (states["Karim"], states["Lola"], states["Nodir"]) == ("archived", "active", "archived")


def test_an_undo_ends_a_dispute_and_a_date_request_on_an_imported_entry(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    batch = applied(client, world, table("Ali,,70000,,"))
    entry = owner.execute("SELECT id FROM ledger_entry WHERE import_batch_id = %s", (batch,)).fetchone()
    assert entry is not None
    link = link_of(owner, world.customer_a)
    asked = client.post(
        f"{ME}/{link}/date-requests",
        json={"entry_id": str(entry[0]), "requested_date": (today() + timedelta(days=45)).isoformat()},
        headers=as_user(world.customer_of_a),
    )
    disputed = client.post(
        f"{ME}/{link}/disputes",
        json={"entry_id": str(entry[0]), "reason": "Bu qarz to'langan"},
        headers=as_user(world.customer_of_a),
    )
    assert (asked.status_code, disputed.status_code) == (201, 201)
    assert act(client, world, batch, "undo").status_code == 200
    assert owner.execute("SELECT status FROM dispute WHERE entry_id = %s", (entry[0],)).fetchone() == ("reversed",)
    assert owner.execute("SELECT status FROM date_change_request WHERE entry_id = %s", (entry[0],)).fetchone() == (
        "expired",
    )


# --- discard, the list, and other shops -----------------------------------------------------------------


def test_a_batch_that_is_not_wanted_is_discarded_and_its_file_goes(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    batch = uploaded(client, world, table("Karim,,250000,,"))
    plan = plan_of(client, world, batch)
    response = act(client, world, batch, "discard")
    assert (response.status_code, response.json()["status"]) == (200, "discarded")
    assert import_file(owner, world)[0][3] <= datetime.now(UTC)
    assert activity(owner, world)[-1] == ("import.discarded", "import", world.manager_a_membership)
    assert preview(client, world, batch).json()["preview"] is None

    for again in (act(client, world, batch, "discard"), apply(client, world, batch, plan)):
        assert (again.status_code, again.json()["error"]["code"]) == (409, "IMPORT_NOT_APPLICABLE")
        assert again.json()["error"]["fields"] == {"reason": "discarded"}
    assert nothing_saved(owner, world) == (3, 1, 1)

    done = applied(client, world, table("Lola,,80000,,"))
    refused = act(client, world, done, "discard")
    assert (refused.status_code, refused.json()["error"]["fields"]) == (409, {"reason": "applied"})
    with_errors = upload(client, world, table("Lola,,abc,,")).json()["id"]
    assert act(client, world, with_errors, "discard").json()["status"] == "discarded"


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
        "id", "status", "format", "author_id", "created_at", "applied_at", "undo_until", "rows", "errors", "applied",
        "undone",
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
        preview(client, world, uuid.uuid4()),
        apply(client, world, uuid.uuid4(), plan),
        act(client, world, uuid.uuid4(), "undo"),
        act(client, world, uuid.uuid4(), "discard"),
        preview(client, world, "template-not"),
    ]
    for response in answers:
        assert (response.status_code, response.json()["error"]["code"]) == (404, "NOT_FOUND")
    assert batches(owner, world)[0][0] == "validated"
    assert nothing_saved(owner, world) == (3, 1, 1)
    # Shop B's own import does not see shop A's customers: its "Ali" is a new customer there.
    theirs = client.post(other, content=table("Ali,,1000,,"), headers={**as_user(world.owner_b), **key()}).json()
    seen = client.get(f"{other}/{theirs['id']}", headers=as_user(world.owner_b)).json()["preview"]
    assert (seen["rows"][0]["action"], seen["rows"][0]["customer"]) == ("create", None)


# --- the file is kept only as long as it is needed ------------------------------------------------------


def test_the_file_of_an_applied_import_is_deleted_by_the_hourly_job_and_the_batch_stays(
    client: TestClient, world: World, owner: psycopg.Connection, app_database_url: str, file_root: Path
) -> None:
    done = applied(client, world, table("Karim,,250000,,"))
    waiting = uploaded(client, world, table("Lola,,80000,,"))
    assert len(stored_objects(file_root)) == 2

    with_services(app_database_url, file_root, lambda notices, files: notices.run_hourly())
    assert [path.read_bytes() for path in stored_objects(file_root)] == [table("Lola,,80000,,")]
    assert len(import_file(owner, world)) == 1
    kept = owner.execute("SELECT id, file_id IS NULL FROM import_batch WHERE shop_id = %s", (world.shop_a,)).fetchall()
    assert sorted(kept) == sorted([(uuid.UUID(done), True), (uuid.UUID(waiting), False)])
    assert preview(client, world, done).json()["status"] == "applied"
    assert act(client, world, done, "undo").status_code == 200, "an undo does not need the file"

    # Thirty days later the batch that was never applied loses its file, and can no longer be applied.
    plan = plan_of(client, world, waiting)
    owner.execute(
        "UPDATE stored_file SET delete_after = now() - interval '1 minute' WHERE shop_id = %s", (world.shop_a,)
    )
    with_services(app_database_url, file_root, lambda notices, files: notices.run_hourly())
    assert stored_objects(file_root) == []
    assert import_file(owner, world) == []
    gone = preview(client, world, waiting).json()
    assert (gone["status"], gone["preview"]) == ("validated", None)
    refused = apply(client, world, waiting, plan)
    assert (refused.status_code, refused.json()["error"]["fields"]) == (409, {"reason": "file_gone"})
    assert count(owner, world, "customer") == 4


def test_a_file_that_has_vanished_from_the_store_cannot_be_applied(
    client: TestClient, world: World, owner: psycopg.Connection, file_root: Path
) -> None:
    batch = uploaded(client, world, table("Karim,,250000,,"))
    plan = plan_of(client, world, batch)
    for path in stored_objects(file_root):
        path.unlink()
    assert preview(client, world, batch).json()["preview"] is None
    refused = apply(client, world, batch, plan)
    assert (refused.status_code, refused.json()["error"]["fields"]) == (409, {"reason": "file_gone"})
    assert nothing_saved(owner, world) == (3, 1, 1)


# --- two thousand rows ----------------------------------------------------------------------------------


def test_two_thousand_rows_are_imported_and_undone_in_reasonable_time(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    lines = [f"Mijoz {number:04d},9{number:08d},{50000 + number},," for number in range(2000)]
    timings: dict[str, float] = {}

    def timed(name: str, call: Any) -> Any:
        started = time.perf_counter()
        response = call()
        timings[name] = time.perf_counter() - started
        assert response.status_code in (200, 201), response.text
        return response

    batch = timed("upload", lambda: upload(client, world, table(*lines))).json()["id"]
    seen = timed("preview", lambda: preview(client, world, batch)).json()["preview"]
    assert seen["counts"] == {
        "new_customers": 2000,
        "existing_customers": 0,
        "entries": 2000,
        "amount": sum(50000 + number for number in range(2000)),
    }
    timed("apply", lambda: apply(client, world, batch, seen["plan"]))
    assert count(owner, world, "customer") == 2003
    assert len(entries(owner, world, batch)) == 2000
    undone = timed("undo", lambda: act(client, world, batch, "undo")).json()
    assert undone["undone"] == {"reversed": 2000, "archived": 2000}
    assert count(owner, world, "ledger_entry") == 4001
    print("\nimport of 2000 rows, seconds: " + ", ".join(f"{name} {spent:.2f}" for name, spent in timings.items()))
    # Generous bounds: the point is that none of the steps is of another order of magnitude.
    assert timings["upload"] < 10
    assert timings["preview"] < 10
    assert timings["apply"] < 20
    assert timings["undo"] < 120

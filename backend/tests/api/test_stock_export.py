"""The stock and the suppliers in the owner's export, and in the erasure of a shop (module I)."""

from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from .conftest import World
from .test_customers_ledger import new_customer
from .test_exports import ask, no_jobs_left_by_earlier_tests, work, workbook
from .test_goods_lines import chosen, sell
from .test_stock import counted_item, document, line, on, patch_item, receive, switch
from .test_stock_documents import cancel, supplier
from .test_suppliers import entry

pytestmark = pytest.mark.db

__all__ = ["no_jobs_left_by_earlier_tests", "on"]

BEFORE = ["Hisobot", "Mijozlar", "Daftar", "Muddatlar tarixi", "Mahsulotlar"]
STOCK = ["Ombor", "Ombor harakatlari", "Ombor hujjatlari", "Ta'minotchilar", "Ta'minotchi hisobi"]


def test_a_shop_without_stock_gets_the_workbook_it_always_got(
    client: TestClient, world: World, on: None, worker_database_url: str, file_root: Path
) -> None:
    """With the switch on but nothing of the stock recorded, the sheets are the five there always were."""
    job = ask(client, world).json()["id"]
    assert work(worker_database_url, file_root) == 1
    assert list(workbook(client, world, job)) == BEFORE


def test_the_workbook_holds_the_stock_its_movements_the_documents_and_the_suppliers(
    client: TestClient, world: World, on: None, owner: psycopg.Connection, worker_database_url: str, file_root: Path
) -> None:
    customer = new_customer(client, world, "Vali")
    who = supplier(client, world)
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    assert patch_item(client, world, rice, low_stock="3", barcodes=["RICE-1", "96385074"]).status_code == 200
    receive(client, world, [line(rice, "10", 10_000)], supplier_id=who, paid=40_000)
    assert sell(client, world, customer, [chosen(rice, "2.5", 15_000)]).status_code == 201
    lost = document(client, world, kind="write_off", reason="expired", post=True, lines=[line(rice, "1")]).json()
    assert cancel(client, world, lost["id"], "Topildi").status_code == 200
    assert entry(client, world, who, "payment", 10_000, note="Naqd").status_code == 201
    # Another shop's stock, which must not appear.
    owner.execute(
        "INSERT INTO supplier (id, shop_id, name, name_norm) VALUES (gen_random_uuid(), %s, 'Begona', 'begona')",
        (world.shop_b,),
    )
    # The export is the owner's copy of what is recorded, whether or not the stock is switched on now.
    switch(owner, "false")

    job = ask(client, world).json()["id"]
    assert work(worker_database_url, file_root) == 1
    book = workbook(client, world, job)
    assert list(book) == BEFORE + STOCK

    assert book["Ombor"][1] == [
        "Guruch",
        "kg",
        15_000,
        Decimal("7.5"),
        3,
        "UZS",
        10_000,
        75_000,
        "RICE-1, 96385074",
        rice,
    ]
    moves = book["Ombor harakatlari"]
    assert [(row[1], row[2], row[3], row[5], row[6], row[11]) for row in moves[1:]] == [
        ("Guruch", "Kirim", 10, 10, 100_000, "Yo'q"),
        ("Guruch", "Sotuv", Decimal("-2.5"), Decimal("7.5"), 25_000, "Yo'q"),
        ("Guruch", "Hisobdan chiqarildi", -1, Decimal("6.5"), 10_000, "Ha"),
        ("Guruch", "Bekor qilish", 1, Decimal("7.5"), 10_000, "Yo'q"),
    ]
    assert moves[1][10] == "Kirim № 1" and moves[2][8] == 37_500 and moves[3][9] == "Muddati o'tgan"
    papers = book["Ombor hujjatlari"]
    assert sorted((row[0], row[1], row[2]) for row in papers[1:]) == [
        (1, "Hisobdan chiqarish", "Bekor qilingan"),
        (1, "Kirim", "O'tkazilgan"),
    ]
    receipt = next(row for row in papers[1:] if row[1] == "Kirim")
    assert receipt[4:9] == ["Ulgurji bozor", None, "UZS", 100_000, 40_000]
    assert [row[:6] for row in book["Ta'minotchilar"][1:]] == [["Ulgurji bozor", None, None, "Faol", 50_000, 0]]
    account = book["Ta'minotchi hisobi"]
    assert [(row[2], row[3], row[4], row[5], row[8]) for row in account[1:]] == [
        ("Tovar olindi", 100_000, "UZS", 100_000, "Kirim № 1"),
        ("To'lov", 40_000, "UZS", -40_000, "Kirim № 1"),
        ("To'lov", 10_000, "UZS", -10_000, None),
    ]
    everything = " ".join(str(cell) for rows in book.values() for row in rows for cell in row)
    assert "Begona" not in everything

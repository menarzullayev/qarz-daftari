"""What the database itself holds the territory reference and a customer's address to (migration 0051),
without the application in between.

The reference belongs to no shop and is read by every shop, so it is not under the tenant policy: these
tests are what says a shop's session still cannot write it, and that nobody can delete a row of it. The
address is columns of the shop's own customer row: its chain is held by foreign keys, and it is as
invisible to another shop as the customer's name. The places are invented.
"""

import uuid
from dataclasses import dataclass

import psycopg
import pytest

from ..conftest import AppSession, Shop, refused

pytestmark = pytest.mark.db

GEO_TABLES = ("geo_region", "geo_district", "geo_mahalla", "geo_street")


@dataclass(frozen=True)
class Places:
    region: uuid.UUID
    district: uuid.UUID
    mahalla: uuid.UUID
    street: uuid.UUID
    other_region: uuid.UUID
    other_district: uuid.UUID  # of the other region
    other_mahalla: uuid.UUID  # of the first region, with no district known
    other_street: uuid.UUID  # of the other mahalla


def _code() -> str:
    return str(uuid.uuid4().int % 10**11).zfill(11)


def _region(owner: psycopg.Connection) -> uuid.UUID:
    region = uuid.uuid4()
    owner.execute(
        "INSERT INTO geo_region (id, soato, name_uz, name_norm) VALUES (%s, %s, 'Sinov viloyati', 'sinov viloyati')",
        (region, _code()),
    )
    return region


def _district(owner: psycopg.Connection, region: uuid.UUID) -> uuid.UUID:
    district = uuid.uuid4()
    owner.execute(
        "INSERT INTO geo_district (id, soato, region_id, name_uz, name_norm) "
        "VALUES (%s, %s, %s, 'Sinov tumani', 'sinov tumani')",
        (district, _code(), region),
    )
    return district


def _mahalla(owner: psycopg.Connection, region: uuid.UUID, district: uuid.UUID | None) -> uuid.UUID:
    mahalla = uuid.uuid4()
    owner.execute(
        "INSERT INTO geo_mahalla (id, source_key, region_id, district_id, name_uz, name_norm) "
        "VALUES (%s, %s, %s, %s, 'Sinov', 'sinov')",
        (mahalla, f"c:{mahalla.hex}", region, district),
    )
    return mahalla


def _street(owner: psycopg.Connection, mahalla: uuid.UUID) -> uuid.UUID:
    street = uuid.uuid4()
    owner.execute(
        "INSERT INTO geo_street (id, mahalla_id, name, name_norm) VALUES (%s, %s, 'Bog''', 'bog''')", (street, mahalla)
    )
    return street


@pytest.fixture
def places(owner: psycopg.Connection) -> Places:
    region, other_region = _region(owner), _region(owner)
    district, other_district = _district(owner, region), _district(owner, other_region)
    mahalla, other_mahalla = _mahalla(owner, region, district), _mahalla(owner, region, None)
    return Places(
        region,
        district,
        mahalla,
        _street(owner, mahalla),
        other_region,
        other_district,
        other_mahalla,
        _street(owner, other_mahalla),
    )


SET = (
    "UPDATE customer SET geo_region_id = %s, geo_district_id = %s, geo_mahalla_id = %s, geo_street_id = %s, "
    "street_text = %s, address_at = CASE WHEN %s::uuid IS NULL THEN NULL ELSE now() END WHERE id = %s"
)


def _set(conn: psycopg.Connection, customer: uuid.UUID, *address: object) -> None:
    region, district, mahalla, street, text = address
    conn.execute(SET, (region, district, mahalla, street, text, region, customer))


def test_a_shop_reads_the_reference_and_cannot_write_or_delete_it(
    as_app: AppSession, shop_a: Shop, places: Places
) -> None:
    with as_app(shop_a.shop_id) as conn:
        for table in GEO_TABLES:
            assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] >= 1  # type: ignore[index]
    for table in GEO_TABLES:
        refused(as_app, f"UPDATE {table} SET status = 'retired'", shop_a.shop_id)
        refused(as_app, f"DELETE FROM {table}", shop_a.shop_id)
    refused(
        as_app,
        "INSERT INTO geo_region (id, soato, name_uz, name_norm) VALUES (gen_random_uuid(), '99', 'X', 'x')",
        shop_a.shop_id,
    )
    refused(as_app, f"UPDATE geo_district SET population = 1 WHERE id = '{places.district}'", shop_a.shop_id)


def test_the_administrators_role_loads_the_reference_and_cannot_delete_from_it(
    as_admin: AppSession, places: Places
) -> None:
    with as_admin(None) as conn:
        conn.execute("UPDATE geo_street SET status = 'retired' WHERE id = %s", (places.street,))
        conn.execute("UPDATE geo_street SET status = 'active' WHERE id = %s", (places.street,))
    for table in GEO_TABLES:
        refused(as_admin, f"DELETE FROM {table}")
        refused(as_admin, f"TRUNCATE {table} CASCADE")


def test_the_worker_reads_the_reference_for_the_export_and_cannot_write_it(
    as_worker: AppSession, places: Places
) -> None:
    with as_worker(None) as conn:
        assert conn.execute("SELECT name_uz FROM geo_region WHERE id = %s", (places.region,)).fetchone() is not None
    for table in GEO_TABLES:
        refused(as_worker, f"UPDATE {table} SET status = 'retired'")
        refused(as_worker, f"DELETE FROM {table}")


def test_a_customers_address_is_kept_when_its_chain_holds(as_app: AppSession, shop_a: Shop, places: Places) -> None:
    with as_app(shop_a.shop_id) as conn:
        _set(conn, shop_a.customer_id, places.region, places.district, places.mahalla, places.street, None)
        _set(conn, shop_a.customer_id, places.region, None, places.other_mahalla, places.other_street, None)
        # The reference does not know this mahalla's district: the district is the shop's own word.
        _set(conn, shop_a.customer_id, places.region, places.district, places.other_mahalla, None, "Bog' 12")
        _set(conn, shop_a.customer_id, places.region, None, None, None, "Bog' 12")
        _set(conn, shop_a.customer_id, None, None, None, None, None)


@pytest.mark.parametrize(
    ("address", "error"),
    [
        # A district, a mahalla and a street that are not of the place above them.
        (("region", "other_district", None, None, None), psycopg.errors.ForeignKeyViolation),
        (("other_region", None, "mahalla", None, None), psycopg.errors.ForeignKeyViolation),
        (("region", "district", "mahalla", "other_street", None), psycopg.errors.ForeignKeyViolation),
        # An address that does not start at a region, a street without its mahalla, a street both ways.
        ((None, "district", None, None, None), psycopg.errors.CheckViolation),
        ((None, None, None, None, "Bog' 12"), psycopg.errors.CheckViolation),
        (("region", None, None, "street", None), psycopg.errors.CheckViolation),
        (("region", "district", "mahalla", "street", "Bog' 12"), psycopg.errors.CheckViolation),
        (("region", None, None, None, ""), psycopg.errors.CheckViolation),
        (("region", None, None, None, "k" * 121), psycopg.errors.CheckViolation),
    ],
)
def test_the_database_refuses_an_address_whose_chain_does_not_hold(
    as_app: AppSession, shop_a: Shop, places: Places, address: tuple[str | None, ...], error: type[Exception]
) -> None:
    *names, text = address
    ids = [None if name is None else getattr(places, name) for name in names]
    with pytest.raises(error), as_app(shop_a.shop_id) as conn:
        _set(conn, shop_a.customer_id, *ids, text)


def test_a_place_a_customer_points_at_cannot_be_deleted_even_by_the_owner_of_the_database(
    owner: psycopg.Connection, shop_a: Shop, places: Places
) -> None:
    _set(owner, shop_a.customer_id, places.region, places.district, places.mahalla, places.street, None)
    for table, place in (
        ("geo_street", places.street),
        ("geo_mahalla", places.mahalla),
        ("geo_district", places.district),
        ("geo_region", places.region),
    ):
        with pytest.raises(psycopg.errors.ForeignKeyViolation), owner.transaction():
            owner.execute(f"DELETE FROM {table} WHERE id = %s", (place,))
    # Retiring it is what the import does instead, and the customer keeps pointing at it.
    owner.execute("UPDATE geo_mahalla SET status = 'retired' WHERE id = %s", (places.mahalla,))
    assert owner.execute("SELECT geo_mahalla_id FROM customer WHERE id = %s", (shop_a.customer_id,)).fetchone() == (
        places.mahalla,
    )


def test_another_shop_sees_nothing_of_a_customers_address(
    as_app: AppSession, owner: psycopg.Connection, shop_a: Shop, shop_b: Shop, places: Places
) -> None:
    _set(owner, shop_a.customer_id, places.region, places.district, places.mahalla, None, "Bog' 12")
    with as_app(shop_b.shop_id) as conn:
        assert conn.execute("SELECT count(*) FROM customer WHERE geo_region_id IS NOT NULL").fetchone() == (0,)
        assert conn.execute("SELECT street_text FROM customer WHERE id = %s", (shop_a.customer_id,)).fetchone() is None
        changed = conn.execute("UPDATE customer SET street_text = 'x' WHERE id = %s", (shop_a.customer_id,))
        assert changed.rowcount == 0
    with as_app(None) as conn:
        assert conn.execute("SELECT count(*) FROM customer WHERE geo_region_id IS NOT NULL").fetchone() == (0,)


def test_the_reference_has_no_column_that_could_hold_a_person(owner: psycopg.Connection) -> None:
    """The boundary the owner drew: places and counts, never residents. A column added later for a
    person's name, phone or document would have to be added to this list, on purpose."""
    columns = {
        table: {
            row[0]
            for row in owner.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = %s",
                (table,),
            )
        }
        for table in GEO_TABLES
    }
    assert columns == {
        "geo_region": {"id", "soato", "name_uz", "name_ru", "name_en", "name_norm", "status", "created_at"},
        "geo_district": {
            "id", "soato", "region_id", "name_uz", "name_ru", "name_en", "name_norm", "population", "families",
            "households", "status", "created_at",
        },
        "geo_mahalla": {
            "id", "source_key", "code", "region_id", "district_id", "source_group", "name_uz", "name_norm",
            "status", "created_at",
        },
        "geo_street": {"id", "mahalla_id", "name", "name_norm", "kind", "road_type", "status", "created_at"},
    }  # fmt: skip

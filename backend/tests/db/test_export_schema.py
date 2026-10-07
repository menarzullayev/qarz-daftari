"""What migration 0022 leaves in the database for exports, and what the database itself enforces."""

import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from ..conftest import AppSession, Shop

pytestmark = pytest.mark.db

NOW = datetime.now(UTC)


@pytest.fixture(autouse=True)
def no_jobs_left_by_earlier_tests(owner: psycopg.Connection) -> None:
    owner.execute(
        "UPDATE export_job SET status = 'failed', error = 'interrupted' WHERE status IN ('queued', 'running')"
    )


def _job(conn: psycopg.Connection, shop: Shop, status: str = "queued", **columns: object) -> uuid.UUID:
    job_id = uuid.uuid4()
    names = ["id", "shop_id", "requested_by", "status", *columns]
    values = [job_id, shop.shop_id, shop.member_id, status, *columns.values()]
    conn.execute(f"INSERT INTO export_job ({', '.join(names)}) VALUES ({', '.join(['%s'] * len(names))})", values)
    return job_id


def _claim(conn: psycopg.Connection, stale_minutes: int = 15) -> tuple[object, ...] | None:
    return conn.execute(
        "SELECT job_id, shop_id, attempts FROM claim_export_job(%s, %s)",
        (datetime.now(UTC), datetime.now(UTC) - timedelta(minutes=stale_minutes)),
    ).fetchone()


def test_export_jobs_of_one_shop_are_invisible_and_untouchable_from_another(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop, shop_b: Shop
) -> None:
    job = _job(owner, shop_a)
    with as_app(shop_a.shop_id) as conn:
        assert conn.execute("SELECT count(*) FROM export_job").fetchone() == (1,)
    for other in (shop_b.shop_id, None):
        with as_app(other) as conn:
            assert conn.execute("SELECT count(*) FROM export_job").fetchone() == (0,)
            assert conn.execute("UPDATE export_job SET status = 'done' WHERE id = %s", (job,)).rowcount == 0
    with pytest.raises(psycopg.errors.InsufficientPrivilege), as_app(shop_b.shop_id) as conn:
        _job(conn, shop_a)
    # The application never deletes a job: its history goes only with the shop.
    with pytest.raises(psycopg.errors.InsufficientPrivilege), as_app(shop_a.shop_id) as conn:
        conn.execute("DELETE FROM export_job WHERE id = %s", (job,))
    assert owner.execute("SELECT status FROM export_job WHERE id = %s", (job,)).fetchone() == ("queued",)


def test_the_database_keeps_a_job_consistent(owner: psycopg.Connection, shop_a: Shop, shop_b: Shop) -> None:
    _job(owner, shop_a)
    with pytest.raises(psycopg.errors.UniqueViolation):
        _job(owner, shop_a, "running")
    _job(owner, shop_b)  # one waiting export per shop, not one in all
    for status, columns in (
        ("failed", {}),  # a failure names its kind
        ("done", {"error": "internal"}),  # and only a failure has one
        ("failed", {"error": "Ali owes 50000"}),  # a kind, never a text
        ("queued", {"file_id": uuid.uuid4()}),  # only a finished job has a file
        ("exported", {}),
    ):
        with pytest.raises(psycopg.errors.CheckViolation):
            _job(owner, shop_b, status, **columns)
    _job(owner, shop_b, "failed", error="timeout")
    _job(owner, shop_b, "done", file_id=uuid.uuid4())


def test_the_file_store_keeps_exports_besides_the_three_earlier_purposes(
    owner: psycopg.Connection, shop_a: Shop
) -> None:
    for purpose in ("subscription_receipt", "payment_notice", "import", "export"):
        owner.execute(
            "INSERT INTO stored_file (id, shop_id, purpose, object_key, sha256, size_bytes, mime) "
            "VALUES (gen_random_uuid(), %s, %s, 'aa/x', %s, 1, 'x/y')",
            (shop_a.shop_id, purpose, b"\x00" * 32),
        )
    with pytest.raises(psycopg.errors.CheckViolation):
        owner.execute(
            "INSERT INTO stored_file (id, shop_id, purpose, object_key, sha256, size_bytes, mime) "
            "VALUES (gen_random_uuid(), %s, 'backup', 'aa/x', %s, 1, 'x/y')",
            (shop_a.shop_id, b"\x00" * 32),
        )


def test_the_worker_takes_the_oldest_waiting_job_and_learns_only_which_it_is(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop, shop_b: Shop
) -> None:
    older = _job(owner, shop_b, created_at=NOW - timedelta(minutes=5))
    newer = _job(owner, shop_a, created_at=NOW - timedelta(minutes=1))
    with as_app(None) as conn:
        assert _claim(conn) == (older, shop_b.shop_id, 1)
    with as_app(None) as conn:
        assert _claim(conn) == (newer, shop_a.shop_id, 1)
    with as_app(None) as conn:
        assert _claim(conn) is None, "both are running now, and neither is stale"
    rows = owner.execute(
        "SELECT status, attempts, started_at IS NOT NULL FROM export_job WHERE id = ANY(%s)", ([older, newer],)
    ).fetchall()
    assert rows == [("running", 1, True)] * 2


def test_a_job_that_has_been_running_too_long_is_taken_again_and_one_that_has_not_is_left(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop, shop_b: Shop
) -> None:
    stale = _job(owner, shop_a, "running", started_at=datetime.now(UTC) - timedelta(minutes=16), attempts=2)
    _job(owner, shop_b, "running", started_at=datetime.now(UTC) - timedelta(minutes=14), attempts=1)
    with as_app(None) as conn:
        assert _claim(conn) == (stale, shop_a.shop_id, 3)
        assert _claim(conn) is None
    owner.execute("UPDATE export_job SET status = 'done' WHERE id = %s", (stale,))
    owner.execute("UPDATE export_job SET status = 'failed', error = 'internal' WHERE shop_id = %s", (shop_b.shop_id,))
    with as_app(None) as conn:
        assert _claim(conn, stale_minutes=-60) is None, "a closed job is never taken, however old"


def test_two_workers_never_take_the_same_job(
    owner: psycopg.Connection, database_url: str, shop_a: Shop, shop_b: Shop
) -> None:
    first_job = _job(owner, shop_a, created_at=NOW - timedelta(minutes=2))
    second_job = _job(owner, shop_b, created_at=NOW - timedelta(minutes=1))
    with psycopg.connect(database_url) as one, psycopg.connect(database_url) as two:
        for conn in (one, two):
            conn.execute("SET ROLE qd_app")
        # Both transactions are open at once: the second does not wait for the first, it takes the next job.
        assert _claim(one) == (first_job, shop_a.shop_id, 1)
        assert _claim(two) == (second_job, shop_b.shop_id, 1)
        with psycopg.connect(database_url) as three:
            three.execute("SET ROLE qd_app")
            assert _claim(three) is None

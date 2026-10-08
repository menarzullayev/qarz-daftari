"""The world that several refusal tests are given in a row (`untouched_world`, conftest.py).

It may be handed to a second test only while it is exactly what was seeded, and that is decided by the
position of the database server's write-ahead log. What matters is the refusing direction: after any
write at all, by anyone, the kept world is not handed over again. Each kind of write is made here against
the real server, the position must have moved, and the world must be given up.

The other direction (the same world again when the position has not moved) is shown with the position
held still, because a real server may write on its own at any moment (a checkpoint, another test session
on the same server), and then a world is seeded once more than needed, which costs time and nothing else.
"""

import dataclasses
import time
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient

from . import conftest
from .conftest import (
    KEPT_WORLD_SECONDS,
    World,
    as_user,
    days_at,
    hand_over_world,
    keep_world,
    still_untouched,
    wal_position,
)

pytestmark = pytest.mark.db


@pytest.fixture
def still_position(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Holds the position still: `wal_position` answers the one value in the list until a test changes it."""
    position = ["0/1000000"]
    monkeypatch.setattr(conftest, "wal_position", lambda owner: position[0])
    yield position
    # A world kept under a made-up position is not left for the tests that follow.
    conftest._kept_world.clear()


# --- any write moves the position, and the world is given up ------------------------------------------------


def _a_row_of_the_world(owner: psycopg.Connection, world: World) -> None:
    owner.execute("UPDATE shop SET name = 'Renamed' WHERE id = %s", (world.shop_a,))


def _a_row_set_to_what_it_was(owner: psycopg.Connection, world: World) -> None:
    owner.execute("UPDATE shop SET name = name WHERE id = %s", (world.shop_a,))


def _a_change_made_and_undone(owner: psycopg.Connection, world: World) -> None:
    owner.execute("UPDATE customer SET status = 'archived' WHERE id = %s", (world.settled_customer_a,))
    owner.execute("UPDATE customer SET status = 'active' WHERE id = %s", (world.settled_customer_a,))


def _a_row_of_another_shop(owner: psycopg.Connection, world: World) -> None:
    owner.execute("INSERT INTO shop (id, name) VALUES (%s, 'Elsewhere')", (uuid.uuid4(),))


def _a_row_that_belongs_to_no_shop(owner: psycopg.Connection, world: World) -> None:
    owner.execute("INSERT INTO app_user (id, tg_id) VALUES (%s, %s)", (uuid.uuid4(), uuid.uuid4().int % 10**15))


def _a_row_removed(owner: psycopg.Connection, world: World) -> None:
    owner.execute("DELETE FROM catalog_item WHERE id = %s", (world.learned_item_a,))


def _a_write_that_was_rolled_back(owner: psycopg.Connection, world: World) -> None:
    with pytest.raises(RuntimeError, match="undo"), owner.transaction():
        owner.execute("UPDATE membership SET role = 'manager' WHERE id = %s", (world.seller_a_membership,))
        raise RuntimeError("undo")
    role = owner.execute("SELECT role FROM membership WHERE id = %s", (world.seller_a_membership,)).fetchone()
    assert role == ("seller",)


WRITES: list[Callable[[psycopg.Connection, World], None]] = [
    _a_row_of_the_world,
    _a_row_set_to_what_it_was,
    _a_change_made_and_undone,
    _a_row_of_another_shop,
    _a_row_that_belongs_to_no_shop,
    _a_row_removed,
    _a_write_that_was_rolled_back,
]


@pytest.mark.parametrize("write", WRITES, ids=[write.__name__.lstrip("_") for write in WRITES])
def test_after_any_write_the_world_is_not_handed_over_again(
    owner: psycopg.Connection, database_url: str, write: Callable[[psycopg.Connection, World], None]
) -> None:
    kept = keep_world(owner, database_url)
    before = wal_position(owner)
    write(owner, kept.world)
    assert wal_position(owner) != before, "the write itself moved the position"
    assert not still_untouched(kept, owner, database_url)
    assert hand_over_world(owner, database_url) is not kept.world


def test_a_write_that_someone_else_has_not_committed_yet_gives_the_world_up(
    owner: psycopg.Connection, database_url: str
) -> None:
    kept = keep_world(owner, database_url)
    before = wal_position(owner)
    with psycopg.connect(database_url) as other:
        other.execute("UPDATE shop SET name = 'Pending' WHERE id = %s", (kept.world.shop_b,))
        assert wal_position(owner) != before
        assert not still_untouched(kept, owner, database_url)
        other.rollback()
    assert not still_untouched(kept, owner, database_url)


def test_a_call_that_goes_through_gives_the_world_up(
    client: TestClient, owner: psycopg.Connection, database_url: str
) -> None:
    kept = keep_world(owner, database_url)
    before = wal_position(owner)
    allowed = client.patch(
        f"/api/v1/shops/{kept.world.shop_a}",
        json={"name": "Renamed"},
        headers={**as_user(kept.world.owner_a), "Idempotency-Key": f"kept-{uuid.uuid4().hex}"},
    )
    assert allowed.status_code == 200, allowed.text
    assert wal_position(owner) != before
    assert not still_untouched(kept, owner, database_url)


def test_the_position_is_read_after_the_seeding_and_never_goes_back(
    owner: psycopg.Connection, database_url: str
) -> None:
    def number(position: str) -> int:
        high, low = position.split("/")
        return (int(high, 16) << 32) + int(low, 16)

    start = number(wal_position(owner))
    kept = keep_world(owner, database_url)
    assert start < number(kept.wal_position) <= number(wal_position(owner))
    owner.execute("UPDATE shop SET name = 'Renamed' WHERE id = %s", (kept.world.shop_a,))
    assert number(wal_position(owner)) > number(kept.wal_position)


# --- with the position held still: handed over again, unless it is old, of yesterday or of another database -


def test_while_the_position_stands_still_the_same_world_is_handed_over(
    owner: psycopg.Connection, database_url: str, still_position: list[str]
) -> None:
    first = hand_over_world(owner, database_url)
    assert hand_over_world(owner, database_url) is first
    assert hand_over_world(owner, database_url) is first
    still_position[0] = "0/1000028"
    second = hand_over_world(owner, database_url)
    assert second is not first
    assert second.shop_a != first.shop_a and second.owner_a != first.owner_a and second.entry_a != first.entry_a
    assert owner.execute("SELECT name FROM shop WHERE id = %s", (second.shop_a,)).fetchone() == ("Shop A",)
    assert hand_over_world(owner, database_url) is second


def test_a_world_is_not_kept_for_long_or_past_the_day_or_for_another_database(
    owner: psycopg.Connection, database_url: str, still_position: list[str]
) -> None:
    kept = keep_world(owner, database_url)
    assert still_untouched(kept, owner, database_url)  # the control for the four below
    old = dataclasses.replace(kept, seeded_at=time.monotonic() - KEPT_WORLD_SECONDS - 1)
    assert not still_untouched(old, owner, database_url)
    assert still_untouched(dataclasses.replace(kept, seeded_at=time.monotonic() - 1), owner, database_url)
    utc, tashkent = kept.days
    assert not still_untouched(dataclasses.replace(kept, days=(utc - timedelta(days=1), tashkent)), owner, database_url)
    assert not still_untouched(dataclasses.replace(kept, days=(utc, tashkent - timedelta(days=1))), owner, database_url)
    assert not still_untouched(kept, owner, database_url + "-other")
    assert not still_untouched(dataclasses.replace(kept, wal_position="0/FFFFFF"), owner, database_url)


def test_both_days_are_watched_the_servers_and_the_services() -> None:
    # From 19:00 to 24:00 UTC it is already tomorrow in Tashkent: the world's dates are counted from both.
    assert days_at(datetime(2026, 3, 1, 18, 59, tzinfo=UTC)) == (date(2026, 3, 1), date(2026, 3, 1))
    assert days_at(datetime(2026, 3, 1, 19, 0, tzinfo=UTC)) == (date(2026, 3, 1), date(2026, 3, 2))
    assert days_at(datetime(2026, 3, 2, 0, 0, tzinfo=UTC)) == (date(2026, 3, 2), date(2026, 3, 2))


def test_the_counts_say_how_often_a_world_was_seeded_and_handed_over_again(
    owner: psycopg.Connection, database_url: str, still_position: list[str]
) -> None:
    still_position[0] = "0/2000000"  # not the position of whatever world an earlier test left kept
    before = dict(conftest.kept_world_counts)
    hand_over_world(owner, database_url)
    hand_over_world(owner, database_url)
    hand_over_world(owner, database_url)
    assert conftest.kept_world_counts["seeded"] == before["seeded"] + 1
    assert conftest.kept_world_counts["handed over again"] == before["handed over again"] + 2

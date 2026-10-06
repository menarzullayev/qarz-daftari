"""The load test's generated data (S19.1, REQ-N13): it must be data the application could have recorded.

A small set is generated in memory and checked against the ledger rules of `qarz.domain.ledger` and the
rules the schema enforces. Each rule the checker knows is then broken on purpose, to show that the
checker would have noticed.
"""

import hashlib
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from loadtest import report
from loadtest.dataset import COLUMNS, FULL, TINY, ShopData, digest, generate, session_token
from loadtest.invariants import problems
from loadtest.load import require_throwaway

NOW = datetime(2026, 10, 7, 12, tzinfo=UTC)


def tiny(seed: int = 1) -> list[ShopData]:
    return list(generate(TINY, seed, NOW))


def column(table: str, name: str) -> int:
    return COLUMNS[table].index(name)


def change(shop: ShopData, table: str, position: int, **values: Any) -> None:
    row = list(shop.rows[table][position])
    for name, value in values.items():
        row[column(table, name)] = value
    shop.rows[table][position] = tuple(row)


def test_the_generated_set_breaks_no_rule() -> None:
    for seed in (1, 2, 3):
        assert problems(tiny(seed)) == []


def test_it_has_the_sizes_the_profile_names() -> None:
    shops = tiny()
    assert len(shops) == TINY.shops
    assert sum(len(shop.rows["customer"]) for shop in shops) == TINY.customers
    large = [shop for shop in shops if shop.large]
    assert len(large) == TINY.large_shops
    assert len(large[0].rows["customer"]) == TINY.large_customers
    assert len(large[0].rows["ledger_entry"]) == TINY.large_entries
    # Several shops, each with its own staff, so tenant isolation is in play.
    assert all(shop.rows["membership"] and shop.rows["customer"] for shop in shops)
    assert len({shop.shop_id for shop in shops}) == TINY.shops


def test_it_holds_every_kind_of_record_the_targets_touch() -> None:
    shops = tiny()
    kinds = {row[column("ledger_entry", "kind")] for shop in shops for row in shop.rows["ledger_entry"]}
    assert kinds == {"credit", "opening", "payment", "reversal"}
    statuses = {row[column("customer", "status")] for shop in shops for row in shop.rows["customer"]}
    assert statuses == {"active", "archived"}
    for table in ("promise", "goods_line", "catalog_item", "customer_link", "user_session", "activity"):
        assert any(shop.rows[table] for shop in shops), table
    roles = {row[column("membership", "role")] for shop in shops for row in shop.rows["membership"]}
    assert roles == {"owner", "manager", "seller"}


def test_the_same_seed_gives_the_same_rows_and_another_seed_does_not() -> None:
    assert digest(tiny(1)) == digest(tiny(1))
    assert digest(tiny(1)) != digest(tiny(2))
    # The anchor instant is part of the input: dates are laid out before it.
    assert digest(tiny(1)) != digest(generate(TINY, 1, NOW + timedelta(days=1)))


def test_every_staff_member_has_a_session_the_driver_can_derive() -> None:
    for shop in tiny(7):
        hashes = {row[column("user_session", "token_hash")] for row in shop.rows["user_session"]}
        for member in shop.rows["membership"]:
            token = session_token(7, member[column("membership", "user_id")])
            assert token.isascii() and 20 <= len(token) <= 128
            assert hashlib.sha256(token.encode("ascii")).digest() in hashes


def test_the_full_profile_is_the_design_capacity() -> None:
    assert (FULL.shops, FULL.customers) == (5000, 500_000)  # REQ-N13
    assert (FULL.large_customers, FULL.large_entries) == (2000, 200_000)  # NFR-005


@pytest.mark.parametrize(
    "values",
    [
        {"shops": 0},
        {"large_shops": 5},
        {"customers": 26},  # nothing left for the three other shops
        {"large_entries": 10},
    ],
)
def test_an_impossible_profile_is_refused(values: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        replace(TINY, **values)


def test_an_anchor_without_a_time_zone_is_refused() -> None:
    with pytest.raises(ValueError):
        next(generate(TINY, 1, datetime(2026, 10, 7, 12)))


# --- the checker notices each broken rule ---------------------------------------------------------------


def _first(shop: ShopData, kind: str) -> int:
    kinds = [row[column("ledger_entry", "kind")] for row in shop.rows["ledger_entry"]]
    return kinds.index(kind)


def _payment_above_the_balance(shop: ShopData) -> None:
    change(shop, "ledger_entry", _first(shop, "payment"), amount=99_000_000)


def _gap_in_seq(shop: ShopData) -> None:
    change(shop, "ledger_entry", 1, seq=40_000)


def _goods_do_not_sum(shop: ShopData) -> None:
    line = shop.rows["goods_line"][0]
    entry_id = line[column("goods_line", "entry_id")]
    position = [row[0] for row in shop.rows["ledger_entry"]].index(entry_id)
    amount = shop.rows["ledger_entry"][position][column("ledger_entry", "amount")]
    change(shop, "ledger_entry", position, amount=amount + 1000)


def _goods_line_rounded_wrongly(shop: ShopData) -> None:
    total = shop.rows["goods_line"][0][column("goods_line", "line_total")]
    change(shop, "goods_line", 0, line_total=total + 1)


def _goods_on_a_payment(shop: ShopData) -> None:
    payment = shop.rows["ledger_entry"][_first(shop, "payment")][0]
    change(shop, "goods_line", 0, entry_id=payment)


def _two_batches_of_goods(shop: ShopData) -> None:
    entries = [row[column("goods_line", "entry_id")] for row in shop.rows["goods_line"]]
    position = next(index for index, entry_id in enumerate(entries) if entries.count(entry_id) > 1)
    change(shop, "goods_line", position, batch_at=NOW)


def _credit_without_a_promise(shop: ShopData) -> None:
    del shop.rows["promise"][0]


def _reversal_of_another_amount(shop: ShopData) -> None:
    position = _first(shop, "reversal")
    amount = shop.rows["ledger_entry"][position][column("ledger_entry", "amount")]
    change(shop, "ledger_entry", position, amount=amount + 1000)


def _entry_reversed_twice(shop: ShopData) -> None:
    reversal = list(shop.rows["ledger_entry"][_first(shop, "reversal")])
    customer = reversal[column("ledger_entry", "customer_id")]
    last = max(row[3] for row in shop.rows["ledger_entry"] if row[column("ledger_entry", "customer_id")] == customer)
    reversal[0], reversal[column("ledger_entry", "seq")] = uuid4(), last + 1
    reversal[column("ledger_entry", "created_at")] = NOW
    shop.rows["ledger_entry"].append(tuple(reversal))


def _archived_customer_owes(shop: ShopData) -> None:
    statuses = [row[column("customer", "status")] for row in shop.rows["customer"]]
    customer = shop.rows["customer"][statuses.index("archived")][0]
    entries = [row for row in shop.rows["ledger_entry"] if row[column("ledger_entry", "customer_id")] == customer]
    # Without its last entry, the payment that settled the account, the customer still owes.
    shop.rows["ledger_entry"].remove(entries[-1])


def _wrong_normalized_name(shop: ShopData) -> None:
    change(shop, "customer", 0, name_norm="someone else")


def _entry_by_a_stranger(shop: ShopData) -> None:
    change(shop, "ledger_entry", 0, author_id=uuid4())


def _row_of_another_shop(shop: ShopData) -> None:
    change(shop, "customer", 0, shop_id=uuid4())


def _entries_out_of_order_in_time(shop: ShopData) -> None:
    change(shop, "ledger_entry", 1, created_at=NOW - timedelta(days=5000))


def _two_owners(shop: ShopData) -> None:
    roles = [row[column("membership", "role")] for row in shop.rows["membership"]]
    change(shop, "membership", roles.index("seller"), role="owner")


def _promise_before_the_sale(shop: ShopData) -> None:
    promised = shop.rows["promise"][0][column("promise", "promised_date")]
    change(shop, "promise", 0, promised_date=promised - timedelta(days=400))


@pytest.mark.parametrize(
    "breakage",
    [
        _payment_above_the_balance,
        _gap_in_seq,
        _goods_do_not_sum,
        _goods_line_rounded_wrongly,
        _goods_on_a_payment,
        _two_batches_of_goods,
        _credit_without_a_promise,
        _reversal_of_another_amount,
        _entry_reversed_twice,
        _archived_customer_owes,
        _wrong_normalized_name,
        _entry_by_a_stranger,
        _row_of_another_shop,
        _entries_out_of_order_in_time,
        _two_owners,
        _promise_before_the_sale,
    ],
)
def test_the_checker_notices_a_broken_rule(breakage: Callable[[ShopData], None]) -> None:
    shops = tiny()
    # The large shop has every kind of row each breakage needs.
    breakage(next(shop for shop in shops if shop.large))
    assert problems(shops) != []


# --- the database name guard ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["qarz", "postgres", "qd_test_abc", "qd_load_", 'qd_load_x"; DROP', "QD_LOAD_X", "qd_load_a b"]
)
def test_only_a_load_test_database_name_is_accepted(name: str) -> None:
    with pytest.raises(ValueError):
        require_throwaway(name)


def test_a_load_test_database_name_is_accepted() -> None:
    require_throwaway("qd_load_full_1")


# --- reading the measurements ---------------------------------------------------------------------------


def test_percentile_is_the_nearest_rank() -> None:
    values = [float(n) for n in range(1, 101)]
    assert report.percentile(values, 0.5) == 50
    assert report.percentile(values, 0.95) == 95
    assert report.percentile(values, 0.99) == 99
    assert report.percentile(values, 1) == 100
    assert report.percentile([7.0], 0.95) == 7
    with pytest.raises(ValueError):
        report.percentile([], 0.95)


def test_a_target_is_met_at_the_95th_percentile() -> None:
    samples = report.Samples(ok_ms=[100.0] * 95 + [2000.0] * 5)
    line = report.summarize("overview", "large", samples)
    assert (line.target_ms, line.requirement, line.verdict) == (300, "NFR-005", report.MET)
    assert (line.p50, line.p95, line.p99, line.worst) == (100, 100, 2000, 2000)


def test_a_target_is_missed_when_more_than_one_call_in_twenty_is_slow() -> None:
    samples = report.Samples(ok_ms=[100.0] * 94 + [301.0] * 6)
    assert report.summarize("overview", "large", samples).verdict == report.MISSED


def test_failed_calls_count_against_a_target() -> None:
    # Every answer that came was fast, but one call in ten did not answer at all.
    samples = report.Samples(ok_ms=[50.0] * 90)
    samples.errors["timeout"] = 10
    line = report.summarize("chat_credit", "other", samples)
    assert (line.errors, line.verdict) == (10, report.MISSED)
    nothing = report.Samples()
    nothing.errors["500"] = 3
    assert report.summarize("chat_credit", "other", nothing).verdict == report.MISSED


def test_an_operation_without_a_target_or_without_calls_says_so() -> None:
    assert report.summarize("customer_page", "large", report.Samples(ok_ms=[900.0])).verdict == report.NO_TARGET
    assert report.summarize("overview", "large", report.Samples()).verdict == report.NO_DATA


def test_every_target_belongs_to_a_reported_operation_and_the_table_shows_it() -> None:
    assert set(report.TARGETS) <= set(report.OPERATIONS)
    results = {("overview", "large"): report.Samples(ok_ms=[120.0, 480.0]), ("api_credit", "other"): report.Samples()}
    table = report.markdown(report.lines(results))
    assert "| overview | large | 2 | 120 | 480 | 480 | 480 | 0 | 0 | 300 ms (NFR-005) | NOT met |" in table
    assert "| api_credit | other | 0 | - | - | - | - | 0 | 0 | - | no target |" in table
    again = report.Samples.from_json(results[("overview", "large")].as_json())
    assert again.ok_ms == [120.0, 480.0]

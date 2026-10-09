"""The rules of the network between shops that need no storage (qarz.domain.network; BR-80 to BR-96).

Each step belongs to one side and one state: the table of each machine is written out here, and every
combination that is not in it is refused.
"""

from datetime import date, timedelta
from decimal import Decimal
from itertools import product

import pytest

from qarz.domain import catalog, permissions
from qarz.domain import network as n
from qarz.domain.access import Role
from qarz.domain.money import Currency


def test_a_link_is_answered_once_by_the_shop_that_invited_and_ended_by_either() -> None:
    for state, invited in product(n.LINK_STATES, (True, False)):
        assert n.may_decide_link(state, invited=invited) is (state == n.REQUESTED and invited)
    assert {state for state in n.LINK_STATES if n.may_end_link(state)} == {n.REQUESTED, n.ACTIVE}


def test_only_the_buyer_orders_and_only_over_an_active_link() -> None:
    allowed = {(state, role) for state, role in product(n.LINK_STATES, n.ROLES) if n.may_send_order(state, role)}
    assert allowed == {(n.ACTIVE, n.BUYER)}


def test_only_the_supplier_accepts_and_only_an_order_that_was_just_sent() -> None:
    allowed = {(status, role) for status, role in product(n.ORDER_STATES, n.ROLES) if n.may_accept_order(status, role)}
    assert allowed == {(n.SENT, n.SUPPLIER)}


def test_an_order_is_closed_only_while_nothing_of_it_is_received() -> None:
    notes = (None, *n.NOTE_STATES)
    allowed = {(status, note) for status, note in product(n.ORDER_STATES, notes) if n.may_close_order(status, note)}
    # Sent or accepted: always. Delivered: unless its note still waits for the buyer's answer.
    assert allowed == {(status, note) for status in (n.SENT, n.ACCEPTED) for note in notes} | {
        (n.DELIVERED, note) for note in notes if note != n.ISSUED
    }
    assert (n.closed_as(n.BUYER), n.closed_as(n.SUPPLIER)) == (n.CANCELLED, n.DECLINED)


def test_the_supplier_issues_one_note_and_corrects_it_until_it_is_received() -> None:
    notes = (None, *n.NOTE_STATES)
    allowed = {
        (order, role, note)
        for order, role, note in product(n.ORDER_STATES, n.ROLES, notes)
        if n.may_issue_note(order, role, note)
    }
    assert allowed == {
        (n.ACCEPTED, n.SUPPLIER, None),
        (n.DELIVERED, n.SUPPLIER, n.ISSUED),
        (n.DELIVERED, n.SUPPLIER, n.REJECTED),
    }


def test_only_the_buyer_answers_a_note_and_only_one_that_waits() -> None:
    allowed = {(status, role) for status, role in product(n.NOTE_STATES, n.ROLES) if n.may_answer_note(status, role)}
    assert allowed == {(n.ISSUED, n.BUYER)}


def test_a_payment_is_answered_by_the_other_side_and_taken_back_by_its_own() -> None:
    for status, own in product(n.PAYMENT_STATES, (True, False)):
        assert n.may_answer_payment(status, recorded_by_own=own) is (status == n.AWAITING and not own)
        assert n.may_withdraw_payment(status, recorded_by_own=own) is (status == n.AWAITING and own)


def test_a_code_is_long_random_and_kept_only_as_its_hash() -> None:
    codes = {n.new_code() for _ in range(200)}
    assert len(codes) == 200 and all(len(code) >= 43 and n.plausible_code(code) for code in codes)
    code = next(iter(codes))
    assert len(n.code_hash(code)) == 32 and n.code_hash(f"  {code}\n") == n.code_hash(code)
    assert n.code_hash(code) != n.code_hash(code[:-1] + ("A" if code[-1] != "A" else "B"))
    for wrong in ("", "short", "x" * 65, "with space inside the code 123", "<script>alert(1)</script>xxxx"):
        assert not n.plausible_code(wrong), wrong
    assert timedelta(days=7) >= n.INVITE_LIFETIME, "the database refuses a longer life"


def test_what_is_typed_is_cleaned_or_refused() -> None:
    assert n.line_name("  Guruch   oliy ") == "Guruch oliy"
    assert n.line_name("x" * 80) == "x" * 80 and n.MAX_LINE_NAME == catalog.MAX_NAME_LENGTH
    assert n.text("  ", limit=5) is None and n.text(None, limit=5) is None
    assert n.reason("  Tovar   kam keldi ") == "Tovar kam keldi"
    assert n.unit("kg") == "kg" and n.offered_qty("0") == Decimal("0")
    today = date(2026, 10, 9)
    assert n.wanted_date(None, today) is None and n.wanted_date(today, today) == today
    for wrong in (
        lambda: n.line_name(" "),
        lambda: n.line_name("x" * (n.MAX_LINE_NAME + 1)),
        # A name the buyer's catalogue would refuse when the line is taken as a new item.
        lambda: n.line_name("x" * 81),
        lambda: n.line_name("ь"),
        lambda: n.text("x" * 6, limit=5),
        lambda: n.reason("xx"),
        lambda: n.reason("x" * (n.MAX_REASON + 1)),
        lambda: n.unit("tonna"),
        lambda: n.offered_qty("-1"),
        lambda: n.offered_qty("1.2345"),
        lambda: n.wanted_date(today - timedelta(days=1), today),
        lambda: n.wanted_date(today + timedelta(days=n.MAX_WANTED_DAYS + 1), today),
    ):
        with pytest.raises(ValueError):
            wrong()


def test_a_note_comes_to_the_same_figure_on_both_sides_and_fits_both_books() -> None:
    lines = [n.PricedLine(1, Decimal("10"), 12_000), n.PricedLine(2, Decimal("4.5"), 11_000)]
    assert [line.total for line in lines] == [120_000, 49_500] and n.total_of(lines) == 169_500
    # Half a so'm goes up, once per line: the stock's rule and the ledger's.
    assert n.PricedLine(1, Decimal("0.333"), 1_000).total == 333
    assert n.PricedLine(1, Decimal("0.0005"), 1_000).total == 1
    assert n.total_problem(Currency.UZS, 100) is None and n.total_problem(Currency.UZS, 100_000_000) is None
    assert n.total_problem(Currency.USD, 1) is None and n.total_problem(Currency.USD, 1_000_000) is None
    for currency, total in (
        (Currency.UZS, 99),
        (Currency.UZS, 100_000_001),
        (Currency.USD, 0),
        (Currency.USD, 1_000_001),
    ):
        assert n.total_problem(currency, total) is not None
    assert [n.payment_terms(100, paid) for paid in (0, 40, 100)] == ["credit", "part", "paid"]


def test_the_permissions_of_the_network_and_who_holds_them_by_default() -> None:
    held = {
        key: {role for role in Role if key in permissions.effective(role)}
        for key in sorted(permissions.ALL_KEYS)
        if key.startswith("network.")
    }
    managers = {Role.MANAGER, Role.OWNER}
    assert held == {
        "network.confirm": managers,
        "network.fulfil": managers,
        "network.manage": {Role.OWNER},
        "network.order": managers,
        "network.view": managers,
    }
    # Each can be granted or denied to one member: none follows the role alone.
    assert not {key for key in held if key in permissions.FIXED_KEYS}
    # A seller given the right to confirm is not thereby given the stock or the supplier accounts.
    seller = permissions.effective(Role.SELLER, frozenset({"network.confirm"}), frozenset())
    assert "network.confirm" in seller and not {"stock.receive", "suppliers.pay", "network.manage"} & seller

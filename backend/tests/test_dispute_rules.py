from datetime import UTC, datetime, timedelta

import pytest

from qarz.domain.disputes import DisputeRefusal, clean_reason, may_dispute
from qarz.domain.ledger import EntryKind

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


def check(**overrides: object) -> DisputeRefusal | None:
    arguments: dict[str, object] = {
        "kind": EntryKind.CREDIT,
        "is_reversed": False,
        "disputed_before": False,
        "notified_at": NOW - timedelta(days=1),
        "now": NOW,
    }
    arguments.update(overrides)
    return may_dispute(**arguments)  # type: ignore[arg-type]


@pytest.mark.parametrize("kind", [EntryKind.CREDIT, EntryKind.OPENING])
def test_an_entry_that_increases_the_debt_may_be_disputed(kind: EntryKind) -> None:
    assert check(kind=kind) is None


@pytest.mark.parametrize("kind", [EntryKind.PAYMENT, EntryKind.REVERSAL])
def test_an_entry_that_does_not_increase_the_debt_may_not(kind: EntryKind) -> None:
    assert check(kind=kind) is DisputeRefusal.NOT_A_DEBT


def test_a_reversed_entry_may_not_be_disputed() -> None:
    assert check(is_reversed=True) is DisputeRefusal.REVERSED


def test_an_entry_is_disputed_at_most_once() -> None:
    assert check(disputed_before=True) is DisputeRefusal.ALREADY_DISPUTED


def test_the_window_is_thirty_days_from_being_notified() -> None:
    assert check(notified_at=NOW - timedelta(days=30)) is None, "the last moment of the thirtieth day is in time"
    assert check(notified_at=NOW - timedelta(days=30, seconds=1)) is DisputeRefusal.TOO_LATE
    assert check(notified_at=NOW) is None


def test_the_first_reason_that_applies_is_given() -> None:
    assert check(kind=EntryKind.PAYMENT, is_reversed=True, disputed_before=True) is DisputeRefusal.NOT_A_DEBT
    assert check(is_reversed=True, disputed_before=True) is DisputeRefusal.REVERSED


@pytest.mark.parametrize(
    ("raw", "cleaned"),
    [
        ("abc", "abc"),
        ("  men   bu  narsani   olmaganman ", "men bu narsani olmaganman"),
        ("x" * 300, "x" * 300),
        ("нет", "нет"),
    ],
)
def test_a_reason_is_tidied(raw: str, cleaned: str) -> None:
    assert clean_reason(raw) == cleaned


@pytest.mark.parametrize("raw", ["", "  ", "ab", " a b ", "x" * 301, "a \n b"])
def test_a_reason_too_short_or_too_long_is_not_one(raw: str) -> None:
    assert clean_reason(raw) is None or len(clean_reason(raw) or "") >= 3
    if raw in ("", "  ", "ab", "x" * 301):
        assert clean_reason(raw) is None

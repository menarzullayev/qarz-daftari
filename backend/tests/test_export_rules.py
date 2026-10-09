"""The pure rules of exports (REQ-028) and the words of the workbook."""

import string
from datetime import UTC, datetime, timedelta

import pytest

from qarz.application.export_texts import RU, UZ, header, word
from qarz.domain.exports import (
    DAILY_LIMIT,
    EXPORT_RETENTION,
    MAX_ATTEMPTS,
    STALE_AFTER,
    ExportError,
    ExportRefusal,
    counts,
    delete_after,
    gives_up,
    may_request,
    signed_effect,
    stale_before,
)

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def test_the_numbers_this_story_decided() -> None:
    assert DAILY_LIMIT == 5
    assert timedelta(days=7) == EXPORT_RETENTION
    assert timedelta(minutes=15) == STALE_AFTER
    assert MAX_ATTEMPTS == 3


def test_one_export_at_a_time_and_five_a_day() -> None:
    assert may_request(in_progress=False, today_count=0) is None
    assert may_request(in_progress=False, today_count=4) is None
    assert may_request(in_progress=False, today_count=5) is ExportRefusal.DAILY_LIMIT
    assert may_request(in_progress=False, today_count=6) is ExportRefusal.DAILY_LIMIT
    assert may_request(in_progress=True, today_count=0) is ExportRefusal.IN_PROGRESS
    # What is being written now is said first: it is the reason that will pass by itself.
    assert may_request(in_progress=True, today_count=5) is ExportRefusal.IN_PROGRESS


def test_a_job_is_started_three_times_and_then_given_up() -> None:
    assert [gives_up(attempts) for attempts in (1, 2, 3, 4, 5)] == [False, False, False, True, True]


def test_a_silent_job_is_stale_after_fifteen_minutes_and_a_file_is_kept_seven_days() -> None:
    assert stale_before(NOW) == NOW - timedelta(minutes=15)
    assert delete_after(NOW) == datetime(2026, 10, 17, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("kind", "reversed_kind", "effect"),
    [
        ("credit", None, 700),
        ("opening", None, 700),
        ("payment", None, -700),
        ("reversal", "credit", -700),
        ("reversal", "opening", -700),
        ("reversal", "payment", 700),
    ],
)
def test_what_each_kind_of_entry_does_to_the_debt(kind: str, reversed_kind: str | None, effect: int) -> None:
    assert signed_effect(kind, 700, reversed_kind) == effect


def test_an_entry_and_its_reversal_cancel_out() -> None:
    for kind in ("credit", "opening", "payment"):
        assert signed_effect(kind, 4500, None) + signed_effect("reversal", 4500, kind) == 0


def test_only_entries_that_are_neither_reversals_nor_reversed_count_in_totals() -> None:
    assert counts("credit", False) and counts("payment", False) and counts("opening", False)
    assert not counts("credit", True) and not counts("payment", True)
    assert not counts("reversal", False)


def test_a_failure_is_named_by_kind_only() -> None:
    assert {error.value for error in ExportError} == {"interrupted", "timeout", "file_store", "internal"}


# --- the words of the workbook -------------------------------------------------------------------------


def test_russian_mirrors_uzbek_key_for_key_and_column_for_column() -> None:
    assert set(RU) == set(UZ)
    for key, value in UZ.items():
        other = RU[key]
        assert type(other) is type(value), key
        if isinstance(value, tuple):
            assert len(other) == len(value) and all(text.strip() for text in (*value, *other)), key
        else:
            assert isinstance(other, str) and value.strip() and other.strip(), key
            assert not [name for _, name, _, _ in string.Formatter().parse(value) if name], key


def test_sheet_names_fit_a_workbook_and_differ_from_each_other() -> None:
    for catalog in (UZ, RU):
        names = [catalog[key] for key in catalog if key.startswith("sheet_")]
        assert len(names) == 11 == len(set(names)), "five of the ledger, the cash book, five of the stock"
        for name in names:
            assert isinstance(name, str) and len(name) <= 25 and not set(name) & set("[]:*?/\\")


def test_words_fall_back_as_the_export_needs() -> None:
    assert word("uz", "kind_credit") == "Nasiya" and word("ru", "kind_credit") == "Продажа в долг"
    assert word("kk", "yes") == "Ha", "an unknown language reads Uzbek"
    assert word("en", "yes") == "Yes" and word("uz-Cyrl", "yes") == "Ҳа"
    assert word("uz", "actor_something_new", "something_new") == "something_new"
    with pytest.raises(KeyError):
        word("uz", "no_such_key")
    with pytest.raises(KeyError):
        word("uz", "ledger")  # a header is not a word
    assert header("ru", "customers")[0] == "Клиент" and len(header("uz", "ledger")) == 14
    with pytest.raises(KeyError):
        header("uz", "yes")


def test_every_value_the_database_allows_has_its_word() -> None:
    for kind in ("credit", "opening", "payment", "reversal"):
        assert f"kind_{kind}" in UZ
    for status in ("active", "archived", "anonymized"):
        assert f"status_{status}" in UZ
    for role in ("seller", "manager", "owner"):
        assert f"role_{role}" in UZ
    for actor in ("default", "staff", "customer_request"):
        assert f"actor_{actor}" in UZ


def test_the_file_store_hands_back_objects_as_large_as_an_export(monkeypatch: pytest.MonkeyPatch) -> None:
    """A receipt is at most 5 MB, a workbook of a large shop is tens of megabytes: the deployed store's own
    limit on what it returns must be the export's, or a large export could be written and never fetched."""
    from qarz.domain.exports import MAX_EXPORT_BYTES
    from qarz.domain.files import MAX_FILE_BYTES
    from qarz.infrastructure.settings import Settings
    from qarz.interface import asgi

    seen: list[int] = []

    def capture(settings: Settings, *, max_object_bytes: int) -> None:
        seen.append(max_object_bytes)

    monkeypatch.setattr(asgi, "build_file_store", capture)
    asgi.build(Settings(database_url="postgresql://qd_app:unused@127.0.0.1:1/unused", bot_token="123:test"))
    assert seen == [MAX_EXPORT_BYTES]
    assert MAX_EXPORT_BYTES == 256 * 1024 * 1024 > 40 * 1024 * 1024 > MAX_FILE_BYTES

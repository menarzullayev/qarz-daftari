"""The shards of the suite (`pytest --shard=INDEX/TOTAL`) and the proof CI runs on them.

A test that fell into no shard would stop running with nothing turning red. Three things stand against
that: the selection is a function of the node identifier alone (here), CI compares real collections
(`scripts/check_shards.py`, whose comparison is tested here against shards that are not a partition), and
the option is exercised through pytest itself (the last tests).
"""

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from .sharding import Shard, parse_shard, shard_of, split

BACKEND = Path(__file__).resolve().parents[1]

NODE_IDS = [f"tests/api/test_authorization_suite.py::test_refused[{number}]" for number in range(2000)] + [
    f"tests/test_ledger.py::test_balance[{number}]" for number in range(2000)
]


def _check_shards() -> ModuleType:
    path = BACKEND / "scripts" / "check_shards.py"
    spec = importlib.util.spec_from_file_location("check_shards", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _shards(total: int) -> list[list[str]]:
    return [
        [NODE_IDS[position] for position in split(NODE_IDS, Shard(index, total))[0]] for index in range(1, total + 1)
    ]


# --- the selection -------------------------------------------------------------------------------------


def test_a_tests_shard_is_fixed_by_its_identifier() -> None:
    # Written out, so that a change of the function (or of the hash) is a visible change: two jobs of one
    # run must agree, and they do only if every process computes the same.
    assert shard_of("tests/test_names.py::test_кириллица", 4) == 1
    assert shard_of("tests/api/test_auth.py::test_sign_in[uz]", 4) == 2
    assert shard_of("tests/db/test_open_debt.py::test_view", 4) == 3
    assert shard_of("tests/worker/test_dispatcher.py::test_retry[sms]", 4) == 4
    assert [shard_of("tests/db/test_open_debt.py::test_view", total) for total in (1, 2, 3)] == [1, 1, 2]


@pytest.mark.parametrize("total", [1, 2, 3, 4, 7])
def test_every_test_is_in_exactly_one_shard(total: int) -> None:
    shards = _shards(total)
    assert sum(len(shard) for shard in shards) == len(NODE_IDS)
    assert sorted(node_id for shard in shards for node_id in shard) == sorted(NODE_IDS)
    assert _check_shards().partition_errors(NODE_IDS, shards) == []


def test_a_shard_keeps_the_order_of_the_collection_and_the_rest_is_what_it_left_out() -> None:
    selected, deselected = split(NODE_IDS, Shard(2, 4))
    assert selected == sorted(selected) and deselected == sorted(deselected)
    assert sorted(selected + deselected) == list(range(len(NODE_IDS)))
    assert all(shard_of(NODE_IDS[position], 4) == 2 for position in selected)
    assert all(shard_of(NODE_IDS[position], 4) != 2 for position in deselected)


def test_one_costly_file_is_spread_over_all_shards() -> None:
    # The authorization suite holds half of the suite's time: each shard gets about a quarter of it.
    costly = [node_id for node_id in NODE_IDS if node_id.startswith("tests/api/")]
    for index in range(1, 5):
        share = len(split(costly, Shard(index, 4))[0])
        assert 425 <= share <= 575, (index, share)


def test_one_shard_of_one_is_the_whole_suite() -> None:
    assert split(NODE_IDS, Shard(1, 1)) == (list(range(len(NODE_IDS))), [])


@pytest.mark.parametrize("value", ["1/4", "4/4", "1/1", "12/12"])
def test_a_shard_is_written_index_slash_total(value: str) -> None:
    shard = parse_shard(value)
    assert f"{shard.index}/{shard.total}" == value


MALFORMED = ["", "1", "/", "1/", "/4", "0/4", "5/4", "1/0", "0/0", "-1/4", "1/-4", "+1/4", "1.0/4", " 1/4", "1/4 "]
MALFORMED += ["1/4/2", "a/b", "١/٤", "²/4"]  # the last two: digits, but not the ASCII ones


@pytest.mark.parametrize("value", MALFORMED)
def test_a_malformed_shard_is_refused_rather_than_read_as_everything(value: str) -> None:
    with pytest.raises(ValueError, match="--shard"):
        parse_shard(value)


def test_no_shards_at_all_is_refused() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        shard_of("tests/test_ledger.py::test_balance", 0)


# --- the proof CI runs: it must fail for shards that are not a partition --------------------------------


def test_the_proof_fails_when_a_test_is_in_no_shard() -> None:
    shards = _shards(4)
    dropped = shards[2].pop()
    errors = _check_shards().partition_errors(NODE_IDS, shards)
    assert any("in no shard" in error and dropped in error for error in errors), errors
    assert any("the shards hold 3999 tests together, the whole suite 4000" in error for error in errors), errors


def test_the_proof_fails_when_a_test_is_in_two_shards() -> None:
    shards = _shards(4)
    shards[0].append(shards[3][0])
    errors = _check_shards().partition_errors(NODE_IDS, shards)
    assert any(error == f"in shard 1 and again in shard 4: {shards[3][0]}" for error in errors), errors


def test_the_proof_fails_when_one_test_replaced_another_and_the_counts_still_agree() -> None:
    shards = _shards(4)
    shards[1][0] = shards[0][0]  # one test twice, another gone: the sum of the counts is unchanged
    errors = _check_shards().partition_errors(NODE_IDS, shards)
    assert any("again in shard 2" in error for error in errors), errors
    assert any("in no shard" in error for error in errors), errors
    assert not any("together" in error for error in errors), errors


def test_the_proof_fails_when_a_shard_selects_by_a_remainder_that_no_shard_has() -> None:
    # A selection that is off by one (index 1..4 compared with a remainder 0..3) silently loses a quarter.
    shards = [[n for n in NODE_IDS if shard_of(n, 4) - 1 == index] for index in range(1, 5)]
    errors = _check_shards().partition_errors(NODE_IDS, shards)
    assert any("shard 4 is empty" in error for error in errors), errors
    assert any("in no shard" in error for error in errors), errors


def test_the_proof_fails_when_a_shard_holds_a_test_the_whole_suite_does_not() -> None:
    # A node identifier that differs from one process to the next (an address or a random value in a
    # parameter's name) shows as this.
    shards = _shards(4)
    shards[0][0] = "tests/test_ledger.py::test_balance[<object at 0x7f00>]"
    errors = _check_shards().partition_errors(NODE_IDS, shards)
    assert any("in a shard but not in the whole suite" in error for error in errors), errors


def test_the_proof_fails_when_nothing_was_collected_or_an_identifier_repeats() -> None:
    check = _check_shards()
    assert any("is empty" in error for error in check.partition_errors([], [[], []]))
    assert any("more than once" in error for error in check.partition_errors(["a::b", "a::b"], [["a::b"], ["a::b"]]))


def test_the_proof_reads_node_identifiers_and_nothing_else_from_pytests_output() -> None:
    output = (
        "shard 2/4: 2 of 5 collected tests\n"
        "tests/test_a.py::test_one\n"
        "tests/api/test_b.py::test_two[uz-a::b]\n"
        "\n"
        "=============================== warnings summary ===============================\n"
        "  tests/x.py:1: DeprecationWarning: use other::thing\n"
        "2/5 tests collected (3 deselected) in 0.10s\n"
    )
    assert _check_shards().node_ids(output) == ["tests/test_a.py::test_one", "tests/api/test_b.py::test_two[uz-a::b]"]


# --- the option, through pytest itself -----------------------------------------------------------------

_SUITE = """
import pytest

@pytest.mark.parametrize("number", range(40))
def test_numbered(number):
    assert number >= 0
"""


def _collect(directory: Path, *options: str) -> tuple[int, list[str]]:
    command = [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *options]
    result = subprocess.run(command, cwd=directory, capture_output=True, text=True, encoding="utf-8", check=False)  # noqa: S603
    return result.returncode, [line for line in (result.stdout + result.stderr).splitlines() if line]


@pytest.fixture
def small_suite(tmp_path: Path) -> Path:
    """A suite of forty tests that uses this suite's own conftest.py, and so its `--shard`."""
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "__init__.py").write_text("", encoding="utf-8")
    for name in ("conftest.py", "sharding.py"):
        (tests / name).write_text((BACKEND / "tests" / name).read_text(encoding="utf-8"), encoding="utf-8")
    (tests / "test_small.py").write_text(_SUITE, encoding="utf-8")
    (tmp_path / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n", encoding="utf-8")
    return tmp_path


def test_without_the_option_everything_is_collected_as_before(small_suite: Path) -> None:
    code, lines = _collect(small_suite)
    assert code == 0, lines
    assert len([line for line in lines if "::" in line]) == 40
    assert not any("shard" in line or "deselected" in line for line in lines), lines


def test_the_shards_pytest_collects_are_a_partition_and_agree_with_the_function(small_suite: Path) -> None:
    check = _check_shards()
    _, everything = _collect(small_suite)
    full = check.node_ids("\n".join(everything))
    shards = []
    for index in range(1, 5):
        code, lines = _collect(small_suite, f"--shard={index}/4")
        assert code == 0, lines
        shard = check.node_ids("\n".join(lines))
        assert shard == [node_id for node_id in full if shard_of(node_id, 4) == index]
        assert f"shard {index}/4: {len(shard)} of 40 collected tests" in lines
        shards.append(shard)
    assert check.partition_errors(full, shards) == []


@pytest.mark.parametrize("value", ["0/4", "5/4", "four", "1/0"])
def test_pytest_stops_on_a_malformed_shard_and_collects_nothing(small_suite: Path, value: str) -> None:
    code, lines = _collect(small_suite, f"--shard={value}")
    assert code == 4, lines  # pytest's exit code for a usage error
    assert not any("::" in line for line in lines), lines
    assert any("--shard" in line for line in lines), lines

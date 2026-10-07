"""The second factor's arithmetic: RFC test vectors, clock skew, replay, and the lock (ADR-017)."""

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from qarz.domain.totp import (
    LOCK,
    MAX_FAILURES,
    FactorState,
    Outcome,
    check_code,
    code_at,
    hotp,
    locked,
    matching_step,
    provisioning_uri,
    time_step,
)

RFC_SECRET = b"12345678901234567890"
NOW = datetime(2026, 10, 7, 9, 0, 15, tzinfo=UTC)
STEP = timedelta(seconds=30)


def _at(seconds: int) -> datetime:
    return datetime.fromtimestamp(seconds, tz=UTC)


# RFC 6238, appendix B, the SHA-1 rows: eight-digit codes at these Unix times.
@pytest.mark.parametrize(
    ("seconds", "code"),
    [
        (59, "94287082"),
        (1111111109, "07081804"),
        (1111111111, "14050471"),
        (1234567890, "89005924"),
        (2000000000, "69279037"),
        (20000000000, "65353130"),
    ],
)
def test_rfc_6238_vectors(seconds: int, code: str) -> None:
    assert hotp(RFC_SECRET, time_step(_at(seconds)), digits=8) == code
    # The six-digit code an authenticator shows is the same number without its two leading digits.
    assert code_at(RFC_SECRET, _at(seconds)) == code[2:]


# RFC 4226, appendix D: six-digit codes for counters 0 to 9.
@pytest.mark.parametrize(
    ("counter", "code"),
    list(
        enumerate(["755224", "287082", "359152", "969429", "338314", "254676", "287922", "162583", "399871", "520489"])
    ),
)
def test_rfc_4226_vectors(counter: int, code: str) -> None:
    assert hotp(RFC_SECRET, counter) == code


def test_a_step_is_thirty_seconds_and_needs_an_aware_time() -> None:
    assert time_step(_at(29)) == 0
    assert time_step(_at(30)) == 1
    assert time_step(_at(59)) == 1
    with pytest.raises(ValueError, match="aware"):
        time_step(datetime(2026, 10, 7, 9, 0, 0))


def test_a_code_keeps_its_leading_zeros() -> None:
    assert code_at(RFC_SECRET, _at(1111111109)) == "081804"
    assert len(code_at(RFC_SECRET, _at(1111111109))) == 6


@pytest.mark.parametrize(("steps_away", "accepted"), [(-2, False), (-1, True), (0, True), (1, True), (2, False)])
def test_clock_skew_of_one_step_is_accepted_and_two_refused(steps_away: int, accepted: bool) -> None:
    code = code_at(RFC_SECRET, NOW + steps_away * STEP)
    step = matching_step(RFC_SECRET, code, NOW)
    assert (step is not None) is accepted
    if accepted:
        assert step == time_step(NOW) + steps_away


@pytest.mark.parametrize("code", ["", "12345", "1234567", "12345a", "١٢٣٤٥٦", " 23456", "-12345"])
def test_a_malformed_code_matches_nothing(code: str) -> None:
    assert matching_step(RFC_SECRET, code, NOW) is None


def test_another_secret_does_not_accept_the_code() -> None:
    code = code_at(RFC_SECRET, NOW)
    assert matching_step(b"another-secret-20byte", code, NOW) is None


def test_the_provisioning_uri_carries_what_an_authenticator_needs() -> None:
    uri = provisioning_uri(RFC_SECRET, "admin-42", "Qarz Daftari")
    parts = urlsplit(uri)
    query = parse_qs(parts.query)
    assert (parts.scheme, parts.netloc) == ("otpauth", "totp")
    assert unquote(parts.path) == "/Qarz Daftari:admin-42"
    # RFC 4648 base32 of the RFC secret, without padding.
    assert query == {
        "secret": ["GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"],
        "issuer": ["Qarz Daftari"],
        "algorithm": ["SHA1"],
        "digits": ["6"],
        "period": ["30"],
    }


# --- attempts ---------------------------------------------------------------------------------------


def test_a_right_code_is_accepted_and_remembered() -> None:
    outcome, state = check_code(FactorState(failures=3), RFC_SECRET, code_at(RFC_SECRET, NOW), NOW)
    assert outcome is Outcome.ACCEPTED
    assert state == FactorState(failures=0, locked_until=None, last_step=time_step(NOW))


def test_a_code_cannot_be_used_twice() -> None:
    code = code_at(RFC_SECRET, NOW)
    _, state = check_code(FactorState(), RFC_SECRET, code, NOW)
    outcome, after = check_code(state, RFC_SECRET, code, NOW + timedelta(seconds=5))
    assert outcome is Outcome.REFUSED
    assert after == FactorState(failures=1, locked_until=None, last_step=time_step(NOW))


def test_after_a_code_no_earlier_step_is_accepted_but_the_next_one_is() -> None:
    _, state = check_code(FactorState(), RFC_SECRET, code_at(RFC_SECRET, NOW), NOW)
    # The previous step's code is still inside the skew window, and must not get in behind a newer one.
    earlier, _ = check_code(state, RFC_SECRET, code_at(RFC_SECRET, NOW - STEP), NOW)
    assert earlier is Outcome.REFUSED
    later, after = check_code(state, RFC_SECRET, code_at(RFC_SECRET, NOW + STEP), NOW + STEP)
    assert later is Outcome.ACCEPTED
    assert after.last_step == time_step(NOW) + 1


def test_a_wrong_code_is_counted() -> None:
    outcome, state = check_code(FactorState(failures=1, last_step=7), RFC_SECRET, "000000", NOW)
    assert code_at(RFC_SECRET, NOW) != "000000"
    assert outcome is Outcome.REFUSED
    assert state == FactorState(failures=2, locked_until=None, last_step=7)


def test_the_fifth_wrong_code_locks_and_the_fourth_does_not() -> None:
    assert MAX_FAILURES == 5
    assert timedelta(minutes=15) == LOCK
    fourth, state = check_code(FactorState(failures=3), RFC_SECRET, "000000", NOW)
    assert fourth is Outcome.REFUSED
    assert state == FactorState(failures=4)
    fifth, state = check_code(state, RFC_SECRET, "000000", NOW)
    assert fifth is Outcome.LOCKED
    assert state == FactorState(failures=0, locked_until=NOW + LOCK)


def test_while_locked_even_the_right_code_is_refused_and_nothing_changes() -> None:
    state = FactorState(failures=0, locked_until=NOW + LOCK, last_step=3)
    just_before_the_end = NOW + LOCK - timedelta(microseconds=1)
    outcome, after = check_code(state, RFC_SECRET, code_at(RFC_SECRET, just_before_the_end), just_before_the_end)
    assert outcome is Outcome.LOCKED
    assert after == state


def test_the_lock_ends_exactly_when_it_says() -> None:
    state = FactorState(failures=0, locked_until=NOW + LOCK)
    assert locked(state, NOW + LOCK - timedelta(microseconds=1))
    assert not locked(state, NOW + LOCK)
    assert not locked(FactorState(), NOW)
    outcome, after = check_code(state, RFC_SECRET, code_at(RFC_SECRET, NOW + LOCK), NOW + LOCK)
    assert outcome is Outcome.ACCEPTED
    assert after.locked_until is None


def test_a_wrong_code_after_the_lock_starts_a_new_count() -> None:
    state = FactorState(failures=0, locked_until=NOW + LOCK)
    outcome, after = check_code(state, RFC_SECRET, "000000", NOW + LOCK)
    assert outcome is Outcome.REFUSED
    assert after == FactorState(failures=1, locked_until=None)


def test_a_secret_that_cannot_be_read_accepts_no_code() -> None:
    outcome, state = check_code(FactorState(), None, code_at(RFC_SECRET, NOW), NOW)
    assert outcome is Outcome.REFUSED
    assert state.failures == 1

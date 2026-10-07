"""The administrator's second factor: time-based one-time codes and what happens to wrong ones (ADR-017).

Codes follow RFC 6238 over RFC 4226 with the parameters every authenticator app defaults to: HMAC-SHA-1,
30-second steps, six digits. The clock of the phone and of the server may differ by one step either way.

A code is accepted once: the step it belongs to is remembered and no code of that step or an earlier one
is accepted again. Five wrong codes in a row lock the factor for fifteen minutes.
"""

import base64
import hashlib
import hmac
import struct
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from urllib.parse import quote

STEP_SECONDS = 30
DIGITS = 6
SKEW_STEPS = 1
SECRET_BYTES = 20  # the SHA-1 block the RFC recommends; 160 bits
MAX_FAILURES = 5
LOCK = timedelta(minutes=15)


def hotp(secret: bytes, counter: int, digits: int = DIGITS) -> str:
    """RFC 4226: the code for one counter value."""
    digest = hmac.new(secret, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(number % 10**digits).zfill(digits)


def time_step(at: datetime) -> int:
    """The number of whole steps since the Unix epoch. Naive datetimes are a programming error."""
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("an aware datetime is required")
    return int(at.timestamp()) // STEP_SECONDS


def code_at(secret: bytes, at: datetime) -> str:
    return hotp(secret, time_step(at))


def matching_step(secret: bytes, code: str, at: datetime) -> int | None:
    """The step, at most one away from now, whose code this is; None when it is nobody's."""
    if len(code) != DIGITS or not code.isascii() or not code.isdigit():
        return None
    now = time_step(at)
    found: int | None = None
    # Every candidate is compared, in constant time, so the answer's timing says nothing about which
    # step matched or how many digits were right.
    for step in range(now - SKEW_STEPS, now + SKEW_STEPS + 1):
        if hmac.compare_digest(hotp(secret, step), code) and found is None:
            found = step
    return found


def provisioning_uri(secret: bytes, account: str, issuer: str) -> str:
    """What an authenticator app scans or opens to add the secret (the "Key URI format")."""
    encoded = base64.b32encode(secret).decode("ascii").rstrip("=")
    label = quote(f"{issuer}:{account}", safe="")
    return (
        f"otpauth://totp/{label}?secret={encoded}&issuer={quote(issuer, safe='')}"
        f"&algorithm=SHA1&digits={DIGITS}&period={STEP_SECONDS}"
    )


@dataclass(frozen=True)
class FactorState:
    """What is remembered about one administrator's second factor between attempts."""

    failures: int = 0
    locked_until: datetime | None = None
    last_step: int | None = None


class Outcome(Enum):
    ACCEPTED = "accepted"
    REFUSED = "refused"
    LOCKED = "locked"


def locked(state: FactorState, now: datetime) -> bool:
    return state.locked_until is not None and now < state.locked_until


def check_code(state: FactorState, secret: bytes | None, code: str, now: datetime) -> tuple[Outcome, FactorState]:
    """Judge one attempt and say what to remember afterwards.

    `secret` is None when the stored secret cannot be read; then no code can be right. While the factor is
    locked nothing is looked at, so a right code neither opens it nor resets the lock. The attempt that
    reaches the limit is itself answered as locked.
    """
    if locked(state, now):
        return Outcome.LOCKED, state
    step = None if secret is None else matching_step(secret, code, now)
    if step is not None and (state.last_step is None or step > state.last_step):
        return Outcome.ACCEPTED, FactorState(0, None, step)
    failures = state.failures + 1
    if failures >= MAX_FAILURES:
        return Outcome.LOCKED, FactorState(0, now + LOCK, state.last_step)
    return Outcome.REFUSED, FactorState(failures, None, state.last_step)

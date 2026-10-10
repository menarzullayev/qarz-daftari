"""An administrator's password: what is accepted as one, how it is kept, and when a login is locked.

The password is one more way to the session the Telegram sign-in gives; it decides nothing about who is
an administrator. It is set from the server's command line only, kept as scrypt's hash with a salt of its
own, and five wrong attempts lock the login for a quarter of an hour.
"""

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

MIN_LENGTH = 14
MAX_LENGTH = 200
MAX_FAILURES = 5
LOCK = timedelta(minutes=15)
SALT_BYTES = 16
HASH_BYTES = 64
# scrypt: 32 MiB and about a tenth of a second on the server. Changing these makes every stored hash
# unreadable, so they change only together with a way to tell old hashes from new.
_N, _R, _P = 2**15, 8, 1

LOGIN = re.compile(r"[a-z0-9][a-z0-9._-]{2,39}")


class WeakPassword(ValueError):
    pass


def check_login(login: str) -> str:
    if not LOGIN.fullmatch(login):
        raise ValueError("a login is 3 to 40 of a-z, 0-9, dot, dash and underscore, and starts with a letter or digit")
    return login


def check_password(password: str) -> str:
    if not MIN_LENGTH <= len(password) <= MAX_LENGTH:
        raise WeakPassword(f"a password is {MIN_LENGTH} to {MAX_LENGTH} characters")
    if len(set(password)) < 6:
        raise WeakPassword("a password uses at least six different characters")
    return password


def new_salt() -> bytes:
    return secrets.token_bytes(SALT_BYTES)


def digest(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, maxmem=2**26, dklen=HASH_BYTES)


def matches(password: str, salt: bytes, stored: bytes) -> bool:
    if not password or len(password) > MAX_LENGTH:
        return False
    return secrets.compare_digest(digest(password, salt), stored)


@dataclass(frozen=True)
class Attempts:
    failures: int
    locked_until: datetime | None


def locked(state: Attempts, now: datetime) -> bool:
    return state.locked_until is not None and now < state.locked_until


def after(state: Attempts, accepted: bool, now: datetime) -> Attempts:
    """The count after one attempt that was looked at (a locked login's attempts are not)."""
    if accepted:
        return Attempts(0, None)
    failures = state.failures + 1
    if failures >= MAX_FAILURES:
        return Attempts(0, now + LOCK)
    return Attempts(failures, None)

"""A customer's secret read-only link: what the token is, how long a link lives, and what it may say.

The link is a bearer secret: whoever holds it sees the one account behind it, without signing in. So the
token is long enough that it cannot be guessed, only its hash is ever stored, and the page behind it
shows less than a member of staff sees: the first word of the customer's name, never a note, never who
recorded an entry.
"""

import hashlib
import hmac
import secrets
from datetime import timedelta

# The platform switch everything here is behind. Off unless an administrator turns it on.
SWITCH = "customer_links_on"

# How long a link works from the moment it is made. This is the one place the lifetime is set; a link
# keeps the expiry it was made with, so changing this changes links made afterwards only.
SHARE_LIFETIME = timedelta(days=90)

# 32 random bytes: 256 bits, written as 43 characters of the URL-safe alphabet without padding.
TOKEN_BYTES = 32
TOKEN_LENGTH = 43
_TOKEN_ALPHABET = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def token_hash(token: str) -> bytes:
    """What is stored and looked up in the token's place."""
    return hashlib.sha256(token.encode("ascii")).digest()


def is_token(value: object) -> bool:
    """Whether the text has the shape of a token at all. Anything else is not looked up."""
    return isinstance(value, str) and len(value) == TOKEN_LENGTH and _TOKEN_ALPHABET.issuperset(value)


def same_hash(stored: bytes, presented: bytes) -> bool:
    """Compared in constant time, so the time an answer takes says nothing about a stored hash."""
    return hmac.compare_digest(stored, presented)


def first_name(display_name: str) -> str:
    """The first word of the name staff typed: enough for the customer to know the page is theirs.

    Staff write a name as they please ("Ali Valiyev", "Ali aka, 5-uy"), and the rest of it may say what
    the customer was never meant to read or what a stranger holding the link should not learn.
    """
    words = display_name.split()
    return words[0].rstrip(",.;:") if words else ""

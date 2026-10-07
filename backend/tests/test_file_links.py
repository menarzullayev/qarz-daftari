"""Signed links to stored files: the pure part (ADR-020; specification: "signed links valid 5 minutes")."""

import base64
import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from qarz.domain.file_links import KEY_LABEL, LINK_LIFETIME, derive_key, expiry, sign, verify

SECRET = "test-only-server-secret-0123456789"
KEY = derive_key(SECRET)
SHOP, FILE = UUID("11111111-2222-4333-8444-555555555555"), UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")
NOW = datetime(2026, 10, 9, 12, 0, 0, 700_000, tzinfo=UTC)


def link(now: datetime = NOW, key: bytes = KEY, shop: UUID = SHOP, file: UUID = FILE) -> str:
    return sign(key, shop, file, expiry(now))


def test_a_link_lives_five_minutes() -> None:
    assert timedelta(minutes=5) == LINK_LIFETIME
    assert expiry(NOW) == datetime(2026, 10, 9, 12, 5, 0, tzinfo=UTC), "at whole seconds"


def test_a_link_names_its_shop_and_file_and_works_until_its_last_second() -> None:
    token = link()
    assert verify(KEY, token, NOW) == (SHOP, FILE)
    assert verify(KEY, token, NOW + timedelta(minutes=4, seconds=59)) == (SHOP, FILE)
    assert verify(KEY, token, NOW + timedelta(minutes=5)) == (SHOP, FILE)
    assert verify(KEY, token, datetime(2026, 10, 9, 12, 5, 0, 999_999, tzinfo=UTC)) == (SHOP, FILE)
    assert verify(KEY, token, datetime(2026, 10, 9, 12, 5, 1, tzinfo=UTC)) is None
    assert verify(KEY, token, NOW + timedelta(days=1)) is None
    # It has one spelling, of fixed length, safe in a URL path.
    assert len(token) == 54 + 1 + 43 and token.count(".") == 1
    assert all(ch.isalnum() or ch in "-_." for ch in token)


def test_the_key_is_derived_from_the_secret_for_this_purpose_only() -> None:
    assert hmac.new(SECRET.encode(), KEY_LABEL, hashlib.sha256).digest() == KEY
    assert SECRET.encode() != KEY and len(KEY) == 32
    assert derive_key(SECRET + "x") != KEY
    assert derive_key("x" * 16)
    for short in ("", "x" * 15):
        with pytest.raises(ValueError, match="too short"):
            derive_key(short)


def test_a_link_signed_with_another_key_is_not_believed() -> None:
    assert verify(KEY, link(key=derive_key("another-server-secret-0123456789")), NOW) is None
    # Not even one signed with the server secret itself rather than the key derived from it.
    assert verify(KEY, link(key=SECRET.encode()), NOW) is None


def _flip(token: str, index: int) -> str:
    replacement = "A" if token[index] != "A" else "B"
    return token[:index] + replacement + token[index + 1 :]


@pytest.mark.parametrize("index", range(54 + 1 + 43))
def test_changing_any_single_character_breaks_the_link(index: int) -> None:
    token = link()
    assert verify(KEY, _flip(token, index), NOW) is None


def test_parts_of_two_links_cannot_be_combined() -> None:
    mine, other = link(), link(file=UUID("99999999-bbbb-4ccc-8ddd-eeeeeeeeeeee"))
    later = link(NOW + timedelta(hours=1))
    elsewhere = link(shop=UUID("99999999-2222-4333-8444-555555555555"))
    for donor in (other, later, elsewhere):
        assert verify(KEY, donor, NOW) is not None, "each is a good link by itself"
        assert verify(KEY, donor[:54] + mine[54:], NOW) is None
        assert verify(KEY, mine[:54] + donor[54:], NOW) is None


def test_an_expired_link_cannot_be_given_a_later_expiry() -> None:
    token = link()
    payload = base64.urlsafe_b64decode(token[:54] + "==")
    extended = payload[:32] + (int.from_bytes(payload[32:], "big") + 3600).to_bytes(8, "big")
    forged = base64.urlsafe_b64encode(extended).rstrip(b"=").decode() + token[54:]
    assert verify(KEY, forged, NOW) is None


@pytest.mark.parametrize(
    "mangle",
    [
        lambda t: "",
        lambda t: ".",
        lambda t: t[:-1],
        lambda t: t + "A",
        lambda t: t + "=",
        lambda t: t.replace(".", "", 1),
        lambda t: t.replace(".", "..", 1),
        lambda t: t[:54] + ".",
        lambda t: t[55:] + "." + t[:54],
        lambda t: " " + t[1:],
        lambda t: t[:10] + "+" + t[11:],
        lambda t: t[:10] + "/" + t[11:],
        lambda t: t[:10] + "é" + t[11:],
        lambda t: t.upper(),
        lambda t: "A" * len(t),
        lambda t: "A" * 54 + "." + "A" * 43,
        lambda t: "x" * 5000,
    ],
    ids=range(17),
)
def test_anything_that_is_not_a_link_is_refused_without_an_error(mangle: object) -> None:
    assert verify(KEY, mangle(link()), NOW) is None  # type: ignore[operator]


def test_the_signature_is_compared_in_constant_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """An ordinary comparison would answer sooner the earlier the first wrong byte is, which tells a
    patient caller how much of a forged signature is already right."""
    compared: list[tuple[bytes, bytes]] = []
    real = hmac.compare_digest

    def spy(left: bytes, right: bytes) -> bool:
        compared.append((left, right))
        return real(left, right)

    monkeypatch.setattr("qarz.domain.file_links.hmac.compare_digest", spy)
    token = link()
    assert verify(KEY, token, NOW) == (SHOP, FILE)
    assert verify(KEY, _flip(token, 70), NOW) is None
    assert len(compared) == 2 and all(len(left) == len(right) == 32 for left, right in compared)

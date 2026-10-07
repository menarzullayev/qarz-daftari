"""The checks of what Payme and Click send, without a database (REQ-056, ADR-019)."""

import asyncio
import base64
import hashlib
from collections.abc import AsyncIterator

import pytest

from qarz.application.online_payment import PaymentKeys
from qarz.domain import online_payment as rules

KEY = "a-key-for-tests-only"


def md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()  # noqa: S324  (Click's protocol)


def basic(text: str) -> str:
    return "Basic " + base64.b64encode(text.encode()).decode()


def test_payme_is_authorized_by_basic_auth_with_its_login_and_our_key() -> None:
    assert rules.payme_authorized(basic(f"Paycom:{KEY}"), KEY)
    # A key that itself contains a colon is still the whole rest of the pair.
    assert rules.payme_authorized(basic("Paycom:a:b:c"), "a:b:c")


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "Basic",
        "Basic ",
        f"Paycom:{KEY}",
        basic(f"Paycom:{KEY}").replace("Basic", "basic"),
        basic(f"Paycom:{KEY}").replace("Basic", "Bearer"),
        basic(f"Paycom:{KEY} "),
        basic(f"Paycom:{KEY[:-1]}"),
        basic(f"Paycom:{KEY.upper()}"),
        basic(f"paycom:{KEY}"),
        basic(f" Paycom:{KEY}"),
        basic(f":{KEY}"),
        basic(KEY),
        basic("Paycom"),
        basic("Paycom:"),
        "Basic %%%",
        "Basic " + base64.b64encode(b"Paycom:\xff\xfe").decode(),
    ],
)
def test_payme_is_refused_with_anything_else(header: str | None) -> None:
    assert not rules.payme_authorized(header, KEY)


def test_no_key_authorizes_nobody() -> None:
    assert not rules.payme_authorized(basic("Paycom:"), "")
    assert not rules.click_signed(md5(""), md5(""), "")


def test_payme_counts_in_tiyin_and_times_out_after_twelve_hours() -> None:
    assert rules.tiyin(100_000) == 10_000_000
    assert rules.PAYME_TIMEOUT_MS == 43_200_000
    assert not rules.payme_timed_out(1_000, 1_000 + 43_200_000)
    assert rules.payme_timed_out(1_000, 1_000 + 43_200_001)
    assert not rules.payme_timed_out(5_000, 1_000), "a clock a little behind Payme's is not a timeout"


def test_the_click_signature_is_the_md5_click_documents() -> None:
    fields = {
        "click_trans_id": "777",
        "service_id": "70001",
        "secret": KEY,
        "merchant_trans_id": "order-1",
        "amount": "100000",
        "sign_time": "2050-03-10 12:00:00",
    }
    prepare = rules.click_sign(**fields, merchant_prepare_id="55", action="0")
    assert prepare == md5(f"77770001{KEY}order-11000000" + "2050-03-10 12:00:00")
    complete = rules.click_sign(**fields, merchant_prepare_id="55", action="1")
    assert complete == md5(f"77770001{KEY}order-1551000001" + "2050-03-10 12:00:00")
    assert rules.click_sign(**fields, merchant_prepare_id=None, action="0") == prepare, "preparing signs no prepare id"
    assert rules.click_sign(**fields, merchant_prepare_id="56", action="1") != complete

    assert rules.click_signed(prepare, prepare, KEY)
    assert rules.click_signed(f" {prepare.upper()}\n", prepare, KEY), "case and surrounding space do not matter"
    assert not rules.click_signed(complete, prepare, KEY)
    assert not rules.click_signed(prepare[:-1], prepare, KEY)
    assert not rules.click_signed("", prepare, KEY)


@pytest.mark.parametrize("sent", ["100000", "100000.0", "100000.00", " 100000 ", "1E+5", "100000.000000"])
def test_click_amounts_equal_to_the_order_match(sent: str) -> None:
    assert rules.click_amount_matches(sent, 100_000)


@pytest.mark.parametrize(
    "sent",
    [
        "",
        "99999",
        "100001",
        "100000.01",
        "99999.99",
        "-100000",
        "abc",
        "NaN",
        "sNaN",
        "Infinity",
        "-Infinity",
        "1 00000",
    ],
)
def test_other_click_amounts_do_not(sent: str) -> None:
    assert not rules.click_amount_matches(sent, 100_000)


def test_an_order_costs_the_price_for_each_month() -> None:
    assert rules.order_amount(100_000, 1) == 100_000
    assert rules.order_amount(150_000, 12) == 1_800_000


def test_the_pay_links_name_the_order_and_its_amount() -> None:
    payme = rules.payme_checkout_url("merchant", "order-1", 100_000)
    assert payme.startswith("https://checkout.paycom.uz/")
    assert base64.b64decode(payme.rsplit("/", 1)[1]).decode() == "m=merchant;ac.order_id=order-1;a=10000000"
    assert rules.click_pay_url("7", "5", "order-1", 100_000) == (
        "https://my.click.uz/services/pay?service_id=7&merchant_id=5&amount=100000&transaction_param=order-1"
    )


def test_a_provider_is_configured_only_with_all_of_its_values() -> None:
    assert not PaymentKeys().payme and not PaymentKeys().click
    assert PaymentKeys(payme_merchant_id="m", payme_key="k").payme
    assert not PaymentKeys(payme_merchant_id="m").payme
    assert not PaymentKeys(payme_key="k").payme
    full = {"click_service_id": "s", "click_merchant_id": "m", "click_key": "k"}
    assert PaymentKeys(**full).click
    for name in full:
        assert not PaymentKeys(**{**full, name: ""}).click, name
    assert not PaymentKeys(**full).payme and not PaymentKeys(payme_merchant_id="m", payme_key="k").click


def test_every_protocol_code_has_its_words() -> None:
    from qarz.application.online_payment import _PAYME_MESSAGES

    payme = {
        value
        for name, value in vars(rules).items()
        if name.startswith("PAYME_") and isinstance(value, int) and value < -30_000
    }
    assert payme == set(_PAYME_MESSAGES)
    assert all(len(texts) == 3 and all(texts) for texts in _PAYME_MESSAGES.values())
    click = {value for name, value in vars(rules).items() if name.startswith("CLICK_") and isinstance(value, int)}
    assert click == set(rules.CLICK_NOTES)
    assert set(rules.PAYME_STATES) == {rules.PENDING, rules.PAID, rules.CANCELLED}


class Pieces:
    """A request whose body arrives in the given pieces; counts how many were asked for."""

    def __init__(self, pieces: list[bytes]) -> None:
        self._pieces = pieces
        self.read = 0

    async def stream(self) -> AsyncIterator[bytes]:
        for piece in self._pieces:
            self.read += 1
            yield piece


def test_a_provider_body_is_read_in_pieces_and_given_up_on_once_too_large() -> None:
    from qarz.interface.online_payment_api import MAX_BODY, _body

    def read(pieces: list[bytes]) -> tuple[bytes | None, int]:
        request = Pieces(pieces)
        return asyncio.run(_body(request)), request.read  # type: ignore[arg-type]

    assert MAX_BODY == 16 * 1024
    assert read([]) == (b"", 0)
    assert read([b"ab", b"", b"cd"]) == (b"abcd", 3)
    half = b"x" * (MAX_BODY // 2)
    assert read([half, half]) == (half + half, 2), "exactly the limit fits"
    assert read([half, half, b"y"]) == (None, 3), "small pieces add up"
    # Nothing after the piece that crosses the limit is read at all.
    assert read([half, half + b"y", half, half]) == (None, 2)

"""Rules of paying the subscription through Payme and Click (REQ-056, ADR-019).

Nothing here talks to a provider: these are the checks of what a provider sends us and the numbers of the
two protocols. The adapters are built and switched off; see `qarz.application.online_payment`.
"""

import base64
import binascii
import hashlib
import hmac
from decimal import Decimal, InvalidOperation

CREATED, PENDING, PAID, CANCELLED = "created", "pending", "paid", "cancelled"
PAYME, CLICK = "payme", "click"

# --- Payme (Merchant API, JSON-RPC) -----------------------------------------------------------------

PAYME_LOGIN = "Paycom"
PAYME_TIMEOUT_MS = 12 * 60 * 60 * 1000  # a transaction not performed in 12 hours is cancelled
PAYME_REASON_TIMEOUT = 4
PAYME_STATES = {PENDING: 1, PAID: 2, CANCELLED: -1}

PAYME_BAD_REQUEST = -32600
PAYME_NO_METHOD = -32601
PAYME_BAD_JSON = -32700
PAYME_NO_ACCESS = -32504
PAYME_WRONG_AMOUNT = -31001
PAYME_NO_TRANSACTION = -31003
PAYME_CANNOT_CANCEL = -31007
PAYME_CANNOT_PERFORM = -31008
PAYME_NO_ORDER = -31050
PAYME_ORDER_BUSY = -31051
PAYME_SHOP_BLOCKED = -31052


def payme_authorized(header: str | None, key: str) -> bool:
    """Whether the Authorization header is HTTP Basic with Payme's login and our key."""
    if not key or header is None:
        return False
    scheme, _, encoded = header.partition(" ")
    if scheme != "Basic":
        return False
    try:
        login, separator, given = base64.b64decode(encoded.strip(), validate=True).decode("utf-8").partition(":")
    except (binascii.Error, UnicodeDecodeError):
        return False
    # Constant-time, so that the answer's timing says nothing about the key.
    return bool(separator) and login == PAYME_LOGIN and hmac.compare_digest(given.encode(), key.encode())


def tiyin(amount_uzs: int) -> int:
    """Payme counts in tiyin: a hundredth of a so'm."""
    return amount_uzs * 100


def payme_timed_out(started_ms: int, now_ms: int) -> bool:
    return now_ms - started_ms > PAYME_TIMEOUT_MS


def payme_checkout_url(merchant_id: str, order_id: str, amount_uzs: int) -> str:
    query = f"m={merchant_id};ac.order_id={order_id};a={tiyin(amount_uzs)}"
    return "https://checkout.paycom.uz/" + base64.b64encode(query.encode()).decode()


# --- Click (Shop API) -------------------------------------------------------------------------------

CLICK_PREPARE, CLICK_COMPLETE = "0", "1"

CLICK_OK = 0
CLICK_BAD_SIGN = -1
CLICK_WRONG_AMOUNT = -2
CLICK_NO_ACTION = -3
CLICK_ALREADY_PAID = -4
CLICK_NO_ORDER = -5
CLICK_NO_TRANSACTION = -6
CLICK_BAD_REQUEST = -8
CLICK_CANCELLED = -9

CLICK_NOTES = {
    CLICK_OK: "Success",
    CLICK_BAD_SIGN: "SIGN CHECK FAILED!",
    CLICK_WRONG_AMOUNT: "Incorrect parameter amount",
    CLICK_NO_ACTION: "Action not found",
    CLICK_ALREADY_PAID: "Already paid",
    CLICK_NO_ORDER: "Order does not exist",
    CLICK_NO_TRANSACTION: "Transaction does not exist",
    CLICK_BAD_REQUEST: "Error in request from click",
    CLICK_CANCELLED: "Transaction cancelled",
}


def click_sign(
    *,
    click_trans_id: str,
    service_id: str,
    secret: str,
    merchant_trans_id: str,
    merchant_prepare_id: str | None,
    amount: str,
    action: str,
    sign_time: str,
) -> str:
    """The signature Click puts on a request. Completing also signs the identifier we gave when preparing."""
    parts = [click_trans_id, service_id, secret, merchant_trans_id]
    if action == CLICK_COMPLETE:
        parts.append(merchant_prepare_id or "")
    parts += [amount, action, sign_time]
    return hashlib.md5("".join(parts).encode("utf-8")).hexdigest()  # noqa: S324  (Click's protocol, not our choice)


def click_signed(given: str, expected: str, secret: str) -> bool:
    return bool(secret) and hmac.compare_digest(given.strip().lower().encode(), expected.encode())


def click_amount_matches(sent: str, amount_uzs: int) -> bool:
    """Click sends the amount as a decimal number of so'm, such as `100000` or `100000.00`."""
    try:
        value = Decimal(sent.strip())
    except InvalidOperation:
        return False
    return value.is_finite() and value == Decimal(amount_uzs)


def click_pay_url(service_id: str, merchant_id: str, order_id: str, amount_uzs: int) -> str:
    return (
        "https://my.click.uz/services/pay"
        f"?service_id={service_id}&merchant_id={merchant_id}&amount={amount_uzs}&transaction_param={order_id}"
    )


def order_amount(price_uzs: int, months: int) -> int:
    return price_uzs * months

"""Pure rules of subscription receipts (DOM-019; BR-27, BR-30; ADR-019) and of the form they arrive in."""

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from qarz.application.errors import ValidationFailed
from qarz.domain.subscription import ACTIVE, LIMITED, MAX_MONTHS, SUSPENDED, TRIAL, after_payment
from qarz.domain.subscription_receipts import (
    GROUP_DECIDER_STATUSES,
    MAX_AMOUNT,
    MAX_REASON,
    MAX_WAITING,
    MIN_AMOUNT,
    MIN_REASON,
    OFFERED_MONTHS,
    RETENTION,
    ReceiptRefusal,
    clean_reason,
    decides_in_review_group,
    delete_file_after,
    expected_amount,
    may_submit,
    months_to_record,
    valid_amount,
    valid_months,
)
from qarz.interface.subscription_receipts_api import SUBSCRIPTION_RECEIPT_UPLOAD, parse_receipt_form

from .receipt_samples import JPEG

D = date
TODAY = D(2026, 10, 7)


@pytest.mark.parametrize(
    ("amount", "valid"),
    [
        (MIN_AMOUNT, True),
        (MIN_AMOUNT - 1, False),
        (MAX_AMOUNT, True),
        (MAX_AMOUNT + 1, False),
        (0, False),
        (-100_000, False),
        (100_000.0, False),
        ("100000", False),
        (True, False),
        (None, False),
    ],
)
def test_a_stated_amount_is_a_whole_number_of_sums_within_bounds(amount: Any, valid: bool) -> None:
    assert valid_amount(amount) is valid


def test_the_bounds_are_the_smallest_price_and_the_largest_price_for_the_longest_period() -> None:
    assert (MIN_AMOUNT, MAX_AMOUNT) == (1_000, 360_000_000)


@pytest.mark.parametrize(
    ("months", "valid"),
    [
        (1, True),
        (0, False),
        (MAX_MONTHS, True),
        (MAX_MONTHS + 1, False),
        (-1, False),
        (1.0, False),
        ("1", False),
        (True, False),
    ],
)
def test_months_are_a_whole_number_up_to_the_longest_period(months: Any, valid: bool) -> None:
    assert valid_months(months) is valid


@pytest.mark.parametrize(
    ("amount", "months", "waiting", "refusal"),
    [
        (100_000, 1, 0, None),
        (100_000, 1, MAX_WAITING - 1, None),
        (100_000, 1, MAX_WAITING, ReceiptRefusal.TOO_MANY_WAITING),
        (999, 1, 0, ReceiptRefusal.AMOUNT_OUT_OF_RANGE),
        (100_000, 0, 0, ReceiptRefusal.MONTHS_OUT_OF_RANGE),
        # What is wrong with the request itself is said before what the shop must wait for.
        (999, 0, MAX_WAITING, ReceiptRefusal.AMOUNT_OUT_OF_RANGE),
        (100_000, 37, MAX_WAITING, ReceiptRefusal.MONTHS_OUT_OF_RANGE),
    ],
)
def test_whether_a_receipt_may_be_sent(amount: int, months: int, waiting: int, refusal: ReceiptRefusal | None) -> None:
    assert may_submit(amount=amount, months=months, waiting=waiting) is refusal
    assert MAX_WAITING == 3


@pytest.mark.parametrize("status", ["creator", "administrator"])
def test_the_creator_and_the_administrators_of_the_review_group_decide_there(status: str) -> None:
    """DEC-064: the Bot API's own words for the people who administer a chat."""
    assert decides_in_review_group(status) is True
    assert status in GROUP_DECIDER_STATUSES and len(GROUP_DECIDER_STATUSES) == 2


@pytest.mark.parametrize(
    "status",
    [
        "member",
        "restricted",
        "left",
        "kicked",
        "owner",  # not a word Telegram uses
        "Administrator",
        "CREATOR",
        " creator",
        "administrator ",
        "",
        None,  # Telegram gave no answer
        True,
        1,
        b"creator",
        ["creator"],
        {"status": "creator"},
    ],
)
def test_nobody_else_decides_in_the_review_group_and_no_answer_is_a_refusal(status: Any) -> None:
    assert decides_in_review_group(status) is False


@pytest.mark.parametrize(
    ("stated", "corrected", "recorded"),
    [
        (3, None, 3),  # confirmed as stated
        (3, 1, 1),  # corrected down
        (1, 12, 12),  # corrected up
        (None, 2, 2),  # an old receipt that stated nothing
        (None, None, None),
        (3, 0, None),  # a correction is never silently replaced by what was stated
        (3, MAX_MONTHS + 1, None),
        (MAX_MONTHS, None, MAX_MONTHS),
    ],
)
def test_the_administrator_records_the_months_that_count(
    stated: int | None, corrected: int | None, recorded: int | None
) -> None:
    assert months_to_record(stated, corrected) == recorded


@pytest.mark.parametrize(
    ("raw", "kept"),
    [
        ("Pul tushmagan", "Pul tushmagan"),
        ("  Summa   mos\nemas ", "Summa mos emas"),
        ("x" * MIN_REASON, "x" * MIN_REASON),
        ("x" * (MIN_REASON - 1), None),
        ("x" * MAX_REASON, "x" * MAX_REASON),
        ("x" * (MAX_REASON + 1), None),
        ("", None),
        ("   ", None),
        (None, None),
        (5, None),
    ],
)
def test_a_rejection_reason_is_tidied_and_bounded(raw: Any, kept: str | None) -> None:
    assert clean_reason(raw) == kept


def test_a_receipt_file_is_kept_three_years() -> None:
    sent = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)
    assert timedelta(days=1095) == RETENTION
    assert delete_file_after(sent) == datetime(2029, 10, 6, 9, 0, tzinfo=UTC)


def test_the_bot_offers_periods_at_the_price_times_the_months() -> None:
    assert OFFERED_MONTHS == (1, 3, 6, 12)
    assert [expected_amount(100_000, months) for months in OFFERED_MONTHS] == [100_000, 300_000, 600_000, 1_200_000]


# --- what a payment does to a subscription (BR-27, BR-30): one rule for a receipt and an online payment ---


@pytest.mark.parametrize(
    ("state", "paid_through", "months", "expected"),
    [
        # Nothing paid yet, or a period that ended: from today, which is the first paid day.
        (TRIAL, None, 1, (ACTIVE, D(2026, 11, 6), None)),
        (LIMITED, D(2026, 10, 6), 1, (ACTIVE, D(2026, 11, 6), None)),
        # Still paid, today included: from the paid-through date.
        (ACTIVE, D(2026, 10, 7), 1, (ACTIVE, D(2026, 11, 7), None)),
        (ACTIVE, D(2026, 10, 20), 3, (ACTIVE, D(2027, 1, 20), None)),
        (ACTIVE, D(2027, 1, 31), 1, (ACTIVE, D(2027, 2, 28), None)),
        # A suspended shop is paid for and stays suspended; it comes back active (BR-30).
        (SUSPENDED, None, 1, (SUSPENDED, D(2026, 11, 6), ACTIVE)),
        (SUSPENDED, D(2026, 12, 1), 2, (SUSPENDED, D(2027, 2, 1), ACTIVE)),
    ],
)
def test_a_payment_extends_the_period_and_makes_the_shop_active_unless_it_is_suspended(
    state: str, paid_through: date | None, months: int, expected: tuple[str, date, str | None]
) -> None:
    assert after_payment(state, None, paid_through, TODAY, months) == expected


@pytest.mark.parametrize("months", [0, -1, MAX_MONTHS + 1])
def test_a_payment_for_no_months_or_too_many_is_an_error(months: int) -> None:
    with pytest.raises(ValueError):
        after_payment(ACTIVE, None, None, TODAY, months)


@pytest.mark.parametrize(
    ("state", "trial_ends", "paid_through", "months", "until"),
    [
        # The trial still runs: the months follow its last day, so paying early costs none of its days.
        (TRIAL, D(2026, 11, 5), None, 1, D(2026, 12, 5)),
        (TRIAL, D(2026, 10, 7), None, 1, D(2026, 11, 7)),  # its last day is today
        (TRIAL, D(2026, 10, 31), None, 4, D(2027, 2, 28)),
        # A paid-through date later than the trial's last day is the one that counts.
        (TRIAL, D(2026, 10, 20), D(2026, 12, 1), 1, D(2027, 1, 1)),
        # The trial ended yesterday: nothing of it is left, the months start today.
        (TRIAL, D(2026, 10, 6), None, 1, D(2026, 11, 6)),
        # Only a shop on trial is owed the trial's days: an old trial date left on an active, limited or
        # suspended shop adds nothing.
        (ACTIVE, D(2026, 12, 31), D(2026, 10, 20), 1, D(2026, 11, 20)),
        (LIMITED, D(2026, 12, 31), None, 1, D(2026, 11, 6)),
        (SUSPENDED, D(2026, 12, 31), None, 1, D(2026, 11, 6)),
    ],
)
def test_months_paid_during_the_trial_are_counted_from_its_last_day(
    state: str, trial_ends: date, paid_through: date | None, months: int, until: date
) -> None:
    assert after_payment(state, trial_ends, paid_through, TODAY, months)[1] == until


def test_paying_on_the_first_day_of_a_trial_is_not_the_same_as_not_paying() -> None:
    """The defect this rule replaces: thirty days of trial and one month paid ended on the same day."""
    trial_ends = TODAY + timedelta(days=30)
    assert after_payment(TRIAL, trial_ends, None, TODAY, 1)[1] > trial_ends


# --- the form ---------------------------------------------------------------------------------------------

BOUNDARY = "qd-test-boundary"
FORM = f"multipart/form-data; boundary={BOUNDARY}"


def form(**parts: bytes) -> bytes:
    body = b""
    for name, content in parts.items():
        field = name.rstrip("_")
        body += f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="{field}"\r\n\r\n'.encode() + content + b"\r\n"
    return body + f"--{BOUNDARY}--\r\n".encode()


def test_a_form_carries_the_amount_the_months_and_the_file() -> None:
    assert parse_receipt_form(FORM, form(amount=b"300000", months=b" 3 ", receipt=JPEG)) == (300_000, 3, JPEG, None)


def test_a_form_may_name_the_card_that_was_paid_to() -> None:
    named = form(amount=b"300000", months=b"3", receipt=JPEG, card=b" 8600 1234 5678 9012 ")
    assert parse_receipt_form(FORM, named) == (300_000, 3, JPEG, "8600 1234 5678 9012")
    # Present and empty is not the same as absent: the application refuses it as a card it does not know.
    assert parse_receipt_form(FORM, form(amount=b"1", months=b"1", receipt=JPEG, card=b""))[3] == ""
    # Digits that are not ASCII never reach the comparison as digits.
    arabic = "٨٦٠٠١٢٣٤٥٦٧٨٩٠١٢".encode()
    parsed = parse_receipt_form(FORM, form(amount=b"1", months=b"1", receipt=JPEG, card=arabic))[3]
    assert parsed is not None and not any(sign.isdigit() for sign in parsed)


@pytest.mark.parametrize(
    ("content_type", "body", "field"),
    [
        ("application/json", b'{"amount": 1, "months": 1}', "_"),
        ("text/plain", b"amount=1", "_"),
        (FORM, form(months=b"1", receipt=JPEG), "amount"),
        (FORM, form(amount=b"100000", receipt=JPEG), "months"),
        (FORM, form(amount=b"100000", months=b"1"), "receipt"),
        (FORM, form(amount=b"100 000", months=b"1", receipt=JPEG), "amount"),
        (FORM, form(amount=b"-100000", months=b"1", receipt=JPEG), "amount"),
        (FORM, form(amount=b"1e5", months=b"1", receipt=JPEG), "amount"),
        (FORM, form(amount=b"100000", months=b"1.5", receipt=JPEG), "months"),
        (FORM, form(amount=b"100000", months=b"", receipt=JPEG), "months"),
        (FORM, form(amount=b"1" * 13, months=b"1", receipt=JPEG), "amount"),
        (FORM, form(amount=b"100000", months=b"1", receipt=JPEG, note=b"x"), "_"),
        (FORM, form(amount=b"100000", amount_=b"200000", months=b"1", receipt=JPEG), "_"),
        (FORM, form(amount=b"100000", months=b"1", receipt=JPEG, card=b"8600", card_=b"5614"), "_"),
        (FORM, b"not a form at all", "_"),
    ],
)
def test_anything_else_is_refused_by_field(content_type: str, body: bytes, field: str) -> None:
    with pytest.raises(ValidationFailed) as refused:
        parse_receipt_form(content_type, body)
    assert set(refused.value.fields) == {field}


def test_only_the_upload_route_itself_may_carry_a_large_body() -> None:
    shop = "0b9f1c2e-3a4d-4e5f-8a6b-7c8d9e0f1a2b"
    allowed = SUBSCRIPTION_RECEIPT_UPLOAD
    assert allowed.method == "POST"
    assert allowed.path.fullmatch(f"/api/v1/shops/{shop}/subscription/receipts")
    for other in (
        f"/api/v1/shops/{shop}/subscription",
        f"/api/v1/shops/{shop}/subscription/receipts/",
        f"/api/v1/shops/{shop}/subscription/receipts/x",
        "/api/v1/shops/not-a-shop/subscription/receipts",
        f"/x/api/v1/shops/{shop}/subscription/receipts",
    ):
        assert not allowed.path.fullmatch(other), other

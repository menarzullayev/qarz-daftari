"""Platform switches and prices: what each may hold and what applies when nothing is stored (ADR-018).

The administrator changes them at run time (REQ-N14). A wrong value takes effect at once, so every value
is checked by type and range, and the changes the specification calls sensitive ask for the second factor
again.
"""

from dataclasses import dataclass
from typing import Any, Literal

from qarz.domain.subscription import DEFAULT_PRICE_UZS

Kind = Literal["switch", "number", "cards", "chat"]
Card = dict[str, str]
Value = bool | int | str | list[Card] | None


class InvalidSetting(ValueError):
    """The value is of the wrong type or outside the range. The message says what is expected."""


@dataclass(frozen=True)
class Setting:
    kind: Kind
    default: Value
    low: int = 0
    high: int = 0
    # Specification, clients table: "changes to price, card number, and switches ask for the code again".
    needs_code: bool = False


CARD_DIGITS = 16
MAX_CARDS = 10
MAX_CARD_LABEL = 40
# What a receipt keeps of the card it was paid to: the label, then this mark and the last four digits.
CARD_TAIL = " ··"
MIN_CHAT_ID = -(10**15)

SETTINGS: dict[str, Setting] = {
    "trial_on": Setting("switch", True, needs_code=True),  # REQ-052: on by default
    "trial_days": Setting("number", 30, 1, 365),
    "price_uzs": Setting("number", DEFAULT_PRICE_UZS, 1_000, 10_000_000, needs_code=True),  # REQ-053
    # The cards owners may pay the subscription to, in the order they are offered: the first is the
    # primary one, shown first; the payer chooses whichever suits them.
    "payment_cards": Setting("cards", [], needs_code=True),
    # The group that sees subscription receipts: sending them elsewhere is as sensitive as the card.
    "review_group": Setting("chat", None, needs_code=True),
    "sms_on": Setting("switch", False, needs_code=True),
    "sms_monthly_quota": Setting("number", 0, 0, 100_000),
    "online_pay_on": Setting("switch", False, needs_code=True),
    # The free plan (BR-33 to BR-35): a shop with no more customers than this works in full without a
    # paid period, and SMS is for paying shops alone. Off, everything is as it was before the plan.
    "free_plan_on": Setting("switch", False, needs_code=True),
    "free_plan_customers": Setting("number", 30, 1, 10_000),
    # Separate permissions per member of staff (expansion module G). Off: roles alone decide, as before.
    "permissions_on": Setting("switch", False, needs_code=True),
    # A customer's secret read-only link and its QR code (the expansion of 2026-10-09, module B).
    "customer_links_on": Setting("switch", False, needs_code=True),
    # US dollars beside so'm (expansion module F): a shop may then keep dollar debts, each owner choosing
    # for their own shop. Off: every shop is so'm only, exactly as before.
    "usd_on": Setting("switch", False, needs_code=True),
    # The cash book: income and expense, categories, cash and card (expansion module H).
    "cash_book_on": Setting("switch", False, needs_code=True),
}


def _cards(value: Any) -> list[Card]:
    """The list as it is stored: numbers without spaces, labels trimmed. Null clears it like an empty list."""
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_CARDS:
        raise InvalidSetting(f"must be a list of at most {MAX_CARDS} cards, or null to clear it")
    cards: list[Card] = []
    for place, item in enumerate(value, start=1):
        if not isinstance(item, dict) or set(item) != {"number", "label"}:
            raise InvalidSetting(f"card {place}: must have a number and a label and nothing else")
        number, label = item["number"], item["label"]
        digits = number.replace(" ", "") if isinstance(number, str) else ""
        if len(digits) != CARD_DIGITS or not digits.isascii() or not digits.isdigit():
            raise InvalidSetting(f"card {place}: the number must be {CARD_DIGITS} digits")
        name = label.strip() if isinstance(label, str) else ""
        if not 1 <= len(name) <= MAX_CARD_LABEL:
            raise InvalidSetting(f"card {place}: the label must be between 1 and {MAX_CARD_LABEL} characters")
        if any(digits == card["number"] for card in cards):
            raise InvalidSetting(f"card {place}: the same number is in the list twice")
        cards.append({"number": digits, "label": name})
    return cards


def validate(key: str, value: Any) -> Value:
    """The value as it is stored, or InvalidSetting. An unknown key is invalid too."""
    setting = SETTINGS.get(key)
    if setting is None:
        raise InvalidSetting("unknown setting")
    if setting.kind == "switch":
        if not isinstance(value, bool):
            raise InvalidSetting("must be true or false")
        return value
    if setting.kind == "number":
        # bool is an int in Python; true must not be read as 1.
        if isinstance(value, bool) or not isinstance(value, int) or not setting.low <= value <= setting.high:
            raise InvalidSetting(f"must be a whole number between {setting.low} and {setting.high}")
        return value
    if setting.kind == "cards":
        return _cards(value)
    if value is None:
        return None
    # A Telegram group or supergroup: its chat identifier is a negative number.
    if isinstance(value, bool) or not isinstance(value, int) or not MIN_CHAT_ID <= value < 0:
        raise InvalidSetting("must be a Telegram group chat identifier (a negative number), or null to clear it")
    return value


def effective(key: str, stored: Any) -> Value:
    """What applies: the stored value when it is valid, otherwise the default."""
    try:
        return SETTINGS[key].default if stored is None else validate(key, stored)
    except InvalidSetting:
        return SETTINGS[key].default


def free_plan_customers(switch: Any, customers: Any) -> int | None:
    """How many customers the free plan holds, from the two stored values; None while it is switched off.

    Only a stored `true` is on, as for every switch that was added off."""
    if switch is not True:
        return None
    held = effective("free_plan_customers", customers)
    assert isinstance(held, int)
    return held


def needs_code(key: str) -> bool:
    return SETTINGS[key].needs_code


def masked(key: str, value: Any) -> Any:
    """The value as it may appear in the audit: a card number shows only its last four digits."""
    if SETTINGS[key].kind == "cards" and isinstance(value, list):
        return [{**card, "number": "*" * (len(card["number"]) - 4) + card["number"][-4:]} for card in value]
    return value


def payment_cards(stored: Any) -> list[Card]:
    """The cards to pay to as they apply: the stored list when it is valid, otherwise none."""
    cards = effective("payment_cards", stored)
    # A list of its own: the default is one object, shared by everything that reads it.
    return list(cards) if isinstance(cards, list) else []


def card_tag(card: Card) -> str:
    """How a receipt names the card it was paid to: the label and the last four digits, never the number."""
    return f"{card['label']}{CARD_TAIL}{card['number'][-4:]}"


def find_card(cards: list[Card], number: Any) -> Card | None:
    """The card of the list with this number, written with or without spaces."""
    digits = number.replace(" ", "") if isinstance(number, str) else ""
    return next((card for card in cards if card["number"] == digits), None)

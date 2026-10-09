"""The network between shops: the rules that need no storage (module J of the expansion; BR-80 to BR-96).

Two shops of the platform connect as buyer and supplier. Everything one side records about the other
takes effect on the other side only when that side confirms it. This module holds the words of that
exchange (roles, states and who may take each step), the checks of what a person types, and the
arithmetic of a delivery note. The database repeats the state rules where two shops meet (migration
0045); this module is how the application says the same thing first, with a better error.

State machines
--------------
Link:     requested -> active | declined | ended;  active -> ended.
          The shop that made the invitation answers a request; either side ends.
Order:    (draft) -> sent -> accepted -> delivered -> received
          sent | accepted | delivered* -> cancelled (the buyer) or declined (the supplier)
          * after a delivery only while its note is rejected, so nothing is posted.
Note:     issued -> received | rejected | superseded | void;  rejected -> superseded | void.
          The supplier issues and corrects; the buyer confirms or rejects.
Payment:  awaiting -> confirmed | declined (the other side) | withdrawn (the side that recorded it) | lapsed.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Final

from qarz.domain import catalog, stock
from qarz.domain.money import Currency, rules

# The platform setting that turns the network on (off by default). It also needs the stock (`stock_on`).
SWITCH: Final = "network_on"

BUYER: Final = "buyer"
SUPPLIER: Final = "supplier"
ROLES: Final = (BUYER, SUPPLIER)

REQUESTED: Final = "requested"
ACTIVE: Final = "active"
DECLINED: Final = "declined"
ENDED: Final = "ended"
LINK_STATES: Final = (REQUESTED, ACTIVE, DECLINED, ENDED)

DRAFT: Final = "draft"
SENT: Final = "sent"
ACCEPTED: Final = "accepted"
DELIVERED: Final = "delivered"
RECEIVED: Final = "received"
CANCELLED: Final = "cancelled"
ORDER_STATES: Final = (SENT, ACCEPTED, DELIVERED, RECEIVED, DECLINED, CANCELLED)

ISSUED: Final = "issued"
REJECTED: Final = "rejected"
SUPERSEDED: Final = "superseded"
VOID: Final = "void"
NOTE_STATES: Final = (ISSUED, RECEIVED, REJECTED, SUPERSEDED, VOID)

AWAITING: Final = "awaiting"
CONFIRMED: Final = "confirmed"
WITHDRAWN: Final = "withdrawn"
LAPSED: Final = "lapsed"
PAYMENT_STATES: Final = (AWAITING, CONFIRMED, DECLINED, WITHDRAWN, LAPSED)

# An invitation code: 32 random bytes, like every token of the service; stored as its SHA-256.
INVITE_LIFETIME: Final = timedelta(hours=48)
MAX_OPEN_INVITES: Final = 10

MAX_LINES: Final = 100
# The name of a line is the catalogue's own rule for the name of a good, and no longer: the buyer may take
# a delivered line into its catalogue as a new item under exactly this name, so a name the catalogue would
# refuse (longer than it allows, or without a letter or a digit) is refused when the order is written.
MAX_LINE_NAME: Final = catalog.MAX_NAME_LENGTH
MAX_NOTE: Final = 200
MIN_REASON: Final = 3
MAX_REASON: Final = 200
# How far ahead the buyer may want the goods.
MAX_WANTED_DAYS: Final = 365


def other(role: str) -> str:
    return SUPPLIER if role == BUYER else BUYER


def new_code() -> str:
    return secrets.token_urlsafe(32)


def code_hash(code: str) -> bytes:
    return hashlib.sha256(code.strip().encode("utf-8")).digest()


def plausible_code(code: str) -> bool:
    """Whether the text can be a code at all: anything else is refused before the database is asked."""
    text = code.strip()
    return 20 <= len(text) <= 64 and all(char.isalnum() or char in "-_" for char in text)


# --- who may take which step ----------------------------------------------------------------------------


def may_decide_link(state: str, *, invited: bool) -> bool:
    """The shop that made the invitation accepts or declines the request, once."""
    return invited and state == REQUESTED


def may_end_link(state: str) -> bool:
    return state in (REQUESTED, ACTIVE)


def may_send_order(link_state: str, role: str) -> bool:
    return role == BUYER and link_state == ACTIVE


def may_accept_order(status: str, role: str) -> bool:
    return role == SUPPLIER and status == SENT


def may_close_order(status: str, note_status: str | None) -> bool:
    """Cancelling (the buyer) or declining (the supplier): while nothing of the order has been received.
    After a delivery that is so only when its note was rejected."""
    if status in (SENT, ACCEPTED):
        return True
    return status == DELIVERED and note_status != ISSUED


def closed_as(role: str) -> str:
    return CANCELLED if role == BUYER else DECLINED


def may_issue_note(order_status: str, role: str, note_status: str | None) -> bool:
    """The first note of an accepted order, or a correction of one that is not received."""
    if role != SUPPLIER:
        return False
    if order_status == ACCEPTED:
        return note_status is None
    return order_status == DELIVERED and note_status in (ISSUED, REJECTED)


def may_answer_note(status: str, role: str) -> bool:
    """The buyer confirms or rejects a note that waits."""
    return role == BUYER and status == ISSUED


def may_answer_payment(status: str, *, recorded_by_own: bool) -> bool:
    """The side that did not record a payment confirms or declines it."""
    return status == AWAITING and not recorded_by_own


def may_withdraw_payment(status: str, *, recorded_by_own: bool) -> bool:
    return status == AWAITING and recorded_by_own


# --- what a person types ----------------------------------------------------------------------------------


def text(raw: str | None, *, limit: int) -> str | None:
    """A free text as stored: spaces collapsed, None when empty."""
    if raw is None:
        return None
    cleaned = " ".join(raw.split())
    if len(cleaned) > limit:
        raise ValueError(f"at most {limit} characters")
    return cleaned or None


def reason(raw: str) -> str:
    """Why something is declined, cancelled, rejected or corrected: the other side reads it."""
    cleaned = " ".join(raw.split())
    if not MIN_REASON <= len(cleaned) <= MAX_REASON:
        raise ValueError(f"between {MIN_REASON} and {MAX_REASON} characters")
    return cleaned


def line_name(raw: str) -> str:
    return catalog.item_name(raw)


def unit(raw: str) -> str:
    if raw not in stock.UNIT_KEYS:
        raise ValueError("must be one of the stock units")
    return raw


def wanted_date(value: date | None, today: date) -> date | None:
    if value is None:
        return None
    if not today <= value <= today + timedelta(days=MAX_WANTED_DAYS):
        raise ValueError(f"today or up to {MAX_WANTED_DAYS} days ahead")
    return value


def offered_qty(raw: str) -> Decimal:
    """What the supplier will deliver of a line: a quantity, or nothing at all."""
    return stock.counted(raw)


# --- a delivery note -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class PricedLine:
    line_no: int
    qty: Decimal
    unit_price: int

    @property
    def total(self) -> int:
        # Quantity times price, rounded half up to a whole minor unit, once: the stock's own rule, so the
        # buyer's receipt and the supplier's sale come to the same figure line by line.
        return stock.line_cost(self.qty, self.unit_price)


def total_of(lines: list[PricedLine]) -> int:
    return sum(line.total for line in lines)


def total_problem(currency: Currency, total: int) -> str | None:
    """Why a note of this total cannot be issued, or None. It becomes one receipt on the buyer's side
    and one credit sale on the supplier's, so it must fit both."""
    bounds = rules(currency)
    if total < bounds.min_entry or total > min(bounds.max_entry, stock.MAX_DOCUMENT_TOTAL):
        return f"the total must be between {bounds.min_entry} and {bounds.max_entry}"
    return None


def payment_terms(total: int, paid: int) -> str:
    """How a note is paid: all of it on delivery, none of it (on credit), or a part."""
    if paid >= total:
        return "paid"
    return "credit" if paid == 0 else "part"

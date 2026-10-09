"""Messages to a linked customer about their own account (REQ-012, REQ-015).

Queued in the transaction that writes the entry, so a customer is told exactly about what was saved: no
message for an entry that failed, and no entry without its message. Only an active link is told; a
customer who is not linked, who disconnected, or who blocked the bot gets nothing.
"""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from qarz.application.chat_texts import day, money, owed, say
from qarz.application.currencies import UZS, balance_in, currency_of
from qarz.application.ports import TenantSession
from qarz.domain.money import Currency

MAX_LINES_SHOWN = 20


def _goods(lang: str, lines: list[dict[str, Any]]) -> str:
    if not lines:
        return ""
    shown = [
        say(
            lang,
            "n_line",
            name=line["name"],
            qty=line["qty"],
            unit=line["unit"],
            total=money(lang, int(line["line_total"])),
        )
        for line in lines[:MAX_LINES_SHOWN]
    ]
    if len(lines) > MAX_LINES_SHOWN:
        shown.append(say(lang, "n_more_lines", count=len(lines) - MAX_LINES_SHOWN))
    return "\n".join(shown) + "\n"


async def _send(
    session: TenantSession, customer_id: UUID, dedupe_key: str, key: str, currency: Currency = UZS, **values: Any
) -> None:
    """`amount` and `balance` are both in `currency`: a message about dollars states the dollar balance."""
    recipient = await session.customer_recipient(customer_id)
    if recipient is None:
        return
    tg_id, lang = recipient
    settings = await session.shop_settings()
    shop = "" if settings is None else settings.name
    amount = money(lang, int(values.pop("amount")), currency)
    balance = owed(lang, int(values.pop("balance")), currency)
    lines = values.pop("lines", None)
    if lines is not None:
        values["goods"] = _goods(lang, lines)
    promised = values.pop("promised", None)
    if promised is not None:
        values["date"] = day(promised)
    dispute_entry = values.pop("dispute_entry", None)
    payload: dict[str, Any] = {"text": say(lang, key, shop=shop, amount=amount, balance=balance, **values)}
    if dispute_entry is not None:
        # There is no confirm button and never will be (BR-10): objecting is the only thing asked.
        payload["reply_markup"] = {
            "inline_keyboard": [
                [{"text": say(lang, "dispute_button"), "callback_data": f"v2:dsp:{UUID(dispute_entry).hex}"}],
                # Only a credit sale's message carries these: it is the one entry with a date to move.
                [{"text": say(lang, "move_date_button"), "callback_data": f"v2:dmv:{UUID(dispute_entry).hex}"}],
            ]
        }
    await session.enqueue(recipient=str(tg_id), payload=payload, dedupe_key=dedupe_key)


async def entry_recorded(session: TenantSession, customer_id: UUID, body: dict[str, Any]) -> None:
    """Tell the customer about a credit sale or a payment just added to their account."""
    entry, customer = body["entry"], body["customer"]
    currency = currency_of(entry)
    common = {
        "name": customer["display_name"],
        "amount": entry["amount"],
        "balance": balance_in(customer, currency),
        "currency": currency,
    }
    if entry["kind"] == "credit":
        await _send(
            session,
            customer_id,
            f"entry:{entry['id']}:notify",
            "n_credit",
            lines=list(entry.get("lines") or []),
            dispute_entry=entry["id"],
            promised=date.fromisoformat(entry["promised_date"]),
            **common,
        )
    else:
        await _send(session, customer_id, f"entry:{entry['id']}:notify", "n_payment", **common)


async def entry_reversed(session: TenantSession, customer_id: UUID, body: dict[str, Any], reversed_kind: str) -> None:
    entry, customer = body["entry"], body["customer"]
    await _send(
        session,
        customer_id,
        f"entry:{entry['id']}:notify",
        "n_reversed_payment" if reversed_kind == "payment" else "n_reversed_credit",
        currency_of(entry),
        name=customer["display_name"],
        amount=entry["amount"],
        balance=balance_in(customer, currency_of(entry)),
    )


async def promise_chosen(session: TenantSession, customer_id: UUID, body: dict[str, Any]) -> None:
    """The date in the first message was the shop default; tell the customer the one that was then chosen."""
    entry, customer = body["entry"], body["customer"]
    await _send(
        session,
        customer_id,
        f"entry:{entry['id']}:promise:{entry['promised_date']}",
        "n_promise",
        currency_of(entry),
        name=customer["display_name"],
        amount=entry["amount"],
        balance=balance_in(customer, currency_of(entry)),
        promised=date.fromisoformat(entry["promised_date"]),
    )


# --- promised dates moved later on (REQ-066, REQ-067) ----------------------------------------------------


def with_reason(lang: str, text: str, reason: str | None) -> str:
    """The text, followed by the reason on a line of its own when one was given."""
    return text if reason is None else f"{text}\n{say(lang, 'reason_line', reason=reason)}"


async def _tell_customer(
    session: TenantSession,
    customer_id: UUID,
    dedupe_key: str,
    key: str,
    reason: str | None,
    currency: Currency = UZS,
    **values: Any,
) -> None:
    recipient = await session.customer_recipient(customer_id)
    if recipient is None:
        return
    tg_id, lang = recipient
    settings = await session.shop_settings()
    text = say(
        lang,
        key,
        shop="" if settings is None else settings.name,
        amount=money(lang, int(values.pop("amount")), currency),
        **{name: day(value) if isinstance(value, date) else value for name, value in values.items()},
    )
    await session.enqueue(
        recipient=str(tg_id), payload={"text": with_reason(lang, text, reason)}, dedupe_key=dedupe_key
    )


async def promise_changed(
    session: TenantSession,
    customer_id: UUID,
    *,
    entry_id: UUID,
    name: str,
    amount: int,
    previous: date,
    promised: date,
    reason: str | None,
    at: datetime,
    currency: Currency = UZS,
) -> None:
    """A manager or owner moved the date. The time is in the key: a date may be set, changed and set again."""
    await _tell_customer(
        session,
        customer_id,
        f"entry:{entry_id}:promise-changed:{at.isoformat()}",
        "n_date_changed",
        reason,
        currency,
        name=name,
        amount=amount,
        old=previous,
        date=promised,
    )


async def date_request_decided(
    session: TenantSession,
    customer_id: UUID,
    *,
    request_id: UUID,
    accepted: bool,
    amount: int,
    requested: date,
    reason: str | None,
    currency: Currency = UZS,
) -> None:
    """Tell the customer what became of their request to move a date."""
    await _tell_customer(
        session,
        customer_id,
        f"date-request:{request_id}:{'accepted' if accepted else 'declined'}",
        "n_date_accepted" if accepted else "n_date_declined",
        reason,
        currency,
        amount=amount,
        date=requested,
    )


async def opening_imported(
    session: TenantSession,
    customer_id: UUID,
    *,
    entry_id: UUID,
    name: str,
    amount: int,
    balance: int,
    promised: date,
    currency: Currency = UZS,
) -> None:
    """An opening balance was added to the account of a customer who is linked (REQ-063).

    It can be objected to and its date moved like a credit sale's, so the message carries both buttons.
    """
    await _send(
        session,
        customer_id,
        f"entry:{entry_id}:notify",
        "n_opening",
        currency,
        name=name,
        amount=amount,
        balance=balance,
        promised=promised,
        dispute_entry=str(entry_id),
    )

"""Messages to a linked customer about their own account (REQ-012, REQ-015).

Queued in the transaction that writes the entry, so a customer is told exactly about what was saved: no
message for an entry that failed, and no entry without its message. Only an active link is told; a
customer who is not linked, who disconnected, or who blocked the bot gets nothing.
"""

from datetime import date
from typing import Any
from uuid import UUID

from qarz.application.chat_texts import day, money, say
from qarz.application.ports import TenantSession

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


async def _send(session: TenantSession, customer_id: UUID, dedupe_key: str, key: str, **values: Any) -> None:
    recipient = await session.customer_recipient(customer_id)
    if recipient is None:
        return
    tg_id, lang = recipient
    settings = await session.shop_settings()
    shop = "" if settings is None else settings.name
    amount = money(lang, int(values.pop("amount")))
    balance = money(lang, int(values.pop("balance")))
    lines = values.pop("lines", None)
    if lines is not None:
        values["goods"] = _goods(lang, lines)
    promised = values.pop("promised", None)
    if promised is not None:
        values["date"] = day(promised)
    text = say(lang, key, shop=shop, amount=amount, balance=balance, **values)
    await session.enqueue(recipient=str(tg_id), payload={"text": text}, dedupe_key=dedupe_key)


async def entry_recorded(session: TenantSession, customer_id: UUID, body: dict[str, Any]) -> None:
    """Tell the customer about a credit sale or a payment just added to their account."""
    entry, customer = body["entry"], body["customer"]
    common = {"name": customer["display_name"], "amount": entry["amount"], "balance": customer["balance"]}
    if entry["kind"] == "credit":
        await _send(
            session,
            customer_id,
            f"entry:{entry['id']}:notify",
            "n_credit",
            lines=list(entry.get("lines") or []),
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
        name=customer["display_name"],
        amount=entry["amount"],
        balance=customer["balance"],
    )


async def promise_chosen(session: TenantSession, customer_id: UUID, body: dict[str, Any]) -> None:
    """The date in the first message was the shop default; tell the customer the one that was then chosen."""
    entry, customer = body["entry"], body["customer"]
    await _send(
        session,
        customer_id,
        f"entry:{entry['id']}:promise:{entry['promised_date']}",
        "n_promise",
        name=customer["display_name"],
        amount=entry["amount"],
        balance=customer["balance"],
        promised=date.fromisoformat(entry["promised_date"]),
    )

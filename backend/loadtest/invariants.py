"""Checks that generated data is data the application could have recorded.

Two forms of the same rules:

- `problems` reads generated rows in memory and is what the test suite runs on a small set;
- `database_problems` asks a loaded database, and is run after every load, because the loader switches
  off one trigger (see `loadtest.load`) and the rules that trigger guards must be shown to hold anyway.

Both return a list of plain sentences; an empty list means nothing was found.
"""

from collections import defaultdict
from datetime import timedelta
from typing import Any, LiteralString
from uuid import UUID

import psycopg

from loadtest.dataset import COLUMNS, Row, ShopData
from qarz.domain import ledger
from qarz.domain.ledger import Entry, EntryKind, LedgerIntegrityError
from qarz.domain.names import normalize_name
from qarz.domain.promise import MAX_PROMISE_DAYS, tashkent_date
from qarz.domain.rounding import line_total

MIN_AMOUNT = 100
MAX_AMOUNT = 100_000_000


def _named(table: str, row: Row) -> dict[str, Any]:
    return dict(zip(COLUMNS[table], row, strict=True))


def problems(shops: list[ShopData]) -> list[str]:
    found: list[str] = []
    telegram_ids: set[int] = set()
    identifiers: set[UUID] = set()
    for shop in shops:
        rows = {table: [_named(table, row) for row in shop.rows[table]] for table in COLUMNS}
        label = f"shop {shop.index}"

        for table, table_rows in rows.items():
            for row in table_rows:
                if "shop_id" in row and row["shop_id"] != shop.shop_id:
                    found.append(f"{label}: a {table} row belongs to another shop")
                key = row.get("id")
                if key is not None:
                    if key in identifiers:
                        found.append(f"{label}: identifier {key} is used twice")
                    identifiers.add(key)
        for user in rows["app_user"]:
            if user["tg_id"] in telegram_ids:
                found.append(f"{label}: Telegram identifier {user['tg_id']} is used twice")
            telegram_ids.add(user["tg_id"])

        owners = [m for m in rows["membership"] if m["role"] == "owner" and m["status"] == "active"]
        if len(owners) != 1:
            found.append(f"{label}: {len(owners)} active owners")
        members = {m["id"] for m in rows["membership"]}
        if len(rows["subscription"]) != 1:
            found.append(f"{label}: {len(rows['subscription'])} subscription rows")

        catalog = {item["id"] for item in rows["catalog_item"]}
        norms = [item["name_norm"] for item in rows["catalog_item"]]
        if len(set(norms)) != len(norms):
            found.append(f"{label}: two catalog items share a normalized name")

        customers = {c["id"]: c for c in rows["customer"]}
        for person in customers.values():
            if person["name_norm"] != normalize_name(person["display_name"]) or not person["name_norm"]:
                found.append(f"{label}: customer {person['id']} has a wrong normalized name")

        promises: dict[UUID, list[dict[str, Any]]] = defaultdict(list)
        for promise in rows["promise"]:
            promises[promise["entry_id"]].append(promise)
        disputed = {d["entry_id"] for d in rows["dispute"] if d["status"] == "open"}
        lines: dict[UUID, list[dict[str, Any]]] = defaultdict(list)
        for line in rows["goods_line"]:
            lines[line["entry_id"]].append(line)

        accounts: dict[UUID, list[dict[str, Any]]] = defaultdict(list)
        for entry in rows["ledger_entry"]:
            accounts[entry["customer_id"]].append(entry)
        entries_by_id = {entry["id"]: entry for entry in rows["ledger_entry"]}

        for entry_id in list(promises) + list(lines) + list(disputed):
            if entry_id not in entries_by_id:
                found.append(f"{label}: a promise, goods line or dispute refers to no entry of this shop")

        for customer_id, account in accounts.items():
            who = f"{label}: customer {customer_id}"
            customer = customers.get(customer_id)
            if customer is None:
                found.append(f"{who} has entries but no customer row")
                continue
            if [entry["seq"] for entry in account] != list(range(1, len(account) + 1)):
                found.append(f"{who}: seq is not 1, 2, 3, ... in the order recorded")
            domain_entries = []
            previous_at = customer["created_at"]
            for entry in account:
                kind = EntryKind(entry["kind"])
                if not MIN_AMOUNT <= entry["amount"] <= MAX_AMOUNT:
                    found.append(f"{who}: entry {entry['seq']} has an amount the application refuses")
                if entry["author_id"] not in members:
                    found.append(f"{who}: entry {entry['seq']} was recorded by nobody in this shop")
                if entry["created_at"] < previous_at:
                    found.append(f"{who}: entry {entry['seq']} is older than the one before it")
                previous_at = entry["created_at"]

                history = sorted(promises.get(entry["id"], []), key=lambda p: (p["created_at"], p["id"]))
                current = history[-1]["promised_date"] if history else None
                if kind in (EntryKind.CREDIT, EntryKind.OPENING):
                    sold_on = tashkent_date(entry["created_at"])
                    for promise in history:
                        if promise["created_at"] < entry["created_at"]:
                            found.append(f"{who}: entry {entry['seq']} was promised before it existed")
                        if not sold_on <= promise["promised_date"] <= sold_on + timedelta(days=MAX_PROMISE_DAYS):
                            found.append(f"{who}: entry {entry['seq']} has a promised date out of range")
                elif history:
                    found.append(f"{who}: entry {entry['seq']} is a {kind.value} with a promise")

                goods = sorted(lines.get(entry["id"], []), key=lambda line: line["line_no"])
                if goods:
                    if kind is not EntryKind.CREDIT:
                        found.append(f"{who}: entry {entry['seq']} is a {kind.value} with goods lines")
                    if [line["line_no"] for line in goods] != list(range(1, len(goods) + 1)):
                        found.append(f"{who}: entry {entry['seq']} has goods lines not numbered from 1")
                    if len({line["batch_at"] for line in goods}) != 1:
                        found.append(f"{who}: entry {entry['seq']} has goods lines from more than one batch")
                    if sum(line["line_total"] for line in goods) != entry["amount"]:
                        found.append(f"{who}: entry {entry['seq']} does not equal the sum of its goods lines")
                    for line in goods:
                        if line["line_total"] != line_total(line["qty"], line["unit_price"]):
                            found.append(f"{who}: entry {entry['seq']} has a goods line that is rounded wrongly")
                        if line["catalog_item_id"] is not None and line["catalog_item_id"] not in catalog:
                            found.append(f"{who}: entry {entry['seq']} names a catalog item of another shop")

                domain_entries.append(
                    Entry(
                        id=entry["id"],
                        seq=entry["seq"],
                        kind=kind,
                        amount=entry["amount"],
                        created_at=entry["created_at"],
                        reverses_id=entry["reverses_id"],
                        promised_date=current,
                        disputed=entry["id"] in disputed,
                    )
                )
            try:
                # The domain refuses a negative running balance, a reversal of a reversal, an entry
                # reversed twice, a reversal with another amount, and a debt without a promise.
                balance = ledger.balance(domain_entries)
            except LedgerIntegrityError as error:
                found.append(f"{who}: {error}")
                continue
            if customer["status"] == "archived" and balance != 0:
                found.append(f"{who} is archived but owes {balance}")

        linked = [link["customer_id"] for link in rows["customer_link"]]
        if len(set(linked)) != len(linked):
            found.append(f"{label}: a customer has two live links")
    return found


# Each query returns the rows that break one rule. They run as the migration owner, across all shops.
_DATABASE_RULES: dict[str, LiteralString] = {
    "seq is not contiguous from 1 for a customer": (
        "SELECT customer_id FROM ledger_entry GROUP BY customer_id HAVING min(seq) <> 1 OR max(seq) <> count(*) LIMIT 5"
    ),
    "created_at goes backwards within a customer's entries": (
        "SELECT id FROM (SELECT id, created_at, lag(created_at) OVER (PARTITION BY customer_id ORDER BY seq) AS before "
        "FROM ledger_entry) e WHERE created_at < before LIMIT 5"
    ),
    "a running balance is negative": (
        "SELECT id FROM (SELECT e.id, sum(CASE "
        "  WHEN e.kind IN ('credit', 'opening') THEN e.amount "
        "  WHEN e.kind = 'payment' THEN -e.amount "
        "  WHEN t.kind = 'payment' THEN e.amount ELSE -e.amount END) "
        "  OVER (PARTITION BY e.customer_id ORDER BY e.seq) AS running "
        "FROM ledger_entry e LEFT JOIN ledger_entry t ON t.id = e.reverses_id) r WHERE running < 0 LIMIT 5"
    ),
    "a reversal does not undo an earlier entry of the same customer, not itself a reversal, for the same amount": (
        "SELECT e.id FROM ledger_entry e JOIN ledger_entry t ON t.id = e.reverses_id "
        "WHERE t.customer_id <> e.customer_id OR t.seq >= e.seq OR t.kind = 'reversal' OR t.amount <> e.amount LIMIT 5"
    ),
    "an entry is in another shop than its customer": (
        "SELECT e.id FROM ledger_entry e JOIN customer c ON c.id = e.customer_id WHERE c.shop_id <> e.shop_id LIMIT 5"
    ),
    "an entry was recorded by a member of another shop": (
        "SELECT e.id FROM ledger_entry e JOIN membership m ON m.id = e.author_id WHERE m.shop_id <> e.shop_id LIMIT 5"
    ),
    "a credit sale or opening balance has no promise": (
        "SELECT e.id FROM ledger_entry e WHERE e.kind IN ('credit', 'opening') "
        "AND NOT EXISTS (SELECT 1 FROM promise p WHERE p.entry_id = e.id) LIMIT 5"
    ),
    "a payment or reversal has a promise": (
        "SELECT e.id FROM ledger_entry e JOIN promise p ON p.entry_id = e.id "
        "WHERE e.kind NOT IN ('credit', 'opening') LIMIT 5"
    ),
    "a promise or goods line is in another shop than its entry": (
        "SELECT e.id FROM ledger_entry e JOIN promise p ON p.entry_id = e.id WHERE p.shop_id <> e.shop_id "
        "UNION ALL "
        "SELECT e.id FROM ledger_entry e JOIN goods_line g ON g.entry_id = e.id WHERE g.shop_id <> e.shop_id LIMIT 5"
    ),
    # The three rules of the goods_line_guard trigger that do not depend on the time of loading.
    "goods lines are on an entry that is not a credit sale": (
        "SELECT e.id FROM ledger_entry e JOIN goods_line g ON g.entry_id = e.id WHERE e.kind <> 'credit' LIMIT 5"
    ),
    "goods lines of one entry come from more than one batch": (
        "SELECT entry_id FROM goods_line GROUP BY entry_id HAVING count(DISTINCT batch_at) > 1 LIMIT 5"
    ),
    "goods lines were added after the end of the day after the sale": (
        "SELECT e.id FROM ledger_entry e JOIN goods_line g ON g.entry_id = e.id WHERE g.batch_at >= "
        "((date_trunc('day', e.created_at AT TIME ZONE 'Asia/Tashkent') + interval '2 days') "
        "AT TIME ZONE 'Asia/Tashkent') LIMIT 5"
    ),
    "goods lines do not sum to the entry total": (
        "SELECT e.id FROM ledger_entry e JOIN (SELECT entry_id, sum(line_total) AS total FROM goods_line "
        "GROUP BY entry_id) g ON g.entry_id = e.id WHERE g.total <> e.amount LIMIT 5"
    ),
    "an archived customer owes something": (
        "SELECT c.id FROM customer c JOIN (SELECT e.customer_id, sum(CASE "
        "  WHEN e.kind IN ('credit', 'opening') THEN e.amount ELSE -e.amount END) AS balance "
        "FROM ledger_entry e WHERE e.kind <> 'reversal' "
        "AND NOT EXISTS (SELECT 1 FROM ledger_entry r WHERE r.reverses_id = e.id) GROUP BY e.customer_id) b "
        "ON b.customer_id = c.id WHERE c.status = 'archived' AND b.balance <> 0 LIMIT 5"
    ),
    "a shop does not have exactly one active owner": (
        "SELECT s.id FROM shop s WHERE (SELECT count(*) FROM membership m WHERE m.shop_id = s.id "
        "AND m.role = 'owner' AND m.status = 'active') <> 1 LIMIT 5"
    ),
}


def database_problems(conn: psycopg.Connection[Any]) -> list[str]:
    found = []
    for rule, query in _DATABASE_RULES.items():
        rows = conn.execute(query).fetchall()
        if rows:
            found.append(f"{rule}: for example {rows[0][0]}")
    return found

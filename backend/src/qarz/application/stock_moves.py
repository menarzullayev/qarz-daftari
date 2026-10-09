"""Writing stock movements inside a tenant transaction the caller has opened and authorized.

Everything that changes the stock goes through `move` or `reverse_movement`: they lock the item, ask
`qarz.domain.stock` what the movement does to its level, and store it. The database then checks that the
movement continues the level and sets the level from it (migration 0043), so nothing here writes a
quantity on hand.

Two hooks are called by the customers' ledger, which knows nothing else about the stock:

- `draw_for_sale`, after the goods lines of a credit sale are stored: a line of a counted item takes its
  quantity out of the stock;
- `before_entry_reversed`, when a ledger entry is cancelled: the movements it caused are reversed.

Both do nothing while the platform switch `stock_on` is off, or when nothing of the kind exists.
"""

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from qarz.application.errors import AppError, NotFound
from qarz.application.ports import GoodsLineRecord, Membership, TenantSession
from qarz.application.stock_ports import MovementRecord, NewMovement, StockItem
from qarz.domain import stock
from qarz.domain.goods import format_qty
from qarz.domain.stock import Effect, Level


class StockInsufficient(AppError):
    """Less is on hand than the movement takes, and this movement may not go below zero."""

    code = "STOCK_INSUFFICIENT"


class StockAlreadyUsed(AppError):
    """Cancelling would take back goods that have already left the stock."""

    code = "STOCK_ALREADY_USED"


class CostCurrencyMismatch(AppError):
    """The item's cost is kept in the other currency while something of it is on hand."""

    code = "COST_CURRENCY_MISMATCH"


class EntryOfDocument(AppError):
    """The entry was written by a document: cancelling the document cancels it, with the goods."""

    code = "ENTRY_OF_DOCUMENT"


async def switched_on(session: TenantSession) -> bool:
    # Only the JSON value `true` turns it on: absent, null, "true" and 1 all mean off.
    return await session.platform_setting(stock.SWITCH) is True


async def require_on(session: TenantSession) -> None:
    """Off is "no such route", to everyone, before the caller is asked who they are."""
    if not await switched_on(session):
        raise NotFound()


def _item_fields(item: StockItem, wanted: Any = None) -> dict[str, str]:
    fields = {"item": str(item.item_id), "name": item.name, "on_hand": format_qty(item.level.on_hand)}
    if wanted is not None:
        fields["wanted"] = format_qty(wanted)
    return fields


async def lock_items(session: TenantSession, item_ids: Sequence[UUID]) -> dict[UUID, StockItem]:
    """Lock the items in one fixed order, so two writers that share items never wait for each other in a
    ring. Returns each as it stands under the lock; an identifier that is no item of the shop is left out."""
    found: dict[UUID, StockItem] = {}
    for item_id in sorted(set(item_ids)):
        item = await session.stock_item(item_id, for_update=True)
        if item is not None:
            found[item_id] = item
    return found


async def move(
    session: TenantSession,
    actor: Membership,
    item_id: UUID,
    *,
    kind: str,
    compute: Callable[[Level], Effect | None],
    now: datetime,
    unit_cost: int | None = None,
    sale_total: int | None = None,
    reason: str | None = None,
    document_id: UUID | None = None,
    line_no: int | None = None,
    ledger_entry_id: UUID | None = None,
    reverses_id: UUID | None = None,
) -> tuple[StockItem, Effect] | None:
    """Store one movement of one item. `compute` is given the level under the item's lock.

    Returns the item as it was before and what the movement did; None when `compute` says nothing moves.
    """
    item = await session.stock_item(item_id, for_update=True)
    if item is None:
        raise NotFound()
    try:
        effect = compute(item.level)
    except stock.NotEnoughOnHand as error:
        raise StockInsufficient(_item_fields(item, error.wanted)) from error
    except stock.CostCurrencyMismatch as error:
        raise CostCurrencyMismatch(
            {"item": str(item.item_id), "name": item.name, "held": error.held, "given": error.given}
        ) from error
    if effect is None:
        return None
    await session.add_movement(
        NewMovement(
            movement_id=uuid4(),
            item_id=item_id,
            item_seq=item.last_seq + 1,
            kind=kind,
            qty=effect.qty,
            value_delta=effect.value_delta,
            on_hand_after=effect.after.on_hand,
            value_after=effect.after.value,
            currency=effect.after.currency,
            author_id=actor.membership_id,
            created_at=now,
            unit_cost=unit_cost,
            cost_total=effect.cost_total,
            sale_total=sale_total,
            reason=reason,
            document_id=document_id,
            line_no=line_no,
            ledger_entry_id=ledger_entry_id,
            reverses_id=reverses_id,
        )
    )
    return item, effect


async def reverse_movement(
    session: TenantSession, actor: Membership, movement: MovementRecord, *, now: datetime
) -> None:
    """Undo one movement with its opposite. The original stays as it was."""

    def compute(level: Level) -> Effect:
        if movement.qty < 0:
            return stock.bring_back(level, -movement.qty, movement.cost_total, movement.currency)
        return stock.take_back(level, movement.qty, max(movement.value_delta, 0))

    try:
        await move(
            session,
            actor,
            movement.item_id,
            kind=stock.REVERSAL,
            compute=compute,
            now=now,
            document_id=movement.document_id,
            line_no=movement.line_no,
            ledger_entry_id=movement.ledger_entry_id,
            reverses_id=movement.movement_id,
        )
    except StockInsufficient as error:
        # Said in its own words: nothing is short for a sale; goods that came in have gone out again.
        raise StockAlreadyUsed(error.fields) from error


async def reverse_movements(
    session: TenantSession, actor: Membership, movements: Sequence[MovementRecord], *, now: datetime
) -> None:
    await lock_items(session, [movement.item_id for movement in movements])
    for movement in movements:
        await reverse_movement(session, actor, movement, now=now)


async def draw_for_sale(
    session: TenantSession, actor: Membership, entry_id: UUID, lines: Sequence[GoodsLineRecord], *, now: datetime
) -> list[dict[str, str]]:
    """Take the goods of a credit sale out of the stock. Returns what the seller should be told.

    Only a line of a counted item moves anything, and only when it is sold in the unit the item is
    counted in: a quantity of "quti" cannot be taken from a stock kept in "dona". A sale may take an item
    below zero unless the shop refuses that; the answer then says so.
    """
    if not lines or not await switched_on(session):
        return []
    item_ids = [line.catalog_item_id for line in lines if line.catalog_item_id is not None]
    known = await session.stock_items_by_ids(sorted(set(item_ids)))
    counted = [item_id for item_id in item_ids if item_id in known and known[item_id].tracked]
    if not counted:
        return []
    refuse = await session.stock_refuse_negative()
    await lock_items(session, counted)
    warnings: list[dict[str, str]] = []
    for line in lines:
        if line.catalog_item_id is None or line.catalog_item_id not in counted:
            continue
        item = known[line.catalog_item_id]
        if line.unit != item.unit:
            warnings.append({"kind": "unit", "item": str(item.item_id), "name": item.name, "unit": item.unit})
            continue
        qty = line.qty
        moved = await move(
            session,
            actor,
            item.item_id,
            kind=stock.SALE,
            compute=lambda level, qty=qty: stock.go_out(level, qty, may_go_negative=not refuse),  # type: ignore[misc]
            now=now,
            sale_total=line.line_total,
            ledger_entry_id=entry_id,
            line_no=line.line_no,
        )
        if moved is not None and moved[1].after.on_hand < 0:
            warnings.append(
                {
                    "kind": "negative",
                    "item": str(item.item_id),
                    "name": item.name,
                    "on_hand": format_qty(moved[1].after.on_hand),
                }
            )
    return warnings


async def before_entry_reversed(session: TenantSession, actor: Membership, entry_id: UUID, *, now: datetime) -> None:
    """A ledger entry is being cancelled: put back what its sale took.

    Runs whether or not the switch is on: goods that left with a sale come back with its cancellation
    even if the stock was switched off in between, or the books would not add up when it is switched on
    again. An entry a document wrote (a customer's return) is not cancelled here: the document is.
    """
    movements = await session.standing_movements_of_entry(entry_id)
    if not movements:
        return
    if any(movement.document_id is not None for movement in movements):
        raise EntryOfDocument()
    await reverse_movements(session, actor, movements, now=now)

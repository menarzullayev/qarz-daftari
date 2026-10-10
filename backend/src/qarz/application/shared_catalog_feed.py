"""Where a shop's own catalogue and the stock meet the shared catalogue.

Two hooks, called inside the tenant transaction that has just changed the shop's item, by code that
knows nothing else about the shared catalogue: an item added by hand is proposed for it, and so is a
barcode attached to an item that was picked from it. Both do nothing while the platform switch
`catalog_on` is off.

What is proposed is the item's name, its unit and a barcode: never a price, a quantity or a cost. It
reaches the catalogue only when an administrator approves it (`qarz.application.shared_catalog`).
"""

from collections.abc import Sequence
from uuid import UUID, uuid4

from qarz.application.errors import NotFound
from qarz.application.ports import PlatformSession, TenantSession
from qarz.domain import shared_catalog
from qarz.domain.names import normalize_name


async def switched_on(session: TenantSession | PlatformSession) -> bool:
    # Only the JSON value `true` turns it on: absent, null, "true" and 1 all mean off.
    return await session.platform_setting(shared_catalog.SWITCH) is True


async def require_on(session: TenantSession | PlatformSession) -> None:
    """Off is "no such route", to everyone, before the caller is asked who they are."""
    if not await switched_on(session):
        raise NotFound()


async def propose_item(session: TenantSession, *, item_id: UUID, name: str, unit: str, barcode: str | None) -> None:
    """Propose an item the shop has just added by hand, inside the transaction that added it.

    Does nothing while the catalogue is off. What is proposed is the name, the unit and the barcode.
    """
    if await switched_on(session):
        await session.suggest_shared_item(
            suggestion_id=uuid4(),
            item_id=item_id,
            name=name,
            name_norm=normalize_name(name),
            unit=unit,
            barcode=barcode,
            max_pending=shared_catalog.MAX_PENDING,
        )


async def propose_barcodes(session: TenantSession, item_id: UUID, codes: Sequence[str]) -> None:
    """Propose the barcodes a shop has attached to an item it picked from the catalogue.

    Does nothing while the catalogue is off, for an item the shop added itself, and for a code the
    catalogue already has.
    """
    if not codes or not await switched_on(session):
        return
    shared_id = await session.shared_link_of(item_id)
    if shared_id is None:
        return
    for code in codes:
        await session.suggest_shared_barcode(
            suggestion_id=uuid4(),
            item_id=item_id,
            shared_item_id=shared_id,
            barcode=code,
            max_pending=shared_catalog.MAX_PENDING,
        )

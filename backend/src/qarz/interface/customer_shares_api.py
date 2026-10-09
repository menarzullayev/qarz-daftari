"""HTTP routes for a customer's secret read-only link: the staff side, and the read behind the link.

Every route here depends on `switched_on` before anything else, so while the platform switch
`customer_links_on` is off each of them answers as a route that does not exist: to a member of staff, to
a stranger, and to someone who is not signed in, alike.

The read behind the link takes the token in a header, never in the address: an address is written to
the proxy's access log and may be kept by anything between the customer and the server.
"""

from collections.abc import Awaitable, Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, FastAPI, Header, Response
from pydantic import BaseModel, ConfigDict

from qarz.application.customer_shares import (
    CREATE_SHARE,
    READ_SHARE,
    READ_SHARE_CONTACT,
    REVOKE_SHARE,
    SET_SHARE_CONTACT,
    VIEW_SHARED_ACCOUNT,
    CustomerShareService,
)
from qarz.interface.answers import ShareContact, SharedAccount, ShareState
from qarz.interface.shops_api import IdempotencyKey

CurrentUser = Callable[..., Awaitable[UUID]]

PUBLIC_PATH = "/api/v1/customer-share"
TOKEN_HEADER = "X-Share-Token"  # noqa: S105 - the header's name, not a secret


class ShareContactChange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    # Null or an empty text clears it.
    phone: str | None


def add_customer_share_routes(app: FastAPI, service: CustomerShareService, current_user: CurrentUser) -> None:
    async def switched_on() -> None:
        await service.require_on()

    # A route's own dependencies are resolved before those of its parameters: the switch is asked before
    # the caller is, so an "off" answer is the same with and without a session.
    behind_switch = [Depends(switched_on)]
    user = Annotated[UUID, Depends(current_user)]
    share = "/api/v1/shops/{shop_id}/customers/{customer_id}/share"
    contact = "/api/v1/shops/{shop_id}/share-contact"

    @app.get(share, name=READ_SHARE.name, response_model=ShareState, dependencies=behind_switch)
    async def read_share(shop_id: UUID, customer_id: UUID, user_id: user) -> dict[str, Any]:
        return await service.state(user_id, shop_id, customer_id)

    @app.post(share, name=CREATE_SHARE.name, status_code=201, dependencies=behind_switch)
    async def create_share(
        shop_id: UUID, customer_id: UUID, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await service.create(user_id, shop_id, customer_id, idempotency_key)

    @app.delete(share, name=REVOKE_SHARE.name, dependencies=behind_switch)
    async def revoke_share(
        shop_id: UUID, customer_id: UUID, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await service.revoke(user_id, shop_id, customer_id, idempotency_key)

    @app.get(contact, name=READ_SHARE_CONTACT.name, response_model=ShareContact, dependencies=behind_switch)
    async def read_share_contact(shop_id: UUID, user_id: user) -> dict[str, Any]:
        return await service.contact(user_id, shop_id)

    @app.put(contact, name=SET_SHARE_CONTACT.name, dependencies=behind_switch)
    async def set_share_contact(
        shop_id: UUID, body: ShareContactChange, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await service.set_contact(user_id, shop_id, body.phone, idempotency_key)

    @app.get(
        PUBLIC_PATH,
        name=VIEW_SHARED_ACCOUNT.name,
        response_model=SharedAccount,
        response_model_exclude_unset=True,
        dependencies=behind_switch,
    )
    async def view_shared_account(
        response: Response, token: Annotated[str | None, Header(alias=TOKEN_HEADER)] = None
    ) -> dict[str, Any]:
        body = await service.view(token)
        # Never listed by a search engine. That it is never kept by a browser or anything in between is
        # said for every answer of the API (observability.py, and the proxy's headers-api.conf).
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        return body

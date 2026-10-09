"""HTTP routes of the administrator's side, under `/api/admin/v1` (technical specification, clients table).

Every route first resolves the caller through one of two dependencies: `entry` for the door, where the
second factor is enrolled and passed, and `admin` for everything behind it. Someone who is signed in but
is not an administrator gets the same answer as for a route that does not exist: same status, same body,
in the same default language. Request bodies are therefore read by the route itself, after that check,
and never by the framework before it.
"""

import logging
from collections.abc import Awaitable, Callable
from datetime import date
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, FastAPI, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from qarz.application.admin import (
    END_TRIAL,
    LIST_AUDIT,
    LIST_SHOPS,
    READ_SETTINGS,
    READ_SHOP,
    SET_PAID_THROUGH,
    SET_TRIAL,
    SUSPEND_SHOP,
    UNSUSPEND_SHOP,
    UPDATE_SETTINGS,
    AdminService,
)
from qarz.application.admin_access import (
    CLOSE_ADMIN_SESSION,
    ENROL_ADMIN,
    OPEN_ADMIN_SESSION,
    READ_ADMIN_AUTH,
    AdminAccess,
    SecondFactorInvalid,
    SecondFactorLocked,
)
from qarz.application.admin_ownership import REASSIGN_OWNER, AdminOwnershipService
from qarz.application.admin_receipts import (
    APPROVE_RECEIPT,
    LIST_RECEIPTS,
    READ_RECEIPT,
    REJECT_RECEIPT,
    AdminReceiptService,
)
from qarz.application.errors import NotFound, Unauthenticated, ValidationFailed
from qarz.interface.observability import SECURITY_BAD_SECOND_FACTOR, SECURITY_OWNER_REASSIGNED
from qarz.interface.shops_api import IdempotencyKey

ADMIN_COOKIE = "qd_admin"
BASE = "/api/admin/v1"

UserOf = Callable[[Request], Awaitable[UUID | None]]
LanguageOf = Callable[[UUID], Awaitable[str | None]]

log = logging.getLogger("qarz.admin")


class _Strict(BaseModel):
    # Unknown fields are rejected, so a typo is an error and not a silently ignored change.
    model_config = ConfigDict(extra="forbid", strict=True)


class CodeBody(_Strict):
    code: str = Field(pattern=r"^[0-9]{6}$")


class ReasonBody(_Strict):
    reason: str = Field(max_length=2000)


class TrialBody(ReasonBody):
    trial_ends: date


class PaidThroughBody(ReasonBody):
    paid_through: date


class OwnerBody(ReasonBody):
    # The person is named as the administrator's side names people: by Telegram identifier. They must
    # have started the bot, so that the service knows them.
    new_owner_tg_id: int = Field(gt=0, le=2**62)
    # Never optional: this change asks for the second factor again every time.
    code: str = Field(pattern=r"^[0-9]{6}$")


class ApproveBody(_Strict):
    # Absent or null approves the months the owner stated; a number corrects them (BR-27).
    months: int | None = None
    reason: str | None = Field(default=None, max_length=2000)


class SettingsPatch(_Strict):
    changes: dict[str, Any] = Field(max_length=16)
    code: str | None = Field(default=None, pattern=r"^[0-9]{6}$")
    reason: str | None = Field(default=None, max_length=2000)


async def _read[Model: BaseModel](request: Request, model: type[Model]) -> Model:
    try:
        return model.model_validate_json(await request.body())
    except ValidationError as error:
        fields = {".".join(str(part) for part in item["loc"]) or "_": item["msg"] for item in error.errors()}
        raise ValidationFailed(fields) from error


def add_admin_routes(
    app: FastAPI,
    access: AdminAccess,
    service: AdminService,
    user_of: UserOf,
    language_of: LanguageOf,
    count: Callable[[UUID], None] | None = None,
    receipts: AdminReceiptService | None = None,
    ownership: AdminOwnershipService | None = None,
) -> Callable[[Request], Awaitable[UUID]]:
    """Adds the routes and returns the dependency that lets only a full administrator through, for
    other modules that add routes to the administrator's side."""

    async def _signed_in(request: Request) -> UUID:
        user_id = await user_of(request)
        if user_id is None:
            raise Unauthenticated()
        request.state.user_id = user_id  # for the request's log line
        if count is not None:
            # The per-user rate limit, as on every other signed-in route, before anything is looked up.
            count(user_id)
        return user_id

    def _refused(request: Request, user_id: UUID) -> None:
        # Specification, error handling: an authorization failure is logged as a security event.
        log.warning("admin_route_refused user=%s method=%s path=%s", user_id, request.method, request.url.path)

    async def entry_user(request: Request) -> UUID:
        user_id = await _signed_in(request)
        try:
            await access.require_candidate(user_id)
        except NotFound:
            _refused(request, user_id)
            raise
        # Only now: a refusal above must read like an unknown route, which knows no caller's language.
        request.state.lang = await language_of(user_id) or "uz"
        return user_id

    async def admin_user(request: Request) -> UUID:
        user_id = await _signed_in(request)
        try:
            await access.require_admin(user_id, request.cookies.get(ADMIN_COOKIE))
        except NotFound:
            _refused(request, user_id)
            raise
        request.state.lang = await language_of(user_id) or "uz"
        return user_id

    entry = Annotated[UUID, Depends(entry_user)]
    admin = Annotated[UUID, Depends(admin_user)]

    # --- the door ---------------------------------------------------------------------------------------

    @app.get(BASE + "/auth", name=READ_ADMIN_AUTH.name)
    async def read_auth(request: Request, user_id: entry) -> dict[str, Any]:
        return await access.status(user_id, request.cookies.get(ADMIN_COOKIE))

    @app.post(BASE + "/auth/enrolment", name=ENROL_ADMIN.name, status_code=201)
    async def enrol(user_id: entry, idempotency_key: IdempotencyKey = None) -> dict[str, Any]:
        return await access.enrol(user_id, idempotency_key)

    @app.post(BASE + "/auth/session", name=OPEN_ADMIN_SESSION.name, status_code=201)
    async def open_session(request: Request, response: Response, user_id: entry) -> dict[str, Any]:
        body = await _read(request, CodeBody)
        try:
            issued = await access.open_session(user_id, body.code)
        except (SecondFactorInvalid, SecondFactorLocked):
            request.state.security_event = SECURITY_BAD_SECOND_FACTOR
            raise
        response.set_cookie(
            ADMIN_COOKIE,
            issued.token,
            expires=issued.expires_at,
            path="/api/admin",
            httponly=True,
            secure=True,
            samesite="strict",
        )
        return {"expires_at": issued.expires_at.isoformat()}

    @app.delete(BASE + "/auth/session", name=CLOSE_ADMIN_SESSION.name, status_code=204)
    async def close_session(response: Response, user_id: admin) -> None:
        await access.close_session(user_id)
        response.delete_cookie(ADMIN_COOKIE, path="/api/admin")

    # --- shops ------------------------------------------------------------------------------------------

    @app.get(BASE + "/shops", name=LIST_SHOPS.name)
    async def list_shops(
        user_id: admin, q: str | None = None, state: str | None = None, cursor: str | None = None, limit: int = 50
    ) -> dict[str, Any]:
        return await service.list_shops(user_id, query=q, state=state, cursor=cursor, limit=limit)

    @app.get(BASE + "/shops/{shop_id}", name=READ_SHOP.name)
    async def read_shop(shop_id: UUID, user_id: admin) -> dict[str, Any]:
        return await service.read_shop(user_id, shop_id)

    @app.post(BASE + "/shops/{shop_id}/trial", name=SET_TRIAL.name)
    async def set_trial(
        shop_id: UUID, request: Request, user_id: admin, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        body = await _read(request, TrialBody)
        return await service.set_trial(user_id, shop_id, body.trial_ends, body.reason, idempotency_key)

    @app.post(BASE + "/shops/{shop_id}/trial/end", name=END_TRIAL.name)
    async def end_trial(
        shop_id: UUID, request: Request, user_id: admin, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        body = await _read(request, ReasonBody)
        return await service.end_trial(user_id, shop_id, body.reason, idempotency_key)

    @app.post(BASE + "/shops/{shop_id}/paid-through", name=SET_PAID_THROUGH.name)
    async def set_paid_through(
        shop_id: UUID, request: Request, user_id: admin, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        body = await _read(request, PaidThroughBody)
        return await service.set_paid_through(user_id, shop_id, body.paid_through, body.reason, idempotency_key)

    @app.post(BASE + "/shops/{shop_id}/suspend", name=SUSPEND_SHOP.name)
    async def suspend(
        shop_id: UUID, request: Request, user_id: admin, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        body = await _read(request, ReasonBody)
        return await service.suspend(user_id, shop_id, body.reason, idempotency_key)

    @app.post(BASE + "/shops/{shop_id}/unsuspend", name=UNSUSPEND_SHOP.name)
    async def unsuspend(
        shop_id: UUID, request: Request, user_id: admin, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        body = await _read(request, ReasonBody)
        return await service.unsuspend(user_id, shop_id, body.reason, idempotency_key)

    if ownership is not None:
        owners = ownership

        @app.post(BASE + "/shops/{shop_id}/owner", name=REASSIGN_OWNER.name)
        async def reassign_owner(
            shop_id: UUID, request: Request, user_id: admin, idempotency_key: IdempotencyKey = None
        ) -> dict[str, Any]:
            body = await _read(request, OwnerBody)
            try:
                answer = await owners.reassign(
                    user_id,
                    shop_id,
                    new_owner_tg_id=body.new_owner_tg_id,
                    code=body.code,
                    reason=body.reason,
                    request_key=idempotency_key,
                )
            except (SecondFactorInvalid, SecondFactorLocked):
                request.state.security_event = SECURITY_BAD_SECOND_FACTOR
                raise
            request.state.security_event = SECURITY_OWNER_REASSIGNED
            return answer

    # --- subscription receipts --------------------------------------------------------------------------

    if receipts is not None:
        held = receipts

        @app.get(BASE + "/receipts", name=LIST_RECEIPTS.name)
        async def list_receipts(
            user_id: admin, status: str | None = None, cursor: str | None = None, limit: int = 50
        ) -> dict[str, Any]:
            return await held.list_receipts(user_id, status=status, cursor=cursor, limit=limit)

        @app.get(BASE + "/receipts/{receipt_id}", name=READ_RECEIPT.name)
        async def read_receipt(receipt_id: UUID, user_id: admin) -> dict[str, Any]:
            return await held.read_receipt(user_id, receipt_id)

        @app.post(BASE + "/receipts/{receipt_id}/approve", name=APPROVE_RECEIPT.name)
        async def approve_receipt(
            receipt_id: UUID, request: Request, user_id: admin, idempotency_key: IdempotencyKey = None
        ) -> dict[str, Any]:
            raw = await request.body()
            body = ApproveBody() if not raw.strip() else await _read(request, ApproveBody)
            return await held.approve(user_id, receipt_id, body.months, body.reason, idempotency_key)

        @app.post(BASE + "/receipts/{receipt_id}/reject", name=REJECT_RECEIPT.name)
        async def reject_receipt(
            receipt_id: UUID, request: Request, user_id: admin, idempotency_key: IdempotencyKey = None
        ) -> dict[str, Any]:
            body = await _read(request, ReasonBody)
            return await held.reject(user_id, receipt_id, body.reason, idempotency_key)

    # --- platform settings and the audit ----------------------------------------------------------------

    @app.get(BASE + "/settings", name=READ_SETTINGS.name)
    async def read_settings(user_id: admin, free_plan_customers: str | None = None) -> dict[str, Any]:
        # The number is read by the service, and only while the free plan is on: with it off the
        # question is not looked at, whatever it holds.
        return await service.read_settings(user_id, free_plan_customers)

    @app.patch(BASE + "/settings", name=UPDATE_SETTINGS.name)
    async def update_settings(
        request: Request, user_id: admin, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        body = await _read(request, SettingsPatch)
        try:
            return await service.update_settings(
                user_id, body.changes, code=body.code, reason=body.reason, request_key=idempotency_key
            )
        except (SecondFactorInvalid, SecondFactorLocked):
            request.state.security_event = SECURITY_BAD_SECOND_FACTOR
            raise

    @app.get(BASE + "/audit", name=LIST_AUDIT.name)
    async def list_audit(
        user_id: admin,
        shop_id: UUID | None = None,
        action: str | None = None,
        admin_id: UUID | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        return await service.list_audit(
            user_id, shop_id=shop_id, action=action, by=admin_id, cursor=cursor, limit=limit
        )

    return admin_user

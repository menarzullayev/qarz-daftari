"""HTTP application factory (technical specification, API contract)."""

import hmac
import time
from collections.abc import Awaitable, Callable, Container
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from fastapi import FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from qarz.application.account import AccountService, ActivityService
from qarz.application.admin import AdminService
from qarz.application.admin_access import AdminAccess
from qarz.application.admin_ownership import AdminOwnershipService
from qarz.application.admin_receipts import AdminReceiptService
from qarz.application.auth import AuthService
from qarz.application.cash_book import CashBookService
from qarz.application.catalog import CatalogService
from qarz.application.chat import ChatService
from qarz.application.credit import CreditService
from qarz.application.customer_account import CustomerAccountService
from qarz.application.customer_shares import CustomerShareService
from qarz.application.customers import CustomerService
from qarz.application.date_requests import DateRequestService
from qarz.application.disputes import DisputeService
from qarz.application.errors import AppError, Unauthenticated
from qarz.application.exports import ExportService
from qarz.application.files import FileService
from qarz.application.imports import ImportService
from qarz.application.ledger_service import LedgerService
from qarz.application.links import LinkService
from qarz.application.online_payment import OnlinePaymentService, PaymentKeys
from qarz.application.ownership import OwnershipService
from qarz.application.payment_notices import PaymentNoticeService
from qarz.application.permissions import PermissionService
from qarz.application.ports import FileStore, Storage, TelegramChatMembers, TelegramFiles
from qarz.application.reminders import ReminderService
from qarz.application.reports import ReportService
from qarz.application.shop_deletion import ShopDeletionService
from qarz.application.shops import ShopService
from qarz.application.staff import StaffService
from qarz.application.stock import StockService
from qarz.application.stock_documents import DocumentService
from qarz.application.subscription import SubscriptionService
from qarz.application.subscription_receipts import SubscriptionReceiptService
from qarz.application.suppliers import SupplierService
from qarz.application.support_access import SupportAccessService
from qarz.application.telegram_updates import UpdateProcessor
from qarz.interface.account_api import add_account_routes
from qarz.interface.admin_api import add_admin_routes
from qarz.interface.auth_api import SessionAuthenticator, add_auth_routes
from qarz.interface.body_limit import BodyLimit
from qarz.interface.cash_api import add_cash_routes
from qarz.interface.catalog_api import add_catalog_routes
from qarz.interface.credit_api import add_credit_routes
from qarz.interface.customer_shares_api import add_customer_share_routes
from qarz.interface.customers_api import add_customer_routes
from qarz.interface.date_requests_api import add_date_request_routes
from qarz.interface.disputes_api import add_dispute_routes
from qarz.interface.errors import app_error_handler, error_response
from qarz.interface.exports_api import add_export_routes
from qarz.interface.imports_api import IMPORT_UPLOAD, add_import_routes
from qarz.interface.links_api import add_link_routes
from qarz.interface.me_api import add_me_routes
from qarz.interface.observability import Metrics, Observe
from qarz.interface.online_payment_api import add_online_order_routes, add_provider_routes
from qarz.interface.payment_notices_api import RECEIPT_UPLOAD, add_file_route, add_payment_notice_routes
from qarz.interface.permissions_api import add_permission_routes
from qarz.interface.rate_limit import RateLimiter, RateLimits
from qarz.interface.reminders_api import add_reminder_routes
from qarz.interface.reports_api import add_report_routes
from qarz.interface.shop_deletion_api import add_shop_deletion_routes
from qarz.interface.shops_api import add_shop_routes
from qarz.interface.staff_api import add_staff_routes
from qarz.interface.stock_api import add_stock_routes
from qarz.interface.subscription_api import add_subscription_routes
from qarz.interface.subscription_receipts_api import SUBSCRIPTION_RECEIPT_UPLOAD, add_subscription_receipt_routes
from qarz.interface.support_api import add_admin_support_routes, add_owner_support_routes
from qarz.interface.telegram_webhook import add_webhook_route

HealthCheck = Callable[[], Awaitable[bool]]


class Authenticator(Protocol):
    """Resolves a request to the signed-in user."""

    async def user_id(self, request: Request) -> UUID | None: ...


# What a caller can be answered without being a member of the shop: not found, and a malformed request.
_STRANGER_ANSWERS = frozenset({404, 422})


def _shop_in_path(request: Request) -> UUID | None:
    raw = request.path_params.get("shop_id")
    try:
        return None if raw is None else UUID(str(raw))
    except ValueError:
        return None


def create_app(
    database_reachable: HealthCheck,
    storage: Storage | None = None,
    *,
    auth: AuthService | None = None,
    admin: AdminAccess | None = None,
    admin_storage: Storage | None = None,
    authenticator: Authenticator | None = None,
    webhook_secret: str | None = None,
    now: Callable[[], datetime] | None = None,
    file_store: FileStore | None = None,
    telegram_files: TelegramFiles | None = None,
    telegram_members: TelegramChatMembers | None = None,
    payment_keys: PaymentKeys | None = None,
    rate_limits: RateLimits | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    metrics_token: str | None = None,
    secrets_key: str | None = None,
    previous_secrets_key: str | None = None,
) -> FastAPI:
    """Build the application.

    With only a health check it serves `/healthz`. With storage and an auth service it serves the API,
    authenticating through Telegram-backed sessions; `authenticator` replaces that only in tests, and `now`
    replaces the clock of the ledger only in tests. `rate_limits` are applied to signed-in callers; the
    deployed application always has them, and most tests leave them out. Without a `file_store`
    receipts are refused; without `telegram_files` a receipt sent to the bot cannot be fetched; without
    `telegram_members` nobody counts as a Telegram administrator of the review group (DEC-064). The
    administrator's side is served only when `admin` is given, which production does only with an
    allow-list and the server secret. It works through `admin_storage`, a connection as the
    administrators' own database role: `storage` is the ordinary role's and cannot reach their tables.
    """
    if admin is not None and admin_storage is None:
        raise ValueError("the administrators' side needs its own storage")
    app = FastAPI(title="Qarz Daftari", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/healthz")
    async def healthz(response: Response) -> dict[str, str]:
        # Reveals only up or down; no versions, hosts, or error text.
        try:
            ok = await database_reachable()
        except Exception:
            ok = False
        if not ok:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return {"status": "down"}
        return {"status": "ok"}

    # An oversized body is refused before any route, handler or sign-in sees it.
    app.add_middleware(BodyLimit, allowances=(RECEIPT_UPLOAD, IMPORT_UPLOAD, SUBSCRIPTION_RECEIPT_UPLOAD))
    app.add_exception_handler(AppError, app_error_handler)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = {".".join(str(part) for part in error["loc"][1:]) or "_": error["msg"] for error in exc.errors()}
        # A malformed identifier in the path must look like any other missing record.
        if any(error["loc"][0] == "path" for error in exc.errors()):
            return error_response("NOT_FOUND", getattr(request.state, "lang", "uz"))
        return error_response("VALIDATION", getattr(request.state, "lang", "uz"), fields)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = "NOT_FOUND" if exc.status_code in (404, 405) else "ERROR"
        response = error_response(code, getattr(request.state, "lang", "uz"))
        response.status_code = 404 if code == "NOT_FOUND" else exc.status_code
        return response

    files = (
        None
        if storage is None
        else FileService(storage, file_store, link_secret=secrets_key, previous_link_secret=previous_secrets_key)
    )
    # Who is told that a subscription receipt waits: the administrators on the allow-list.
    reviewers: Container[int] = () if admin is None else admin.allowed_tg_ids
    if files is not None:
        # Served whoever asks: a link is given only after authorization and works for five minutes.
        add_file_route(app, files, now or (lambda: datetime.now(UTC)))
    payments = None if storage is None else OnlinePaymentService(storage, payment_keys or PaymentKeys(), now)
    if payments is not None:
        # Served whatever the configuration, so that a provider is always answered: "disabled" until
        # the platform switch is on and that provider's key is set (ADR-019).
        add_provider_routes(app, payments)

    if storage is not None and auth is not None and payments is not None and files is not None:
        resolver: Authenticator = authenticator or SessionAuthenticator(auth)
        limiter = None if rate_limits is None else RateLimiter(rate_limits, monotonic)

        async def current_user(request: Request) -> UUID:
            user_id = await resolver.user_id(request)
            if user_id is None:
                raise Unauthenticated()
            request.state.user_id = user_id  # for the request's log line
            if limiter is not None:
                shop_id = _shop_in_path(request)
                # Before anything else is done for the request, so that a flood costs little.
                limiter.check(user_id, shop_id)
                request.state.counted_for = (user_id, shop_id)
            request.state.lang = await storage.user_language(user_id) or "uz"
            return user_id

        if limiter is not None:
            counted = limiter

            @app.middleware("http")
            async def count_for_the_shop(
                request: Request, call_next: Callable[[Request], Awaitable[Response]]
            ) -> Response:
                response = await call_next(request)
                user_id, shop_id = getattr(request.state, "counted_for", (None, None))
                # Only an answer given to a member counts against the shop. A stranger gets 404, or 422 when
                # the request is malformed, which is found before anyone is asked who they are.
                if user_id is not None and shop_id is not None and response.status_code not in _STRANGER_ANSWERS:
                    counted.answered(user_id, shop_id)
                return response

        add_auth_routes(app, auth, current_user, None if admin is None else admin.end_sessions_of)
        add_shop_routes(app, ShopService(storage), current_user)
        add_staff_routes(app, StaffService(storage), current_user)
        add_permission_routes(app, PermissionService(storage), current_user)
        add_link_routes(app, LinkService(storage, now), current_user)
        add_customer_share_routes(app, CustomerShareService(storage, now), current_user)
        add_cash_routes(app, CashBookService(storage, now), current_user)
        add_me_routes(app, CustomerAccountService(storage, now), current_user)
        add_shop_deletion_routes(app, ShopDeletionService(storage, now), current_user)
        add_subscription_routes(app, SubscriptionService(storage, now), current_user)
        add_online_order_routes(app, payments, current_user)
        add_subscription_receipt_routes(
            app, SubscriptionReceiptService(storage, files, now, admin_tg_ids=reviewers), current_user
        )
        add_credit_routes(app, CreditService(storage, now), current_user)
        add_reminder_routes(app, ReminderService(storage, now), current_user)
        add_owner_support_routes(app, SupportAccessService(storage, now), current_user)
        add_report_routes(app, ReportService(storage, now), current_user)
        add_dispute_routes(app, DisputeService(storage, now), current_user)
        add_payment_notice_routes(app, PaymentNoticeService(storage, files, now), current_user)
        add_export_routes(app, ExportService(storage, files, now), current_user)
        add_date_request_routes(app, DateRequestService(storage, now), current_user)
        add_import_routes(app, ImportService(storage, files, now), current_user)
        add_customer_routes(app, CustomerService(storage, now), LedgerService(storage, now), current_user)
        add_catalog_routes(app, CatalogService(storage, now), current_user)
        add_stock_routes(
            app,
            StockService(storage, now),
            DocumentService(storage, now),
            SupplierService(storage, now),
            current_user,
        )
        add_account_routes(
            app, AccountService(storage), ActivityService(storage), OwnershipService(storage), current_user
        )

        if admin is not None and admin_storage is not None:
            # Who the caller is, and their language, is the ordinary side's knowledge; everything the
            # administrator then does goes through the administrators' role.
            admin_user = add_admin_routes(
                app,
                admin,
                AdminService(admin_storage, admin, now),
                resolver.user_id,
                storage.user_language,
                None if limiter is None else (lambda user_id: counted.check(user_id, None)),
                AdminReceiptService(admin_storage, files, now),
                AdminOwnershipService(admin_storage, admin, now),
            )
            add_admin_support_routes(app, SupportAccessService(admin_storage, now), admin_user)

    if webhook_secret is not None and storage is not None:
        chat = ChatService(
            storage,
            ShopService(storage, now),
            StaffService(storage, now),
            now,
            files,
            reviewers,
            admin_storage=admin_storage,
        )
        add_webhook_route(app, UpdateProcessor(storage, chat, telegram_files, telegram_members), webhook_secret)

    metrics = Metrics()
    if metrics_token is not None:
        if len(metrics_token) < 16:
            raise ValueError("the metrics token is too short")
        expected = f"Bearer {metrics_token}".encode()

        @app.get("/metrics", include_in_schema=False)
        async def read_metrics(request: Request) -> Response:
            # For the monitoring system on the same host only; without the token it does not exist.
            given = request.headers.get("authorization", "").encode("utf-8")
            if not hmac.compare_digest(given, expected):
                return error_response("NOT_FOUND", "uz")
            gauges: dict[str, dict[str, float]] = {}
            if storage is not None:
                async with storage.platform() as session:
                    gauges = await session.health_figures()
            return Response(metrics.render(gauges), media_type="text/plain; version=0.0.4")

    # Added last, so it is outermost: every request is identified, measured and logged, whatever
    # answers it.
    app.add_middleware(Observe, metrics=metrics)
    return app

"""Paying the subscription through Payme and Click (REQ-056, ADR-019). Built and switched off.

Nothing here works until an administrator turns the platform switch `online_pay_on` on and the provider's
key is configured: until then an owner cannot make an order and a provider is answered "disabled".

An owner makes an order for a number of months at the price of that moment. The provider then tells us,
in its own protocol, that the money is being taken, was taken, or was not. When it was taken the shop is
paid through a later date by the same rule as a transfer an administrator approves (BR-27).
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

from qarz.application import idempotency
from qarz.application.chat_texts import day, money, say
from qarz.application.errors import AppError, ValidationFailed
from qarz.application.operations import operation
from qarz.application.ports import OnlinePayment, Storage, TenantSession
from qarz.application.shops import require_member
from qarz.application.subscription import PRICE
from qarz.domain import online_payment as rules
from qarz.domain.access import Capability
from qarz.domain.online_payment import CANCELLED, CLICK, CREATED, PAID, PAYME, PENDING
from qarz.domain.promise import tashkent_date
from qarz.domain.subscription import DEFAULT_PRICE_UZS, MAX_MONTHS, SUSPENDED, extend_paid_through

CREATE_ONLINE_ORDER = operation("shop.subscription.online_order.create", Capability.ADMINISTER_SHOP)

SWITCH = "online_pay_on"


class OnlinePayOff(AppError):
    code = "ONLINE_PAY_OFF"


@dataclass(frozen=True)
class PaymentKeys:
    """What the providers gave us. Empty means that provider is not configured."""

    payme_merchant_id: str = ""
    payme_key: str = ""
    click_service_id: str = ""
    click_merchant_id: str = ""
    click_key: str = ""

    @property
    def payme(self) -> bool:
        return bool(self.payme_merchant_id and self.payme_key)

    @property
    def click(self) -> bool:
        return bool(self.click_service_id and self.click_merchant_id and self.click_key)


def _ms(moment: datetime | None) -> int:
    return 0 if moment is None else int(moment.timestamp() * 1000)


def _payme_error(code: int, data: str | None = None) -> dict[str, Any]:
    text = _PAYME_MESSAGES[code]
    error: dict[str, Any] = {"code": code, "message": {"uz": text[0], "ru": text[1], "en": text[2]}}
    if data is not None:
        error["data"] = data
    return {"error": error}


_PAYME_MESSAGES = {
    rules.PAYME_BAD_JSON: ("So'rov o'qilmadi", "Запрос не разобран", "Could not parse the request"),
    rules.PAYME_BAD_REQUEST: ("So'rov noto'g'ri", "Неверный запрос", "Invalid request"),
    rules.PAYME_NO_METHOD: ("Usul topilmadi", "Метод не найден", "Method not found"),
    rules.PAYME_NO_ACCESS: ("Ruxsat yo'q", "Недостаточно прав", "Insufficient privileges"),
    rules.PAYME_WRONG_AMOUNT: ("Summa noto'g'ri", "Неверная сумма", "Wrong amount"),
    rules.PAYME_NO_TRANSACTION: ("Tranzaksiya topilmadi", "Транзакция не найдена", "Transaction not found"),
    rules.PAYME_CANNOT_CANCEL: (
        "To'lov bajarilgan, bekor qilib bo'lmaydi",
        "Оплата выполнена, отменить нельзя",
        "The payment is done and cannot be cancelled",
    ),
    rules.PAYME_CANNOT_PERFORM: ("Amalni bajarib bo'lmaydi", "Невозможно выполнить операцию", "Cannot do this"),
    rules.PAYME_NO_ORDER: ("Buyurtma topilmadi", "Заказ не найден", "Order not found"),
    rules.PAYME_ORDER_BUSY: (
        "Buyurtma uchun to'lov allaqachon boshlangan",
        "Оплата этого заказа уже начата",
        "This order is already being paid",
    ),
    rules.PAYME_SHOP_BLOCKED: ("Do'kon to'lay olmaydi", "Магазин не может платить", "The shop cannot pay"),
}


def _uuid(value: Any) -> UUID | None:
    if not isinstance(value, str):
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


def _integer(value: Any) -> int | None:
    # bool is a subclass of int in Python; `true` is not a number of tiyin.
    return value if isinstance(value, int) and not isinstance(value, bool) else None


class OnlinePaymentService:
    def __init__(
        self, storage: Storage, keys: PaymentKeys | None = None, now: Callable[[], datetime] | None = None
    ) -> None:
        self._storage = storage
        self._keys = keys or PaymentKeys()
        self._now = now or (lambda: datetime.now(UTC))

    def _today(self) -> date:
        return tashkent_date(self._now())

    async def _switched_on(self) -> bool:
        async with self._storage.platform() as session:
            # Only the JSON value `true` turns it on: absent, null, "true" and 1 all mean off.
            return await session.platform_setting(SWITCH) is True

    async def enabled(self, provider: str) -> bool:
        """Whether the provider's calls are answered at all: its key is set and the switch is on."""
        configured = self._keys.payme if provider == PAYME else self._keys.click
        return configured and await self._switched_on()

    # --- the owner makes an order ---------------------------------------------------------------

    async def create_order(self, user_id: UUID, shop_id: UUID, months: int, request_key: str | None) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            # Like reading the subscription, this is open in every mode: paying is how a shop leaves one.
            await require_member(session, user_id, CREATE_ONLINE_ORDER)
            key = idempotency.validate_key(request_key)
            if not 1 <= months <= MAX_MONTHS:
                raise ValidationFailed({"months": f"must be between 1 and {MAX_MONTHS}"})
            keys = self._keys
            if not (keys.payme or keys.click) or await session.platform_setting(SWITCH) is not True:
                raise OnlinePayOff()

            async def apply() -> dict[str, Any]:
                price = await session.platform_setting(PRICE)
                if not isinstance(price, int) or isinstance(price, bool) or price <= 0:
                    price = DEFAULT_PRICE_UZS
                order = await session.create_online_payment(
                    order_id=uuid4(), months=months, amount=rules.order_amount(price, months)
                )
                urls: dict[str, str] = {}
                if keys.payme:
                    urls[PAYME] = rules.payme_checkout_url(keys.payme_merchant_id, str(order.id), order.amount)
                if keys.click:
                    urls[CLICK] = rules.click_pay_url(
                        keys.click_service_id, keys.click_merchant_id, str(order.id), order.amount
                    )
                return {"id": str(order.id), "months": order.months, "amount": order.amount, "pay_urls": urls}

            return await idempotency.run_once(
                session,
                key=key,
                operation=CREATE_ONLINE_ORDER.name,
                user_id=user_id,
                request={"months": months},
                action=apply,
            )

    # --- what both providers lead to ------------------------------------------------------------

    async def _shop_of_order(self, order_id: UUID | None) -> UUID | None:
        if order_id is None:
            return None
        async with self._storage.platform() as session:
            return await session.online_payment_shop(order_id)

    async def _shop_of_txn(self, provider: str, txn: str) -> UUID | None:
        async with self._storage.platform() as session:
            return await session.online_payment_shop_by_txn(provider, txn)

    async def _pay(self, session: TenantSession, shop_id: UUID, order: OnlinePayment) -> datetime:
        """The money was taken: the order is paid and the shop is paid through a later date."""
        now = self._now()
        stored = await session.subscription_locked()
        paid_through = extend_paid_through(None if stored is None else stored[2], self._today(), order.months)
        await session.pay_subscription(paid_through, now)
        await session.finish_online_payment(order.id, now)
        await session.record_system_activity(action="subscription.paid_online", subject_id=order.id)
        settings = await session.shop_settings()
        for tg_id, lang in await session.staff_recipients(["owner"]):
            text = say(
                lang,
                "sub_paid_online",
                shop="" if settings is None else settings.name,
                amount=money(lang, order.amount),
                date=day(paid_through),
            )
            await session.enqueue(
                recipient=str(tg_id), payload={"text": text}, dedupe_key=f"sub:paid_online:{order.id}:{tg_id}"
            )
        return now

    # --- Payme ----------------------------------------------------------------------------------

    async def payme(self, authorization: str | None, body: Any) -> dict[str, Any] | None:
        """Answer one Merchant API call. None means the adapter is switched off."""
        if not await self.enabled(PAYME):
            return None
        request_id = body.get("id") if isinstance(body, dict) else None
        answer = await self._payme_answer(authorization, body)
        return {"jsonrpc": "2.0", "id": request_id, **answer}

    async def _payme_answer(self, authorization: str | None, body: Any) -> dict[str, Any]:
        if not rules.payme_authorized(authorization, self._keys.payme_key):
            return _payme_error(rules.PAYME_NO_ACCESS)
        if not isinstance(body, dict):
            return _payme_error(rules.PAYME_BAD_JSON)
        method, params = body.get("method"), body.get("params")
        if not isinstance(method, str) or not isinstance(params, dict):
            return _payme_error(rules.PAYME_BAD_REQUEST)
        handler = {
            "CheckPerformTransaction": self._payme_check_perform,
            "CreateTransaction": self._payme_create,
            "PerformTransaction": self._payme_perform,
            "CancelTransaction": self._payme_cancel,
            "CheckTransaction": self._payme_check,
            "GetStatement": self._payme_statement,
        }.get(method)
        if handler is None:
            return _payme_error(rules.PAYME_NO_METHOD, method)
        return await handler(params)

    @staticmethod
    def _payme_order_id(params: dict[str, Any]) -> UUID | None:
        account = params.get("account")
        return _uuid(account.get("order_id")) if isinstance(account, dict) else None

    @staticmethod
    async def _payable(session: TenantSession, order: OnlinePayment | None, amount: Any) -> dict[str, Any] | None:
        """Why a new transaction may not start on the order, in Payme's terms; None when it may."""
        if order is None:
            return _payme_error(rules.PAYME_NO_ORDER, "order_id")
        if order.state != CREATED:
            return _payme_error(rules.PAYME_ORDER_BUSY, "order_id")
        if amount != rules.tiyin(order.amount):
            return _payme_error(rules.PAYME_WRONG_AMOUNT)
        stored = await session.subscription()
        if stored is None or stored[0] == SUSPENDED:
            return _payme_error(rules.PAYME_SHOP_BLOCKED, "order_id")
        return None

    async def _payme_check_perform(self, params: dict[str, Any]) -> dict[str, Any]:
        amount = _integer(params.get("amount"))
        if amount is None or not isinstance(params.get("account"), dict):
            return _payme_error(rules.PAYME_BAD_REQUEST)
        order_id = self._payme_order_id(params)
        shop_id = await self._shop_of_order(order_id)
        if shop_id is None or order_id is None:
            return _payme_error(rules.PAYME_NO_ORDER, "order_id")
        async with self._storage.tenant(shop_id) as session:
            refusal = await self._payable(session, await session.online_payment(order_id), amount)
        return refusal or {"result": {"allow": True}}

    async def _payme_create(self, params: dict[str, Any]) -> dict[str, Any]:
        txn, time_ms, amount = params.get("id"), _integer(params.get("time")), _integer(params.get("amount"))
        if not isinstance(txn, str) or not txn or time_ms is None or amount is None:
            return _payme_error(rules.PAYME_BAD_REQUEST)
        order_id = self._payme_order_id(params)
        shop_id = await self._shop_of_txn(PAYME, txn) or await self._shop_of_order(order_id)
        if shop_id is None:
            return _payme_error(rules.PAYME_NO_ORDER, "order_id")
        now = self._now()
        async with self._storage.tenant(shop_id) as session:
            order = await session.online_payment_by_txn(PAYME, txn)
            if order is None and order_id is not None:
                # Locked, so that two transactions cannot both start on one order.
                order = await session.online_payment(order_id, for_update=True)
            if order is not None and (order.provider, order.provider_txn) == (PAYME, txn):
                # Payme repeats a call it got no answer to: the same transaction gets the same answer.
                if order.state != PENDING:
                    return _payme_error(rules.PAYME_CANNOT_PERFORM)
                if rules.payme_timed_out(_ms(order.started_at), _ms(now)):
                    await session.cancel_online_payment(order.id, reason=rules.PAYME_REASON_TIMEOUT, now=now)
                    return _payme_error(rules.PAYME_CANNOT_PERFORM)
                return {"result": self._payme_created(order)}
            refusal = await self._payable(session, order, amount)
            if refusal is not None or order is None:
                return refusal or _payme_error(rules.PAYME_NO_ORDER, "order_id")
            if rules.payme_timed_out(time_ms, _ms(now)):
                return _payme_error(rules.PAYME_CANNOT_PERFORM)
            await session.start_online_payment(order.id, provider=PAYME, txn=txn, provider_time=time_ms, now=now)
            started = await session.online_payment(order.id)
            assert started is not None
            return {"result": self._payme_created(started)}

    @staticmethod
    def _payme_created(order: OnlinePayment) -> dict[str, Any]:
        return {"create_time": _ms(order.started_at), "transaction": str(order.id), "state": 1}

    async def _payme_txn(self, params: dict[str, Any]) -> tuple[UUID, str] | dict[str, Any]:
        txn = params.get("id")
        if not isinstance(txn, str) or not txn:
            return _payme_error(rules.PAYME_BAD_REQUEST)
        shop_id = await self._shop_of_txn(PAYME, txn)
        return _payme_error(rules.PAYME_NO_TRANSACTION) if shop_id is None else (shop_id, txn)

    async def _payme_perform(self, params: dict[str, Any]) -> dict[str, Any]:
        found = await self._payme_txn(params)
        if isinstance(found, dict):
            return found
        shop_id, txn = found
        async with self._storage.tenant(shop_id) as session:
            order = await session.online_payment_by_txn(PAYME, txn)
            if order is None:
                return _payme_error(rules.PAYME_NO_TRANSACTION)
            if order.state == PAID:
                return {"result": {"transaction": str(order.id), "perform_time": _ms(order.paid_at), "state": 2}}
            if order.state != PENDING:
                return _payme_error(rules.PAYME_CANNOT_PERFORM)
            now = self._now()
            if rules.payme_timed_out(_ms(order.started_at), _ms(now)):
                await session.cancel_online_payment(order.id, reason=rules.PAYME_REASON_TIMEOUT, now=now)
                return _payme_error(rules.PAYME_CANNOT_PERFORM)
            paid_at = await self._pay(session, shop_id, order)
            return {"result": {"transaction": str(order.id), "perform_time": _ms(paid_at), "state": 2}}

    async def _payme_cancel(self, params: dict[str, Any]) -> dict[str, Any]:
        reason = _integer(params.get("reason"))
        if reason is None or not -32768 <= reason <= 32767:
            return _payme_error(rules.PAYME_BAD_REQUEST)
        found = await self._payme_txn(params)
        if isinstance(found, dict):
            return found
        shop_id, txn = found
        async with self._storage.tenant(shop_id) as session:
            order = await session.online_payment_by_txn(PAYME, txn)
            if order is None:
                return _payme_error(rules.PAYME_NO_TRANSACTION)
            if order.state == PAID:
                # The paid period was already given. Taking it back is an administrator's decision, not
                # something a provider's call may do.
                return _payme_error(rules.PAYME_CANNOT_CANCEL)
            cancelled_at = order.cancelled_at
            if order.state == PENDING:
                cancelled_at = self._now()
                await session.cancel_online_payment(order.id, reason=reason, now=cancelled_at)
            return {"result": {"transaction": str(order.id), "cancel_time": _ms(cancelled_at), "state": -1}}

    @staticmethod
    def _payme_view(order: OnlinePayment) -> dict[str, Any]:
        return {
            "create_time": _ms(order.started_at),
            "perform_time": _ms(order.paid_at),
            "cancel_time": _ms(order.cancelled_at),
            "transaction": str(order.id),
            "state": rules.PAYME_STATES[order.state],
            "reason": order.cancel_reason,
        }

    async def _payme_check(self, params: dict[str, Any]) -> dict[str, Any]:
        found = await self._payme_txn(params)
        if isinstance(found, dict):
            return found
        shop_id, txn = found
        async with self._storage.tenant(shop_id) as session:
            order = await session.online_payment_by_txn(PAYME, txn)
        return _payme_error(rules.PAYME_NO_TRANSACTION) if order is None else {"result": self._payme_view(order)}

    async def _payme_statement(self, params: dict[str, Any]) -> dict[str, Any]:
        start, end = _integer(params.get("from")), _integer(params.get("to"))
        if start is None or end is None:
            return _payme_error(rules.PAYME_BAD_REQUEST)
        async with self._storage.platform() as session:
            orders = await session.payme_statement(start, end)
        return {
            "result": {
                "transactions": [
                    {
                        "id": order.provider_txn,
                        "time": order.provider_time,
                        "amount": rules.tiyin(order.amount),
                        "account": {"order_id": str(order.id)},
                        **self._payme_view(order),
                    }
                    for order in orders
                ]
            }
        }

    # --- Click ----------------------------------------------------------------------------------

    async def click(self, form: Mapping[str, str]) -> dict[str, Any] | None:
        """Answer one Shop API call (prepare or complete). None means the adapter is switched off."""
        if not await self.enabled(CLICK):
            return None
        answer = {
            "click_trans_id": form.get("click_trans_id", ""),
            "merchant_trans_id": form.get("merchant_trans_id", ""),
        }
        code, extra = await self._click_answer(form)
        return {**answer, **extra, "error": code, "error_note": rules.CLICK_NOTES[code]}

    async def _click_answer(self, form: Mapping[str, str]) -> tuple[int, dict[str, Any]]:
        needed = ("click_trans_id", "service_id", "merchant_trans_id", "amount", "action", "sign_time", "sign_string")
        if any(not form.get(name) for name in needed):
            return rules.CLICK_BAD_REQUEST, {}
        action, txn = form["action"], form["click_trans_id"]
        expected = rules.click_sign(
            click_trans_id=txn,
            service_id=form["service_id"],
            secret=self._keys.click_key,
            merchant_trans_id=form["merchant_trans_id"],
            merchant_prepare_id=form.get("merchant_prepare_id"),
            amount=form["amount"],
            action=action,
            sign_time=form["sign_time"],
        )
        if not rules.click_signed(form["sign_string"], expected, self._keys.click_key):
            return rules.CLICK_BAD_SIGN, {}
        if form["service_id"] != self._keys.click_service_id:
            return rules.CLICK_BAD_REQUEST, {}
        if action not in (rules.CLICK_PREPARE, rules.CLICK_COMPLETE):
            return rules.CLICK_NO_ACTION, {}
        order_id = _uuid(form["merchant_trans_id"])
        shop_id = await self._shop_of_order(order_id)
        if shop_id is None or order_id is None:
            return rules.CLICK_NO_ORDER, {}
        async with self._storage.tenant(shop_id) as session:
            order = await session.online_payment(order_id, for_update=True)
            if order is None:
                return rules.CLICK_NO_ORDER, {}
            if not rules.click_amount_matches(form["amount"], order.amount):
                return rules.CLICK_WRONG_AMOUNT, {}
            if action == rules.CLICK_PREPARE:
                return await self._click_prepare(session, order, txn)
            return await self._click_complete(session, shop_id, order, txn, form)

    async def _click_prepare(
        self, session: TenantSession, order: OnlinePayment, txn: str
    ) -> tuple[int, dict[str, Any]]:
        mine = (order.provider, order.provider_txn) == (CLICK, txn)
        if order.state == PAID:
            return rules.CLICK_ALREADY_PAID, {}
        if order.state == CANCELLED:
            return rules.CLICK_CANCELLED, {}
        if order.state == PENDING:
            # Click repeats a call it got no answer to; another transaction on the same order is refused.
            return (
                (rules.CLICK_OK, {"merchant_prepare_id": order.prepare_id}) if mine else (rules.CLICK_BAD_REQUEST, {})
            )
        stored = await session.subscription()
        if stored is None or stored[0] == SUSPENDED:
            return rules.CLICK_NO_ORDER, {}
        await session.start_online_payment(order.id, provider=CLICK, txn=txn, provider_time=None, now=self._now())
        return rules.CLICK_OK, {"merchant_prepare_id": order.prepare_id}

    async def _click_complete(
        self, session: TenantSession, shop_id: UUID, order: OnlinePayment, txn: str, form: Mapping[str, str]
    ) -> tuple[int, dict[str, Any]]:
        prepared = form.get("merchant_prepare_id", "")
        if (order.provider, order.provider_txn) != (CLICK, txn) or prepared != str(order.prepare_id):
            return rules.CLICK_NO_TRANSACTION, {}
        if order.state == PAID:
            return rules.CLICK_ALREADY_PAID, {}
        if order.state != PENDING:
            return rules.CLICK_CANCELLED, {}
        try:
            failed = int(form.get("error", "0") or "0") < 0
        except ValueError:
            return rules.CLICK_BAD_REQUEST, {}
        if failed:
            # Click says the money was not taken.
            await session.cancel_online_payment(order.id, reason=None, now=self._now())
            return rules.CLICK_CANCELLED, {}
        await self._pay(session, shop_id, order)
        return rules.CLICK_OK, {"merchant_confirm_id": order.prepare_id}

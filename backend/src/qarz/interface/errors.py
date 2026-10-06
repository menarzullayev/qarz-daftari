"""One error shape for the whole API, with the message in the caller's language (REQ-N01)."""

from fastapi import Request
from fastapi.responses import JSONResponse

from qarz.application.errors import AppError

_STATUS = {
    "UNAUTHENTICATED": 401,
    "NOT_FOUND": 404,
    "FORBIDDEN_ROLE": 403,
    "VALIDATION": 422,
    "IDEMPOTENCY_KEY_REUSED": 409,
    "ALREADY_MEMBER": 409,
    "OWNER_MEMBERSHIP_FIXED": 409,
    "TRANSFER_PENDING": 409,
    "TRANSFER_TARGET_INVALID": 409,
    "NOT_TRANSFER_TARGET": 409,
    "SUBSCRIPTION_LIMITED": 402,
    "SHOP_SUSPENDED": 403,
    "CUSTOMER_ARCHIVED": 409,
    "CUSTOMER_HAS_BALANCE": 409,
    "EXCEEDS_BALANCE": 409,
    "ALREADY_REVERSED": 409,
    "CANNOT_REVERSE_REVERSAL": 409,
    "WOULD_GO_NEGATIVE": 409,
}

_MESSAGES = {
    "uz": {
        "UNAUTHENTICATED": "Avval tizimga kiring.",
        "NOT_FOUND": "Topilmadi.",
        "FORBIDDEN_ROLE": "Bu amal uchun sizning rolingiz yetarli emas.",
        "VALIDATION": "Ma'lumotlar noto'g'ri kiritilgan.",
        "IDEMPOTENCY_KEY_REUSED": "Bu so'rov kaliti boshqa amal uchun ishlatilgan.",
        "ALREADY_MEMBER": "Siz allaqachon shu do'kon xodimisiz.",
        "OWNER_MEMBERSHIP_FIXED": "Do'kon egasining a'zoligi faqat egalikni o'tkazish orqali o'zgaradi.",
        "TRANSFER_PENDING": "Egalikni o'tkazish taklifi allaqachon javob kutmoqda.",
        "TRANSFER_TARGET_INVALID": "Egalikni faqat shu do'konning faol menejeriga o'tkazish mumkin.",
        "NOT_TRANSFER_TARGET": "Bu taklifga faqat taklif qilingan menejer javob bera oladi.",
        "SUBSCRIPTION_LIMITED": "Obuna tugagan: yangi nasiya yozilmaydi. To'lov qabul qilish va ko'rish ishlayveradi.",
        "SHOP_SUSPENDED": "Do'kon to'xtatilgan. Faqat do'kon egasi ma'lumotlarni ko'ra oladi va eksport qila oladi.",
        "CUSTOMER_ARCHIVED": "Bu mijoz arxivda. Avval arxivdan chiqaring.",
        "CUSTOMER_HAS_BALANCE": "Qarzi bor mijozni arxivlab bo'lmaydi.",
        "EXCEEDS_BALANCE": "To'lov mijozning qarzidan katta bo'lishi mumkin emas.",
        "ALREADY_REVERSED": "Bu yozuv allaqachon bekor qilingan.",
        "CANNOT_REVERSE_REVERSAL": "Bekor qilish yozuvini bekor qilib bo'lmaydi.",
        "WOULD_GO_NEGATIVE": "Bekor qilinsa qarz manfiy bo'lib qoladi. Avval keyingi to'lovni bekor qiling.",
        "ERROR": "Xatolik yuz berdi.",
    },
    "ru": {
        "UNAUTHENTICATED": "Сначала войдите в систему.",
        "NOT_FOUND": "Не найдено.",
        "FORBIDDEN_ROLE": "Вашей роли недостаточно для этого действия.",
        "VALIDATION": "Данные введены неверно.",
        "IDEMPOTENCY_KEY_REUSED": "Этот ключ запроса уже использован для другого действия.",
        "ALREADY_MEMBER": "Вы уже сотрудник этого магазина.",
        "OWNER_MEMBERSHIP_FIXED": "Участие владельца меняется только через передачу магазина.",
        "TRANSFER_PENDING": "Предложение о передаче магазина уже ожидает ответа.",
        "TRANSFER_TARGET_INVALID": "Магазин можно передать только активному менеджеру этого магазина.",
        "NOT_TRANSFER_TARGET": "Ответить на предложение может только менеджер, которому оно адресовано.",
        "SUBSCRIPTION_LIMITED": "Подписка истекла: новые продажи в долг недоступны. Оплаты и просмотр работают.",
        "SHOP_SUSPENDED": "Магазин временно приостановлен. Только владелец может просматривать и выгружать данные.",
        "CUSTOMER_ARCHIVED": "Этот клиент в архиве. Сначала верните его из архива.",
        "CUSTOMER_HAS_BALANCE": "Клиента с долгом нельзя отправить в архив.",
        "EXCEEDS_BALANCE": "Оплата не может быть больше долга клиента.",
        "ALREADY_REVERSED": "Эта запись уже отменена.",
        "CANNOT_REVERSE_REVERSAL": "Запись об отмене отменить нельзя.",
        "WOULD_GO_NEGATIVE": "После такой отмены долг стал бы отрицательным. Сначала отмените более позднюю оплату.",
        "ERROR": "Произошла ошибка.",
    },
}


def error_response(code: str, lang: str, fields: dict[str, str] | None = None) -> JSONResponse:
    messages = _MESSAGES.get(lang, _MESSAGES["uz"])
    body = {"error": {"code": code, "message": messages.get(code, messages["ERROR"]), "fields": fields or {}}}
    return JSONResponse(body, status_code=_STATUS.get(code, 500))


async def app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    lang = getattr(request.state, "lang", "uz")
    return error_response(exc.code, lang, exc.fields)

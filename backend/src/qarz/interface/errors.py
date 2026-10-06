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
}

_MESSAGES = {
    "uz": {
        "UNAUTHENTICATED": "Avval tizimga kiring.",
        "NOT_FOUND": "Topilmadi.",
        "FORBIDDEN_ROLE": "Bu amal uchun sizning rolingiz yetarli emas.",
        "VALIDATION": "Ma'lumotlar noto'g'ri kiritilgan.",
        "IDEMPOTENCY_KEY_REUSED": "Bu so'rov kaliti boshqa amal uchun ishlatilgan.",
        "ERROR": "Xatolik yuz berdi.",
    },
    "ru": {
        "UNAUTHENTICATED": "Сначала войдите в систему.",
        "NOT_FOUND": "Не найдено.",
        "FORBIDDEN_ROLE": "Вашей роли недостаточно для этого действия.",
        "VALIDATION": "Данные введены неверно.",
        "IDEMPOTENCY_KEY_REUSED": "Этот ключ запроса уже использован для другого действия.",
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

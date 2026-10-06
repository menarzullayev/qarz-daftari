"""Texts of the staff chat in Uzbek and Russian (ADR-021: interface text lives in catalogs, not in logic).

Uzbek defines the set of keys; `tests/test_chat_texts.py` requires Russian to have the same keys and the
same placeholders.
"""

from datetime import date

UZ = {
    "welcome_new": (
        "Assalomu alaykum! Qarz Daftari — do'kondagi nasiya hisobi.\n"
        "Boshlash uchun do'kon oching. Xodim bo'lsangiz, do'kon egasi yuborgan havola orqali kiring."
    ),
    "welcome_staff": (
        "Faol do'kon: {shop}\n\n"
        "Nasiya yozish: Ali 45000\n"
        "To'lov yozish: Ali -20000 yoki Ali 20000 berdi\n"
        "Izoh qo'shish: Ali 45000 non, sut"
    ),
    "help": (
        "Nasiya yozish: Ali 45000\n"
        "To'lov yozish: Ali -20000 yoki Ali 20000 berdi\n"
        "Summani 45 000, 45.000 yoki 45k ko'rinishida yozish mumkin.\n\n"
        "/dokon — faol do'konni almashtirish\n"
        "/til — tilni almashtirish\n"
        "/yordam — shu yordam"
    ),
    "soon": "Bu buyruq hali tayyor emas.",
    "only_text": "Hozircha faqat matnli xabarlarni tushunaman. Namuna: Ali 45000",
    "open_shop": "🏪 Do'kon ochish",
    "ask_shop_name": "Do'koningiz nomini yozing (80 belgigacha).",
    "shop_name_invalid": "Do'kon nomi 1 dan 80 belgigacha bo'lishi kerak. Qaytadan yozing.",
    "shop_created": "✅ «{shop}» do'koni ochildi.\n\nEndi nasiya yozishingiz mumkin, masalan: Ali 45000",
    "lang_prompt": "Tilni tanlang:",
    "lang_set": "Til o'zgartirildi: o'zbekcha.",
    "no_shops": "Siz hali hech bir do'konga a'zo emassiz.",
    "choose_shop": "Qaysi do'kon bilan ishlaysiz?",
    "shop_switched": "Faol do'kon: {shop}",
    "joined_shop": "✅ Siz «{shop}» do'koniga qo'shildingiz.\n\nNasiya yozish: Ali 45000",
    "invitation_invalid": "Bu taklif havolasi yaroqsiz yoki muddati o'tgan. Do'kon egasidan yangisini so'rang.",
    "already_member": "Siz allaqachon shu do'kon xodimisiz.",
    "credit_saved": "✅ {shop}\n{name}: +{amount}\nJami qarzi: {balance}\nTo'lash muddati: {date}",
    "payment_saved": "✅ {shop}\n{name}: to'lov {amount}\nQolgan qarzi: {balance}",
    "unknown_customer_credit": (
        "{shop}\n«{name}» degan mijoz topilmadi.\nYangi mijoz qo'shib, {amount} nasiya yozilsinmi?"
    ),
    "unknown_customer_payment": "{shop}\n«{name}» degan mijoz topilmadi. To'lov yozilmadi.",
    "pick_customer": "{shop}\n«{name}» — qaysi mijoz? ({amount})",
    "add_and_record": "➕ Qo'shish va yozish",
    "new_customer_option": "➕ Yangi mijoz: {name}",
    "cancel": "Bekor",
    "cancelled": "Bekor qilindi. Hech narsa yozilmadi.",
    "expired": "Bu tugma eskirgan. Xabarni qaytadan yozing.",
    "tomorrow": "Ertaga",
    "end_of_week": "Hafta oxiri",
    "in_two_weeks": "2 hafta",
    "in_a_month": "1 oy",
    "other_date": "📅 Boshqa sana",
    "reverse": "↩️ Bekor qilish",
    "reverse_yes": "Ha, bekor qilinsin",
    "reverse_no": "Yo'q",
    "ask_date": "To'lash sanasini yozing: kun.oy, masalan 25.10",
    "promise_closed": "Bu yozuvning muddati allaqachon belgilangan. Uni endi menejer yoki do'kon egasi o'zgartiradi.",
    "reversed": "↩️ {shop}\n{name}: {amount} yozuvi bekor qilindi.\nQarzi: {balance}",
    "forbidden": "Bu amal uchun sizning rolingiz yetarli emas.",
    "not_found": "Yozuv topilmadi.",
    "parse_hint": "Tushunmadim. Namuna:\nAli 45000\nAli -20000\nAli 20000 berdi",
    "parse_amount_not_whole": "Summa butun so'mda yoziladi, tiyinsiz. Namuna: Ali 45000",
    "parse_ambiguous": "Qaysi son summa ekanini ajrata olmadim. Avval ism, keyin bitta summa yozing: Ali 45000",
    "parse_too_long": "Xabar juda uzun. Qisqaroq yozing: Ali 45000 izoh",
    "amount_range": "Summa 100 so'mdan 100 000 000 so'mgacha bo'lishi kerak.",
    "PROMISE_BEFORE_SALE": "Muddat savdo kunidan oldin bo'lishi mumkin emas.",
    "PROMISE_TOO_FAR": "Muddat savdo kunidan ko'pi bilan 365 kun keyin bo'lishi mumkin.",
    "SUBSCRIPTION_LIMITED": "Obuna tugagan: yangi nasiya yozilmaydi. To'lov qabul qilish ishlayveradi. /obuna",
    "SHOP_SUSPENDED": "Do'kon vaqtincha to'xtatilgan.",
    "EXCEEDS_BALANCE": "To'lov mijozning qarzidan katta bo'lishi mumkin emas.",
    "CUSTOMER_ARCHIVED": "Bu mijoz arxivda. Avval arxivdan chiqaring.",
    "ALREADY_REVERSED": "Bu yozuv allaqachon bekor qilingan.",
    "CANNOT_REVERSE_REVERSAL": "Bekor qilish yozuvini bekor qilib bo'lmaydi.",
    "WOULD_GO_NEGATIVE": "Bekor qilinsa qarz manfiy bo'lib qoladi. Avval keyingi to'lovni bekor qiling.",
    "error": "Xatolik yuz berdi. Qaytadan urinib ko'ring.",
    "currency": "so'm",
}

RU = {
    "welcome_new": (
        "Здравствуйте! Qarz Daftari — учёт продаж в долг в магазине.\n"
        "Чтобы начать, откройте магазин. Если вы сотрудник, войдите по ссылке от владельца магазина."
    ),
    "welcome_staff": (
        "Активный магазин: {shop}\n\n"
        "Записать долг: Али 45000\n"
        "Записать оплату: Али -20000 или Али 20000 оплатил\n"
        "С заметкой: Али 45000 хлеб, молоко"
    ),
    "help": (
        "Записать долг: Али 45000\n"
        "Записать оплату: Али -20000 или Али 20000 оплатил\n"
        "Сумму можно писать как 45 000, 45.000 или 45к.\n\n"
        "/dokon — сменить активный магазин\n"
        "/til — сменить язык\n"
        "/yordam — эта справка"
    ),
    "soon": "Эта команда пока не готова.",
    "only_text": "Пока я понимаю только текстовые сообщения. Пример: Али 45000",
    "open_shop": "🏪 Открыть магазин",
    "ask_shop_name": "Напишите название вашего магазина (до 80 символов).",
    "shop_name_invalid": "Название магазина должно быть от 1 до 80 символов. Напишите ещё раз.",
    "shop_created": "✅ Магазин «{shop}» открыт.\n\nТеперь можно записывать долги, например: Али 45000",
    "lang_prompt": "Выберите язык:",
    "lang_set": "Язык изменён: русский.",
    "no_shops": "Вы пока не состоите ни в одном магазине.",
    "choose_shop": "С каким магазином работаете?",
    "shop_switched": "Активный магазин: {shop}",
    "joined_shop": "✅ Вы присоединились к магазину «{shop}».\n\nЗаписать долг: Али 45000",
    "invitation_invalid": "Эта ссылка-приглашение недействительна или устарела. Попросите у владельца новую.",
    "already_member": "Вы уже сотрудник этого магазина.",
    "credit_saved": "✅ {shop}\n{name}: +{amount}\nВсего долг: {balance}\nСрок оплаты: {date}",
    "payment_saved": "✅ {shop}\n{name}: оплата {amount}\nОстаток долга: {balance}",
    "unknown_customer_credit": (
        "{shop}\nКлиент «{name}» не найден.\nДобавить нового клиента и записать долг {amount}?"
    ),
    "unknown_customer_payment": "{shop}\nКлиент «{name}» не найден. Оплата не записана.",
    "pick_customer": "{shop}\n«{name}» — какой клиент? ({amount})",
    "add_and_record": "➕ Добавить и записать",
    "new_customer_option": "➕ Новый клиент: {name}",
    "cancel": "Отмена",
    "cancelled": "Отменено. Ничего не записано.",
    "expired": "Эта кнопка устарела. Напишите сообщение заново.",
    "tomorrow": "Завтра",
    "end_of_week": "Конец недели",
    "in_two_weeks": "2 недели",
    "in_a_month": "1 месяц",
    "other_date": "📅 Другая дата",
    "reverse": "↩️ Отменить",
    "reverse_yes": "Да, отменить",
    "reverse_no": "Нет",
    "ask_date": "Напишите дату оплаты: день.месяц, например 25.10",
    "promise_closed": "Срок этой записи уже задан. Теперь его меняет менеджер или владелец магазина.",
    "reversed": "↩️ {shop}\n{name}: запись {amount} отменена.\nДолг: {balance}",
    "forbidden": "Вашей роли недостаточно для этого действия.",
    "not_found": "Запись не найдена.",
    "parse_hint": "Не понял. Пример:\nАли 45000\nАли -20000\nАли 20000 оплатил",
    "parse_amount_not_whole": "Сумма пишется в целых сумах, без тийинов. Пример: Али 45000",
    "parse_ambiguous": "Не удалось понять, какое число — сумма. Сначала имя, затем одна сумма: Али 45000",
    "parse_too_long": "Сообщение слишком длинное. Напишите короче: Али 45000 заметка",
    "amount_range": "Сумма должна быть от 100 до 100 000 000 сумов.",
    "PROMISE_BEFORE_SALE": "Срок не может быть раньше дня продажи.",
    "PROMISE_TOO_FAR": "Срок может быть не позже чем через 365 дней после продажи.",
    "SUBSCRIPTION_LIMITED": "Подписка истекла: новые продажи в долг недоступны. Приём оплат работает. /obuna",
    "SHOP_SUSPENDED": "Магазин временно приостановлен.",
    "EXCEEDS_BALANCE": "Оплата не может быть больше долга клиента.",
    "CUSTOMER_ARCHIVED": "Этот клиент в архиве. Сначала верните его из архива.",
    "ALREADY_REVERSED": "Эта запись уже отменена.",
    "CANNOT_REVERSE_REVERSAL": "Запись об отмене отменить нельзя.",
    "WOULD_GO_NEGATIVE": "После такой отмены долг стал бы отрицательным. Сначала отмените более позднюю оплату.",
    "error": "Произошла ошибка. Попробуйте ещё раз.",
    "currency": "сум",
}

CATALOGS = {"uz": UZ, "ru": RU}
LANGUAGE_NAMES = {"uz": "O'zbekcha", "ru": "Русский"}


def say(lang: str, key: str, **values: object) -> str:
    """The text for `key` in the given language. An unknown language falls back to Uzbek."""
    return CATALOGS.get(lang, UZ)[key].format(**values)


def money(lang: str, amount: int) -> str:
    """45000 -> "45 000 so'm". The separator is a no-break space so an amount never wraps."""
    return f"{amount:,}".replace(",", " ") + " " + CATALOGS.get(lang, UZ)["currency"]


def day(value: date) -> str:
    return value.strftime("%d.%m.%Y")

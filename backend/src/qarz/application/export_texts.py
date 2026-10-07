"""Words of the export workbook in Uzbek and Russian (ADR-021: interface text lives in catalogs).

Uzbek defines the keys; `tests/test_export_texts.py` requires Russian to mirror them, list for list.
"""

UZ: dict[str, str | tuple[str, ...]] = {
    "sheet_summary": "Hisobot",
    "sheet_customers": "Mijozlar",
    "sheet_ledger": "Daftar",
    "sheet_promises": "Muddatlar tarixi",
    "sheet_goods": "Mahsulotlar",
    "customers": ("Mijoz", "Telefon", "Holati", "Nasiya limiti", "Qarzi", "Qo'shilgan sana", "Mijoz ID"),
    "ledger": (
        "Sana va vaqt",
        "Mijoz",
        "Turi",
        "Summa",
        "Qarzga ta'siri",
        "Izoh",
        "To'lash muddati",
        "Bekor qilinganmi",
        "Bekor qilgan yozuvi (ID)",
        "Kim yozgan",
        "Xodim ID",
        "Mijozdagi tartib raqami",
        "Yozuv ID",
        "Mijoz ID",
    ),
    "promises": ("Yozuv ID", "Mijoz", "To'lash muddati", "Belgilangan vaqt", "Kim belgilagan", "Sabab"),
    "goods": ("Yozuv ID", "Sana va vaqt", "Mijoz", "Qator", "Mahsulot", "Miqdor", "Birlik", "Narxi", "Jami"),
    "months": (
        "Oy",
        "Nasiya summasi",
        "Nasiya soni",
        "Boshlang'ich qarz summasi",
        "To'lovlar summasi",
        "To'lovlar soni",
        "Bekor qilingan yozuvlar soni",
    ),
    "summary_shop": "Do'kon",
    "summary_made": "Eksport vaqti (Toshkent)",
    "summary_customers": "Mijozlar soni",
    "summary_debtors": "Qarzdor mijozlar soni",
    "summary_outstanding": "Jami qarz",
    "summary_entries": "Daftardagi yozuvlar soni",
    "summary_months": "Oylar bo'yicha (bekor qilingan yozuvlar va bekor qilish yozuvlari hisobga olinmagan)",
    "kind_credit": "Nasiya",
    "kind_opening": "Boshlang'ich qarz",
    "kind_payment": "To'lov",
    "kind_reversal": "Bekor qilish",
    "status_active": "Faol",
    "status_archived": "Arxivda",
    "status_anonymized": "Ma'lumotlari o'chirilgan",
    "role_seller": "Sotuvchi",
    "role_manager": "Menejer",
    "role_owner": "Do'kon egasi",
    "actor_default": "Do'kon bo'yicha odatiy muddat",
    "actor_staff": "Xodim",
    "actor_customer_request": "Mijoz so'rovi bilan",
    "yes": "Ha",
    "no": "Yo'q",
}

RU: dict[str, str | tuple[str, ...]] = {
    "sheet_summary": "Отчёт",
    "sheet_customers": "Клиенты",
    "sheet_ledger": "Книга",
    "sheet_promises": "История сроков",
    "sheet_goods": "Товары",
    "customers": ("Клиент", "Телефон", "Состояние", "Лимит долга", "Долг", "Дата добавления", "ID клиента"),
    "ledger": (
        "Дата и время",
        "Клиент",
        "Вид",
        "Сумма",
        "Влияние на долг",
        "Заметка",
        "Срок оплаты",
        "Отменена ли",
        "Какую запись отменяет (ID)",
        "Кто записал",
        "ID сотрудника",
        "Номер у клиента",
        "ID записи",
        "ID клиента",
    ),
    "promises": ("ID записи", "Клиент", "Срок оплаты", "Когда задан", "Кто задал", "Причина"),
    "goods": ("ID записи", "Дата и время", "Клиент", "Строка", "Товар", "Количество", "Единица", "Цена", "Итого"),
    "months": (
        "Месяц",
        "Сумма продаж в долг",
        "Число продаж в долг",
        "Сумма начальных долгов",
        "Сумма оплат",
        "Число оплат",
        "Число отменённых записей",
    ),
    "summary_shop": "Магазин",
    "summary_made": "Время выгрузки (Ташкент)",
    "summary_customers": "Число клиентов",
    "summary_debtors": "Число должников",
    "summary_outstanding": "Всего долг",
    "summary_entries": "Число записей в книге",
    "summary_months": "По месяцам (отменённые записи и записи об отмене не учтены)",
    "kind_credit": "Продажа в долг",
    "kind_opening": "Начальный долг",
    "kind_payment": "Оплата",
    "kind_reversal": "Отмена",
    "status_active": "Активен",
    "status_archived": "В архиве",
    "status_anonymized": "Данные удалены",
    "role_seller": "Продавец",
    "role_manager": "Менеджер",
    "role_owner": "Владелец",
    "actor_default": "Срок магазина по умолчанию",
    "actor_staff": "Сотрудник",
    "actor_customer_request": "По просьбе клиента",
    "yes": "Да",
    "no": "Нет",
}

CATALOGS = {"uz": UZ, "ru": RU}


def word(lang: str, key: str, fallback: str | None = None) -> str:
    """One word or phrase. An unknown language reads Uzbek; an unknown key reads `fallback` when given."""
    value = CATALOGS.get(lang, UZ).get(key, fallback)
    if not isinstance(value, str):
        raise KeyError(key)
    return value


def header(lang: str, key: str) -> tuple[str, ...]:
    value = CATALOGS.get(lang, UZ)[key]
    if isinstance(value, str):
        raise KeyError(key)
    return value

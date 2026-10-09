"""One error shape for the whole API, with the message in the caller's language (REQ-N01)."""

from functools import cache

from fastapi import Request
from fastapi.responses import JSONResponse

from qarz.application import texts_en, texts_kaa, texts_tg
from qarz.application.errors import AppError
from qarz.domain import languages
from qarz.domain.uz_cyrillic import to_cyrillic

_STATUS = {
    "UNAUTHENTICATED": 401,
    "NOT_FOUND": 404,
    "FORBIDDEN_ROLE": 403,
    "FORBIDDEN_PERMISSION": 403,
    "BEYOND_OWN_PERMISSIONS": 403,
    "VALIDATION": 422,
    "IDEMPOTENCY_KEY_REUSED": 409,
    "ALREADY_MEMBER": 409,
    "OWNER_MEMBERSHIP_FIXED": 409,
    "TRANSFER_PENDING": 409,
    "TRANSFER_TARGET_INVALID": 409,
    "NOT_TRANSFER_TARGET": 409,
    "SUBSCRIPTION_LIMITED": 402,
    "FREE_PLAN_FULL": 402,
    "SHOP_SUSPENDED": 403,
    "RATE_LIMITED": 429,
    "TIMEOUT": 503,
    "CUSTOMER_ARCHIVED": 409,
    "CUSTOMER_HAS_BALANCE": 409,
    "USD_BALANCE_OPEN": 409,
    "USD_SUPPLIER_BALANCE_OPEN": 409,
    "USD_STOCK_OPEN": 409,
    "EXCEEDS_BALANCE": 409,
    "ALREADY_REVERSED": 409,
    "CANNOT_REVERSE_REVERSAL": 409,
    "WOULD_GO_NEGATIVE": 409,
    "PROMISE_ALREADY_SET": 409,
    "CUSTOMER_ALREADY_LINKED": 409,
    "DISPUTE_NOT_ALLOWED": 409,
    "DELETION_ALREADY_REQUESTED": 409,
    "DELETION_NOT_REQUESTED": 409,
    "ONLINE_PAY_OFF": 409,
    "LIMIT_REACHED": 409,
    "REMINDERS_OFF": 409,
    "REMINDER_NOT_DUE": 409,
    "REMINDER_LIMIT_REACHED": 409,
    "CUSTOMER_UNREACHABLE": 409,
    "REQUEST_ALREADY_OPEN": 409,
    "DATE_REQUEST_NOT_ALLOWED": 409,
    "PROMISE_NOT_CHANGEABLE": 409,
    "CATALOG_NAME_TAKEN": 409,
    "CATALOG_ITEM_NOT_LEARNED": 409,
    "CATALOG_MERGE_TARGET_INVALID": 409,
    "LINES_ALREADY_ADDED": 409,
    "LINES_SUM_MISMATCH": 409,
    "LINES_WINDOW_CLOSED": 409,
    "SECOND_FACTOR_INVALID": 403,
    "SECOND_FACTOR_LOCKED": 429,
    "ADMIN_ALREADY_ENROLLED": 409,
    "ADMIN_NOT_ENROLLED": 409,
    "SUBSCRIPTION_CHANGE_REFUSED": 409,
    "OWNER_REASSIGNMENT_REFUSED": 409,
    "SUPPORT_ACCESS_REQUIRED": 403,
    "SUPPORT_ACCESS_ALREADY_OPEN": 409,
    "SUPPORT_ACCESS_NOT_OPEN": 409,
    "PAYMENT_NOTICE_NOT_ALLOWED": 409,
    "PAYMENT_NOTICE_NOT_OPEN": 409,
    "EXPORT_NOT_ALLOWED": 409,
    "EXPORT_NOT_READY": 409,
    "FILE_STORE_UNAVAILABLE": 503,
    "IMPORT_NOT_APPLICABLE": 409,
    "IMPORT_UNDO_REFUSED": 409,
    "SUBSCRIPTION_RECEIPT_NOT_ALLOWED": 409,
    "RECEIPT_ALREADY_DECIDED": 409,
    "STOCK_INSUFFICIENT": 409,
    "STOCK_ALREADY_USED": 409,
    "COST_CURRENCY_MISMATCH": 409,
    "ENTRY_OF_DOCUMENT": 409,
    "BARCODE_TAKEN": 409,
    "ITEM_NOT_COUNTABLE": 409,
    "DOCUMENT_NOT_DRAFT": 409,
    "DOCUMENT_CANCELLED": 409,
    "SUPPLIER_NAME_TAKEN": 409,
    "SUPPLIER_ARCHIVED": 409,
    "SUPPLIER_HAS_BALANCE": 409,
    "NETWORK_STATE": 409,
    "NETWORK_INVITE_INVALID": 404,
    "NETWORK_LINK_EXISTS": 409,
    "NETWORK_TOO_MANY_INVITES": 409,
    "NETWORK_PARTNER_UNAVAILABLE": 409,
    "NETWORK_COUNTERPART_INVALID": 409,
    "NETWORK_CURRENCY": 409,
    "NETWORK_BOOKS_MISMATCH": 409,
    "NETWORK_PARTNER_REFUSED": 409,
    "CASH_ENTRY_OF_LEDGER": 409,
    "CASH_ENTRY_OF_STOCK": 409,
    "CASH_ENTRY_CANCELLED": 409,
    "CASH_CATEGORY_ARCHIVED": 409,
    "CASH_CATEGORY_NAME_TAKEN": 409,
    "CASH_CATEGORY_FIXED": 409,
    "CASH_CATEGORY_IN_USE": 409,
}

_MESSAGES = {
    "uz": {
        "UNAUTHENTICATED": "Avval tizimga kiring.",
        "NOT_FOUND": "Topilmadi.",
        "FORBIDDEN_ROLE": "Bu amal uchun sizning rolingiz yetarli emas.",
        "FORBIDDEN_PERMISSION": "Bu amal uchun sizda ruxsat yo'q. Do'kon egasiga murojaat qiling.",
        "BEYOND_OWN_PERMISSIONS": "Bu sizning ruxsatlaringizdan yuqori: buni faqat do'kon egasi qila oladi.",
        "VALIDATION": "Ma'lumotlar noto'g'ri kiritilgan.",
        "IDEMPOTENCY_KEY_REUSED": "Bu so'rov kaliti boshqa amal uchun ishlatilgan.",
        "ALREADY_MEMBER": "Siz allaqachon shu do'kon xodimisiz.",
        "OWNER_MEMBERSHIP_FIXED": "Do'kon egasining a'zoligi faqat egalikni o'tkazish orqali o'zgaradi.",
        "TRANSFER_PENDING": "Egalikni o'tkazish taklifi allaqachon javob kutmoqda.",
        "TRANSFER_TARGET_INVALID": "Egalikni faqat shu do'konning faol menejeriga o'tkazish mumkin.",
        "NOT_TRANSFER_TARGET": "Bu taklifga faqat taklif qilingan menejer javob bera oladi.",
        "SUBSCRIPTION_LIMITED": "Obuna tugagan: yangi nasiya yozilmaydi. To'lov qabul qilish va ko'rish ishlayveradi.",
        "FREE_PLAN_FULL": (
            "Bepul tarif {limit} tagacha mijozni o'z ichiga oladi, yangi mijoz qo'shilmadi. "
            "Ko'proq mijoz uchun obuna to'lang: botda /obuna yoki «Obuna» sahifasi."
        ),
        "SHOP_SUSPENDED": "Do'kon to'xtatilgan. Faqat do'kon egasi ma'lumotlarni ko'ra oladi va eksport qila oladi.",
        "CUSTOMER_ARCHIVED": "Bu mijoz arxivda. Avval arxivdan chiqaring.",
        "CUSTOMER_HAS_BALANCE": "Qarzi bor mijozni arxivlab bo'lmaydi.",
        "USD_BALANCE_OPEN": (
            "Dollarni o'chirib bo'lmaydi: mijozlarda dollarda qarz bor. Avval dollardagi barcha qarzlar yopilsin."
        ),
        "USD_SUPPLIER_BALANCE_OPEN": (
            "Dollarni o'chirib bo'lmaydi: ta'minotchilar bilan dollarda hisob-kitob yopilmagan. "
            "Avval ta'minotchilar bilan dollardagi hisob nolga keltirilsin."
        ),
        "USD_STOCK_OPEN": (
            "Dollarni o'chirib bo'lmaydi: omborda tannarxi dollarda yuritilgan tovar bor. "
            "Avval bu tovarlar sotilsin, qaytarilsin yoki hisobdan chiqarilsin."
        ),
        "EXCEEDS_BALANCE": "To'lov mijozning qarzidan katta bo'lishi mumkin emas.",
        "ALREADY_REVERSED": "Bu yozuv allaqachon bekor qilingan.",
        "CANNOT_REVERSE_REVERSAL": "Bekor qilish yozuvini bekor qilib bo'lmaydi.",
        "WOULD_GO_NEGATIVE": "Bekor qilinsa qarz manfiy bo'lib qoladi. Avval keyingi to'lovni bekor qiling.",
        "PROMISE_ALREADY_SET": "Muddat allaqachon belgilangan. Endi uni menejer yoki do'kon egasi o'zgartiradi.",
        "CATALOG_NAME_TAKEN": "Katalogda shu nomli mahsulot bor (yashirilgan bo'lishi ham mumkin).",
        "CATALOG_ITEM_NOT_LEARNED": "Bu mahsulot allaqachon ko'rib chiqilgan.",
        "CATALOG_MERGE_TARGET_INVALID": "Faqat katalogda ko'rinadigan, ko'rib chiqilgan mahsulotga birlashtiriladi.",
        "CUSTOMER_ALREADY_LINKED": "Bu mijoz allaqachon Telegram hisobiga ulangan.",
        "DISPUTE_NOT_ALLOWED": "Bu yozuv bo'yicha e'tiroz bildirib bo'lmaydi yoki u allaqachon ko'rib chiqilgan.",
        "REQUEST_ALREADY_OPEN": "Bu yozuv bo'yicha muddatni ko'chirish so'rovi allaqachon ko'rib chiqilmoqda.",
        "DATE_REQUEST_NOT_ALLOWED": "Bu yozuv muddatini ko'chirish so'rovini hozir qabul qilib bo'lmaydi.",
        "PROMISE_NOT_CHANGEABLE": "Bu yozuvning to'lash muddati yo'q yoki yozuv bekor qilingan.",
        "LINES_ALREADY_ADDED": "Bu yozuvga mahsulotlar allaqachon qo'shilgan.",
        "LINES_SUM_MISMATCH": "Mahsulotlar yig'indisi yozuv summasiga teng emas.",
        "LINES_WINDOW_CLOSED": "Mahsulot qo'shish muddati o'tgan: bu faqat sotuvdan keyingi kun oxirigacha mumkin.",
        "GOODS_NOT_IN_DOLLARS": (
            "Dollardagi nasiyaga mahsulotlar ro'yxati qo'shilmaydi: mahsulot narxlari so'mda yuritiladi. "
            "Dollardagi savdoni summasi bilan yozing."
        ),
        "REMINDERS_OFF": "Eslatmalar do'kon yoki shu mijoz uchun o'chirilgan.",
        "REMINDER_NOT_DUE": "Bu mijozda muddati o'tgan yoki bugun to'lanadigan qarz yo'q.",
        "REMINDER_LIMIT_REACHED": "Bu mijozga bugun eslatma allaqachon yuborilgan. Kuniga bitta mumkin.",
        "CUSTOMER_UNREACHABLE": "Bu mijozga yetib bo'lmaydi: Telegram ulanmagan, SMS esa o'chiq yoki raqam yo'q.",
        "CUSTOMER_UNREACHABLE_USD": (
            "Bu mijozning to'lash muddati kelgan qarzi faqat dollarda, SMS esa faqat so'mdagi qarzni aytadi. "
            "Eslatma yuborish uchun mijozni Telegramga ulang."
        ),
        "LIMIT_REACHED": "Bu savdo mijozning nasiya limitidan oshadi. Menejer yoki do'kon egasi yoza oladi.",
        "DELETION_ALREADY_REQUESTED": "Do'konni o'chirish allaqachon so'ralgan.",
        "DELETION_NOT_REQUESTED": "Do'konni o'chirish so'ralmagan.",
        "SECOND_FACTOR_INVALID": "Kod noto'g'ri, eskirgan yoki allaqachon ishlatilgan. Yangi kodni kiriting.",
        "SECOND_FACTOR_LOCKED": "Juda ko'p noto'g'ri kod kiritildi. Birozdan keyin qayta urinib ko'ring.",
        "ADMIN_ALREADY_ENROLLED": "Ikkinchi omil allaqachon ulangan. Almashtirish uchun operatorga murojaat qiling.",
        "ADMIN_NOT_ENROLLED": "Avval ikkinchi omilni (autentifikator ilovasini) ulang.",
        "SUBSCRIPTION_CHANGE_REFUSED": "Obunaning hozirgi holatida bu o'zgarishni qilib bo'lmaydi.",
        "OWNER_REASSIGNMENT_REFUSED": "Do'konni bu odamga berib bo'lmaydi.",
        "SUPPORT_ACCESS_REQUIRED": "Do'kon ma'lumotlarini ko'rish uchun avval sabab ko'rsatib ruxsat oching.",
        "SUPPORT_ACCESS_ALREADY_OPEN": "Bu do'kon uchun ochiq ruxsatingiz allaqachon bor.",
        "SUPPORT_ACCESS_NOT_OPEN": "Ochiq ruxsat yo'q: u tugagan yoki allaqachon yopilgan.",
        "PAYMENT_NOTICE_NOT_ALLOWED": "Ko'rib chiqilmagan to'lov xabarlaringiz juda ko'p. Do'kon javobini kuting.",
        "PAYMENT_NOTICE_NOT_OPEN": "Bu to'lov xabari allaqachon ko'rib chiqilgan yoki muddati o'tgan.",
        "EXPORT_NOT_ALLOWED": "Eksport allaqachon tayyorlanmoqda yoki bugungi eksportlar soni tugagan.",
        "EXPORT_NOT_READY": "Bu eksport fayli hali tayyor emas yoki muddati o'tgan.",
        "FILE_STORE_UNAVAILABLE": "Fayllarni saqlash hozir ishlamayapti. Birozdan keyin qayta urinib ko'ring.",
        "IMPORT_NOT_APPLICABLE": "Bu importni hozirgi holatida qo'llab bo'lmaydi. Ko'rib chiqishni yangilang.",
        "IMPORT_UNDO_REFUSED": "Bu importni bekor qilib bo'lmaydi: muddat o'tgan yoki yozuvlarga to'lov qilingan.",
        "SUBSCRIPTION_RECEIPT_NOT_ALLOWED": "Ko'rib chiqilmagan cheklaringiz juda ko'p. Administrator javobini kuting.",
        "RECEIPT_ALREADY_DECIDED": "Bu chek bo'yicha qaror allaqachon qabul qilingan.",
        "CASH_ENTRY_OF_LEDGER": (
            "Bu yozuv mijozning to'lovi. Uni bekor qilish uchun mijoz sahifasida o'sha to'lovni bekor qiling."
        ),
        "CASH_ENTRY_OF_STOCK": (
            "Bu yozuvni ombor yozgan (ta'minotchiga to'lov, tovar xaridi yoki qaytarish). "
            "Uni bekor qilish uchun o'sha to'lovni yoki hujjatni bekor qiling."
        ),
        "CASH_ENTRY_CANCELLED": "Bu kassa yozuvi allaqachon bekor qilingan.",
        "CASH_CATEGORY_ARCHIVED": "Bu toifa arxivda. Boshqa toifani tanlang yoki uni arxivdan chiqaring.",
        "CASH_CATEGORY_NAME_TAKEN": "Shu nomli toifa allaqachon bor (arxivda bo'lishi ham mumkin).",
        "CASH_CATEGORY_FIXED": "Mijozlar to'lovi tushadigan toifani arxivlab yoki o'chirib bo'lmaydi.",
        "CASH_CATEGORY_IN_USE": "Bu toifada yozuvlar bor, uni o'chirib bo'lmaydi. Arxivlash mumkin.",
        "ONLINE_PAY_OFF": "Onlayn to'lov hozircha yoqilmagan. Karta orqali to'lash: /obuna",
        "STOCK_INSUFFICIENT": "Omborda bu tovar yetarli emas. Avval kirim yozing yoki miqdorni kamaytiring.",
        "STOCK_ALREADY_USED": (
            "Bekor qilib bo'lmaydi: bu tovarlar ombordan allaqachon chiqib ketgan. "
            "Ta'minotchiga qaytarish yoki hisobdan chiqarish yozing."
        ),
        "COST_CURRENCY_MISMATCH": (
            "Bu tovarning tannarxi boshqa valyutada yuritiladi. Qoldiq tugagach, boshqa valyutada kirim qilish mumkin."
        ),
        "ENTRY_OF_DOCUMENT": "Bu yozuvni hujjat yaratgan. Uni bekor qilish uchun hujjatning o'zini bekor qiling.",
        "BARCODE_TAKEN": "Bu shtrix-kod boshqa tovarga biriktirilgan.",
        "ITEM_NOT_COUNTABLE": (
            "Bu tovarni omborda hisoblab bo'lmaydi: avval uni ko'rib chiqing va ombor o'lchov birligini tanlang."
        ),
        "DOCUMENT_NOT_DRAFT": "Bu hujjat allaqachon o'tkazilgan yoki bekor qilingan.",
        "DOCUMENT_CANCELLED": "Bu hujjat allaqachon bekor qilingan.",
        "SUPPLIER_NAME_TAKEN": "Shu nomli ta'minotchi bor (arxivda bo'lishi ham mumkin).",
        "SUPPLIER_ARCHIVED": "Bu ta'minotchi arxivda. Avval arxivdan chiqaring.",
        "SUPPLIER_HAS_BALANCE": "Hisob-kitobi yopilmagan ta'minotchini arxivlab bo'lmaydi.",
        "NETWORK_STATE": ("Bu amalni hozir bajarib bo'lmaydi: holat o'zgargan yoki bu qadam hamkor tomonniki."),
        "NETWORK_INVITE_INVALID": "Taklif kodi yaroqsiz yoki muddati o'tgan. Hamkordan yangi kod so'rang.",
        "NETWORK_LINK_EXISTS": "Bu do'kon bilan shunday aloqa allaqachon bor yoki javob kutilmoqda.",
        "NETWORK_TOO_MANY_INVITES": "Ochiq taklif kodlari juda ko'p. Avval eskilarini bekor qiling.",
        "NETWORK_PARTNER_UNAVAILABLE": "Hamkor do'kon hozir mavjud emas.",
        "NETWORK_COUNTERPART_INVALID": (
            "Hamkor uchun ta'minotchi yoki mijoz yozuvi mos emas. Faol va boshqa hamkorga bog'lanmagan yozuvni tanlang."
        ),
        "NETWORK_CURRENCY": "Ikki do'kondan biri bu valyutada ishlamaydi.",
        "NETWORK_BOOKS_MISMATCH": (
            "Daftardagi yozuv hujjatga mos kelmadi. Hech narsa saqlanmadi, qayta urinib ko'ring."
        ),
        "NETWORK_PARTNER_REFUSED": (
            "Hamkorning daftari bu yozuvni qabul qila olmadi. Hech narsa saqlanmadi. Hamkor bilan bog'laning."
        ),
        "RATE_LIMITED": "So'rovlar juda ko'p. Biroz kutib, qayta urinib ko'ring.",
        "TIMEOUT": "So'rov juda uzoq davom etdi va to'xtatildi. Hech narsa saqlanmadi. Qayta urinib ko'ring.",
        "ERROR": "Xatolik yuz berdi.",
    },
    "ru": {
        "UNAUTHENTICATED": "Сначала войдите в систему.",
        "NOT_FOUND": "Не найдено.",
        "FORBIDDEN_ROLE": "Вашей роли недостаточно для этого действия.",
        "FORBIDDEN_PERMISSION": "У вас нет разрешения на это действие. Обратитесь к владельцу магазина.",
        "BEYOND_OWN_PERMISSIONS": "Это выше ваших разрешений: так может сделать только владелец магазина.",
        "VALIDATION": "Данные введены неверно.",
        "IDEMPOTENCY_KEY_REUSED": "Этот ключ запроса уже использован для другого действия.",
        "ALREADY_MEMBER": "Вы уже сотрудник этого магазина.",
        "OWNER_MEMBERSHIP_FIXED": "Участие владельца меняется только через передачу магазина.",
        "TRANSFER_PENDING": "Предложение о передаче магазина уже ожидает ответа.",
        "TRANSFER_TARGET_INVALID": "Магазин можно передать только активному менеджеру этого магазина.",
        "NOT_TRANSFER_TARGET": "Ответить на предложение может только менеджер, которому оно адресовано.",
        "SUBSCRIPTION_LIMITED": "Подписка истекла: новые продажи в долг недоступны. Оплаты и просмотр работают.",
        "FREE_PLAN_FULL": (
            "Бесплатный тариф вмещает до {limit} клиентов, новый клиент не добавлен. "
            "Чтобы добавить больше, оплатите подписку: /obuna в боте или страница «Подписка»."
        ),
        "SHOP_SUSPENDED": "Магазин временно приостановлен. Только владелец может просматривать и выгружать данные.",
        "CUSTOMER_ARCHIVED": "Этот клиент в архиве. Сначала верните его из архива.",
        "CUSTOMER_HAS_BALANCE": "Клиента с долгом нельзя отправить в архив.",
        "USD_BALANCE_OPEN": (
            "Доллары нельзя выключить: у клиентов есть долг в долларах. Сначала закройте все долги в долларах."
        ),
        "USD_SUPPLIER_BALANCE_OPEN": (
            "Доллары нельзя выключить: расчёты с поставщиками в долларах не закрыты. "
            "Сначала сведите счёт с поставщиками в долларах к нулю."
        ),
        "USD_STOCK_OPEN": (
            "Доллары нельзя выключить: на складе есть товар, себестоимость которого ведётся в долларах. "
            "Сначала продайте, верните или спишите эти товары."
        ),
        "EXCEEDS_BALANCE": "Оплата не может быть больше долга клиента.",
        "ALREADY_REVERSED": "Эта запись уже отменена.",
        "CANNOT_REVERSE_REVERSAL": "Запись об отмене отменить нельзя.",
        "WOULD_GO_NEGATIVE": "После такой отмены долг стал бы отрицательным. Сначала отмените более позднюю оплату.",
        "PROMISE_ALREADY_SET": "Срок уже задан. Теперь его меняет менеджер или владелец магазина.",
        "CATALOG_NAME_TAKEN": "В каталоге уже есть товар с таким названием (возможно, он скрыт).",
        "CATALOG_ITEM_NOT_LEARNED": "Этот товар уже проверен.",
        "CATALOG_MERGE_TARGET_INVALID": "Объединить можно только с проверенным товаром, который виден в каталоге.",
        "CUSTOMER_ALREADY_LINKED": "Этот клиент уже подключён к аккаунту Telegram.",
        "DISPUTE_NOT_ALLOWED": "По этой записи нельзя подать возражение, или оно уже рассмотрено.",
        "REQUEST_ALREADY_OPEN": "Просьба о переносе срока по этой записи уже рассматривается.",
        "DATE_REQUEST_NOT_ALLOWED": "Просьбу о переносе срока по этой записи сейчас принять нельзя.",
        "PROMISE_NOT_CHANGEABLE": "У этой записи нет срока оплаты, или она отменена.",
        "LINES_ALREADY_ADDED": "К этой записи товары уже добавлены.",
        "LINES_SUM_MISMATCH": "Сумма товаров не равна сумме записи.",
        "LINES_WINDOW_CLOSED": "Срок добавления товаров истёк: это возможно только до конца дня после продажи.",
        "GOODS_NOT_IN_DOLLARS": (
            "К продаже в долларах список товаров не добавляется: цены товаров ведутся в сумах. "
            "Запишите продажу в долларах суммой."
        ),
        "REMINDERS_OFF": "Напоминания выключены для магазина или для этого клиента.",
        "REMINDER_NOT_DUE": "У этого клиента нет просроченного долга и долга со сроком сегодня.",
        "REMINDER_LIMIT_REACHED": "Этому клиенту сегодня уже отправлено напоминание. Можно одно в день.",
        "CUSTOMER_UNREACHABLE": "С этим клиентом нет связи: Telegram не подключён, а SMS выключены или нет номера.",
        "CUSTOMER_UNREACHABLE_USD": (
            "У этого клиента долг с наступившим сроком только в долларах, а в SMS называется только долг в сумах. "
            "Чтобы отправить напоминание, подключите клиента к Telegram."
        ),
        "LIMIT_REACHED": "Эта продажа превысит лимит клиента. Записать может менеджер или владелец.",
        "DELETION_ALREADY_REQUESTED": "Удаление магазина уже запрошено.",
        "DELETION_NOT_REQUESTED": "Удаление магазина не запрашивалось.",
        "SECOND_FACTOR_INVALID": "Код неверный, устарел или уже использован. Введите новый код.",
        "SECOND_FACTOR_LOCKED": "Слишком много неверных кодов. Повторите попытку позже.",
        "ADMIN_ALREADY_ENROLLED": "Второй фактор уже подключён. Чтобы заменить его, обратитесь к оператору.",
        "ADMIN_NOT_ENROLLED": "Сначала подключите второй фактор (приложение-аутентификатор).",
        "SUBSCRIPTION_CHANGE_REFUSED": "При текущем состоянии подписки это изменение невозможно.",
        "OWNER_REASSIGNMENT_REFUSED": "Магазин нельзя передать этому человеку.",
        "SUPPORT_ACCESS_REQUIRED": "Чтобы видеть данные магазина, сначала откройте доступ с указанием причины.",
        "SUPPORT_ACCESS_ALREADY_OPEN": "У вас уже есть открытый доступ к этому магазину.",
        "SUPPORT_ACCESS_NOT_OPEN": "Открытого доступа нет: он истёк или уже закрыт.",
        "PAYMENT_NOTICE_NOT_ALLOWED": "У вас слишком много нерассмотренных сообщений об оплате. Дождитесь ответа.",
        "PAYMENT_NOTICE_NOT_OPEN": "Это сообщение об оплате уже рассмотрено или его срок истёк.",
        "EXPORT_NOT_ALLOWED": "Выгрузка уже готовится, или на сегодня выгрузок больше нет.",
        "EXPORT_NOT_READY": "Файл этой выгрузки ещё не готов или срок его хранения истёк.",
        "FILE_STORE_UNAVAILABLE": "Хранилище файлов сейчас недоступно. Попробуйте ещё раз чуть позже.",
        "IMPORT_NOT_APPLICABLE": "Этот импорт в его нынешнем состоянии применить нельзя. Обновите просмотр.",
        "IMPORT_UNDO_REFUSED": "Этот импорт отменить нельзя: срок истёк или по записям уже есть оплаты.",
        "SUBSCRIPTION_RECEIPT_NOT_ALLOWED": (
            "У вас слишком много нерассмотренных чеков. Дождитесь ответа администратора."
        ),
        "RECEIPT_ALREADY_DECIDED": "По этому чеку решение уже принято.",
        "CASH_ENTRY_OF_LEDGER": (
            "Эта запись — оплата клиента. Чтобы отменить её, отмените эту оплату на странице клиента."
        ),
        "CASH_ENTRY_OF_STOCK": (
            "Эту запись сделал склад (оплата поставщику, закупка товара или возврат). "
            "Чтобы отменить её, отмените саму оплату или документ."
        ),
        "CASH_ENTRY_CANCELLED": "Эта запись кассы уже отменена.",
        "CASH_CATEGORY_ARCHIVED": "Эта статья в архиве. Выберите другую или верните её из архива.",
        "CASH_CATEGORY_NAME_TAKEN": "Статья с таким названием уже есть (возможно, в архиве).",
        "CASH_CATEGORY_FIXED": "Статью, в которую попадают оплаты клиентов, нельзя архивировать или удалить.",
        "CASH_CATEGORY_IN_USE": "В этой статье есть записи, удалить её нельзя. Можно архивировать.",
        "ONLINE_PAY_OFF": "Онлайн-оплата пока не включена. Оплата переводом на карту: /obuna",
        "STOCK_INSUFFICIENT": "На складе недостаточно этого товара. Сначала запишите приход или уменьшите количество.",
        "STOCK_ALREADY_USED": (
            "Отменить нельзя: эти товары уже ушли со склада. Оформите возврат поставщику или списание."
        ),
        "COST_CURRENCY_MISMATCH": (
            "Себестоимость этого товара ведётся в другой валюте. "
            "Приход в другой валюте возможен, когда остаток закончится."
        ),
        "ENTRY_OF_DOCUMENT": "Эту запись создал документ. Чтобы отменить её, отмените сам документ.",
        "BARCODE_TAKEN": "Этот штрихкод уже привязан к другому товару.",
        "ITEM_NOT_COUNTABLE": (
            "Этот товар нельзя учитывать на складе: сначала проверьте его и выберите складскую единицу измерения."
        ),
        "DOCUMENT_NOT_DRAFT": "Этот документ уже проведён или отменён.",
        "DOCUMENT_CANCELLED": "Этот документ уже отменён.",
        "SUPPLIER_NAME_TAKEN": "Поставщик с таким названием уже есть (возможно, он в архиве).",
        "SUPPLIER_ARCHIVED": "Этот поставщик в архиве. Сначала верните его из архива.",
        "SUPPLIER_HAS_BALANCE": "Поставщика с незакрытыми расчётами нельзя отправить в архив.",
        "NETWORK_STATE": ("Сейчас это действие недоступно: состояние изменилось или этот шаг делает партнёр."),
        "NETWORK_INVITE_INVALID": "Код приглашения недействителен или устарел. Попросите у партнёра новый.",
        "NETWORK_LINK_EXISTS": "Такая связь с этим магазином уже есть или ожидает ответа.",
        "NETWORK_TOO_MANY_INVITES": "Слишком много открытых кодов приглашения. Сначала отзовите старые.",
        "NETWORK_PARTNER_UNAVAILABLE": "Магазин партнёра сейчас недоступен.",
        "NETWORK_COUNTERPART_INVALID": (
            "Запись поставщика или клиента для партнёра не подходит. "
            "Выберите активную запись, не связанную с другим партнёром."
        ),
        "NETWORK_CURRENCY": "Один из двух магазинов не работает в этой валюте.",
        "NETWORK_BOOKS_MISMATCH": "Запись в учёте не совпала с документом. Ничего не сохранено, повторите попытку.",
        "NETWORK_PARTNER_REFUSED": (
            "Учёт партнёра не смог принять эту запись. Ничего не сохранено. Свяжитесь с партнёром."
        ),
        "RATE_LIMITED": "Слишком много запросов. Подождите немного и повторите.",
        "TIMEOUT": "Запрос выполнялся слишком долго и был остановлен. Ничего не сохранено. Повторите попытку.",
        "ERROR": "Произошла ошибка.",
    },
}


# What Tajik, Karakalpak and English have of their own; Uzbek Cyrillic is made from the Uzbek text.
_PARTIAL = {"tg": texts_tg.ERRORS, "kaa": texts_kaa.ERRORS, "en": texts_en.ERRORS}


@cache
def message_text(lang: str, code: str) -> str:
    """The wording of a refusal in a language; in Uzbek where the language does not have it."""
    source = _MESSAGES["uz"][code]
    if lang == languages.UZ_CYRILLIC:
        return to_cyrillic(source)
    return _MESSAGES.get(lang, _PARTIAL.get(lang, {})).get(code, source)


# The messages that name a number the refusal carries in its fields.
_WITH_FIELDS = {"FREE_PLAN_FULL": ("limit",)}


# The messages that say more than their code does (`AppError.wording`), and the code each belongs to.
# They are not codes: a client never sees their names, only their words under the code's own name.
_WORDINGS = {"GOODS_NOT_IN_DOLLARS": "VALIDATION", "CUSTOMER_UNREACHABLE_USD": "CUSTOMER_UNREACHABLE"}


def error_response(
    code: str, lang: str, fields: dict[str, str] | None = None, wording: str | None = None
) -> JSONResponse:
    """`wording` picks a more exact message for the same code; one that is not this code's is ignored."""
    said = wording if wording is not None and _WORDINGS.get(wording) == code else code
    message = message_text(lang, said if said in _MESSAGES["uz"] else "ERROR")
    if code in _WITH_FIELDS:
        message = message.format(**{name: (fields or {}).get(name, "") for name in _WITH_FIELDS[code]})
    body = {"error": {"code": code, "message": message, "fields": fields or {}}}
    return JSONResponse(body, status_code=_STATUS.get(code, 500))


async def app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    lang = getattr(request.state, "lang", "uz")
    response = error_response(exc.code, lang, exc.fields, exc.wording)
    retry_after = getattr(exc, "retry_after", None)
    if retry_after is not None:
        response.headers["Retry-After"] = str(retry_after)
    return response

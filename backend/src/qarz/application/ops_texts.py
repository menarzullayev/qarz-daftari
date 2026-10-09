"""Texts of the operations alerts in Uzbek and Russian (ADR-021; DEC-078).

They are part of the chat catalogs (`chat_texts.UZ` and `chat_texts.RU` include them), so the same test
holds them to the same keys and placeholders in both languages. One text for each rule of
`qarz.domain.ops_alerts.RULES`, under `ops_rule_<name>`, and the few lines every message is built of.

A text names what is wrong and nothing else: no shop, no person, no amount. What varies in a message is
the rule's label (a channel, a job, a backup type, a path), a figure and two moments.
"""

OPS_UZ = {
    "ops_title": "⚠️ {brand}: tizim nazorati",
    "ops_firing": "🔴 Boshlandi:",
    "ops_reminder": "🟠 Hali ham davom etmoqda:",
    "ops_resolved": "🟢 Tuzaldi:",
    "ops_line": "• {name}: {text}{value} ({since} dan beri)",
    "ops_line_resolved": "• {name}: {text} ({since} — {until})",
    "ops_more": "…yana {count} ta ogohlantirish keyingi xabarda.",
    "ops_footer": "Nima qilish kerak: runbook 16 (docs/10-operations/runbooks.md).",
    "ops_test": (
        "✅ {brand}: SINOV ogohlantirishi ({at}).\n"
        "Bu haqiqiy nosozlik emas. Tizim nazorati xabarlari shu chatga yetib kelishini tekshirish uchun yuborildi."
    ),
    "ops_db_down": (
        "🔴 {brand}: ishchi jarayon ma'lumotlar bazasiga ulana olmayapti ({since} dan beri).\n"
        "Nazorat holati saqlanmayapti, boshqa tekshiruvlar to'xtagan. Runbook 16."
    ),
    "ops_db_up": "🟢 {brand}: ma'lumotlar bazasi yana ishlayapti ({since} — {until}).",
    "ops_rule_ErrorRateHigh": "so'rovlarning 2% dan ko'pi server xatosi bilan tugayapti",
    "ops_rule_OutboxOld": "xabarlar yuborilmayapti: navbatdagi xabar 10 daqiqadan ko'p kutmoqda",
    "ops_rule_RemindersNotRunning": "eslatmalar ishi bir soatdan beri tugallanmagan",
    "ops_rule_SmsRefused": "SMS provayder tomonidan rad etildi yoki bir kundan keyin tashlab yuborildi",
    "ops_rule_SmsNotGoingOut": "SMS navbatda turibdi, provayder qabul qilmayapti",
    "ops_rule_ReceiptsWaiting": "obuna cheki bir kundan ortiq qaror kutmoqda",
    "ops_rule_CrossTenantAttempt": "tizimga kirgan foydalanuvchi o'ziga tegishli bo'lmagan do'konni so'radi",
    "ops_rule_InvalidSignaturesRepeated": "noto'g'ri imzo bilan takroriy urinishlar",
    "ops_rule_AdminSecondFactorRepeated": "administratorning ikkinchi omil kodi qayta-qayta rad etildi",
    "ops_rule_SupportAccessOpened": "administrator do'konga yordam uchun kirish ochdi",
    "ops_rule_AdminWithoutSupportAccess": "administrator yordam kirishisiz do'kon ma'lumotini so'radi",
    "ops_rule_ShopOwnerReassigned": "administrator do'kon egasini almashtirdi",
    "ops_rule_MetricsMissing": "API ko'rsatkichlarini o'qib bo'lmayapti",
    "ops_rule_BackupFailed": "oxirgi zaxira nusxa olish muvaffaqiyatsiz tugadi",
    "ops_rule_BackupMissing": "omborda yangi zaxira nusxa yo'q (26 soatda birorta ham, yoki 8 kunda to'liq nusxa)",
    "ops_rule_WalArchiveStale": "WAL arxivi 5 daqiqadan eski yoki tekshiruvning o'zi ishlamayapti",
    "ops_rule_RestoreTestNotPassed": "tiklash sinovi 8 kun ichida o'tmagan yoki hech qachon o'tmagan",
    "ops_rule_RestoreTestFailed": "oxirgi tiklash sinovi muvaffaqiyatsiz tugadi",
    "ops_rule_FilesCopyStale": "saqlangan fayllar omborga nusxalanmayapti",
    "ops_rule_DiskAlmostFull": "disk 80% dan ko'p to'lgan",
    "ops_rule_JobNotRunning": "rejali ish o'z davrini tugallamagan",
    "ops_rule_LedgerMismatch": "saqlangan ochiq qarzlar daftar yozuvlaridan farq qilmoqda",
    "ops_rule_StockMismatch": "ombordagi saqlangan qoldiq yoki ta'minotchiga qarz o'z yozuvlaridan farq qilmoqda",
    "ops_rule_ApiDown": "API /healthz so'roviga javob bermayapti",
    "ops_rule_TelegramRefusesBot": "Telegram bot tokenini rad etmoqda",
    "ops_rule_TelegramUnreachable": "Telegram bilan aloqa yo'q",
    "ops_rule_DispatcherFailing": "xabar yuboruvchi 5 daqiqadan beri birorta aylanishni tugallamagan",
}

OPS_RU = {
    "ops_title": "⚠️ {brand}: контроль системы",
    "ops_firing": "🔴 Началось:",
    "ops_reminder": "🟠 Всё ещё продолжается:",
    "ops_resolved": "🟢 Исправлено:",
    "ops_line": "• {name}: {text}{value} (с {since})",
    "ops_line_resolved": "• {name}: {text} ({since} — {until})",
    "ops_more": "…ещё предупреждений: {count}, они будут в следующем сообщении.",
    "ops_footer": "Что делать: runbook 16 (docs/10-operations/runbooks.md).",
    "ops_test": (
        "✅ {brand}: ПРОБНОЕ предупреждение ({at}).\n"
        "Это не настоящая неисправность. Оно отправлено, чтобы проверить, что сообщения контроля доходят в этот чат."
    ),
    "ops_db_down": (
        "🔴 {brand}: рабочий процесс не может подключиться к базе данных (с {since}).\n"
        "Состояние контроля не сохраняется, остальные проверки остановлены. Runbook 16."
    ),
    "ops_db_up": "🟢 {brand}: база данных снова работает ({since} — {until}).",
    "ops_rule_ErrorRateHigh": "более 2% запросов завершаются ошибкой сервера",
    "ops_rule_OutboxOld": "сообщения не отправляются: очередное ждёт больше 10 минут",
    "ops_rule_RemindersNotRunning": "задание напоминаний не завершалось больше часа",
    "ops_rule_SmsRefused": "SMS отклонено провайдером или снято после суток попыток",
    "ops_rule_SmsNotGoingOut": "SMS стоят в очереди, провайдер их не принимает",
    "ops_rule_ReceiptsWaiting": "чек подписки ждёт решения больше суток",
    "ops_rule_CrossTenantAttempt": "вошедший пользователь запросил магазин, который ему не принадлежит",
    "ops_rule_InvalidSignaturesRepeated": "повторные попытки с неверной подписью",
    "ops_rule_AdminSecondFactorRepeated": "код второго фактора администратора отклонён несколько раз подряд",
    "ops_rule_SupportAccessOpened": "администратор открыл доступ поддержки к магазину",
    "ops_rule_AdminWithoutSupportAccess": "администратор запросил данные магазина без доступа поддержки",
    "ops_rule_ShopOwnerReassigned": "администратор сменил владельца магазина",
    "ops_rule_MetricsMissing": "показатели API не читаются",
    "ops_rule_BackupFailed": "последнее резервное копирование завершилось неудачей",
    "ops_rule_BackupMissing": "в хранилище нет свежей копии (ни одной за 26 часов или полной за 8 дней)",
    "ops_rule_WalArchiveStale": "архив WAL старше 5 минут, или сама проверка не выполняется",
    "ops_rule_RestoreTestNotPassed": "проверка восстановления не проходила 8 дней или не проходила никогда",
    "ops_rule_RestoreTestFailed": "последняя проверка восстановления завершилась неудачей",
    "ops_rule_FilesCopyStale": "сохранённые файлы не копируются в хранилище",
    "ops_rule_DiskAlmostFull": "диск заполнен больше чем на 80%",
    "ops_rule_JobNotRunning": "плановое задание не завершило свой период",
    "ops_rule_LedgerMismatch": "сохранённые открытые долги расходятся с записями журнала",
    "ops_rule_StockMismatch": "сохранённый остаток склада или долг поставщику расходится со своими записями",
    "ops_rule_ApiDown": "API не отвечает на /healthz",
    "ops_rule_TelegramRefusesBot": "Telegram отклоняет токен бота",
    "ops_rule_TelegramUnreachable": "нет связи с Telegram",
    "ops_rule_DispatcherFailing": "отправка сообщений не завершила ни одного круга за 5 минут",
}

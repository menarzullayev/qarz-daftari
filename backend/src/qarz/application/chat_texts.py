"""Texts of the staff chat (ADR-021: interface text lives in catalogs, not in logic).

Uzbek defines the set of keys; `tests/test_chat_texts.py` requires Russian to have the same keys and the
same placeholders. The other languages (`qarz.domain.languages`) may trail behind: Tajik, Karakalpak and
English are in `texts_tg`, `texts_kaa` and `texts_en`, Uzbek Cyrillic is made from the Uzbek text, and a
key that a language does not have is read in Uzbek (`say`). `tests/test_languages.py` holds them to
that; `scripts/i18n_missing.py` lists what each still lacks.
"""

from collections.abc import Mapping
from datetime import date
from functools import cache

from qarz.application import texts_en, texts_kaa, texts_tg
from qarz.application.ops_texts import OPS_RU, OPS_UZ
from qarz.domain import languages
from qarz.domain.money import Currency, format_money
from qarz.domain.uz_cyrillic import to_cyrillic

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
    # /ombor: what runs low in the active shop (only while the stock is switched on).
    "ombor_low": "📦 {shop}: kam qolgan tovarlar",
    "ombor_line": "• {name}: {qty} {unit} (chegara {low})",
    "ombor_more": "…va yana boshqalar. To'liq ro'yxat ilovadagi «Ombor» bo'limida.",
    "ombor_none": "📦 {shop}: kam qolgan tovar yo'q.",
    "cash_help": "/kassa — bugungi kassa: kirim, chiqim va qoldiq",
    "cash_today": "💰 {shop}\nKassa, {date}",
    "cash_line": "{method}: kirim {income}, chiqim {expense}, qoldiq {closing}",
    "cash_total": "Jami: kirim {income}, chiqim {expense}, qoldiq {closing}",
    "cash_method_cash": "Naqd",
    "cash_method_card": "Karta",
    "cash_method_transfer": "O'tkazma",
    "cash_forbidden": "Kassani ko'rish uchun sizda ruxsat yo'q. Do'kon egasiga murojaat qiling.",
    "only_text": "Hozircha faqat matnli xabarlarni tushunaman. Namuna: Ali 45000",
    "open_shop": "🏪 Do'kon ochish",
    "new_shop": "➕ Yangi do'kon",
    "ask_shop_name": "Do'koningiz nomini yozing (80 belgigacha).",
    "shop_name_invalid": "Do'kon nomi 1 dan 80 belgigacha bo'lishi kerak. Qaytadan yozing.",
    "shop_created_limited": (
        "✅ «{shop}» do'koni ochildi.\n\nBepul sinov muddati faqat birinchi do'konga beriladi. "
        "Bu do'konda nasiya yozish uchun obuna to'lovini qiling."
    ),
    "shop_created_free": (
        "✅ «{shop}» do'koni ochildi.\n\nBepul sinov muddati faqat birinchi do'konga beriladi. Bu do'kon "
        "bepul tarifda ishlaydi: nasiya yozishingiz mumkin, masalan: Ali 45000. Tarif haqida: /obuna"
    ),
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
    "forbidden_permission": "Bu amal uchun sizda ruxsat yo'q. Do'kon egasiga murojaat qiling.",
    "not_found": "Yozuv topilmadi.",
    "parse_hint": "Tushunmadim. Namuna:\nAli 45000\nAli -20000\nAli 20000 berdi",
    "parse_amount_not_whole": "Summa butun so'mda yoziladi, tiyinsiz. Namuna: Ali 45000",
    "parse_ambiguous": "Qaysi son summa ekanini ajrata olmadim. Avval ism, keyin bitta summa yozing: Ali 45000",
    "parse_too_long": "Xabar juda uzun. Qisqaroq yozing: Ali 45000 izoh",
    "amount_range": "Summa 100 so'mdan 100 000 000 so'mgacha bo'lishi kerak.",
    # Only a shop that works in dollars is ever told these.
    "amount_range_usd": "Dollardagi summa 0.01 $ dan 10 000 $ gacha bo'lishi kerak.",
    "parse_amount_too_precise": "Dollar summasida nuqtadan keyin ko'pi bilan ikki raqam yoziladi. Namuna: Ali 50.25$",
    "parse_ambiguous_usd": (
        "Dollar summasini aniq tushuna olmadim. Bitta summa va bitta valyuta yozing: Ali 50$ yoki Ali 1250.50$"
    ),
    "notice_amount_invalid_usd": (
        "Faqat summani yozing. So'mda: 50000. Dollarda summadan keyin $ belgisini qo'ying: 50$ yoki 50.25$"
    ),
    "two_amounts": "{first} va {second}",
    "PROMISE_BEFORE_SALE": "Muddat savdo kunidan oldin bo'lishi mumkin emas.",
    "PROMISE_TOO_FAR": "Muddat savdo kunidan ko'pi bilan 365 kun keyin bo'lishi mumkin.",
    "SUBSCRIPTION_LIMITED": "Obuna tugagan: yangi nasiya yozilmaydi. To'lov qabul qilish ishlayveradi. /obuna",
    "FREE_PLAN_FULL": (
        "Bepul tarif {limit} tagacha mijozni o'z ichiga oladi, yangi mijoz qo'shilmadi. "
        "Ko'proq mijoz uchun obuna to'lang: /obuna"
    ),
    "SHOP_SUSPENDED": "Do'kon vaqtincha to'xtatilgan.",
    "EXCEEDS_BALANCE": "To'lov mijozning qarzidan katta bo'lishi mumkin emas.",
    "CUSTOMER_ARCHIVED": "Bu mijoz arxivda. Avval arxivdan chiqaring.",
    "ALREADY_REVERSED": "Bu yozuv allaqachon bekor qilingan.",
    # The entry a customer's return of goods wrote: it is cancelled with its document, in the application.
    "ENTRY_OF_DOCUMENT": (
        "Bu yozuvni tovar qaytarish hujjati yaratgan. Uni bekor qilish uchun ilovadagi «Ombor» bo'limida "
        "hujjatning o'zini bekor qiling."
    ),
    "CANNOT_REVERSE_REVERSAL": "Bekor qilish yozuvini bekor qilib bo'lmaydi.",
    "WOULD_GO_NEGATIVE": "Bekor qilinsa qarz manfiy bo'lib qoladi. Avval keyingi to'lovni bekor qiling.",
    "error": "Xatolik yuz berdi. Qaytadan urinib ko'ring.",
    "TIMEOUT": "Juda uzoq davom etdi va to'xtatildi. Hech narsa yozilmadi. Qaytadan urinib ko'ring.",
    "consent_v2": (
        "{shop} do'koni sizning nasiya xaridlaringiz va to'lovlaringizni Qarz Daftari xizmati orqali "
        "yuritadi. Saqlanadigan ma'lumotlar: do'kon sizni qanday nomlagani, telefon raqamingiz (agar bergan "
        "bo'lsangiz), Telegram hisobingiz identifikatori, nasiya va to'lov yozuvlari, olingan mahsulotlar. "
        "Maqsad: qarz hisobini siz ham ko'rib turishingiz va eslatmalar yuborish. Ma'lumotlar faqat sizga "
        "va shu do'kon xodimlariga ko'rinadi, boshqa do'konlarga berilmaydi. Istalgan payt /uzish orqali "
        "uzilishingiz yoki /ochirish orqali ma'lumotlaringizni o'chirishni so'rashingiz mumkin. Rozimisiz?"
    ),
    "consent_yes": "✅ Roziman",
    "consent_no": "Yo'q",
    "consent_declined": "Rozilik berilmadi. Siz haqingizda hech narsa saqlanmadi.",
    "linked": (
        "✅ Siz «{shop}» do'konidagi hisobingizga ulandingiz. Endi har bir yozuv haqida shu yerda xabar "
        "olasiz.\n"
        "Qarzingizni ko'rish: /qarzim"
    ),
    "waiting_ok": "✅ So'rovingiz «{shop}» do'koniga yuborildi. Sotuvchi sizni daftardagi yozuvingizga ulaydi.",
    "waiting_full": (
        "«{shop}» do'konida ulanishni kutayotganlar ro'yxati hozir to'la. "
        "Sotuvchidan ro'yxatni ko'rib chiqishni so'rang yoki keyinroq qayta urinib ko'ring."
    ),
    "link_invalid": "Bu havola yaroqsiz yoki muddati o'tgan. Do'kondan yangisini so'rang.",
    "link_taken": "Bu hisob boshqa Telegram hisobiga ulangan. Do'konga murojaat qiling.",
    "link_already": "Siz bu do'konga allaqachon ulangansiz yoki ulanishni kutyapsiz. Qarzingiz: /qarzim",
    "accounts_header": "Sizning qarzlaringiz:",
    "account_line": "{shop}: {balance}",
    "no_accounts": "Siz hali hech bir do'kondagi hisobga ulanmagansiz. Do'kondan havola yoki QR kod so'rang.",
    "unlink_choose": "Qaysi do'kondan uzilasiz? Xabarlar to'xtaydi; do'kon daftaridagi yozuvlar qoladi.",
    "unlink_button": "Uzilish: {shop}",
    "unlinked": "Siz «{shop}» do'konidan uzildingiz. Endi bu do'kondan xabar kelmaydi.",
    "n_credit": (
        "{shop}\n{name}, sizga nasiya yozildi: {amount}\n{goods}To'lash muddati: {date}\nJami qarzingiz: {balance}"
    ),
    "n_payment": "{shop}\n{name}, to'lovingiz qabul qilindi: {amount}\nQolgan qarzingiz: {balance}",
    "n_reversed_credit": "{shop}\n{name}, {amount} nasiya yozuvi bekor qilindi.\nJami qarzingiz: {balance}",
    "n_reversed_payment": "{shop}\n{name}, {amount} to'lov yozuvi bekor qilindi.\nJami qarzingiz: {balance}",
    "n_promise": "{shop}\n{name}, {amount} nasiyaning to'lash muddati: {date}\nJami qarzingiz: {balance}",
    "n_line": "• {name} — {qty} {unit}: {total}",
    "n_more_lines": "… va yana {count} ta mahsulot",
    "removal_choose": "Qaysi do'kondagi ma'lumotlaringiz o'chirilsin?",
    "removal_button": "O'chirish: {shop}",
    "removal_confirm": (
        "«{shop}» do'konidagi ismingiz, telefon raqamingiz va Telegram hisobingiz bilan bog'lanish "
        "o'chiriladi. Qarz va to'lov summalari do'kon daftarida ismsiz qoladi. Qarzingiz bo'lsa, u to'liq "
        "to'langanda o'chiriladi. Buni ortga qaytarib bo'lmaydi. Davom etilsinmi?"
    ),
    "removal_yes": "Ha, o'chirilsin",
    "removal_done": "«{shop}» do'konidagi ma'lumotlaringiz o'chirildi.",
    "removal_waiting": (
        "So'rovingiz qabul qilindi. «{shop}» do'konidagi qarzingiz ({balance}) to'liq to'langach, "
        "ma'lumotlaringiz o'chiriladi."
    ),
    "dispute_button": "⚠️ E'tiroz bildirish",
    "ask_dispute_reason": "E'tirozingiz sababini qisqacha yozing (3 tadan 300 tagacha belgi).",
    "reason_invalid": "Sabab 3 tadan 300 tagacha belgi bo'lishi kerak. Tugmani qayta bosib, yana yozing.",
    "dispute_sent": "E'tirozingiz «{shop}» do'koniga yuborildi. Yozuv ko'rib chiqilguncha qarzingizda turadi.",
    "DISPUTE_NOT_ALLOWED": "Bu yozuv bo'yicha e'tiroz bildirib bo'lmaydi yoki u allaqachon ko'rib chiqilgan.",
    "s_dispute": "⚠️ {shop}\n{name} {amount} yozuviga e'tiroz bildirdi:\n«{reason}»",
    "decline_button": "Rad etish",
    "ask_decline_reason": "Rad etish sababini yozing. U mijozga yuboriladi.",
    "dispute_declined_staff": "E'tiroz rad etildi. Mijozga sababi bilan xabar yuborildi.",
    "n_dispute_declined": "{shop}\n{amount} yozuvi bo'yicha e'tirozingiz rad etildi.\nSabab: {reason}",
    "s_dispute_withdrawn": "{shop}\n{name} {amount} yozuvi bo'yicha e'tirozini qaytarib oldi.",
    "r1_due_today": "Assalomu alaykum, {name}! «{shop}» do'konidan eslatma: bugun {amount} to'lash kuni. Rahmat!",
    "r1_overdue": (
        "Assalomu alaykum, {name}! «{shop}» do'konidagi {amount} qarzingizning to'lash muddati o'tgan. "
        "Imkon topib to'lab qo'ysangiz, minnatdor bo'lamiz."
    ),
    "r2_due_today": "«{shop}»: {name}, bugun {amount} to'lash kuni.",
    "r2_overdue": "«{shop}»: {name}, {amount} qarzning to'lash muddati o'tgan. Iltimos, to'lab qo'ying.",
    "r3_due_today": "Hurmatli {name}, «{shop}» do'koni sizni qadrlaydi. Bugun {amount} to'lash kuni ekanini eslatamiz.",
    "r3_overdue": (
        "Hurmatli {name}, «{shop}» do'konidagi {amount} qarzingizning muddati o'tganini eslatamiz. Qulay "
        "vaqtda to'lab qo'ysangiz, xursand bo'lamiz."
    ),
    "sms_due_today": "{shop}: {name}, bugun {amount} to'lash kuni. Rahmat.",
    "sms_overdue": "{shop}: {name}, {amount} qarz muddati o'tgan. Iltimos, to'lab qo'ying.",
    "shop_suspended": (
        "«{shop}» do'koni xizmat ma'muriyati tomonidan to'xtatildi. Sabab: {reason}\n"
        "Endi faqat do'kon egasi ma'lumotlarni ko'ra oladi va eksport qila oladi."
    ),
    "shop_unsuspended": "«{shop}» do'koni yana ishlamoqda: to'xtatish bekor qilindi. Izoh: {reason}",
    # To an account that may be in someone else's hands: the bare fact and nothing else.
    "owner_reassigned_old": "«{shop}» do'konining egaligi xizmat ma'muriyati tomonidan o'zgartirildi.",
    "owner_reassigned_new": (
        "Xizmat ma'muriyati qaroriga ko'ra endi siz «{shop}» do'konining egasisiz. Do'kon panelda ochiladi."
    ),
    "owner_reassigned_new_deletion": (
        "Xizmat ma'muriyati qaroriga ko'ra endi siz «{shop}» do'konining egasisiz. Do'kon panelda ochiladi.\n"
        "Diqqat: do'kon o'chirishni kutmoqda, {due} kuni butunlay o'chiriladi. "
        "Buni panelda bekor qilishingiz mumkin."
    ),
    "support_opened": (
        "«{shop}»: xizmat ma'muri yordam berish uchun do'kon ma'lumotlarini ko'rish ruxsatini ochdi. "
        "Sabab: {reason}\n"
        "Ruxsat {until} gacha amal qiladi va o'zi tugaydi. U faqat ko'rish uchun: hech narsa o'zgartirilmaydi. "
        "Istalgan payt panelda yopishingiz mumkin."
    ),
    "support_closed": "«{shop}»: xizmat ma'muri do'kon ma'lumotlarini ko'rish ruxsatini yopdi.",
    "LIMIT_REACHED": "Bu savdo mijozning nasiya limitidan oshadi. Uni menejer yoki do'kon egasi yoza oladi.",
    "USD_BALANCE_OPEN": (
        "Dollarni o'chirib bo'lmaydi: mijozlarda dollarda qarz bor. Avval dollardagi barcha qarzlar yopilsin."
    ),
    "limit_warning": "⚠️ Qarz limitdan oshdi: limit {limit}, qarz {balance}.",
    "sub_header": "«{shop}» — obuna",
    "sub_state_trial": "Sinov muddati: {date} gacha ({days} kun qoldi).",
    "sub_state_active": "To'langan: {date} gacha ({days} kun qoldi).",
    "sub_state_limited": (
        "Muddat tugagan: yangi nasiya yozilmaydi. To'lov qabul qilish, ko'rish va mijozlarga xabarlar ishlayveradi."
    ),
    "sub_state_suspended": "Do'kon vaqtincha to'xtatilgan. Qo'llab-quvvatlashga murojaat qiling.",
    "sub_state_free": "Bepul tarif: muddati yo'q, do'kon to'liq ishlaydi.",
    "sub_customers": "Mijozlar: {used} ta. Bepul tarif {limit} tagacha mijozni o'z ichiga oladi.",
    "sub_then_free": "To'lov qilinmasa, do'kon bepul tarifda to'liq ishlayveradi ({limit} tagacha mijoz).",
    "sub_then_limited": (
        "To'lov qilinmasa, yangi nasiya yozilmaydi: sizda {used} ta mijoz bor, bepul tarif esa {limit} tagacha."
    ),
    "sub_quota_left": "SMS eslatmalar obunaga kiradi: shu oyda {left} ta qoldi (oyiga {quota} ta).",
    "sub_paying_adds": "To'lov qilsangiz, mijozlar soni cheklanmaydi.",
    "sub_paying_adds_quota": (
        "To'lov qilsangiz, mijozlar soni cheklanmaydi va oyiga {quota} tagacha SMS eslatma yuboriladi."
    ),
    "sub_price": "Narxi: oyiga {price}.",
    "sub_pay_to": "To'lov uchun karta — {label}: {card}. O'tkazmadan so'ng chekni shu yerga yuboring.",
    "sub_other_cards_button": "Boshqa karta ({count})",
    "sub_choose_card": "Qaysi kartaga to'laysiz? Tanlang.",
    "sub_cards_back": "⬅️ Orqaga",
    "receipt_card": "Karta: {card}",
    "sub_no_card": "To'lov rekvizitlari hali kiritilmagan.",
    "sub_paid_online": "«{shop}»: {amount} to'lov qabul qilindi. Obuna {date} gacha to'langan.",
    "sub_choose_months": "To'lovdan so'ng necha oy uchun to'laganingizni tanlang va chekni yuboring.",
    "sub_months_button": "{months} oy — {amount}",
    "ask_sub_receipt": (
        "«{shop}»: {months} oy uchun {amount}. Endi chekning rasmini yoki PDF faylini shu yerga yuboring."
    ),
    "sub_receipt_invalid": (
        "Bu faylni qabul qila olmadim. Chek JPEG, PNG yoki WebP rasm yoki PDF bo'lishi va 5 MB dan oshmasligi "
        "kerak. Qaytadan yuboring yoki bekor qiling."
    ),
    "sub_receipt_sent": (
        "✅ «{shop}»: chek qabul qilindi ({months} oy, {amount}). Administrator ko'rib chiqqach, sizga xabar beramiz."
    ),
    "sub_receipt_approved": "✅ «{shop}»: to'lov tasdiqlandi ({months} oy). Obuna {date} gacha to'langan.",
    "sub_receipt_rejected": "«{shop}»: {amount} to'lov cheki rad etildi. Sabab: {reason}",
    "a_receipt_new": "Yangi obuna cheki: «{shop}», {amount}, {months} oy. Admin panelda ko'rib chiqing.",
    "a_receipt_copies": "⚠️ Aynan shu fayl avval ham yuborilgan: {count} ta chekda.",
    "a_receipt_no_file": "⚠️ Chek faylini xabarga biriktirib bo'lmadi. Uni admin panelda ko'ring.",
    "receipt_approve_button": "✅ Tasdiqlash",
    "receipt_reject_button": "Rad etish",
    "a_sign_in_first": "Avval admin panelga kirib, kodingizni tasdiqlang. Shundan keyin bu tugmalar ishlaydi.",
    "a_receipt_approved": "✅ «{shop}»: chek tasdiqlandi ({months} oy). Obuna {date} gacha to'langan.",
    "a_receipt_rejected": "«{shop}»: chek rad etildi. Sabab: {reason}",
    "a_receipt_decided": "Bu chek bo'yicha qaror allaqachon qabul qilingan.",
    "a_receipt_use_panel": "Bu chekni admin panelda ko'rib chiqing: oylar sonini kiritish kerak.",
    # Shown over the button to a Telegram administrator of the review group (at most 200 characters).
    "g_reason_in_private": (
        "Rad etish sababini botga shaxsiy xabarda yozing. Bot bilan hali yozishmagan bo'lsangiz, "
        "avval botni ochib «Start»ni bosing va «Rad etish»ni qayta bosing."
    ),
    "g_receipt_needs_panel": "Bu chekda oylar soni ko'rsatilmagan. Uni platforma administratori panelda hal qiladi.",
    "ask_receipt_reject_reason": "Rad etish sababini yozing (3–500 belgi). U do'kon egasiga yuboriladi.",
    "a_reason_invalid": "Sabab 3 tadan 500 tagacha belgi bo'lishi kerak. Qaytadan yozing yoki bekor qiling.",
    "sub_trial_ending": "«{shop}»: sinov muddati {days} kundan keyin, {date} kuni tugaydi. Davom ettirish: /obuna",
    "sub_paid_ending": "«{shop}»: to'langan muddat {days} kundan keyin, {date} kuni tugaydi. Uzaytirish: /obuna",
    "sub_limited": (
        "«{shop}»: obuna muddati tugadi. Endi yangi nasiya yozilmaydi; to'lov qabul qilish, ko'rish va "
        "mijozlarga xabarlar ishlayveradi. To'lash: /obuna"
    ),
    "sub_free_now": (
        "«{shop}»: muddat tugadi. Do'kon bepul tarifga o'tdi va to'liq ishlayveradi: {limit} tagacha mijoz. "
        "Ko'proq mijoz kerak bo'lsa: /obuna"
    ),
    "move_date_button": "📅 Muddatni ko'chirish",
    "ask_move_date": "To'lash muddati qaysi sanaga ko'chirilsin? kun.oy ko'rinishida yozing, masalan 25.10",
    "move_date_invalid": "Sanani tushunmadim. kun.oy ko'rinishida yozing, masalan 25.10",
    "date_request_sent": (
        "So'rovingiz «{shop}» do'koniga yuborildi: to'lash muddatini {date} ga ko'chirish. Javobi shu yerga keladi."
    ),
    "REQUEST_ALREADY_OPEN": "Bu yozuv bo'yicha muddatni ko'chirish so'rovi allaqachon ko'rib chiqilmoqda.",
    "DATE_REQUEST_NOT_ALLOWED": "Bu yozuv muddatini ko'chirish so'rovini hozir qabul qilib bo'lmaydi.",
    "PROMISE_NOT_CHANGEABLE": "Bu yozuvning to'lash muddati yo'q yoki yozuv bekor qilingan.",
    "date_not_later": "Yangi sana hozirgi to'lash muddatidan keyin bo'lishi kerak.",
    "date_fully_paid": "Bu yozuv to'liq to'langan yoki bekor qilingan: muddatini ko'chirishga hojat yo'q.",
    "date_declined_recently": (
        "Bu yozuv bo'yicha so'rovingiz yaqinda rad etilgan. Rad etilganidan 7 kun o'tgach qayta so'rash mumkin."
    ),
    "date_request_closed": "Bu so'rov allaqachon ko'rib chiqilgan yoki o'z kuchini yo'qotgan.",
    "s_date_request": (
        "📅 {shop}\n{name} {amount} nasiyaning to'lash muddatini {old} dan {date} ga ko'chirishni so'rayapti."
    ),
    "accept_button": "✅ Qabul qilish",
    "reason_line": "Sabab: {reason}",
    "date_accepted_staff": "✅ {name}: {amount} nasiyaning to'lash muddati {date} ga ko'chirildi.",
    "date_declined_staff": "So'rov rad etildi: {name}, {amount}. To'lash muddati o'zgarmadi.",
    "n_date_accepted": (
        "{shop}\n{amount} nasiya muddatini ko'chirish so'rovingiz qabul qilindi.\nYangi to'lash muddati: {date}"
    ),
    "n_date_declined": "{shop}\n{amount} nasiya muddatini {date} ga ko'chirish so'rovingiz rad etildi.",
    "n_date_changed": "{shop}\n{name}, {amount} nasiyaning to'lash muddati o'zgartirildi: {old} → {date}",
    "n_opening": (
        "{shop}\n{name}, daftarga oldingi qarzingiz kiritildi: {amount}\n"
        "To'lash muddati: {date}\nJami qarzingiz: {balance}"
    ),
    "s_import_applied": (
        "📥 {shop}\nImport qo'llandi: {entries} ta qarz yozuvi, jami {amount}. Yangi mijozlar: {customers} ta.\n"
        "24 soat ichida butunlay bekor qilish mumkin."
    ),
    "s_import_undone": "↩️ {shop}\nImport bekor qilindi: {entries} ta yozuv qaytarildi. Import summasi: {amount}.",
    "import_checked": (
        "📥 {shop}\nImport fayli tekshirildi: {rows} ta qator, xatosiz. Ilovada ko'rib chiqib, qo'llashingiz mumkin."
    ),
    "import_rejected": (
        "📥 {shop}\nImport faylining {errors} ta qatorida xato topildi. Ilovada ro'yxatini ko'rib, "
        "tuzatilgan faylni qayta yuklang."
    ),
    "import_unreadable": "📥 {shop}\nImport faylini jadval sifatida o'qib bo'lmadi. Sababi ilovada ko'rsatilgan.",
    "import_refused": (
        "📥 {shop}\nImport qo'llanmadi: tekshiruvdan keyin ma'lumotlar o'zgargan. Ilovada qayta ko'rib chiqing."
    ),
    "import_refused_free_plan": (
        "📥 {shop}\nImport qo'llanmadi: bepul tarif {limit} tagacha mijozni o'z ichiga oladi. "
        "Ko'proq mijoz uchun obuna to'lang: /obuna"
    ),
    "import_undo_refused": (
        "↩️ {shop}\nImportni bekor qilib bo'lmadi: uning yozuvlariga to'lov qilingan. Hech narsa o'zgarmadi."
    ),
    "import_failed": (
        "📥 {shop}\nImport bo'yicha so'ralgan amal bajarilmadi. Hech narsa o'zgarmadi; qayta urinib ko'ring."
    ),
    "shop_deletion_requested": (
        "«{shop}» do'konini o'chirish so'raldi. Ma'lumotlar {date} kuni butunlay o'chiriladi. Shu kungacha "
        "eksport qilishingiz yoki bekor qilishingiz mumkin."
    ),
    "shop_deletion_cancelled": "«{shop}» do'konini o'chirish bekor qilindi. Do'kon avvalgidek ishlaydi.",
    "shop_erased": "«{shop}» do'koni va uning barcha ma'lumotlari o'chirildi.",
    # --- payment notices (REQ-060, REQ-061) ---
    "notice_choose_shop": "Qaysi do'konga to'lov qilganingizni bildirasiz?",
    "notice_shop_button": "{shop}: {balance}",
    "notice_nothing_owed": "«{shop}» do'konida qarzingiz yo'q.",
    "ask_notice_amount": (
        "«{shop}» do'konidagi qarzingiz: {balance}\nQancha to'ladingiz? Faqat summani yozing, masalan: 50000"
    ),
    "notice_amount_invalid": (
        "Faqat summani yozing, masalan: 50000 yoki 50 000. Summa butun so'mda, 100 so'mdan 100 000 000 so'mgacha."
    ),
    "notice_amount_exceeds": "Summa qarzingizdan katta bo'lishi mumkin emas. Qarzingiz: {balance}",
    "ask_notice_receipt": (
        "To'lov: {amount}\nChekingiz bo'lsa, uning rasmini yoki PDF faylini shu yerga yuboring. "
        "Chek bo'lmasa, tugmani bosing."
    ),
    "notice_without_receipt": "Cheksiz yuborish",
    "notice_receipt_hint": "Chek rasmini yoki PDF faylini yuboring yoki tugmalardan birini bosing.",
    "notice_receipt_invalid": (
        "Bu faylni qabul qila olmadim. Chek JPEG, PNG yoki WebP rasm yoki PDF bo'lishi va 5 MB dan oshmasligi "
        "kerak. Qaytadan yuboring yoki tugmalardan birini bosing."
    ),
    "notice_sent": (
        "✅ {amount} to'lov haqidagi xabaringiz «{shop}» do'koniga yuborildi. Do'kon qabul qilgach, qarzingiz kamayadi."
    ),
    "s_notice": "💵 {shop}\n{name} {amount} to'laganini bildirdi.\nHozirgi qarzi: {balance}",
    "s_notice_receipt": (
        "💵 {shop}\n{name} {amount} to'laganini bildirdi.\nHozirgi qarzi: {balance}\n"
        "📎 Chek ilova qilingan: uni ilovada ko'rishingiz mumkin."
    ),
    "s_receipt_seen_before": "⚠️ Aynan shu chek bu do'konga avval ham yuborilgan.",
    "notice_accept_button": "✅ Qabul qilish",
    "notice_accepted_staff": "✅ {shop}\n{name}: to'lov {amount} qabul qilindi.\nQolgan qarzi: {balance}",
    "notice_declined_staff": "To'lov xabari rad etildi. Mijozga sababi bilan xabar yuborildi.",
    "n_notice_accepted": "{shop}\n{amount} to'lov haqidagi xabaringiz qabul qilindi.",
    "n_notice_corrected": (
        "{shop}\n{amount} to'lov haqidagi xabaringiz qabul qilindi, lekin to'lov {recorded} deb yozildi."
    ),
    "n_notice_declined": "{shop}\n{amount} to'lov haqidagi xabaringiz rad etildi.\nSabab: {reason}",
    "PAYMENT_NOTICE_NOT_ALLOWED": (
        "Ko'rib chiqilmagan to'lov xabarlaringiz juda ko'p. Avval do'kon ularga javob berishini kuting."
    ),
    "PAYMENT_NOTICE_NOT_OPEN": "Bu to'lov xabari allaqachon ko'rib chiqilgan yoki muddati o'tgan.",
    "FILE_STORE_UNAVAILABLE": "Fayllarni saqlash hozir ishlamayapti. Birozdan keyin qayta urinib ko'ring.",
    "export_ready": (
        "✅ «{shop}» do'konining eksporti tayyor. Uni ilovaning eksport bo'limidan yuklab oling; fayl 7 kun saqlanadi."
    ),
    "export_failed": "«{shop}» do'konining eksportini tayyorlab bo'lmadi. Birozdan keyin qaytadan so'rang.",
    "EXPORT_NOT_ALLOWED": "Eksport allaqachon tayyorlanmoqda yoki bugungi eksportlar soni tugagan.",
    "EXPORT_NOT_READY": "Bu eksport fayli hali tayyor emas yoki muddati o'tgan.",
    "SUBSCRIPTION_RECEIPT_NOT_ALLOWED": ("Ko'rib chiqilmagan cheklaringiz juda ko'p. Administrator javobini kuting."),
    "currency": "so'm",
    # The operations alerts (DEC-078), kept in their own module.
    **OPS_UZ,
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
    "ombor_low": "📦 {shop}: товары на исходе",
    "ombor_line": "• {name}: {qty} {unit} (порог {low})",
    "ombor_more": "…и другие. Полный список в разделе «Склад» приложения.",
    "ombor_none": "📦 {shop}: товаров на исходе нет.",
    "cash_help": "/kassa — касса за сегодня: приход, расход и остаток",
    "cash_today": "💰 {shop}\nКасса, {date}",
    "cash_line": "{method}: приход {income}, расход {expense}, остаток {closing}",
    "cash_total": "Итого: приход {income}, расход {expense}, остаток {closing}",
    "cash_method_cash": "Наличные",
    "cash_method_card": "Карта",
    "cash_method_transfer": "Перевод",
    "cash_forbidden": "У вас нет разрешения смотреть кассу. Обратитесь к владельцу магазина.",
    "only_text": "Пока я понимаю только текстовые сообщения. Пример: Али 45000",
    "open_shop": "🏪 Открыть магазин",
    "new_shop": "➕ Новый магазин",
    "ask_shop_name": "Напишите название вашего магазина (до 80 символов).",
    "shop_name_invalid": "Название магазина должно быть от 1 до 80 символов. Напишите ещё раз.",
    "shop_created_limited": (
        "✅ Магазин «{shop}» открыт.\n\nБесплатный пробный период даётся только первому магазину. "
        "Чтобы записывать долги в этом магазине, оплатите подписку."
    ),
    "shop_created_free": (
        "✅ Магазин «{shop}» открыт.\n\nБесплатный пробный период даётся только первому магазину. Этот "
        "магазин работает на бесплатном тарифе: можно записывать долги, например: Али 45000. О тарифе: /obuna"
    ),
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
    "forbidden_permission": "У вас нет разрешения на это действие. Обратитесь к владельцу магазина.",
    "not_found": "Запись не найдена.",
    "parse_hint": "Не понял. Пример:\nАли 45000\nАли -20000\nАли 20000 оплатил",
    "parse_amount_not_whole": "Сумма пишется в целых сумах, без тийинов. Пример: Али 45000",
    "parse_ambiguous": "Не удалось понять, какое число — сумма. Сначала имя, затем одна сумма: Али 45000",
    "parse_too_long": "Сообщение слишком длинное. Напишите короче: Али 45000 заметка",
    "amount_range": "Сумма должна быть от 100 до 100 000 000 сумов.",
    "amount_range_usd": "Сумма в долларах должна быть от 0.01 $ до 10 000 $.",
    "parse_amount_too_precise": "В сумме в долларах после точки не больше двух цифр. Пример: Али 50.25$",
    "parse_ambiguous_usd": (
        "Не удалось точно понять сумму в долларах. Напишите одну сумму и одну валюту: Али 50$ или Али 1250.50$"
    ),
    "notice_amount_invalid_usd": (
        "Напишите только сумму. В сумах: 50000. В долларах поставьте знак $ после суммы: 50$ или 50.25$"
    ),
    "two_amounts": "{first} и {second}",
    "PROMISE_BEFORE_SALE": "Срок не может быть раньше дня продажи.",
    "PROMISE_TOO_FAR": "Срок может быть не позже чем через 365 дней после продажи.",
    "SUBSCRIPTION_LIMITED": "Подписка истекла: новые продажи в долг недоступны. Приём оплат работает. /obuna",
    "FREE_PLAN_FULL": (
        "Бесплатный тариф вмещает до {limit} клиентов, новый клиент не добавлен. "
        "Чтобы добавить больше, оплатите подписку: /obuna"
    ),
    "SHOP_SUSPENDED": "Магазин временно приостановлен.",
    "EXCEEDS_BALANCE": "Оплата не может быть больше долга клиента.",
    "CUSTOMER_ARCHIVED": "Этот клиент в архиве. Сначала верните его из архива.",
    "ALREADY_REVERSED": "Эта запись уже отменена.",
    "ENTRY_OF_DOCUMENT": (
        "Эту запись создал документ возврата товара. Чтобы отменить её, отмените сам документ "
        "в разделе «Склад» приложения."
    ),
    "CANNOT_REVERSE_REVERSAL": "Запись об отмене отменить нельзя.",
    "WOULD_GO_NEGATIVE": "После такой отмены долг стал бы отрицательным. Сначала отмените более позднюю оплату.",
    "error": "Произошла ошибка. Попробуйте ещё раз.",
    "TIMEOUT": "Это заняло слишком много времени и было остановлено. Ничего не записано. Попробуйте ещё раз.",
    "consent_v2": (
        "Магазин {shop} ведёт учёт ваших покупок в долг и оплат через сервис Qarz Daftari. Хранятся: как "
        "магазин вас записал, ваш номер телефона (если вы его дали), идентификатор вашего аккаунта "
        "Telegram, записи о долгах и оплатах, купленные товары. Цель: чтобы вы тоже видели свой долг, и для "
        "отправки напоминаний. Данные видны только вам и сотрудникам этого магазина и не передаются другим "
        "магазинам. В любой момент можно отключиться командой /uzish или попросить удалить ваши данные "
        "командой /ochirish. Вы согласны?"
    ),
    "consent_yes": "✅ Согласен",
    "consent_no": "Нет",
    "consent_declined": "Согласие не дано. О вас ничего не сохранено.",
    "linked": (
        "✅ Вы подключены к своему счёту в магазине «{shop}». Теперь о каждой записи вы будете узнавать "
        "здесь.\n"
        "Посмотреть долг: /qarzim"
    ),
    "waiting_ok": "✅ Запрос отправлен в магазин «{shop}». Продавец подключит вас к вашей записи в книге.",
    "waiting_full": (
        "В магазине «{shop}» список ожидающих подключения сейчас заполнен. "
        "Попросите продавца просмотреть список или попробуйте позже."
    ),
    "link_invalid": "Эта ссылка недействительна или устарела. Попросите в магазине новую.",
    "link_taken": "Этот счёт подключён к другому аккаунту Telegram. Обратитесь в магазин.",
    "link_already": "Вы уже подключены к этому магазину или ждёте подключения. Ваш долг: /qarzim",
    "accounts_header": "Ваши долги:",
    "account_line": "{shop}: {balance}",
    "no_accounts": "Вы пока не подключены ни к одному счёту в магазине. Попросите в магазине ссылку или QR-код.",
    "unlink_choose": "От какого магазина отключиться? Сообщения прекратятся; записи в книге магазина останутся.",
    "unlink_button": "Отключиться: {shop}",
    "unlinked": "Вы отключились от магазина «{shop}». Сообщений от него больше не будет.",
    "n_credit": "{shop}\n{name}, вам записан долг: {amount}\n{goods}Срок оплаты: {date}\nВсего долг: {balance}",
    "n_payment": "{shop}\n{name}, ваша оплата принята: {amount}\nОстаток долга: {balance}",
    "n_reversed_credit": "{shop}\n{name}, запись о долге {amount} отменена.\nВсего долг: {balance}",
    "n_reversed_payment": "{shop}\n{name}, запись об оплате {amount} отменена.\nВсего долг: {balance}",
    "n_promise": "{shop}\n{name}, срок оплаты долга {amount}: {date}\nВсего долг: {balance}",
    "n_line": "• {name} — {qty} {unit}: {total}",
    "n_more_lines": "… и ещё товаров: {count}",
    "removal_choose": "В каком магазине удалить ваши данные?",
    "removal_button": "Удалить: {shop}",
    "removal_confirm": (
        "В магазине «{shop}» будут удалены ваше имя, номер телефона и связь с вашим аккаунтом Telegram. "
        "Суммы долгов и оплат останутся в книге магазина без имени. Если у вас есть долг, данные будут "
        "удалены после его полной оплаты. Это нельзя отменить. Продолжить?"
    ),
    "removal_yes": "Да, удалить",
    "removal_done": "Ваши данные в магазине «{shop}» удалены.",
    "removal_waiting": (
        "Запрос принят. После полной оплаты долга в магазине «{shop}» ({balance}) ваши данные будут удалены."
    ),
    "dispute_button": "⚠️ Возразить",
    "ask_dispute_reason": "Коротко напишите причину возражения (от 3 до 300 символов).",
    "reason_invalid": "Причина должна быть от 3 до 300 символов. Нажмите кнопку ещё раз и напишите снова.",
    "dispute_sent": "Ваше возражение отправлено в магазин «{shop}». Пока его рассматривают, запись остаётся в долге.",
    "DISPUTE_NOT_ALLOWED": "По этой записи нельзя подать возражение, или оно уже рассмотрено.",
    "s_dispute": "⚠️ {shop}\n{name} возражает против записи {amount}:\n«{reason}»",
    "decline_button": "Отклонить",
    "ask_decline_reason": "Напишите причину отказа. Она будет отправлена клиенту.",
    "dispute_declined_staff": "Возражение отклонено. Клиенту отправлено сообщение с причиной.",
    "n_dispute_declined": "{shop}\nВаше возражение по записи {amount} отклонено.\nПричина: {reason}",
    "s_dispute_withdrawn": "{shop}\n{name} отозвал возражение по записи {amount}.",
    "r1_due_today": "Здравствуйте, {name}! Напоминание от магазина «{shop}»: сегодня срок оплаты {amount}. Спасибо!",
    "r1_overdue": (
        "Здравствуйте, {name}! Срок оплаты вашего долга {amount} в магазине «{shop}» прошёл. Будем "
        "благодарны, если вы оплатите его при возможности."
    ),
    "r2_due_today": "«{shop}»: {name}, сегодня срок оплаты {amount}.",
    "r2_overdue": "«{shop}»: {name}, срок оплаты долга {amount} прошёл. Пожалуйста, оплатите.",
    "r3_due_today": "Уважаемый(ая) {name}, магазин «{shop}» ценит вас. Напоминаем, что сегодня срок оплаты {amount}.",
    "r3_overdue": (
        "Уважаемый(ая) {name}, напоминаем, что срок оплаты вашего долга {amount} в магазине «{shop}» "
        "прошёл. Будем рады, если вы оплатите его в удобное время."
    ),
    "sms_due_today": "{shop}: {name}, сегодня срок оплаты {amount}. Спасибо.",
    "sms_overdue": "{shop}: {name}, срок оплаты долга {amount} прошёл. Пожалуйста, оплатите.",
    "shop_suspended": (
        "Магазин «{shop}» приостановлен администрацией сервиса. Причина: {reason}\n"
        "Теперь только владелец может просматривать и выгружать данные."
    ),
    "shop_unsuspended": "Магазин «{shop}» снова работает: приостановка снята. Комментарий: {reason}",
    "owner_reassigned_old": "Владелец магазина «{shop}» изменён администрацией сервиса.",
    "owner_reassigned_new": (
        "По решению администрации сервиса вы теперь владелец магазина «{shop}». Магазин открывается в панели."
    ),
    "owner_reassigned_new_deletion": (
        "По решению администрации сервиса вы теперь владелец магазина «{shop}». Магазин открывается в панели.\n"
        "Внимание: магазин ожидает удаления и будет удалён полностью {due}. "
        "Вы можете отменить это в панели."
    ),
    "support_opened": (
        "«{shop}»: администратор сервиса открыл доступ к просмотру данных магазина, чтобы помочь. "
        "Причина: {reason}\n"
        "Доступ действует до {until} и закончится сам. Он только для просмотра: ничего не изменяется. "
        "Вы можете закрыть его в панели в любой момент."
    ),
    "support_closed": "«{shop}»: администратор сервиса закрыл доступ к просмотру данных магазина.",
    "LIMIT_REACHED": "Эта продажа превысит лимит клиента. Записать её может менеджер или владелец.",
    "USD_BALANCE_OPEN": (
        "Доллары нельзя выключить: у клиентов есть долг в долларах. Сначала закройте все долги в долларах."
    ),
    "limit_warning": "⚠️ Долг превысил лимит: лимит {limit}, долг {balance}.",
    "sub_header": "«{shop}» — подписка",
    "sub_state_trial": "Пробный период: до {date} (осталось дней: {days}).",
    "sub_state_active": "Оплачено: до {date} (осталось дней: {days}).",
    "sub_state_limited": (
        "Срок истёк: новые продажи в долг не записываются. Приём оплат, просмотр и сообщения клиентам работают."
    ),
    "sub_state_suspended": "Магазин временно приостановлен. Обратитесь в поддержку.",
    "sub_state_free": "Бесплатный тариф: без срока, магазин работает полностью.",
    "sub_customers": "Клиентов: {used}. Бесплатный тариф вмещает до {limit} клиентов.",
    "sub_then_free": "Без оплаты магазин продолжит работать полностью на бесплатном тарифе (до {limit} клиентов).",
    "sub_then_limited": (
        "Без оплаты новые продажи в долг не записываются: у вас клиентов: {used}, "
        "а бесплатный тариф вмещает до {limit}."
    ),
    "sub_quota_left": "SMS-напоминания входят в подписку: в этом месяце осталось {left} (в месяц: {quota}).",
    "sub_paying_adds": "С оплатой число клиентов не ограничено.",
    "sub_paying_adds_quota": (
        "С оплатой число клиентов не ограничено и отправляется до {quota} SMS-напоминаний в месяц."
    ),
    "sub_price": "Цена: {price} в месяц.",
    "sub_pay_to": "Карта для оплаты — {label}: {card}. После перевода отправьте чек сюда.",
    "sub_other_cards_button": "Другая карта ({count})",
    "sub_choose_card": "На какую карту будете платить? Выберите.",
    "sub_cards_back": "⬅️ Назад",
    "receipt_card": "Карта: {card}",
    "sub_no_card": "Реквизиты для оплаты пока не указаны.",
    "sub_paid_online": "«{shop}»: оплата {amount} получена. Подписка оплачена до {date}.",
    "sub_choose_months": "После перевода выберите, за сколько месяцев вы заплатили, и пришлите чек.",
    "sub_months_button": "{months} мес. — {amount}",
    "ask_sub_receipt": "«{shop}»: {amount} за {months} мес. Теперь пришлите сюда фото чека или PDF-файл.",
    "sub_receipt_invalid": (
        "Не удалось принять этот файл. Чек должен быть изображением JPEG, PNG или WebP либо PDF и не больше "
        "5 МБ. Пришлите ещё раз или отмените."
    ),
    "sub_receipt_sent": (
        "✅ «{shop}»: чек принят ({months} мес., {amount}). Администратор проверит его, и мы вам сообщим."
    ),
    "sub_receipt_approved": "✅ «{shop}»: оплата подтверждена ({months} мес.). Подписка оплачена до {date}.",
    "sub_receipt_rejected": "«{shop}»: чек на {amount} отклонён. Причина: {reason}",
    "a_receipt_new": "Новый чек за подписку: «{shop}», {amount}, {months} мес. Проверьте в панели администратора.",
    "a_receipt_copies": "⚠️ Точно такой же файл уже присылали: в {count} чек.",
    "a_receipt_no_file": "⚠️ Файл чека не удалось приложить к сообщению. Посмотрите его в панели администратора.",
    "receipt_approve_button": "✅ Подтвердить",
    "receipt_reject_button": "Отклонить",
    "a_sign_in_first": "Сначала войдите в панель администратора и подтвердите код. После этого кнопки заработают.",
    "a_receipt_approved": "✅ «{shop}»: чек подтверждён ({months} мес.). Подписка оплачена до {date}.",
    "a_receipt_rejected": "«{shop}»: чек отклонён. Причина: {reason}",
    "a_receipt_decided": "По этому чеку решение уже принято.",
    "a_receipt_use_panel": "Рассмотрите этот чек в панели администратора: нужно указать число месяцев.",
    "g_reason_in_private": (
        "Напишите причину отказа боту в личном чате. Если вы ещё не писали боту, "
        "сначала откройте его, нажмите «Start» и нажмите «Отклонить» ещё раз."
    ),
    "g_receipt_needs_panel": "В этом чеке не указано число месяцев. Его рассмотрит администратор платформы в панели.",
    "ask_receipt_reject_reason": "Напишите причину отказа (3–500 символов). Она будет отправлена владельцу магазина.",
    "a_reason_invalid": "Причина должна быть от 3 до 500 символов. Напишите ещё раз или отмените.",
    "sub_trial_ending": "«{shop}»: пробный период закончится через {days} дн., {date}. Продолжить: /obuna",
    "sub_paid_ending": "«{shop}»: оплаченный период закончится через {days} дн., {date}. Продлить: /obuna",
    "sub_limited": (
        "«{shop}»: срок подписки истёк. Новые продажи в долг не записываются; приём оплат, просмотр и "
        "сообщения клиентам работают. Оплатить: /obuna"
    ),
    "sub_free_now": (
        "«{shop}»: срок истёк. Магазин перешёл на бесплатный тариф и работает полностью: до {limit} клиентов. "
        "Если нужно больше клиентов: /obuna"
    ),
    "move_date_button": "📅 Перенести срок",
    "ask_move_date": "На какую дату перенести срок оплаты? Напишите день.месяц, например 25.10",
    "move_date_invalid": "Не понял дату. Напишите день.месяц, например 25.10",
    "date_request_sent": (
        "Ваша просьба отправлена в магазин «{shop}»: перенести срок оплаты на {date}. Ответ придёт сюда."
    ),
    "REQUEST_ALREADY_OPEN": "Просьба о переносе срока по этой записи уже рассматривается.",
    "DATE_REQUEST_NOT_ALLOWED": "Просьбу о переносе срока по этой записи сейчас принять нельзя.",
    "PROMISE_NOT_CHANGEABLE": "У этой записи нет срока оплаты, или она отменена.",
    "date_not_later": "Новая дата должна быть позже нынешнего срока оплаты.",
    "date_fully_paid": "Эта запись полностью оплачена или отменена: переносить срок не нужно.",
    "date_declined_recently": (
        "Вашу просьбу по этой записи недавно отклонили. Попросить снова можно через 7 дней после отказа."
    ),
    "date_request_closed": "Эта просьба уже рассмотрена или утратила силу.",
    "s_date_request": "📅 {shop}\n{name} просит перенести срок оплаты долга {amount} с {old} на {date}.",
    "accept_button": "✅ Принять",
    "reason_line": "Причина: {reason}",
    "date_accepted_staff": "✅ {name}: срок оплаты долга {amount} перенесён на {date}.",
    "date_declined_staff": "Просьба отклонена: {name}, {amount}. Срок оплаты не изменился.",
    "n_date_accepted": "{shop}\nВаша просьба перенести срок оплаты долга {amount} принята.\nНовый срок оплаты: {date}",
    "n_date_declined": "{shop}\nВаша просьба перенести срок оплаты долга {amount} на {date} отклонена.",
    "n_date_changed": "{shop}\n{name}, срок оплаты долга {amount} изменён: {old} → {date}",
    "n_opening": (
        "{shop}\n{name}, в книгу внесён ваш прежний долг: {amount}\nСрок оплаты: {date}\nВсего долг: {balance}"
    ),
    "s_import_applied": (
        "📥 {shop}\nИмпорт применён: записей о долге — {entries}, всего {amount}. Новых клиентов: {customers}.\n"
        "В течение 24 часов его можно отменить целиком."
    ),
    "s_import_undone": "↩️ {shop}\nИмпорт отменён: отменено записей — {entries}. Сумма импорта: {amount}.",
    "import_checked": (
        "📥 {shop}\nФайл импорта проверен: строк — {rows}, ошибок нет. Просмотрите его в приложении и примените."
    ),
    "import_rejected": (
        "📥 {shop}\nВ файле импорта ошибки в строках: {errors}. Список — в приложении; загрузите исправленный файл."
    ),
    "import_unreadable": "📥 {shop}\nФайл импорта не удалось прочитать как таблицу. Причина указана в приложении.",
    "import_refused": (
        "📥 {shop}\nИмпорт не применён: после проверки данные изменились. Просмотрите его в приложении ещё раз."
    ),
    "import_refused_free_plan": (
        "📥 {shop}\nИмпорт не применён: бесплатный тариф вмещает до {limit} клиентов. "
        "Чтобы добавить больше, оплатите подписку: /obuna"
    ),
    "import_undo_refused": (
        "↩️ {shop}\nИмпорт отменить не удалось: по его записям уже есть оплаты. Ничего не изменено."
    ),
    "import_failed": "📥 {shop}\nЗапрошенное действие с импортом не выполнено. Ничего не изменено; попробуйте ещё раз.",
    "shop_deletion_requested": (
        "Запрошено удаление магазина «{shop}». Данные будут полностью удалены {date}. До этого дня можно "
        "выгрузить данные или отменить удаление."
    ),
    "shop_deletion_cancelled": "Удаление магазина «{shop}» отменено. Магазин работает как раньше.",
    "shop_erased": "Магазин «{shop}» и все его данные удалены.",
    # --- payment notices (REQ-060, REQ-061) ---
    "notice_choose_shop": "Какому магазину сообщить об оплате?",
    "notice_shop_button": "{shop}: {balance}",
    "notice_nothing_owed": "В магазине «{shop}» у вас нет долга.",
    "ask_notice_amount": (
        "Ваш долг в магазине «{shop}»: {balance}\nСколько вы оплатили? Напишите только сумму, например: 50000"
    ),
    "notice_amount_invalid": (
        "Напишите только сумму, например: 50000 или 50 000. Сумма в целых сумах, от 100 до 100 000 000 сумов."
    ),
    "notice_amount_exceeds": "Сумма не может быть больше вашего долга. Ваш долг: {balance}",
    "ask_notice_receipt": (
        "Оплата: {amount}\nЕсли есть чек, пришлите сюда его фото или PDF-файл. Если чека нет, нажмите кнопку."
    ),
    "notice_without_receipt": "Отправить без чека",
    "notice_receipt_hint": "Пришлите фото чека или PDF-файл либо нажмите одну из кнопок.",
    "notice_receipt_invalid": (
        "Не удалось принять этот файл. Чек должен быть изображением JPEG, PNG или WebP либо PDF и не больше "
        "5 МБ. Пришлите ещё раз или нажмите одну из кнопок."
    ),
    "notice_sent": (
        "✅ Сообщение об оплате {amount} отправлено в магазин «{shop}». Когда магазин его примет, ваш долг уменьшится."
    ),
    "s_notice": "💵 {shop}\n{name} сообщает об оплате {amount}.\nТекущий долг: {balance}",
    "s_notice_receipt": (
        "💵 {shop}\n{name} сообщает об оплате {amount}.\nТекущий долг: {balance}\n"
        "📎 Приложен чек: его можно посмотреть в приложении."
    ),
    "s_receipt_seen_before": "⚠️ Точно такой же чек уже присылали в этот магазин.",
    "notice_accept_button": "✅ Принять",
    "notice_accepted_staff": "✅ {shop}\n{name}: оплата {amount} принята.\nОстаток долга: {balance}",
    "notice_declined_staff": "Сообщение об оплате отклонено. Клиенту отправлено сообщение с причиной.",
    "n_notice_accepted": "{shop}\nВаше сообщение об оплате {amount} принято.",
    "n_notice_corrected": (
        "{shop}\nВаше сообщение об оплате {amount} принято, но оплата записана на сумму {recorded}."
    ),
    "n_notice_declined": "{shop}\nВаше сообщение об оплате {amount} отклонено.\nПричина: {reason}",
    "PAYMENT_NOTICE_NOT_ALLOWED": (
        "У вас слишком много нерассмотренных сообщений об оплате. Дождитесь ответа магазина."
    ),
    "PAYMENT_NOTICE_NOT_OPEN": "Это сообщение об оплате уже рассмотрено или его срок истёк.",
    "FILE_STORE_UNAVAILABLE": "Хранилище файлов сейчас недоступно. Попробуйте ещё раз чуть позже.",
    "export_ready": (
        "✅ Выгрузка магазина «{shop}» готова. Скачайте её в разделе выгрузок приложения; файл хранится 7 дней."
    ),
    "export_failed": "Не удалось подготовить выгрузку магазина «{shop}». Запросите её ещё раз чуть позже.",
    "EXPORT_NOT_ALLOWED": "Выгрузка уже готовится, или на сегодня выгрузок больше нет.",
    "EXPORT_NOT_READY": "Файл этой выгрузки ещё не готов или срок его хранения истёк.",
    "SUBSCRIPTION_RECEIPT_NOT_ALLOWED": ("У вас слишком много нерассмотренных чеков. Дождитесь ответа администратора."),
    "currency": "сум",
    **OPS_RU,
}

# The version of the consent text a customer agrees to (REQ-014). Changing the text means a new version.
CONSENT_VERSION = 2

# Uzbek Cyrillic texts that are written by hand because the transliteration of the Uzbek text would be
# wrong. Empty until a reviewer finds one; a word the rules get wrong belongs in the transliterator's
# own tables instead (`qarz.domain.uz_cyrillic`).
UZ_CYRILLIC: dict[str, str] = {}

# What each language has of its own. Uzbek and Russian are complete; the others may lack keys.
CATALOGS: Mapping[str, Mapping[str, str]] = {
    "uz": UZ,
    languages.UZ_CYRILLIC: UZ_CYRILLIC,
    "ru": RU,
    "tg": texts_tg.CHAT,
    "kaa": texts_kaa.CHAT,
    "en": texts_en.CHAT,
}
# Each language under its own name, in the order `/til` offers them.
LANGUAGE_NAMES = {
    "uz": "O'zbekcha",
    languages.UZ_CYRILLIC: "Ўзбекча",
    "ru": "Русский",
    "tg": "Тоҷикӣ",
    "kaa": "Qaraqalpaqsha",
    "en": "English",
}


@cache
def template(lang: str, key: str) -> str:
    """The wording of `key` in a language, before its values are filled in.

    A language's own text first. Without one, Uzbek: transliterated for Uzbek Cyrillic, as it is for
    every other language and for a language the service does not know. A key that Uzbek does not have
    is a mistake in the code and raises `KeyError`.
    """
    own = CATALOGS.get(lang, UZ).get(key)
    if own is not None:
        return own
    return to_cyrillic(UZ[key]) if lang == languages.UZ_CYRILLIC else UZ[key]


def say(lang: str, key: str, **values: object) -> str:
    """The text for `key` in the given language; in Uzbek where the language does not have it."""
    return template(lang, key).format(**values)


def money(lang: str, amount: int, currency: Currency = Currency.UZS) -> str:
    """45000 -> "45 000 so'm"; 125050 cents -> "1 250.50 $" (qarz.domain.money).

    The separator is a no-break space so an amount never wraps.
    """
    return format_money(currency, amount, lang)


def both(lang: str, amount: int, dollars: int | None) -> str:
    """What is owed in so'm and in dollars, side by side and never added: "45 000 so'm va 12.50 $".

    `dollars` is None for a shop without dollars, and the text is then the so'm amount alone, as it
    always was. Of the two, an amount of zero is left out when the other is not.
    """
    if not dollars:
        return money(lang, amount)
    in_dollars = money(lang, dollars, Currency.USD)
    return in_dollars if amount == 0 else say(lang, "two_amounts", first=money(lang, amount), second=in_dollars)


def day(value: date) -> str:
    return value.strftime("%d.%m.%Y")

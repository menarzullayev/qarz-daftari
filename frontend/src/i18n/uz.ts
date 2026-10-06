import type { UzPlural } from "./types";

/**
 * Uzbek (Latin) catalog and the source of the key set. The apostrophe in o', g' and after a consonant is
 * always the plain ASCII one (U+0027), so text stays searchable and typeable on any keyboard.
 */
export const uz = {
  "app.name": "Qarz Daftari",
  "entry.app": "Xodimlar ish joyi",
  "entry.panel": "Boshqaruv paneli",
  "entry.admin": "Platforma boshqaruvi",

  "nav.overview": "Umumiy ko'rinish",
  "nav.customers": "Mijozlar",
  "nav.newEntry": "Yangi yozuv",
  "nav.catalog": "Katalog",
  "nav.reminders": "Eslatmalar",
  "nav.reports": "Hisobotlar",
  "nav.disputes": "E'tirozlar va so'rovlar",
  "nav.importExport": "Import va eksport",
  "nav.staff": "Xodimlar",
  "nav.activityLog": "Amallar jurnali",
  "nav.subscription": "Obuna",
  "nav.shopSettings": "Do'kon sozlamalari",
  "nav.more": "Yana",

  "admin.nav.shops": "Do'konlar",
  "admin.nav.receipts": "To'lov cheklari",
  "admin.nav.settings": "Sozlamalar",
  "admin.nav.supportAccess": "Yordam uchun kirish",
  "admin.nav.audit": "Audit jurnali",

  "shell.mainNav": "Asosiy menyu",
  "shell.skipToContent": "Asosiy qismga o'tish",
  "shell.activeShop": "Faol do'kon",
  "shell.noShop": "Do'kon tanlanmagan",
  "shell.language": "Til",
  "lang.uz": "O'zbekcha",
  "lang.ru": "Русский",

  "role.owner": "Do'kon egasi",
  "role.manager": "Menejer",
  "role.seller": "Sotuvchi",

  "screen.placeholder": "Bu bo'lim tez orada tayyor bo'ladi.",
  "screen.signInRequired.title": "Kirish talab qilinadi",
  "screen.signInRequired.body": "Davom etish uchun Telegram hisobingiz orqali kiring.",
  "notFound.title": "Sahifa topilmadi",
  "notFound.body": "Bunday sahifa yo'q yoki sizda unga ruxsat yo'q.",
  "notFound.home": "Bosh sahifaga qaytish",

  "action.save": "Saqlash",
  "action.cancel": "Bekor qilish",
  "action.confirm": "Tasdiqlash",
  "action.delete": "O'chirish",
  "action.back": "Orqaga",
  "action.close": "Yopish",
  "action.search": "Qidirish",
  "action.retry": "Qayta urinish",

  "state.loading": "Yuklanmoqda…",
  "state.error": "Xatolik yuz berdi",
  "state.empty": "Hozircha ma'lumot yo'q",

  "customers.count": { other: "{count} ta mijoz" } satisfies UzPlural,
  "overdue.days": { other: "{count} kun kechikkan" } satisfies UzPlural,
  "due.inDays": { other: "{count} kun qoldi" } satisfies UzPlural,
  "due.today": "Muddati bugun",

  "money.uzs": "{amount} so'm",
  "date.dayMonth": "{day}-{month}",
  "date.dayMonthYear": "{year}-yil {day}-{month}",
  "month.1": "yanvar",
  "month.2": "fevral",
  "month.3": "mart",
  "month.4": "aprel",
  "month.5": "may",
  "month.6": "iyun",
  "month.7": "iyul",
  "month.8": "avgust",
  "month.9": "sentabr",
  "month.10": "oktabr",
  "month.11": "noyabr",
  "month.12": "dekabr",
} satisfies Record<string, string | UzPlural>;

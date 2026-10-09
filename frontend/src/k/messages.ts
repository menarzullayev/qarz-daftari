/**
 * The text of the page behind a customer's read-only link, in Uzbek (Latin) and Russian.
 *
 * The page has a catalog of its own, and a small one: whoever opens the link is a shop's customer on a
 * phone, often on a slow connection, and must not download the staff application's text to read one
 * number. Both languages are here because the reader may switch between them without the network.
 */
export const LANGUAGES = ["uz", "ru"] as const;
export type Language = (typeof LANGUAGES)[number];

const uz = {
  "page.title": "Mening qarzim",
  "lang.uz": "O'zbekcha",
  "lang.ru": "Русский",
  "lang.choose": "Til",
  "state.loading": "Yuklanmoqda…",
  "gone.title": "Havola ishlamayapti",
  "gone.body":
    "Havolaning muddati tugagan, u bekor qilingan yoki noto'g'ri ko'chirilgan. Do'kondan yangi havola so'rang.",
  "limited.title": "Juda ko'p urinish",
  "limited.body": "Bir daqiqadan so'ng sahifani qayta oching.",
  "offline.title": "Ulanib bo'lmadi",
  "offline.body": "Internetni tekshirib, qayta urinib ko'ring.",
  "action.retry": "Qayta urinish",
  "greeting": "{name}, bu sizning hisobingiz",
  "greeting.noName": "Bu sizning hisobingiz",
  "shop.phone": "Do'kon telefoni: ",
  "balance.owed": "Qarzingiz",
  "balance.none": "Qarzingiz yo'q",
  "balance.credit": "Ortiqcha to'lovingiz",
  "overdue": "Shundan muddati o'tgani: {amount}",
  "dueToday": "Bugun to'lanishi kerak: {amount}",
  "entries.title": "Yozuvlar",
  "entries.empty": "Hozircha yozuv yo'q.",
  "entries.shown": "Oxirgi {shown} ta yozuv ko'rsatilgan, jami {total} ta.",
  "kind.credit": "Nasiya",
  "kind.opening": "Boshlang'ich qarz",
  "kind.payment": "To'lov",
  "kind.reversal": "Bekor qilish yozuvi",
  "kind.other": "Yozuv",
  "entry.promised": "To'lash muddati: {date}",
  "entry.reversed": "Bekor qilingan",
  "line": "{name} — {qty} {unit} × {price} = {total}",
  "money": "{amount} so'm",
  "date": "{year}-yil {day}-{month}",
  "foot.readOnly": "Bu sahifa faqat o'qish uchun. Xato ko'rsangiz, do'konga murojaat qiling.",
  "foot.expires": "Havola {date} gacha amal qiladi.",
  "foot.secret": "Bu havola faqat siz uchun: uni boshqalarga yubormang.",
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
} as const;

export type MessageKey = keyof typeof uz;

const ru: Readonly<Record<MessageKey, string>> = {
  "page.title": "Мой долг",
  "lang.uz": "O'zbekcha",
  "lang.ru": "Русский",
  "lang.choose": "Язык",
  "state.loading": "Загрузка…",
  "gone.title": "Ссылка не работает",
  "gone.body":
    "Срок ссылки истёк, она отозвана или скопирована с ошибкой. Попросите в магазине новую ссылку.",
  "limited.title": "Слишком много попыток",
  "limited.body": "Откройте страницу снова через минуту.",
  "offline.title": "Нет соединения",
  "offline.body": "Проверьте интернет и попробуйте ещё раз.",
  "action.retry": "Повторить",
  "greeting": "{name}, это ваш счёт",
  "greeting.noName": "Это ваш счёт",
  "shop.phone": "Телефон магазина: ",
  "balance.owed": "Ваш долг",
  "balance.none": "Долга нет",
  "balance.credit": "Ваша переплата",
  "overdue": "Из них просрочено: {amount}",
  "dueToday": "Оплатить сегодня: {amount}",
  "entries.title": "Записи",
  "entries.empty": "Записей пока нет.",
  "entries.shown": "Показаны последние {shown} из {total} записей.",
  "kind.credit": "В долг",
  "kind.opening": "Начальный долг",
  "kind.payment": "Оплата",
  "kind.reversal": "Запись об отмене",
  "kind.other": "Запись",
  "entry.promised": "Срок оплаты: {date}",
  "entry.reversed": "Отменена",
  "line": "{name} — {qty} {unit} × {price} = {total}",
  "money": "{amount} сум",
  "date": "{day} {month} {year} г.",
  "foot.readOnly": "Эта страница только для чтения. Если видите ошибку, обратитесь в магазин.",
  "foot.expires": "Ссылка действует до {date}.",
  "foot.secret": "Эта ссылка только для вас: не пересылайте её другим.",
  "month.1": "января",
  "month.2": "февраля",
  "month.3": "марта",
  "month.4": "апреля",
  "month.5": "мая",
  "month.6": "июня",
  "month.7": "июля",
  "month.8": "августа",
  "month.9": "сентября",
  "month.10": "октября",
  "month.11": "ноября",
  "month.12": "декабря",
};

export const CATALOGS: Readonly<Record<Language, Readonly<Record<MessageKey, string>>>> = { uz, ru };

const PLACEHOLDER = /\{(\w+)\}/g;

/** The message with its `{param}` places filled in. A missing parameter is a mistake in the page, said loudly. */
export function say(language: Language, key: MessageKey, params: Readonly<Record<string, string | number>> = {}): string {
  return CATALOGS[language][key].replace(PLACEHOLDER, (_whole, name: string) => {
    const value = params[name];
    if (value === undefined) {
      throw new Error(`message "${key}" needs the parameter "${name}"`);
    }
    return String(value);
  });
}

export function normalizeLanguage(code: unknown): Language | null {
  if (typeof code !== "string") {
    return null;
  }
  const primary = code.trim().toLowerCase().split(/[-_]/)[0];
  return LANGUAGES.find((language) => language === primary) ?? null;
}

/** Whole UZS with non-breaking spaces between thousands and the currency word: "45 000 so'm". */
export function money(language: Language, amount: number): string {
  if (!Number.isSafeInteger(amount)) {
    throw new RangeError("amount must be a whole number of UZS");
  }
  const digits = Math.abs(amount)
    .toString()
    .replace(/\B(?=(\d{3})+(?!\d))/g, " ");
  return say(language, "money", { amount: (amount < 0 ? "-" : "") + digits });
}

// Timestamps are UTC and shown in Tashkent time, UTC+5 all year: a fixed offset gives the same day on
// every phone, including one whose browser has no time zone data.
const TASHKENT_OFFSET_MS = 5 * 60 * 60 * 1000;

function dayText(language: Language, year: number, month: number, day: number): string {
  return say(language, "date", { year, day, month: say(language, `month.${month}` as MessageKey) });
}

/** The Tashkent calendar day of an instant written as ISO 8601; the text itself when it is not one. */
export function dayOfInstant(language: Language, iso: string): string {
  const instant = new Date(iso);
  if (Number.isNaN(instant.getTime())) {
    return iso;
  }
  const shifted = new Date(instant.getTime() + TASHKENT_OFFSET_MS);
  return dayText(language, shifted.getUTCFullYear(), shifted.getUTCMonth() + 1, shifted.getUTCDate());
}

/** A calendar date written as YYYY-MM-DD, such as a promised date; the text itself when it is not one. */
export function dayOfDate(language: Language, iso: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  const month = match ? Number(match[2]) : 0;
  if (!match || month < 1 || month > 12) {
    return iso;
  }
  return dayText(language, Number(match[1]), month, Number(match[3]));
}

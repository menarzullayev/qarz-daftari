import { en } from "./en";
import { kaa } from "./kaa";
import { tg } from "./tg";
import { uz } from "./uz";
import { uzCyrl } from "./uzCyrl";

/**
 * The text of the page behind a customer's read-only link, in the six languages of the product.
 *
 * The page has a catalog of its own, and a small one: whoever opens the link is a shop's customer on a
 * phone, often on a slow connection, and must not download the staff application's text to read one
 * number. Every language is here, because the reader may switch between them without the network: a
 * few dozen short messages each. Uzbek is the source (`uz.ts`) and Russian mirrors it key for key;
 * Tajik, Karakalpak and English may lack a key and then read Uzbek. Uzbek Cyrillic is not typed: its
 * file is written from the Uzbek text by `npm run i18n:k`, with the rules the staff application uses
 * when it runs. The page itself shares no code with that application.
 */
export const LANGUAGES = ["uz", "uz-Cyrl", "ru", "tg", "kaa", "en"] as const;
export type Language = (typeof LANGUAGES)[number];

export type MessageKey = keyof typeof uz;

const ru: Readonly<Record<MessageKey, string>> = {
  "page.title": "Мой долг",
  "lang.uz": "O'zbekcha",
  "lang.ru": "Русский",
  "lang.uz-Cyrl": "Ўзбекча",
  "lang.tg": "Тоҷикӣ",
  "lang.kaa": "Qaraqalpaqsha",
  "lang.en": "English",
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

/** What each language has of its own. Uzbek and Russian have every key; the others may lack some. */
export const CATALOGS: Readonly<Record<Language, Readonly<Partial<Record<MessageKey, string>>>>> = {
  uz,
  "uz-Cyrl": uzCyrl,
  ru,
  tg,
  kaa,
  en,
};

/** The wording of a message before its places are filled in: the language's own, else Uzbek's. */
export function wording(language: Language, key: MessageKey): string {
  return CATALOGS[language][key] ?? uz[key];
}

const PLACEHOLDER = /\{(\w+)\}/g;

/** The message with its `{param}` places filled in. A missing parameter is a mistake in the page, said loudly. */
export function say(language: Language, key: MessageKey, params: Readonly<Record<string, string | number>> = {}): string {
  return wording(language, key).replace(PLACEHOLDER, (_whole, name: string) => {
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
  const [primary, ...rest] = code.trim().toLowerCase().split(/[-_]/);
  if (primary === "uz" && rest.includes("cyrl")) {
    return "uz-Cyrl";
  }
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

/**
 * Whole cents as US dollars, the same in both languages: "1 250.50 $", always two decimals, with
 * non-breaking spaces between thousands and before the sign. The cents are split by whole division,
 * so no fraction is ever computed.
 */
export function dollars(cents: number): string {
  if (!Number.isSafeInteger(cents)) {
    throw new RangeError("amount must be a whole number of cents");
  }
  const size = Math.abs(cents);
  const rest = size % 100;
  const whole = ((size - rest) / 100).toString().replace(/\B(?=(\d{3})+(?!\d))/g, "\u00a0");
  return `${cents < 0 ? "-" : ""}${whole}.${rest < 10 ? "0" : ""}${rest}\u00a0$`;
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

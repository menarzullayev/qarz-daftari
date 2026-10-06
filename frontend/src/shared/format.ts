import { translate } from "../i18n/catalog";
import type { Language, MessageKey } from "../i18n/types";
import { formatUzs } from "./money";

/**
 * Timestamps are stored in UTC and shown in Tashkent time, which is UTC+5 all year (technical
 * specification, conventions). A fixed offset keeps the result identical on every device, including
 * phones whose browser has no time zone data.
 */
const TASHKENT_OFFSET_MS = 5 * 60 * 60 * 1000;
const DAY_MS = 24 * 60 * 60 * 1000;

function assertValid(date: Date): void {
  if (Number.isNaN(date.getTime())) {
    throw new RangeError("date is invalid");
  }
}

type CalendarDay = { year: number; month: number; day: number };

/** Calendar date in Tashkent for an instant. */
export function tashkentDay(date: Date): CalendarDay {
  assertValid(date);
  const shifted = new Date(date.getTime() + TASHKENT_OFFSET_MS);
  return { year: shifted.getUTCFullYear(), month: shifted.getUTCMonth() + 1, day: shifted.getUTCDate() };
}

/** Number of whole Tashkent calendar days since the epoch; the difference of two is a day count. */
function tashkentDayNumber(date: Date): number {
  assertValid(date);
  return Math.floor((date.getTime() + TASHKENT_OFFSET_MS) / DAY_MS);
}

function monthName(lang: Language, month: number): string {
  return translate(lang, `month.${month}` as MessageKey);
}

/** "6-oktabr" in Uzbek, "6 октября" in Russian. */
export function formatDayMonth(date: Date, lang: Language): string {
  const { month, day } = tashkentDay(date);
  return translate(lang, "date.dayMonth", { day, month: monthName(lang, month) });
}

/** "2026-yil 6-oktabr" in Uzbek, "6 октября 2026 г." in Russian. */
export function formatFullDate(date: Date, lang: Language): string {
  const { year, month, day } = tashkentDay(date);
  return translate(lang, "date.dayMonthYear", { year, day, month: monthName(lang, month) });
}

/**
 * Text for a due date relative to now, by Tashkent calendar days: "3 kun kechikkan" after the due date,
 * "Muddati bugun" on it, "3 kun qoldi" before it.
 */
export function formatDueStatus(due: Date, now: Date, lang: Language): string {
  const daysLate = tashkentDayNumber(now) - tashkentDayNumber(due);
  if (daysLate > 0) {
    return translate(lang, "overdue.days", { count: daysLate });
  }
  if (daysLate === 0) {
    return translate(lang, "due.today");
  }
  return translate(lang, "due.inDays", { count: -daysLate });
}

/** Whole UZS with the currency word: "45 000 so'm" or "45 000 сум". */
export function formatMoney(amount: number, lang: Language): string {
  return translate(lang, "money.uzs", { amount: formatUzs(amount) });
}

/** "5 ta mijoz" or "5 клиентов". */
export function formatCustomerCount(count: number, lang: Language): string {
  return translate(lang, "customers.count", { count });
}

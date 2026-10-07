/** Dates as the shop sees them: the product works in Tashkent time (UTC+5, no daylight saving). */
const TASHKENT_OFFSET_MS = 5 * 60 * 60 * 1000;
const DAY_MS = 24 * 60 * 60 * 1000;

const UZ_MONTHS = ["yanvar", "fevral", "mart", "aprel", "may", "iyun", "iyul", "avgust", "sentabr", "oktabr", "noyabr", "dekabr"];

/** The date `days` from today in Tashkent, as YYYY-MM-DD. */
export function inDays(days: number, now: Date = new Date()): string {
  return new Date(now.getTime() + TASHKENT_OFFSET_MS + days * DAY_MS).toISOString().slice(0, 10);
}

/** A YYYY-MM-DD date as the Uzbek screens write it: "2026-yil 20-oktabr". */
export function uzDate(iso: string): string {
  const [year, month, day] = iso.split("-").map(Number);
  return `${year}-yil ${day}-${UZ_MONTHS[(month ?? 1) - 1]}`;
}

/** The current month in Tashkent, as YYYY-MM. */
export function thisMonth(now: Date = new Date()): string {
  return inDays(0, now).slice(0, 7);
}

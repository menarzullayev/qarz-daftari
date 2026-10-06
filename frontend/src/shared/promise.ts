import type { CalendarDay } from "./format";

/**
 * Promised repayment dates on the credit form (REQ-008). A port of backend/src/qarz/domain/promise.py,
 * which is the authority: the server checks every date again. Everything here is a pure function of the
 * Tashkent calendar date of the sale.
 */

export const MAX_PROMISE_DAYS = 365;

const DAY_MS = 24 * 60 * 60 * 1000;

function toUtc(day: CalendarDay): number {
  return Date.UTC(day.year, day.month - 1, day.day);
}

function fromUtc(ms: number): CalendarDay {
  const date = new Date(ms);
  return { year: date.getUTCFullYear(), month: date.getUTCMonth() + 1, day: date.getUTCDate() };
}

export function addDays(day: CalendarDay, count: number): CalendarDay {
  return fromUtc(toUtc(day) + count * DAY_MS);
}

/** Monday is 1 and Sunday is 7, as in ISO 8601. */
export function isoWeekday(day: CalendarDay): number {
  return ((new Date(toUtc(day)).getUTCDay() + 6) % 7) + 1;
}

export function compareDays(a: CalendarDay, b: CalendarDay): number {
  return toUtc(a) - toUtc(b);
}

export function tomorrow(sale: CalendarDay): CalendarDay {
  return addDays(sale, 1);
}

/**
 * The coming Sunday, strictly after the sale date: the week runs Monday to Sunday, and a sale made on a
 * Sunday gets the following Sunday, because a promise for the day of the sale would already be due.
 */
export function endOfWeek(sale: CalendarDay): CalendarDay {
  const daysAhead = (7 - isoWeekday(sale)) % 7;
  return addDays(sale, daysAhead === 0 ? 7 : daysAhead);
}

export function inTwoWeeks(sale: CalendarDay): CalendarDay {
  return addDays(sale, 14);
}

/** The same day of the next month, or that month's last day when it is shorter (31 January → 28 February). */
export function inAMonth(sale: CalendarDay): CalendarDay {
  const year = sale.month === 12 ? sale.year + 1 : sale.year;
  const month = sale.month === 12 ? 1 : sale.month + 1;
  // Day 0 of the month after is the last day of this one.
  const lastDay = new Date(Date.UTC(year, month, 0)).getUTCDate();
  return { year, month, day: Math.min(sale.day, lastDay) };
}

export const QUICK_CHOICES = ["tomorrow", "end_of_week", "in_two_weeks", "in_a_month"] as const;
export type QuickChoice = (typeof QUICK_CHOICES)[number];

export function quickChoiceDate(choice: QuickChoice, sale: CalendarDay): CalendarDay {
  switch (choice) {
    case "tomorrow":
      return tomorrow(sale);
    case "end_of_week":
      return endOfWeek(sale);
    case "in_two_weeks":
      return inTwoWeeks(sale);
    case "in_a_month":
      return inAMonth(sale);
  }
}

export type PromiseDateProblem = "before_sale" | "too_far";

/** A chosen date may be the sale date itself and at most 365 days after it. */
export function promiseDateProblem(sale: CalendarDay, chosen: CalendarDay): PromiseDateProblem | null {
  if (compareDays(chosen, sale) < 0) {
    return "before_sale";
  }
  if (compareDays(chosen, addDays(sale, MAX_PROMISE_DAYS)) > 0) {
    return "too_far";
  }
  return null;
}

const pad = (value: number, length: number) => String(value).padStart(length, "0");

/** "2026-10-06", the form the API takes and returns. */
export function toIsoDate(day: CalendarDay): string {
  return `${pad(day.year, 4)}-${pad(day.month, 2)}-${pad(day.day, 2)}`;
}

/** Reads "2026-10-06"; null for anything else, including a date that does not exist. */
export function parseIsoDate(text: string): CalendarDay | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text);
  if (!match) {
    return null;
  }
  const day = { year: Number(match[1]), month: Number(match[2]), day: Number(match[3]) };
  return toIsoDate(fromUtc(toUtc(day))) === text ? day : null;
}

import type { DateRequest } from "./api";
import { type CalendarDay, tashkentDay } from "./format";
import { addDays, compareDays, MAX_PROMISE_DAYS, parseIsoDate } from "./promise";

/**
 * Moving a promised date (REQ-066, REQ-067): what a customer may ask for and what the shop may set.
 * A port of backend/src/qarz/domain/date_requests.py, which is the authority: the server checks every
 * date again, and these rules only keep a person from sending what it would refuse.
 */

/** The longest reason, counted in characters after white space is tidied. */
export const DATE_REASON_MAX = 300;

/** After a decline the same entry cannot be asked about again for this many days (BR-15). */
export const REPEAT_AFTER_DECLINE_DAYS = 7;

const DAY_MS = 24 * 60 * 60 * 1000;

/** Only a credit sale or an opening balance has a promised date. */
export function isDebtKind(kind: string): boolean {
  return kind === "credit" || kind === "opening";
}

/** An optional reason with white space tidied; null when nothing was written. */
export function tidyReason(raw: string): string | null {
  return raw.split(/\s+/).filter(Boolean).join(" ") || null;
}

export function reasonFits(reason: string | null): boolean {
  return reason === null || [...reason].length <= DATE_REASON_MAX;
}

/** The Tashkent calendar day an entry was recorded on, from which its dates are bound; null if unreadable. */
export function saleDay(createdAt: string): CalendarDay | null {
  const made = new Date(createdAt);
  return Number.isNaN(made.getTime()) ? null : tashkentDay(made);
}

export type DayRange = { min: CalendarDay; max: CalendarDay };

/** What the shop may set: the sale's day itself and at most 365 days after it. */
export function changeRange(sale: CalendarDay): DayRange {
  return { min: sale, max: addDays(sale, MAX_PROMISE_DAYS) };
}

/**
 * What a customer may ask for: a day after the current date, within the same 365 days of the sale.
 * Null when the current date is already the last day there is.
 */
export function requestRange(sale: CalendarDay, current: CalendarDay): DayRange | null {
  const first = addDays(current, 1);
  const min = compareDays(first, sale) < 0 ? sale : first;
  const max = addDays(sale, MAX_PROMISE_DAYS);
  return compareDays(min, max) > 0 ? null : { min, max };
}

export type DateProblem = "required" | "before_sale" | "too_far" | "not_later" | "unchanged";
export type DateChoice = { ok: true; date: string } | { ok: false; problem: DateProblem };

function bounded(text: string, sale: CalendarDay): { day: CalendarDay } | { problem: DateProblem } {
  const day = parseIsoDate(text.trim());
  if (day === null) {
    return { problem: "required" };
  }
  if (compareDays(day, sale) < 0) {
    return { problem: "before_sale" };
  }
  return compareDays(day, addDays(sale, MAX_PROMISE_DAYS)) > 0 ? { problem: "too_far" } : { day };
}

/** The day a customer typed, as a request the server would take: later than the current date. */
export function requestedDate(text: string, sale: CalendarDay, current: CalendarDay): DateChoice {
  const read = bounded(text, sale);
  if ("problem" in read) {
    return { ok: false, problem: read.problem };
  }
  return compareDays(read.day, current) > 0 ? { ok: true, date: text.trim() } : { ok: false, problem: "not_later" };
}

/** The day a manager typed, as a change the server would take: any day in range but the current one. */
export function changedDate(text: string, sale: CalendarDay, current: CalendarDay | null): DateChoice {
  const read = bounded(text, sale);
  if ("problem" in read) {
    return { ok: false, problem: read.problem };
  }
  return current !== null && compareDays(read.day, current) === 0
    ? { ok: false, problem: "unchanged" }
    : { ok: true, date: text.trim() };
}

/**
 * The instant from which an entry may be asked about again after its request was declined, or null
 * when nothing stands in the way. The seven days end exactly seven days after the decline.
 */
export function askAgainAt(request: DateRequest | null, now: Date): Date | null {
  if (request?.status !== "declined" || request.closedAt === null) {
    return null;
  }
  const closed = new Date(request.closedAt);
  if (Number.isNaN(closed.getTime())) {
    return null;
  }
  const again = new Date(closed.getTime() + REPEAT_AFTER_DECLINE_DAYS * DAY_MS);
  return again.getTime() > now.getTime() ? again : null;
}

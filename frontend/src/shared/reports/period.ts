import type { CalendarDay } from "../format";
import { addDays, compareDays, isoWeekday, parseIsoDate, toIsoDate } from "../promise";

/**
 * The period of a report (REQ-046): a run of Tashkent calendar days with both ends included. A port of
 * backend/src/qarz/domain/reports.py (`validate_period`), which is the authority; the server checks the
 * period again and answers the same four problems.
 */

export const MAX_PERIOD_DAYS = 366;

export const PRESETS = ["today", "week", "month", "lastMonth"] as const;
export type Preset = (typeof PRESETS)[number];

/** Both ends as "YYYY-MM-DD", the form the API takes. */
export type Period = { from: string; to: string };

const DAY_MS = 24 * 60 * 60 * 1000;

function daysBetween(first: CalendarDay, last: CalendarDay): number {
  return Math.round(compareDays(last, first) / DAY_MS);
}

/**
 * The period a preset stands for on `today`. A week runs Monday to Sunday, as everywhere in the
 * product; "this week" and "this month" end today, because a period may not reach into the future.
 */
export function presetPeriod(preset: Preset, today: CalendarDay): Period {
  const monthStart = { ...today, day: 1 };
  switch (preset) {
    case "today":
      return { from: toIsoDate(today), to: toIsoDate(today) };
    case "week":
      return { from: toIsoDate(addDays(today, 1 - isoWeekday(today))), to: toIsoDate(today) };
    case "month":
      return { from: toIsoDate(monthStart), to: toIsoDate(today) };
    case "lastMonth": {
      const last = addDays(monthStart, -1);
      return { from: toIsoDate({ ...last, day: 1 }), to: toIsoDate(last) };
    }
  }
}

/** The preset a period is, on `today`; null for a period typed by hand that matches none. */
export function presetOf(period: Period, today: CalendarDay): Preset | null {
  return (
    PRESETS.find((preset) => {
      const candidate = presetPeriod(preset, today);
      return candidate.from === period.from && candidate.to === period.to;
    }) ?? null
  );
}

/** The server's names for what is wrong with a period (`fields.from`, `fields.to`). */
export const PERIOD_PROBLEMS = ["DATE_INVALID", "FROM_AFTER_TO", "IN_FUTURE", "PERIOD_TOO_LONG"] as const;
export type PeriodProblem = (typeof PERIOD_PROBLEMS)[number];

export function isPeriodProblem(value: unknown): value is PeriodProblem {
  return PERIOD_PROBLEMS.some((problem) => problem === value);
}

export type PeriodProblems = { from: PeriodProblem | null; to: PeriodProblem | null };
export type PeriodCheck = { ok: true; period: Period } | { ok: false; problems: PeriodProblems };

/** The two typed dates as a period the server would take, or what is wrong and with which field. */
export function checkPeriod(fromText: string, toText: string, today: CalendarDay): PeriodCheck {
  const first = parseIsoDate(fromText.trim());
  const last = parseIsoDate(toText.trim());
  if (first === null || last === null) {
    return {
      ok: false,
      problems: { from: first === null ? "DATE_INVALID" : null, to: last === null ? "DATE_INVALID" : null },
    };
  }
  if (compareDays(first, last) > 0) {
    return { ok: false, problems: { from: "FROM_AFTER_TO", to: null } };
  }
  if (compareDays(last, today) > 0) {
    return { ok: false, problems: { from: null, to: "IN_FUTURE" } };
  }
  if (daysBetween(first, last) + 1 > MAX_PERIOD_DAYS) {
    return { ok: false, problems: { from: null, to: "PERIOD_TOO_LONG" } };
  }
  return { ok: true, period: { from: toIsoDate(first), to: toIsoDate(last) } };
}

import { describe, expect, it } from "vitest";

import {
  addDays,
  endOfWeek,
  inAMonth,
  inTwoWeeks,
  isoWeekday,
  parseIsoDate,
  promiseDateProblem,
  QUICK_CHOICES,
  quickChoiceDate,
  toIsoDate,
  tomorrow,
} from "./promise";

const day = (text: string) => {
  const parsed = parseIsoDate(text);
  if (!parsed) {
    throw new Error(`not a date: ${text}`);
  }
  return parsed;
};

// The cases are those of backend/tests/test_promise.py, so the form offers what the server would compute.
describe("quick promised dates", () => {
  it.each([
    ["2026-01-05", "2026-01-06"],
    ["2026-01-31", "2026-02-01"],
    ["2026-12-31", "2027-01-01"],
    ["2028-02-28", "2028-02-29"],
  ])("tomorrow after %s is %s", (sale, expected) => {
    expect(toIsoDate(tomorrow(day(sale)))).toBe(expected);
  });

  it.each([
    ["2026-01-05", "2026-01-11"], // Monday → Sunday of the same week
    ["2026-01-07", "2026-01-11"], // Wednesday
    ["2026-01-09", "2026-01-11"], // Friday
    ["2026-01-10", "2026-01-11"], // Saturday → tomorrow
    ["2026-01-11", "2026-01-18"], // Sunday → the next Sunday, not the day of the sale
    ["2026-12-30", "2027-01-03"], // the week crosses the year
  ])("end of the week for %s is %s", (sale, expected) => {
    expect(toIsoDate(endOfWeek(day(sale)))).toBe(expected);
  });

  it("end of the week is always a coming Sunday", () => {
    for (let offset = 0; offset < 800; offset += 1) {
      const sale = addDays(day("2026-01-01"), offset);
      const result = endOfWeek(sale);
      expect(isoWeekday(result)).toBe(7);
      const ahead = (Date.parse(toIsoDate(result)) - Date.parse(toIsoDate(sale))) / 86_400_000;
      expect(ahead).toBeGreaterThanOrEqual(1);
      expect(ahead).toBeLessThanOrEqual(7);
    }
  });

  it.each([
    ["2026-01-05", "2026-01-19"],
    ["2026-02-20", "2026-03-06"],
    ["2026-12-25", "2027-01-08"],
  ])("two weeks after %s is %s", (sale, expected) => {
    expect(toIsoDate(inTwoWeeks(day(sale)))).toBe(expected);
  });

  it.each([
    ["2026-01-15", "2026-02-15"],
    ["2026-01-28", "2026-02-28"],
    ["2026-01-29", "2026-02-28"], // clamped
    ["2026-01-31", "2026-02-28"], // clamped
    ["2028-01-31", "2028-02-29"], // clamped to a leap day
    ["2026-02-28", "2026-03-28"], // same day, not "last day to last day"
    ["2026-03-31", "2026-04-30"],
    ["2026-05-31", "2026-06-30"],
    ["2026-12-15", "2027-01-15"],
    ["2026-12-31", "2027-01-31"],
  ])("a month after %s is %s", (sale, expected) => {
    expect(toIsoDate(inAMonth(day(sale)))).toBe(expected);
  });

  it.each([
    ["tomorrow", "2026-01-31"],
    ["end_of_week", "2026-02-01"],
    ["in_two_weeks", "2026-02-13"],
    ["in_a_month", "2026-02-28"],
  ] as const)("%s on Friday 30 January 2026 is %s", (choice, expected) => {
    expect(toIsoDate(quickChoiceDate(choice, day("2026-01-30")))).toBe(expected);
  });

  it("offers exactly the server's four choices, each a date the server accepts", () => {
    expect([...QUICK_CHOICES]).toEqual(["tomorrow", "end_of_week", "in_two_weeks", "in_a_month"]);
    for (let offset = 0; offset < 800; offset += 1) {
      const sale = addDays(day("2026-01-01"), offset);
      for (const choice of QUICK_CHOICES) {
        expect(promiseDateProblem(sale, quickChoiceDate(choice, sale))).toBeNull();
      }
    }
  });
});

describe("promiseDateProblem", () => {
  it.each([
    ["2026-01-05", "2026-01-05"], // the day of the sale itself
    ["2026-01-05", "2026-01-06"],
    ["2026-01-05", "2026-06-30"],
    ["2026-01-05", "2027-01-05"], // exactly 365 days
    ["2028-01-05", "2029-01-04"], // exactly 365 days across a leap day
  ])("accepts %s → %s", (sale, chosen) => {
    expect(promiseDateProblem(day(sale), day(chosen))).toBeNull();
  });

  it.each([
    ["2026-01-05", "2026-01-04", "before_sale"],
    ["2026-01-05", "2025-01-05", "before_sale"],
    ["2026-01-05", "2027-01-06", "too_far"], // 366 days
    ["2028-01-05", "2029-01-05", "too_far"], // 366 days across a leap day
    ["2026-01-05", "2030-01-01", "too_far"],
  ])("refuses %s → %s as %s", (sale, chosen, expected) => {
    expect(promiseDateProblem(day(sale), day(chosen))).toBe(expected);
  });
});

describe("ISO dates", () => {
  it("writes and reads YYYY-MM-DD", () => {
    expect(toIsoDate({ year: 2026, month: 3, day: 7 })).toBe("2026-03-07");
    expect(parseIsoDate("2026-03-07")).toEqual({ year: 2026, month: 3, day: 7 });
    expect(parseIsoDate("2028-02-29")).toEqual({ year: 2028, month: 2, day: 29 });
  });

  it.each(["", "2026-3-7", "07.03.2026", "2026-02-30", "2026-13-01", "2026-00-10", "2026-03-07T00:00:00Z"])(
    "refuses %j",
    (text) => {
      expect(parseIsoDate(text)).toBeNull();
    },
  );
});

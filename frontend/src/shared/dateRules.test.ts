import { describe, expect, it } from "vitest";

import type { DateRequest } from "./api";
import {
  askAgainAt,
  changedDate,
  changeRange,
  DATE_REASON_MAX,
  isDebtKind,
  reasonFits,
  requestedDate,
  requestRange,
  saleDay,
  tidyReason,
} from "./dateRules";
import { presetOf, presetPeriod, checkPeriod, MAX_PERIOD_DAYS, PRESETS } from "./reports/period";

const SALE = { year: 2026, month: 10, day: 6 };
const CURRENT = { year: 2026, month: 11, day: 5 };

describe("the day an entry was sold", () => {
  it("is its Tashkent calendar day, which may be the day after the UTC one", () => {
    expect(saleDay("2026-10-05T19:30:00+00:00")).toEqual(SALE);
    expect(saleDay("2026-10-05T18:59:59+00:00")).toEqual({ year: 2026, month: 10, day: 5 });
    expect(saleDay("yesterday")).toBeNull();
  });

  it("matters only for a debt: a credit sale or an opening balance", () => {
    expect(["credit", "opening", "payment", "reversal", ""].map(isDebtKind)).toEqual([true, true, false, false, false]);
  });
});

describe("a reason", () => {
  it("is optional, has its white space tidied, and is nothing when only white space was typed", () => {
    expect(tidyReason("  Oylik \n kechikdi  ")).toBe("Oylik kechikdi");
    expect(tidyReason(" \n\t ")).toBeNull();
    expect(tidyReason("")).toBeNull();
  });

  it("fits up to 300 characters, counted as characters and not as UTF-16 units", () => {
    expect(DATE_REASON_MAX).toBe(300);
    expect(reasonFits(null)).toBe(true);
    expect(reasonFits("a".repeat(300))).toBe(true);
    expect(reasonFits("a".repeat(301))).toBe(false);
    expect(reasonFits("😀".repeat(300))).toBe(true);
    expect(reasonFits("😀".repeat(301))).toBe(false);
  });
});

describe("what a customer may ask for", () => {
  it("is a day after the current date and at most 365 days after the sale", () => {
    expect(requestRange(SALE, CURRENT)).toEqual({ min: { year: 2026, month: 11, day: 6 }, max: { year: 2027, month: 10, day: 6 } });
    expect(requestedDate("2026-11-06", SALE, CURRENT)).toEqual({ ok: true, date: "2026-11-06" });
    expect(requestedDate("2027-10-06", SALE, CURRENT)).toEqual({ ok: true, date: "2027-10-06" });
  });

  it.each([
    ["", "required"],
    ["06.11.2026", "required"],
    ["2026-02-30", "required"],
    ["2026-11-05", "not_later"],
    ["2026-11-04", "not_later"],
    ["2026-10-05", "before_sale"],
    ["2027-10-07", "too_far"],
  ] as const)("refuses %j as %s", (text, problem) => {
    expect(requestedDate(text, SALE, CURRENT)).toEqual({ ok: false, problem });
  });

  it("is nothing at all when the current date is already the last day there is", () => {
    expect(requestRange(SALE, { year: 2027, month: 10, day: 6 })).toBeNull();
    expect(requestRange(SALE, { year: 2027, month: 10, day: 5 })).toEqual({
      min: { year: 2027, month: 10, day: 6 },
      max: { year: 2027, month: 10, day: 6 },
    });
  });
});

describe("what the shop may set", () => {
  it("is any day from the sale to 365 days after it, earlier or later than the current date", () => {
    expect(changeRange(SALE)).toEqual({ min: SALE, max: { year: 2027, month: 10, day: 6 } });
    expect(changedDate("2026-10-06", SALE, CURRENT)).toEqual({ ok: true, date: "2026-10-06" });
    expect(changedDate("2026-10-20", SALE, CURRENT)).toEqual({ ok: true, date: "2026-10-20" });
    expect(changedDate("2027-10-06", SALE, CURRENT)).toEqual({ ok: true, date: "2027-10-06" });
    expect(changedDate("2026-11-05", SALE, null)).toEqual({ ok: true, date: "2026-11-05" });
  });

  it.each([
    ["", "required"],
    ["2026-11-05", "unchanged"],
    ["2026-10-05", "before_sale"],
    ["2027-10-07", "too_far"],
  ] as const)("refuses %j as %s", (text, problem) => {
    expect(changedDate(text, SALE, CURRENT)).toEqual({ ok: false, problem });
  });
});

describe("asking again after a decline", () => {
  const request = (overrides: Partial<DateRequest>): DateRequest => ({
    id: "r",
    entryId: "e",
    status: "declined",
    requestedDate: "2026-11-20",
    reason: null,
    declineReason: null,
    createdAt: "2026-10-01T05:00:00+00:00",
    closedAt: "2026-10-02T07:00:00+00:00",
    ...overrides,
  });

  it("waits until exactly seven days after the decline", () => {
    const again = new Date("2026-10-09T07:00:00Z");
    expect(askAgainAt(request({}), new Date("2026-10-06T07:00:00Z"))).toEqual(again);
    expect(askAgainAt(request({}), new Date("2026-10-09T06:59:59Z"))).toEqual(again);
    expect(askAgainAt(request({}), again)).toBeNull();
  });

  it("does not wait after anything but a decline", () => {
    const now = new Date("2026-10-06T07:00:00Z");
    for (const status of ["open", "accepted", "expired"]) {
      expect(askAgainAt(request({ status }), now)).toBeNull();
    }
    expect(askAgainAt(null, now)).toBeNull();
    expect(askAgainAt(request({ closedAt: null }), now)).toBeNull();
    expect(askAgainAt(request({ closedAt: "soon" }), now)).toBeNull();
  });
});

describe("the period of a report", () => {
  // Tuesday 6 October 2026.
  const TODAY = { year: 2026, month: 10, day: 6 };

  it("has four presets, none of which reaches past today", () => {
    expect(PRESETS).toEqual(["today", "week", "month", "lastMonth"]);
    expect(presetPeriod("today", TODAY)).toEqual({ from: "2026-10-06", to: "2026-10-06" });
    expect(presetPeriod("week", TODAY)).toEqual({ from: "2026-10-05", to: "2026-10-06" });
    expect(presetPeriod("month", TODAY)).toEqual({ from: "2026-10-01", to: "2026-10-06" });
    expect(presetPeriod("lastMonth", TODAY)).toEqual({ from: "2026-09-01", to: "2026-09-30" });
  });

  it("starts the week on Monday and crosses a year and a short month correctly", () => {
    expect(presetPeriod("week", { year: 2026, month: 10, day: 5 })).toEqual({ from: "2026-10-05", to: "2026-10-05" });
    expect(presetPeriod("week", { year: 2026, month: 10, day: 11 })).toEqual({ from: "2026-10-05", to: "2026-10-11" });
    expect(presetPeriod("week", { year: 2027, month: 1, day: 1 })).toEqual({ from: "2026-12-28", to: "2027-01-01" });
    expect(presetPeriod("lastMonth", { year: 2027, month: 1, day: 15 })).toEqual({ from: "2026-12-01", to: "2026-12-31" });
    expect(presetPeriod("lastMonth", { year: 2028, month: 3, day: 31 })).toEqual({ from: "2028-02-01", to: "2028-02-29" });
  });

  it("knows which preset a period is, and that a typed one may be none", () => {
    expect(presetOf({ from: "2026-10-01", to: "2026-10-06" }, TODAY)).toBe("month");
    expect(presetOf({ from: "2026-10-06", to: "2026-10-06" }, TODAY)).toBe("today");
    expect(presetOf({ from: "2026-10-02", to: "2026-10-06" }, TODAY)).toBeNull();
  });

  it("accepts a period that ends today and one of exactly 366 days", () => {
    expect(MAX_PERIOD_DAYS).toBe(366);
    expect(checkPeriod(" 2026-10-01 ", "2026-10-06", TODAY)).toEqual({ ok: true, period: { from: "2026-10-01", to: "2026-10-06" } });
    expect(checkPeriod("2025-10-06", "2026-10-06", TODAY)).toMatchObject({ ok: true });
  });

  it.each([
    ["", "2026-10-06", { from: "DATE_INVALID", to: null }],
    ["2026-10-01", "6 oktabr", { from: null, to: "DATE_INVALID" }],
    ["", "", { from: "DATE_INVALID", to: "DATE_INVALID" }],
    ["2026-10-06", "2026-10-05", { from: "FROM_AFTER_TO", to: null }],
    ["2026-10-01", "2026-10-07", { from: null, to: "IN_FUTURE" }],
    ["2025-10-05", "2026-10-06", { from: null, to: "PERIOD_TOO_LONG" }],
  ] as const)("refuses %j to %j", (from, to, problems) => {
    expect(checkPeriod(from, to, TODAY)).toEqual({ ok: false, problems });
  });
});

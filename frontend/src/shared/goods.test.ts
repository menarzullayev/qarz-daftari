import { describe, expect, it } from "vitest";

import {
  formatQty,
  goodsWindowOpen,
  linesSumProblem,
  lineTotal,
  MAX_QTY_THOUSANDTHS,
  parsePrice,
  parseQty,
  qtyToApi,
  readServerQty,
} from "./goods";

const qty = (text: string): number => {
  const result = parseQty(text);
  if (!result.ok) {
    throw new Error(`"${text}" is not a quantity: ${result.problem}`);
  }
  return result.thousandths;
};

describe("line total (a port of backend/src/qarz/domain/rounding.py)", () => {
  // The cases of backend/tests/test_rounding.py, with the values that module's tests expect.
  it.each([
    ["1", 4_000, 4_000],
    ["5", 4_000, 20_000],
    ["0.5", 25_000, 12_500],
    ["0.333", 10_000, 3_330],
    ["0.125", 4, 1], // 0.5 rounds up
    ["0.375", 4, 2], // 1.5 rounds up, not to even
    ["0.625", 4, 3], // 2.5 rounds up, not to even
    ["1.234", 999, 1_233], // 1232.766 rounds up
    ["1.001", 1_000, 1_001],
  ])("%s × %i = %i", (quantity, price, expected) => {
    expect(lineTotal(qty(quantity), price)).toBe(expected);
  });

  it.each([
    ["0.001", 100], // rounds to zero UZS
    ["1", 0],
    ["1", -5],
  ])("refuses %s × %i", (quantity, price) => {
    expect(lineTotal(qty(quantity), price)).toBeNull();
  });

  it("refuses a quantity that is zero, negative, fractional in thousandths, or beyond the range", () => {
    expect(lineTotal(0, 1_000)).toBeNull();
    expect(lineTotal(-1_000, 1_000)).toBeNull();
    expect(lineTotal(1_000.5, 1_000)).toBeNull();
    expect(lineTotal(MAX_QTY_THOUSANDTHS + 1, 1_000)).toBeNull();
    expect(lineTotal(1_000, 100_000_001)).toBeNull();
    expect(lineTotal(1_000, 4_000.5)).toBeNull();
  });

  it("rounds just below a half down and exactly a half up", () => {
    expect(lineTotal(qty("0.499"), 1)).toBeNull(); // 0.499 UZS is below 1 UZS after rounding
    expect(lineTotal(qty("0.5"), 1)).toBe(1);
    expect(lineTotal(qty("1.499"), 1)).toBe(1);
    expect(lineTotal(qty("1.5"), 1)).toBe(2);
    expect(lineTotal(qty("2.5"), 1)).toBe(3);
    expect(lineTotal(qty("2.5"), 3)).toBe(8); // 7.5
    expect(lineTotal(qty("0.005"), 100)).toBe(1); // 0.5
    expect(lineTotal(qty("0.004"), 100)).toBeNull(); // 0.4
  });

  it("is exact where binary floating point is not", () => {
    // In doubles 1.005 * 1000 is 1004.9999999999999 and 0.57 * 100 is 56.99999999999999.
    expect(lineTotal(qty("1.005"), 1_000)).toBe(1_005);
    expect(lineTotal(qty("0.57"), 100)).toBe(57);
    expect(lineTotal(qty("0.615"), 1_000)).toBe(615);
    expect(lineTotal(qty("1.15"), 10)).toBe(12); // 11.5 rounds up; in doubles it is 11.499999999999998
    expect(lineTotal(qty("8.345"), 100)).toBe(835); // 834.5 rounds up; in doubles it is 834.4999999999999
  });

  it("stays exact at the largest quantity and price", () => {
    expect(lineTotal(MAX_QTY_THOUSANDTHS, 100_000_000)).toBe(99_999_999_900_000);
    expect(lineTotal(qty("999999.999"), 99_999_999)).toBe(99_999_998_900_000);
  });
});

describe("reading a quantity", () => {
  it.each([
    ["1", 1_000],
    ["2", 2_000],
    ["1.5", 1_500],
    ["1,5", 1_500],
    ["0,125", 125],
    ["0.001", 1],
    [" 3 ", 3_000],
    ["007", 7_000],
    ["1.50", 1_500],
    ["999999.999", 999_999_999],
  ])("reads %j as %i thousandths", (text, thousandths) => {
    expect(parseQty(text)).toEqual({ ok: true, thousandths });
  });

  it.each([
    ["", "empty"],
    ["   ", "empty"],
    ["abc", "invalid"],
    ["-1", "invalid"],
    ["1.", "invalid"],
    [".5", "invalid"],
    ["1e3", "invalid"],
    ["1 5", "invalid"],
    ["1.2.3", "invalid"],
    ["1.0001", "too_precise"],
    ["1.2345", "too_precise"],
    ["1.5000", "too_precise"],
    ["0", "not_positive"],
    ["0,000", "not_positive"],
    ["1000000", "too_large"],
  ])("refuses %j as %s", (text, problem) => {
    expect(parseQty(text)).toEqual({ ok: false, problem });
  });

  it("adds decimals exactly", () => {
    expect(qty("0.1") + qty("0.2")).toBe(qty("0.3"));
  });
});

describe("writing a quantity", () => {
  it.each([
    [1_000, "1", "1"],
    [1_500, "1.5", "1,5"],
    [125, "0.125", "0,125"],
    [1, "0.001", "0,001"],
    [10, "0.01", "0,01"],
    [12_340, "12.34", "12,34"],
    [999_999_999, "999999.999", "999999,999"],
  ])("writes %i thousandths as %s for the API and %s for people", (thousandths, api, shown) => {
    expect(qtyToApi(thousandths)).toBe(api);
    expect(formatQty(thousandths)).toBe(shown);
    expect(parseQty(shown)).toEqual({ ok: true, thousandths });
  });

  it("refuses to write a quantity that is not a positive whole number of thousandths", () => {
    expect(() => qtyToApi(0)).toThrow(RangeError);
    expect(() => qtyToApi(1.5)).toThrow(RangeError);
    expect(() => formatQty(-1)).toThrow(RangeError);
  });

  it.each([
    ["2", 2_000],
    ["1.5", 1_500],
    ["1.500", 1_500],
    ["0.125", 125],
    ["1.5000", 1_500],
  ])("reads the server's %s", (text, thousandths) => {
    expect(readServerQty(text)).toBe(thousandths);
  });

  it.each(["", "1,5", "abc", "-1", "0", "0.000", "1.2345", "1e3", " 1"])("refuses %j from the server", (text) => {
    expect(readServerQty(text)).toBeNull();
  });
});

describe("unit price", () => {
  it.each([
    ["1", 1],
    ["50", 50],
    ["4000", 4_000],
    ["4 ming", 4_000],
    ["100000000", 100_000_000],
  ])("reads %s", (text, price) => {
    expect(parsePrice(text)).toEqual({ ok: true, amount: price });
  });

  it.each([
    ["", "empty"],
    ["0", "too_small"],
    ["100000001", "too_large"],
    ["4.5", "not_whole"],
    ["arzon", "invalid"],
  ])("refuses %j as %s", (text, problem) => {
    expect(parsePrice(text)).toEqual({ ok: false, problem });
  });
});

describe("sum of the lines", () => {
  it("must be an entry amount: 100 to 100 000 000", () => {
    expect(linesSumProblem(99)).toBe("too_small");
    expect(linesSumProblem(100)).toBeNull();
    expect(linesSumProblem(100_000_000)).toBeNull();
    expect(linesSumProblem(100_000_001)).toBe("too_large");
  });
});

describe("window for adding goods later (REQ-038)", () => {
  // A sale at 23:30 on Monday 5 October 2026 in Tashkent (UTC+5).
  const sale = new Date("2026-10-05T18:30:00Z");

  it("is open until the end of the day after the sale, Tashkent time", () => {
    expect(goodsWindowOpen(sale, sale)).toBe(true);
    expect(goodsWindowOpen(sale, new Date("2026-10-05T19:00:00Z"))).toBe(true); // Tuesday 00:00
    expect(goodsWindowOpen(sale, new Date("2026-10-06T18:59:59.999Z"))).toBe(true); // Tuesday 23:59:59.999
  });

  it("is closed from the first instant of the second day after the sale", () => {
    expect(goodsWindowOpen(sale, new Date("2026-10-06T19:00:00Z"))).toBe(false); // Wednesday 00:00
    expect(goodsWindowOpen(sale, new Date("2026-10-20T07:00:00Z"))).toBe(false);
  });

  it("counts Tashkent days, not UTC days and not hours", () => {
    // Sold at 00:10 Tashkent on the 6th, which is still the 5th in UTC: open through the 7th in Tashkent.
    const early = new Date("2026-10-05T19:10:00Z");
    expect(goodsWindowOpen(early, new Date("2026-10-07T18:59:00Z"))).toBe(true); // 47 h 49 min later
    expect(goodsWindowOpen(early, new Date("2026-10-07T19:00:00Z"))).toBe(false);
    // Sold at 23:59 Tashkent on the 5th: closed 24 h 1 min later.
    const late = new Date("2026-10-05T18:59:00Z");
    expect(goodsWindowOpen(late, new Date("2026-10-06T19:00:00Z"))).toBe(false);
  });

  it("is closed for a date that cannot be read", () => {
    expect(goodsWindowOpen(new Date("nonsense"), sale)).toBe(false);
  });
});

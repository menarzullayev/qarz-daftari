import { describe, expect, it } from "vitest";

import { translate } from "../i18n/catalog";
import { formatMoney } from "./format";
import {
  amountInput,
  CURRENCIES,
  currencyOf,
  ENTRY_RANGE,
  formatAmount,
  formatDollars,
  formatUsd,
  parseMoney,
  parseUsd,
  USD_LIMIT_RANGE,
  usdInput,
} from "./money";

const NBSP = "\u00a0";
const entry = (input: string) => parseUsd(input, ENTRY_RANGE.USD.min, ENTRY_RANGE.USD.max);

describe("the two currencies", () => {
  it("offers so'm first and dollars second, and nothing else", () => {
    expect(CURRENCIES).toEqual(["UZS", "USD"]);
  });

  it("has the server's ranges, in each currency's minor unit (domain/money.py)", () => {
    expect(ENTRY_RANGE).toEqual({ UZS: { min: 100, max: 100_000_000 }, USD: { min: 1, max: 1_000_000 } });
    expect(USD_LIMIT_RANGE).toEqual({ min: 100, max: 100_000_000 });
  });

  it("reads so'm from the absence of a tag and dollars from the tag", () => {
    expect(currencyOf({})).toBe("UZS");
    expect(currencyOf({ currency: "UZS" })).toBe("UZS");
    expect(currencyOf({ currency: "USD" })).toBe("USD");
  });
});

describe("formatting dollars", () => {
  it.each([
    [0, "0.00"],
    [1, "0.01"],
    [10, "0.10"],
    [99, "0.99"],
    [100, "1.00"],
    [1250, "12.50"],
    [1999, "19.99"],
    [125050, `1${NBSP}250.50`],
    [1_000_000, `10${NBSP}000.00`],
    [100_000_000, `1${NBSP}000${NBSP}000.00`],
    [-125050, `-1${NBSP}250.50`],
    [-5, "-0.05"],
  ])("writes %i cents as %j: always two decimals, thousands apart", (cents, text) => {
    expect(formatUsd(cents)).toBe(text);
  });

  it("puts the sign after the amount, with spaces that do not break", () => {
    expect(formatDollars(125050)).toBe(`1${NBSP}250.50${NBSP}$`);
    expect(formatDollars(1)).toBe(`0.01${NBSP}$`);
    // An ordinary space would let "1 250.50" and "$" part at the end of a line.
    expect(formatDollars(125050)).not.toContain(" ");
  });

  it("is the same in both languages, while so'm keep their word", () => {
    expect(formatMoney(125050, "uz", "USD")).toBe(`1${NBSP}250.50${NBSP}$`);
    expect(formatMoney(125050, "ru", "USD")).toBe(`1${NBSP}250.50${NBSP}$`);
    expect(formatMoney(45000, "uz", "UZS")).toBe(`45${NBSP}000 so'm`);
    expect(formatMoney(45000, "ru", "UZS")).toBe(`45${NBSP}000 сум`);
  });

  it("leaves so'm exactly as they were when no currency is named", () => {
    expect(formatMoney(45000, "uz")).toBe(`45${NBSP}000 so'm`);
    expect(formatMoney(45000, "uz")).toBe(translate("uz", "money.uzs", { amount: `45${NBSP}000` }));
    // Cents are never mistaken for so'm, nor so'm for cents: 45 000 so'm is not 450.00 $.
    expect(formatMoney(45000, "uz")).not.toContain("$");
    expect(formatMoney(45000, "uz", "USD")).toBe(`450.00${NBSP}$`);
  });

  it("refuses an amount that is not a whole number of cents", () => {
    for (const cents of [0.5, 12.5, Number.NaN, Number.POSITIVE_INFINITY, 2 ** 53]) {
      expect(() => formatUsd(cents), String(cents)).toThrow(RangeError);
      expect(() => usdInput(cents), String(cents)).toThrow(RangeError);
    }
  });

  it("writes an amount without its currency word for a range or a field", () => {
    expect(formatAmount(125050, "USD")).toBe(`1${NBSP}250.50`);
    expect(formatAmount(125050, "UZS")).toBe(`125${NBSP}050`);
    expect(usdInput(125050)).toBe("1250.50");
    expect(amountInput(125050, "USD")).toBe("1250.50");
    expect(amountInput(125050, "UZS")).toBe(`125${NBSP}050`);
  });
});

describe("reading dollars as a person types them", () => {
  it.each([
    ["12", 1200],
    ["12.5", 1250],
    ["12.50", 1250],
    ["12,50", 1250],
    ["12,5", 1250],
    ["0.01", 1],
    ["0,01", 1],
    ["0.1", 10],
    ["0.10", 10],
    ["19.99", 1999],
    ["1250.50", 125050],
    ["10000", 1_000_000],
    ["10000.00", 1_000_000],
    ["007", 700],
    ["0012.05", 1205],
    ["  12.50  ", 1250],
    [`${NBSP}12.50${NBSP}`, 1250],
  ])("reads %j as %i cents", (input, cents) => {
    expect(entry(input)).toEqual({ ok: true, amount: cents });
  });

  it("joins the digits as whole numbers, so no float takes part", () => {
    // The traps of a float: 19.99 * 100 is 1998.9999999999998, and 0.1 + 0.2 is not 0.3.
    expect(19.99 * 100).not.toBe(1999);
    expect(0.1 + 0.2).not.toBe(0.3);
    expect(entry("19.99")).toEqual({ ok: true, amount: 1999 });
    const sum = [entry("0.1"), entry("0.2")].reduce((total, part) => total + (part.ok ? part.amount : Number.NaN), 0);
    expect(sum).toBe(30);
    expect(entry("0.3")).toEqual({ ok: true, amount: sum });
    // Every amount with cents in a dollar comes back as the cents it was written from.
    for (const text of ["1.15", "4.35", "8.2", "9.95", "16.9", "32.3", "64.4", "1.005".slice(0, 4), "2.675".slice(0, 4)]) {
      const [dollars = "", fraction = ""] = text.split(".");
      expect(entry(text), text).toEqual({ ok: true, amount: Number(dollars) * 100 + Number(fraction.padEnd(2, "0")) });
    }
    for (let cents = 1; cents <= 20_000; cents += 7) {
      expect(entry(usdInput(cents)), String(cents)).toEqual({ ok: true, amount: cents });
    }
  });

  it.each([
    // A third decimal is refused, never rounded up or down.
    ["12.505", "not_whole"],
    ["12.500", "not_whole"],
    ["0.001", "not_whole"],
    ["12,345", "not_whole"],
    ["0.019", "not_whole"],
    // Nothing typed.
    ["", "empty"],
    ["   ", "empty"],
    [NBSP, "empty"],
    // Letters and words.
    ["abc", "invalid"],
    ["12 dollar", "invalid"],
    ["12usd", "invalid"],
    ["o.5", "invalid"],
    ["1e3", "invalid"],
    ["0x10", "invalid"],
    ["NaN", "invalid"],
    ["Infinity", "invalid"],
    // A sign, the currency sign, or a space inside the number.
    ["-12.50", "invalid"],
    ["+12.50", "invalid"],
    ["$12.50", "invalid"],
    ["12.50$", "invalid"],
    ["12.50 $", "invalid"],
    ["1 250.50", "invalid"],
    [`1${NBSP}250.50`, "invalid"],
    // More than one separator, or a separator with nothing on one side.
    ["1,250.50", "invalid"],
    ["1.250,50", "invalid"],
    ["12..5", "invalid"],
    ["12.5.0", "invalid"],
    ["12.", "invalid"],
    ["12,", "invalid"],
    [".5", "invalid"],
    [",50", "invalid"],
    [".", "invalid"],
    ["12\n13", "invalid"],
    ["9".repeat(501), "invalid"],
    // Outside the range of one entry.
    ["0", "too_small"],
    ["0.00", "too_small"],
    ["0.0", "too_small"],
    ["10000.01", "too_large"],
    ["10001", "too_large"],
    ["99999999999", "too_large"],
    ["9".repeat(400), "too_large"],
  ])("refuses %j as %s", (input, problem) => {
    expect(entry(input)).toEqual({ ok: false, problem });
  });

  it.each([
    ["0.00", "too_small"],
    ["0.01", 1],
    ["10000.00", 1_000_000],
    ["10000.01", "too_large"],
  ])("has the boundary of one entry at %s", (input, expected) => {
    const result = parseMoney(input, "USD");
    expect(result.ok ? result.amount : result.problem).toBe(expected);
  });

  it.each([
    ["0.99", "too_small"],
    ["1", 100],
    ["1000000", 100_000_000],
    ["1000000.01", "too_large"],
  ])("has the boundary of a limit at %s", (input, expected) => {
    const result = parseMoney(input, "USD", USD_LIMIT_RANGE);
    expect(result.ok ? result.amount : result.problem).toBe(expected);
  });

  it("reads each currency by its own rules, and never one as the other", () => {
    // Decimals are dollars' own: so'm are whole.
    expect(parseMoney("12.50", "USD")).toEqual({ ok: true, amount: 1250 });
    expect(parseMoney("12.50", "UZS")).toEqual({ ok: false, problem: "not_whole" });
    // Thousand words and spaces are so'm's own: a dollar field takes digits only.
    expect(parseMoney("45 ming", "UZS")).toEqual({ ok: true, amount: 45000 });
    expect(parseMoney("45 ming", "USD")).toEqual({ ok: false, problem: "invalid" });
    expect(parseMoney("45 000", "UZS")).toEqual({ ok: true, amount: 45000 });
    expect(parseMoney("45 000", "USD")).toEqual({ ok: false, problem: "invalid" });
    // The same digits are different amounts: 500 so'm, and 500 dollars in cents.
    expect(parseMoney("500", "UZS")).toEqual({ ok: true, amount: 500 });
    expect(parseMoney("500", "USD")).toEqual({ ok: true, amount: 50000 });
    // So'm keep the range they had.
    expect(parseMoney("99", "UZS")).toEqual({ ok: false, problem: "too_small" });
    expect(parseMoney("100000001", "UZS")).toEqual({ ok: false, problem: "too_large" });
  });

  it("never returns a fraction or an amount outside the range", () => {
    const pieces = ["0", "1", "45", "99", "000", " ", ".", ",", "$", "-", "x", NBSP];
    let seed = 20261009;
    const next = (bound: number) => {
      seed = (Math.imul(seed, 1103515245) + 12345) & 0x7fffffff;
      return (seed >>> 9) % bound;
    };
    let accepted = 0;
    for (let round = 0; round < 3000; round += 1) {
      let text = "";
      for (let count = 1 + next(5); count > 0; count -= 1) {
        text += pieces[next(pieces.length)] ?? "";
      }
      const result = entry(text);
      if (result.ok) {
        accepted += 1;
        expect(Number.isSafeInteger(result.amount), text).toBe(true);
        expect(result.amount, text).toBeGreaterThanOrEqual(1);
        expect(result.amount, text).toBeLessThanOrEqual(1_000_000);
        expect(text.trim().replace(/\u00a0/g, ""), text).toMatch(/^\d+([.,]\d{1,2})?$/);
      }
    }
    // The check above would say nothing if the generator never made an amount.
    expect(accepted).toBeGreaterThan(100);
  });
});

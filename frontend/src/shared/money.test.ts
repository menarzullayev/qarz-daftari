import { describe, expect, it } from "vitest";

import { formatUzs, parseAmount, parseUzs } from "./money";

describe("formatUzs", () => {
  it.each([
    [0, "0"],
    [999, "999"],
    [1000, "1\u00a0000"],
    [45000, "45\u00a0000"],
    [100000000, "100\u00a0000\u00a0000"],
    [-45000, "-45\u00a0000"],
  ])("formats %i", (amount, expected) => {
    expect(formatUzs(amount)).toBe(expected);
  });

  it.each([1.5, Number.NaN, Number.POSITIVE_INFINITY])("rejects %d", (amount) => {
    expect(() => formatUzs(amount)).toThrow(RangeError);
  });
});

describe("parseUzs", () => {
  it.each([
    ["45000", 45000],
    ["45 000", 45000],
    ["45.000", 45000],
    ["45\u00a0000", 45000],
    [" 45000 ", 45000],
    ["45k", 45000],
    ["45K", 45000],
    ["45 ming", 45000],
    ["45 тыс", 45000],
    ["1 250 000", 1250000],
    ["100", 100],
    ["100000000", 100000000],
  ])("accepts %s", (input, expected) => {
    expect(parseUzs(input)).toBe(expected);
  });

  it.each([
    "",
    "abc",
    "45,5",
    "45.5",
    "45.50",
    "45 00",
    "-45000",
    "99",
    "100000001",
    "100001k",
    "4 5000",
    "1e5",
  ])("rejects %s", (input) => {
    expect(parseUzs(input)).toBeNull();
  });
});

/**
 * The chat parser (backend/src/qarz/domain/chat_entry.py) is the authority on how an amount is written.
 * Every row below is an input on which this module used to answer differently from it. The expected
 * value is what the chat parser gives for "Ali <input>" (see backend/tests/test_chat_entry.py); the last
 * column is what this module answered before the fix.
 */
describe("parseUzs agrees with the chat parser where it used to differ", () => {
  it.each([
    // A leading zero makes dotted or spaced groups a decimal or an ambiguity, never thousand groups.
    ["0.500", null, 500],
    ["0 500", null, 500],
    ["045.000", null, 45000],
    ["045 000", null, 45000],
    ["00 100", null, 100],
    // U+FEFF is an invisible character, not white space.
    ["\ufeff45000", null, 45000],
    // Any run of white space separates thousand groups, including the narrow no-break space.
    ["45  000", 45000, null],
    ["45\u202f000", 45000, null],
    ["45\t000", 45000, null],
    // One trailing full stop or comma ends the amount.
    ["45000.", 45000, null],
    ["45000,", 45000, null],
    ["45.000.", 45000, null],
    ["45 000.", 45000, null],
    // Thousand words: Cyrillic forms, and a full stop after an abbreviation.
    ["45к", 45000, null],
    ["45 минг", 45000, null],
    ["45минг", 45000, null],
    ["45 тыс.", 45000, null],
    ["45k.", 45000, null],
    ["45 ming.", 45000, null],
    // A currency word after the amount is not part of it.
    ["45000 so'm", 45000, null],
    ["45000so'm", 45000, null],
    ["45000 so\u2018m", 45000, null],
    ["45000 сум", 45000, null],
    ["45000 сўм", 45000, null],
    ["45000 sum", 45000, null],
    ["45000 SUM", 45000, null],
    ["45000 uzs", 45000, null],
    ["45 ming so'm", 45000, null],
    ["45k so'm", 45000, null],
  ] as const)("%j is %j (was %j)", (input, expected, before) => {
    expect(parseUzs(input)).toBe(expected);
    expect(before).not.toBe(expected);
  });
});

describe("parseAmount", () => {
  it.each([
    ["1.250.000", 1250000],
    ["1 250.000", 1250000],
    ["1.500k", 1500000],
    ["1 500 ming", 1500000],
    ["045000", 45000],
    ["100.000.000", 100000000],
    ["100 000 000", 100000000],
    ["100000k", 100000000],
  ])("reads %s as %i", (input, amount) => {
    expect(parseAmount(input)).toEqual({ ok: true, amount });
  });

  it.each([
    ["", "empty"],
    ["   ", "empty"],
    [" \t\n\u00a0\u202f ", "empty"],
    // Decimals are refused, never rounded.
    ["45.5", "not_whole"],
    ["45,5", "not_whole"],
    ["45.00", "not_whole"],
    ["45000.00", "not_whole"],
    ["45,000", "not_whole"],
    ["1.5k", "not_whole"],
    ["1,5 ming", "not_whole"],
    ["45.0000", "not_whole"],
    ["4500.000", "not_whole"],
    ["1.25.000", "not_whole"],
    ["0.500", "not_whole"],
    ["99.9", "not_whole"],
    // Range: 100 to 100 000 000 UZS.
    ["0", "too_small"],
    ["45", "too_small"],
    ["000", "too_small"],
    ["99", "too_small"],
    ["100000001", "too_large"],
    ["100001k", "too_large"],
    ["100.000.001", "too_large"],
    ["1 000 000 000", "too_large"],
    ["9223372036854775808", "too_large"],
    ["9".repeat(400), "too_large"],
    // Not an amount, or readable in two ways.
    ["abc", "invalid"],
    ["45..000", "invalid"],
    ["45000abc", "invalid"],
    ["1e5", "invalid"],
    ["0x45000", "invalid"],
    ["45mingso'm", "invalid"],
    ["2 45000", "invalid"],
    ["45 000 2", "invalid"],
    ["45000 500", "invalid"],
    ["45 000,5", "invalid"],
    ["45000\n30000", "invalid"],
    ["45\u200b000", "invalid"],
    ["\u202e00054", "invalid"],
    ["9".repeat(501), "invalid"],
    // The form says whether it is a sale or a payment, and has its own note field.
    ["-45000", "invalid"],
    ["\u221245000", "invalid"],
    ["+45000", "invalid"],
    ["45000 berdi", "invalid"],
    ["45000 non", "invalid"],
    ["45000 so'm non", "invalid"],
  ])("refuses %j as %s", (input, problem) => {
    expect(parseAmount(input)).toEqual({ ok: false, problem });
  });

  it.each([
    ["99", "too_small"],
    ["100", 100],
    ["100000000", 100000000],
    ["100000001", "too_large"],
  ])("has the boundary at %s", (input, expected) => {
    const result = parseAmount(input);
    expect(result.ok ? result.amount : result.problem).toBe(expected);
  });

  it("never returns a fraction or an amount outside the range", () => {
    const pieces = ["0", "1", "45", "500", "000", " ", ".", ",", "k", "ming", " so'm", "\u00a0", "-", "x"];
    let seed = 20261006;
    const next = (bound: number) => {
      seed = (Math.imul(seed, 1103515245) + 12345) & 0x7fffffff;
      return (seed >>> 9) % bound;
    };
    for (let round = 0; round < 3000; round += 1) {
      let text = "";
      for (let count = 1 + next(6); count > 0; count -= 1) {
        text += pieces[next(pieces.length)] ?? "";
      }
      const result = parseAmount(text);
      if (result.ok) {
        expect(Number.isSafeInteger(result.amount), text).toBe(true);
        expect(result.amount, text).toBeGreaterThanOrEqual(100);
        expect(result.amount, text).toBeLessThanOrEqual(100_000_000);
      }
    }
  });
});

import { describe, expect, it } from "vitest";

import { formatUzs, parseUzs } from "./money";

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
    "45000 so'm",
    "4 5000",
    "1e5",
  ])("rejects %s", (input) => {
    expect(parseUzs(input)).toBeNull();
  });
});

import { describe, expect, it } from "vitest";

import { ApiError } from "../api";
import { effectiveLimit, exceedsLimit, mayExceed, parseLimit, refusedLimit } from "./creditRules";
import { endsSoon, modeOfRefusal, modeOfState, WARN_DAYS } from "./shopMode";

const BOUNDS = { min: 1000, max: 10_000_000_000 };

describe("the limit that applies (REQ-044)", () => {
  it("is the customer's own, else the shop's default, else none", () => {
    expect(effectiveLimit(300000, 500000)).toBe(300000);
    expect(effectiveLimit(null, 500000)).toBe(500000);
    expect(effectiveLimit(300000, null)).toBe(300000);
    expect(effectiveLimit(null, null)).toBeNull();
  });

  it("is exceeded only above it: a balance equal to the limit is within it", () => {
    expect(exceedsLimit(150000, 120000, 29999)).toBe(false);
    expect(exceedsLimit(150000, 120000, 30000)).toBe(false);
    expect(exceedsLimit(150000, 120000, 30001)).toBe(true);
    expect(exceedsLimit(150000, 150000, 100)).toBe(true);
  });

  it("is never exceeded when there is none", () => {
    expect(exceedsLimit(null, 120000, 100_000_000)).toBe(false);
  });

  it("stops only a seller, and only where the shop has not let sellers proceed", () => {
    expect(mayExceed("seller", { sellersMayExceed: false })).toBe(false);
    expect(mayExceed("seller", { sellersMayExceed: true })).toBe(true);
    expect(mayExceed("manager", { sellersMayExceed: false })).toBe(true);
    expect(mayExceed("owner", { sellersMayExceed: false })).toBe(true);
  });
});

describe("a typed limit", () => {
  it.each([
    ["1000", 1000],
    ["1 000", 1000],
    ["500 ming", 500000],
    ["10000000000", 10_000_000_000],
  ])("reads %s as %i", (typed, amount) => {
    expect(parseLimit(typed, BOUNDS)).toEqual({ ok: true, amount });
  });

  it.each([
    ["", "empty"],
    ["999", "too_small"],
    ["0", "too_small"],
    ["10000000001", "too_large"],
    ["500.5", "not_whole"],
    ["-5000", "invalid"],
    ["ko'p", "invalid"],
  ])("refuses %j as %s", (typed, problem) => {
    expect(parseLimit(typed, BOUNDS)).toEqual({ ok: false, problem });
  });

  it("follows the bounds the server gave, not its own", () => {
    expect(parseLimit("5000", { min: 10000, max: 20000 })).toEqual({ ok: false, problem: "too_small" });
    expect(parseLimit("20001", { min: 10000, max: 20000 })).toEqual({ ok: false, problem: "too_large" });
    expect(parseLimit("20000", { min: 10000, max: 20000 })).toEqual({ ok: true, amount: 20000 });
  });
});

describe("the figures of a LIMIT_REACHED refusal", () => {
  const refusal = (code: string, fields: Record<string, string>) => new ApiError(409, code, "x", fields);

  it("are read as whole so'm from the server's strings", () => {
    expect(refusedLimit(refusal("LIMIT_REACHED", { limit: "150000", balance: "170000" }))).toEqual({
      limit: 150000,
      balance: 170000,
    });
  });

  it.each([
    [{ limit: "150000" }],
    [{ balance: "170000" }],
    [{ limit: "150000.5", balance: "170000" }],
    [{ limit: "1e5", balance: "170000" }],
    [{ limit: "-1", balance: "170000" }],
    [{ limit: "", balance: "" }],
    [{}],
  ])("are not made up from %j", (fields) => {
    expect(refusedLimit(refusal("LIMIT_REACHED", fields))).toBeNull();
  });

  it("belong to that refusal only", () => {
    expect(refusedLimit(refusal("VALIDATION", { limit: "150000", balance: "170000" }))).toBeNull();
    expect(refusedLimit(null)).toBeNull();
  });
});

describe("what the shop's mode is known from", () => {
  it("reads limited and suspended from the two refusals and nothing from any other", () => {
    expect(modeOfRefusal(new ApiError(402, "SUBSCRIPTION_LIMITED", "x"))).toBe("limited");
    expect(modeOfRefusal(new ApiError(403, "SHOP_SUSPENDED", "x"))).toBe("suspended");
    expect(modeOfRefusal(new ApiError(403, "FORBIDDEN_ROLE", "x"))).toBeNull();
    expect(modeOfRefusal(new ApiError(402, "ERROR", null))).toBeNull();
  });

  it("reads limited and suspended from the subscription state, and nothing from a shop that works", () => {
    expect(modeOfState("limited")).toBe("limited");
    expect(modeOfState("suspended")).toBe("suspended");
    expect(modeOfState("trial")).toBeNull();
    expect(modeOfState("active")).toBeNull();
  });

  it("calls a period ending when seven days or fewer are left", () => {
    expect(WARN_DAYS).toBe(7);
    expect(endsSoon(8)).toBe(false);
    expect(endsSoon(7)).toBe(true);
    expect(endsSoon(1)).toBe(true);
    expect(endsSoon(0)).toBe(true);
    expect(endsSoon(null)).toBe(false);
  });
});

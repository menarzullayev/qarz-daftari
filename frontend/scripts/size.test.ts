import { describe, expect, it } from "vitest";

import { DEFAULT_BUDGET_KB, checkBudget, collectInitialAssets, parseBudgetKb } from "./size.ts";

describe("collectInitialAssets", () => {
  const html = `
    <head>
      <script src="https://telegram.org/js/telegram-web-app.js"></script>
      <script type="module" crossorigin src="/assets/app-abc.js"></script>
      <link rel="modulepreload" crossorigin href="/assets/shell-def.js">
      <link rel="stylesheet" crossorigin href="/assets/shell-ghi.css">
      <link rel="icon" href="/favicon.ico">
      <link rel="modulepreload" href="/assets/shell-def.js">
    </head>`;

  it("collects scripts, preloaded modules, and style sheets once each", () => {
    expect(collectInitialAssets(html).local).toEqual([
      "/assets/app-abc.js",
      "/assets/shell-def.js",
      "/assets/shell-ghi.css",
    ]);
  });

  it("separates resources on other hosts and ignores unrelated links", () => {
    expect(collectInitialAssets(html).external).toEqual(["https://telegram.org/js/telegram-web-app.js"]);
    expect(collectInitialAssets(html).local).not.toContain("/favicon.ico");
  });
});

describe("checkBudget", () => {
  it("passes at or under the budget", () => {
    expect(checkBudget([100 * 1024, 200 * 1024], 300).withinBudget).toBe(true);
    expect(checkBudget([70_000], DEFAULT_BUDGET_KB)).toEqual({
      totalBytes: 70_000,
      budgetBytes: 307_200,
      withinBudget: true,
    });
  });

  it("fails one byte over the budget, and when the budget is lowered below the real size", () => {
    expect(checkBudget([300 * 1024 + 1], 300).withinBudget).toBe(false);
    expect(checkBudget([70_000], 50).withinBudget).toBe(false);
  });

  it("rejects a budget that is not a positive number", () => {
    expect(() => checkBudget([1], 0)).toThrow(RangeError);
    expect(() => checkBudget([1], Number.NaN)).toThrow(RangeError);
  });
});

describe("parseBudgetKb", () => {
  it("defaults to 300 KB (NFR-010)", () => {
    expect(DEFAULT_BUDGET_KB).toBe(300);
    expect(parseBudgetKb([])).toBe(300);
  });

  it("reads --budget-kb and rejects nonsense", () => {
    expect(parseBudgetKb(["--budget-kb=50"])).toBe(50);
    expect(() => parseBudgetKb(["--budget-kb=abc"])).toThrow(RangeError);
    expect(() => parseBudgetKb(["--budget-kb=-1"])).toThrow(RangeError);
  });
});

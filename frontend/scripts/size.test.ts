import { describe, expect, it } from "vitest";

import {
  ADMIN_BUDGET_KB,
  BUDGETS,
  CUSTOMER_PAGE_BUDGET_KB,
  DEFAULT_BUDGET_KB,
  PANEL_BUDGET_KB,
  checkBudget,
  collectInitialAssets,
  parseBudgetKb,
  verdict,
} from "./size.ts";

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

  it("reads the customer page's own budget the same way, and keeps it far under the Mini App's", () => {
    expect(CUSTOMER_PAGE_BUDGET_KB).toBe(20);
    const name = "--customer-page-budget-kb";
    expect(parseBudgetKb([], name, CUSTOMER_PAGE_BUDGET_KB)).toBe(20);
    expect(parseBudgetKb(["--budget-kb=50", `${name}=5`], name, CUSTOMER_PAGE_BUDGET_KB)).toBe(5);
    expect(parseBudgetKb([`${name}=5`])).toBe(300);
    expect(() => parseBudgetKb([`${name}=0`], name, CUSTOMER_PAGE_BUDGET_KB)).toThrow(RangeError);
    // What the check then does with it: 4.4 KB passes, the staff application's 105 KB does not.
    expect(checkBudget([4_400], CUSTOMER_PAGE_BUDGET_KB).withinBudget).toBe(true);
    expect(checkBudget([105_000], CUSTOMER_PAGE_BUDGET_KB).withinBudget).toBe(false);
  });
});

describe("the budgets of the panel and the administration panel", () => {
  it("are their sizes when written plus about 15 %, each with an option of its own", () => {
    expect(PANEL_BUDGET_KB).toBe(167);
    expect(ADMIN_BUDGET_KB).toBe(150);
    expect(145.03 * 1.15).toBeGreaterThan(PANEL_BUDGET_KB - 1);
    expect(130.94 * 1.15).toBeGreaterThan(ADMIN_BUDGET_KB - 1);
    expect(parseBudgetKb([], "--panel-budget-kb", PANEL_BUDGET_KB)).toBe(167);
    expect(parseBudgetKb(["--panel-budget-kb=1"], "--panel-budget-kb", PANEL_BUDGET_KB)).toBe(1);
    expect(parseBudgetKb(["--panel-budget-kb=1"], "--admin-budget-kb", ADMIN_BUDGET_KB)).toBe(150);
  });

  it("every page of the build has a budget, and no two share an option", () => {
    expect(BUDGETS.map(({ entry }) => entry)).toEqual(["app", "panel", "admin", "k"]);
    expect(new Set(BUDGETS.map(({ option }) => option)).size).toBe(BUDGETS.length);
    expect(BUDGETS.map(({ budgetKb }) => budgetKb)).toEqual([300, 167, 150, 20]);
  });

  it("pass what the panel weighs today and fail it with a screen's worth more", () => {
    const today = Math.round(145.03 * 1024);
    expect(verdict("the web panel's first load", today, PANEL_BUDGET_KB)).toEqual({
      ok: true,
      line: "OK: the web panel's first load is 145.03 KB gzip, budget is 167 KB",
    });
    // The stock screens are 26 KB compressed: fetched with the first load, they would be noticed.
    const withStock = verdict("the web panel's first load", today + 26 * 1024, PANEL_BUDGET_KB);
    expect(withStock.ok).toBe(false);
    expect(withStock.line).toBe("FAIL: the web panel's first load is 171.03 KB gzip, budget is 167 KB");
    expect(verdict("the administration panel's first load", 151 * 1024, ADMIN_BUDGET_KB).ok).toBe(false);
    expect(verdict("the administration panel's first load", 150 * 1024, ADMIN_BUDGET_KB).ok).toBe(true);
  });
});

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { definedTokens, styleProblems } from "../testing/styleGuard";
import { DESKTOP_QUERY } from "./tables";

const css = readFileSync(resolve(import.meta.dirname, "panel.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");

describe("the panel's style sheet", () => {
  it("changes layout at the same width at which the screens change from rows to tables", () => {
    expect(DESKTOP_QUERY).toBe("(min-width: 1024px)");
    expect(css).toContain("@media (min-width: 1024px)");
    // The stacked tables end exactly where the desktop layout begins.
    expect([...css.matchAll(/@media \(([^)]+)\)/g)].map((match) => match[1])).toEqual(["max-width: 1023.98px", "min-width: 1024px"]);
  });

  it("never removes the focus ring that shell.css gives every control", () => {
    expect(css).not.toMatch(/outline\s*:/);
    expect(css).not.toMatch(/:focus/);
  });

  it("uses only the design tokens, so the contrast check of the tokens covers it", () => {
    expect(css).not.toMatch(/#[0-9a-f]{3,8}\b/i);
    expect(css).not.toMatch(/\b(?:rgb|hsl)a?\(/i);
    expect(styleProblems(css, definedTokens())).toEqual([]);
    // The colors it combines: muted text on a card and on the page ground, hairlines between rows.
    const used = [...new Set([...css.matchAll(/var\((--qd-[\w-]+)\)/g)].map((match) => match[1] ?? ""))];
    const colors = used.filter((name) => !/^--qd-(?:space|radius|shadow|type|tracking|header)-/.test(name));
    expect(colors.sort()).toEqual(["--qd-bg", "--qd-line", "--qd-muted", "--qd-surface"]);
  });

  it("hides the stacked table's header row from sight only, so a screen reader still has the column names", () => {
    const narrow = /@media \(max-width: 1023\.98px\) \{([\s\S]*?)\n\}/.exec(css)?.[1] ?? "";
    expect(narrow).toContain("content: attr(data-label)");
    expect(narrow).not.toMatch(/display:\s*none/);
    expect(narrow).not.toMatch(/visibility:\s*hidden/);
  });
});

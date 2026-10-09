import { describe, expect, it } from "vitest";

import { definedTokens, readStyleSheet, styleProblems, usedTokens } from "../testing/styleGuard";

const tokens = definedTokens();

const SHEETS: readonly (readonly string[])[] = [
  ["shared", "shell.css"],
  ["shared", "workspace", "workspace.css"],
  ["shared", "reports", "reports.css"],
  ["panel", "panel.css"],
];

/** A style sheet with its `prefers-reduced-motion: no-preference` blocks taken out. */
function withoutMotionBlocks(css: string): string {
  return css.replace(/@media \(prefers-reduced-motion: no-preference\) \{[\s\S]*?\n\}/g, "");
}

function ruleOf(css: string, selector: string): string {
  const escaped = selector.replace(/[.[\]"=]/g, "\\$&");
  return new RegExp("(?:^|\\n)" + escaped + " \\{([^}]*)\\}").exec(css)?.[1] ?? "";
}

describe("the style sheets use the design tokens and nothing else", () => {
  it.each([
    [["shared", "workspace", "workspace.css"]],
    [["shared", "reports", "reports.css"]],
    [["panel", "panel.css"]],
  ])("%j: no color written out, no unknown variable, the focus ring left alone", (path) => {
    expect(styleProblems(readStyleSheet(...path), tokens)).toEqual([]);
  });

  it("shell.css: the same, except that it is the one that draws the focus ring", () => {
    const css = readStyleSheet("shared", "shell.css");
    expect(styleProblems(css, tokens, { ownsFocusRing: true })).toEqual([]);
    // A 3px ring, 2px away, on everything that takes keyboard focus.
    expect(css).toMatch(/\n:focus-visible \{\s*outline: 3px solid var\(--qd-focus\);\s*outline-offset: 2px;\s*\}/);
    // The only place a ring is removed is the main region, which is not a control.
    expect([...css.matchAll(/([^{}]+)\{[^}]*outline:\s*(?:none|0)/g)].map((match) => (match[1] ?? "").trim())).toEqual([
      ".shell__main:focus",
    ]);
  });

  it("tokens.css defines every variable the style sheets use, and each sheet uses some", () => {
    for (const path of SHEETS) {
      const used = usedTokens(readStyleSheet(...path));
      expect(used.length, path.join("/")).toBeGreaterThan(0);
      expect(
        used.filter((name) => !tokens.has(name)),
        path.join("/"),
      ).toEqual([]);
    }
  });

  it("stops the skeleton's pulse and the spinner for a person who asked for less motion", () => {
    const css = readStyleSheet("shared", "workspace", "workspace.css");
    expect(css).toMatch(/\.skeleton \{\s*animation: qd-pulse/);
    expect(css).toMatch(/\.spinner \{\s*animation: qd-spin/);
    for (const path of SHEETS) {
      expect(withoutMotionBlocks(readStyleSheet(...path)), path.join("/")).not.toMatch(/animation|transition|@keyframes/);
    }
  });

  it("keeps every control at least 2.75rem tall", () => {
    const css = readStyleSheet("shared", "shell.css") + readStyleSheet("shared", "workspace", "workspace.css");
    expect(readStyleSheet("shared", "tokens.css")).toMatch(/--qd-control-height: 2\.75rem;/);
    for (const selector of [".button", ".button--small", ".input", ".row__link", ".pick", ".choice", ".toast__action", ".skip-link"]) {
      expect(ruleOf(css, selector), selector).toContain("min-height: var(--qd-control-height)");
    }
    expect(ruleOf(css, ".language__option,\n.theme-toggle__option,\n.toggle__option")).toContain(
      "min-height: var(--qd-control-height)",
    );
    // Would a shorter control be noticed? This is how the check reads a rule.
    expect(ruleOf(".button {\n  min-height: 2rem;\n}", ".button")).not.toContain("min-height: var(--qd-control-height)");
  });
});

describe("the guard itself", () => {
  it("refuses a color written out in any notation", () => {
    expect(styleProblems(".a { color: #c00; }", tokens)).toEqual(["color written out: #c00"]);
    expect(styleProblems(".a { color: #0B63CE; }", tokens)).toEqual(["color written out: #0B63CE"]);
    expect(styleProblems(".a { background: rgba(0, 0, 0, 0.5); }", tokens)).toEqual(["color written out: rgba("]);
    expect(styleProblems(".a { color: hsl(210 80% 40%); }", tokens)).toEqual(["color written out: hsl("]);
    expect(styleProblems(".a { color: oklch(60% 0.1 250); }", tokens)).toEqual(["color written out: oklch("]);
  });

  it("refuses a variable tokens.css does not define", () => {
    expect(styleProblems(".a { color: var(--qd-brand); }", tokens)).toEqual(["unknown variable: --qd-brand"]);
    expect(styleProblems(".a { color: var(--tg-theme-bg-color, red); }", tokens)).toEqual([
      "unknown variable: --tg-theme-bg-color",
    ]);
  });

  it("refuses a rule that touches the focus ring", () => {
    expect(styleProblems(".a { outline: none; }", tokens)).toEqual(["outline changed"]);
    expect(styleProblems(".a { outline-width: 0; }", tokens)).toEqual(["outline changed"]);
    expect(styleProblems(".a:focus-visible { border-color: var(--qd-focus); }", tokens)).toEqual([":focus rule"]);
    expect(styleProblems(".a { outline: none; }", tokens, { ownsFocusRing: true })).toEqual([]);
  });

  it("does not read comments, and passes a rule made of tokens", () => {
    expect(styleProblems("/* was #fff */ .a { color: var(--qd-text); margin: var(--qd-space-2); }", tokens)).toEqual([]);
  });
});

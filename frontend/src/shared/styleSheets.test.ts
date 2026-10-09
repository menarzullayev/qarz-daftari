import { describe, expect, it } from "vitest";

import { definedTokens, readStyleSheet, styleProblems, usedTokens } from "../testing/styleGuard";

const tokens = definedTokens();

const SHEETS: readonly (readonly string[])[] = [
  ["shared", "shell.css"],
  ["shared", "workspace", "workspace.css"],
  ["shared", "reports", "reports.css"],
  ["panel", "panel.css"],
  // The page behind a customer's read-only link: its own sheet, held to the same tokens.
  ["k", "k.css"],
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
    [["k", "k.css"]],
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
    expect(ruleOf(css, ".theme-toggle__option,\n.toggle__option")).toContain(
      "min-height: var(--qd-control-height)",
    );
    // Would a shorter control be noticed? This is how the check reads a rule.
    expect(ruleOf(".button {\n  min-height: 2rem;\n}", ".button")).not.toContain("min-height: var(--qd-control-height)");
  });
});

/**
 * What in a phone's shell keeps the tab bar off the content: nothing, if the bar is taken out of the
 * flow and the shell keeps a height free for it that is written down apart (the bar grows with the
 * text; the number does not). Empty when the room the bar takes is the bar's own height.
 */
function tabBarProblems(css: string): string[] {
  // The phone's rules are the ones outside every media query.
  const phone = css.replace(/@media[^{]*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}/g, "");
  const shell = ruleOf(phone, ".shell");
  const bar = ruleOf(phone, ".shell__nav");
  const problems: string[] = [];
  if (!/position: sticky;/.test(bar) || !/\n\s*bottom: 0;/.test(bar)) {
    problems.push("the bar is not held to the bottom edge inside the flow");
  }
  if (/position: (?:fixed|absolute)/.test(bar)) {
    problems.push("the bar is out of the flow");
  }
  if (!/display: flex;/.test(shell) || !/flex-direction: column;/.test(shell)) {
    problems.push("the shell is not a column the bar is the last item of");
  }
  if (/padding-bottom|margin-bottom/.test(shell)) {
    problems.push("the shell keeps a height of its own free for the bar");
  }
  if (!/\n\s*order: 1;/.test(bar)) {
    problems.push("the bar is not after the content");
  }
  return problems;
}

describe("the phone's tab bar takes the room it needs, at any text size", () => {
  const css = readStyleSheet("shared", "shell.css");

  it("is the last item of the shell's column and sticks to the bottom, so no height is written down for it", () => {
    expect(tabBarProblems(css)).toEqual([]);
    // The content takes what is left and is never squeezed under the bar.
    expect(ruleOf(css, ".shell__main")).toContain("flex: 1 0 auto;");
  });

  it("is still the side list from 720px, where the shell is the grid it was", () => {
    const wide = /@media \(min-width: 720px\) \{([\s\S]*?)\n\}/.exec(css)?.[1] ?? "";
    expect(ruleOf(wide, "  .shell")).toContain("display: grid;");
    const bar = ruleOf(wide, "  .shell__nav");
    expect(bar).toContain("top: 0;");
    expect(bar).toContain("bottom: auto;");
    expect(bar).toContain("margin-top: 0;");
  });

  it("would be noticed: a bar fixed over the content with a height kept free by a number", () => {
    const before = ".shell {\n  display: grid;\n  padding-bottom: calc(var(--qd-nav-height) + env(safe-area-inset-bottom, 0px));\n}\n.shell__nav {\n  position: fixed;\n  bottom: 0;\n}";
    expect(tabBarProblems(before)).toEqual([
      "the bar is not held to the bottom edge inside the flow",
      "the bar is out of the flow",
      "the shell is not a column the bar is the last item of",
      "the shell keeps a height of its own free for the bar",
      "the bar is not after the content",
    ]);
    // And a media query's own rules are not taken for the phone's.
    expect(tabBarProblems(css.replace(/\n\.shell__nav \{[^}]*\}/, "\n.shell__nav {\n  position: fixed;\n}"))).toContain("the bar is out of the flow");
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

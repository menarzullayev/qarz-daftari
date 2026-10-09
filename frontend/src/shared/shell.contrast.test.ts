import { describe, expect, it } from "vitest";

import { readStyleSheet } from "../testing/styleGuard";

/** Relative luminance and contrast ratio as defined by WCAG 2.x. */
function luminance(hex: string): number {
  const channels = [1, 3, 5].map((start) => {
    const value = Number.parseInt(hex.slice(start, start + 2), 16) / 255;
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * (channels[0] ?? 0) + 0.7152 * (channels[1] ?? 0) + 0.0722 * (channels[2] ?? 0);
}

function contrast(foreground: string, background: string): number {
  const [lighter, darker] = [luminance(foreground), luminance(background)].sort((a, b) => b - a);
  return ((lighter ?? 0) + 0.05) / ((darker ?? 0) + 0.05);
}

type Theme = Readonly<Record<string, string>>;

/** The six-digit hex colors a block of declarations gives to `--qd-*` variables. */
function colorsOf(block: string): Theme {
  return Object.fromEntries([...block.matchAll(/(--qd-[\w-]+):\s*(#[0-9a-f]{6})\s*;/gi)].map((m) => [m[1], m[2]]));
}

const css = readStyleSheet("shared", "tokens.css");

/** The three places tokens.css sets the colors: the default, the chosen dark, and the device's dark. */
const BLOCKS = {
  light: /(?:^|\n):root \{([^}]*)\}/.exec(css)?.[1] ?? "",
  dark: /\n:root\[data-theme="dark"\] \{([^}]*)\}/.exec(css)?.[1] ?? "",
  systemDark: /@media \(prefers-color-scheme: dark\) \{\s*:root:not\(\[data-theme\]\) \{([^}]*)\}/.exec(css)?.[1] ?? "",
};

const light = colorsOf(BLOCKS.light);
const dark = colorsOf(BLOCKS.dark);

const AA_TEXT = 4.5;
const AA_NON_TEXT = 3;

type Pair = readonly [foreground: string, background: string];

/**
 * Every text/ground pair the tokens are meant for (the design system's notes on each token), and the
 * few more the style sheets combine. A pair that is not here must not appear in a style sheet.
 */
const TEXT_PAIRS: readonly Pair[] = [
  // Body copy and headings: on cards, the page ground, tracks, and every soft ground.
  ["--qd-text", "--qd-bg"],
  ["--qd-text", "--qd-surface"],
  ["--qd-text", "--qd-surface-2"],
  ["--qd-text", "--qd-accent-soft"],
  ["--qd-text", "--qd-success-soft"],
  ["--qd-text", "--qd-warning-soft"],
  ["--qd-text", "--qd-danger-soft"],
  // Labels, hints, meta lines, idle navigation.
  ["--qd-muted", "--qd-bg"],
  ["--qd-muted", "--qd-surface"],
  ["--qd-muted", "--qd-surface-2"],
  // Links, the current navigation item, accent text.
  ["--qd-link", "--qd-bg"],
  ["--qd-link", "--qd-surface"],
  ["--qd-link", "--qd-accent-soft"],
  // The primary button and the main figure, at rest and under the pointer.
  ["--qd-on-accent", "--qd-accent"],
  ["--qd-on-accent", "--qd-accent-hover"],
  // Status text on a card and on its own soft ground (badges, notices).
  ["--qd-success", "--qd-bg"],
  ["--qd-success", "--qd-success-soft"],
  ["--qd-warning", "--qd-bg"],
  ["--qd-warning", "--qd-warning-soft"],
  ["--qd-danger", "--qd-bg"],
  ["--qd-danger", "--qd-danger-soft"],
  ["--qd-on-danger", "--qd-danger"],
  // The toast is the page inverted.
  ["--qd-bg", "--qd-text"],
  // Combined by the style sheets beyond the notes: a warning line or a field's error that sits on the
  // page ground or inside an information notice.
  ["--qd-danger", "--qd-surface"],
  ["--qd-danger", "--qd-accent-soft"],
];

/** Borders of controls and the focus ring, against every ground a control sits on. */
const NON_TEXT_PAIRS: readonly Pair[] = [
  ["--qd-border", "--qd-bg"],
  ["--qd-border", "--qd-surface"],
  ["--qd-focus", "--qd-bg"],
  ["--qd-focus", "--qd-surface"],
  ["--qd-focus", "--qd-surface-2"],
  ["--qd-focus", "--qd-accent-soft"],
  ["--qd-focus", "--qd-success-soft"],
  ["--qd-focus", "--qd-warning-soft"],
  ["--qd-focus", "--qd-danger-soft"],
  // The marks of a notice and of a field's error are drawn in these.
  ["--qd-danger", "--qd-danger-soft"],
  ["--qd-success", "--qd-success-soft"],
  ["--qd-warning", "--qd-warning-soft"],
  ["--qd-link", "--qd-accent-soft"],
];

/** The pairs of a theme that fall short of a ratio, or name a color the theme does not have. */
function failures(theme: Theme, pairs: readonly Pair[], minimum: number): string[] {
  return pairs.flatMap(([foreground, background]) => {
    const fg = theme[foreground];
    const bg = theme[background];
    if (fg === undefined || bg === undefined) {
      return [`${foreground} on ${background}: not defined`];
    }
    const ratio = contrast(fg, bg);
    return ratio >= minimum ? [] : [`${foreground} on ${background}: ${ratio.toFixed(2)}`];
  });
}

describe.each([
  ["light", light],
  ["dark", dark],
])("%s theme contrast (WCAG AA)", (_name, theme) => {
  it.each(TEXT_PAIRS)("%s on %s is at least 4.5:1", (foreground, background) => {
    expect(failures(theme, [[foreground, background]], AA_TEXT)).toEqual([]);
  });

  it.each(NON_TEXT_PAIRS)("%s against %s is at least 3:1 (borders, the focus ring, marks)", (foreground, background) => {
    expect(failures(theme, [[foreground, background]], AA_NON_TEXT)).toEqual([]);
  });
});

describe("the themes of tokens.css", () => {
  it("has the 22 color tokens, 21 of them plain hex and the overlay translucent", () => {
    expect(Object.keys(light)).toHaveLength(21);
    expect(BLOCKS.light).toMatch(/--qd-overlay:\s*rgba\(/);
    // The nine names the Telegram theme replaces (shared/telegram.ts) are still there.
    for (const name of ["bg", "surface", "text", "muted", "link", "accent", "on-accent", "border", "focus"]) {
      expect(light[`--qd-${name}`], name).toBeDefined();
    }
  });

  it("gives the dark theme every color the light theme has", () => {
    expect(Object.keys(dark).sort()).toEqual(Object.keys(light).sort());
    expect(BLOCKS.dark).toMatch(/--qd-overlay:\s*rgba\(/);
  });

  it("uses the same dark colors for a chosen dark theme and for a device that asks for dark", () => {
    expect(BLOCKS.systemDark).not.toBe("");
    const normalize = (block: string) =>
      block
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean);
    expect(normalize(BLOCKS.systemDark)).toEqual(normalize(BLOCKS.dark));
  });

  it("leaves the light theme to a device that asks for dark once a person chose light", () => {
    // "System" is the absence of data-theme: the media query must not reach a page that has one.
    expect(css).toMatch(/@media \(prefers-color-scheme: dark\) \{\s*:root:not\(\[data-theme\]\) \{/);
    expect(css).not.toMatch(/@media \(prefers-color-scheme: dark\) \{\s*:root \{/);
  });
});

describe("the check itself", () => {
  it("measures contrast correctly", () => {
    expect(contrast("#000000", "#ffffff")).toBeCloseTo(21, 5);
    expect(contrast("#777777", "#ffffff")).toBeCloseTo(4.48, 2);
    expect(contrast("#777777", "#ffffff")).toBeLessThan(AA_TEXT);
    expect(contrast("#999999", "#ffffff")).toBeLessThan(AA_NON_TEXT);
  });

  it("fails on a weak pair: grey text that is a little too light for a white card", () => {
    const weak = { ...light, "--qd-muted": "#777777" };
    expect(failures(weak, TEXT_PAIRS, AA_TEXT)).toEqual([
      "--qd-muted on --qd-bg: 4.48",
      "--qd-muted on --qd-surface: 4.03",
      "--qd-muted on --qd-surface-2: 3.68",
    ]);
  });

  it("fails on a dark theme that kept a light theme's color", () => {
    // The light theme's link blue on the dark theme's card: the mistake of filling in a theme halfway.
    const half = { ...dark, "--qd-link": light["--qd-link"] ?? "" };
    const failed = failures(half, TEXT_PAIRS, AA_TEXT);
    expect(failed).toHaveLength(3);
    expect(failed[0]).toMatch(/^--qd-link on --qd-bg: [12]\.\d\d$/);
  });

  it("fails on a border too faint to show where a field is, and on a color that is missing", () => {
    expect(failures({ ...light, "--qd-border": light["--qd-line"] ?? "" }, NON_TEXT_PAIRS, AA_NON_TEXT)).toHaveLength(2);
    const withoutSoft = Object.fromEntries(Object.entries(light).filter(([name]) => name !== "--qd-danger-soft"));
    expect(failures(withoutSoft, [["--qd-danger", "--qd-danger-soft"]], AA_TEXT)).toEqual([
      "--qd-danger on --qd-danger-soft: not defined",
    ]);
  });
});

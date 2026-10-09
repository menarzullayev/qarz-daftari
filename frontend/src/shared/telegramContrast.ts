/**
 * A guard between Telegram's theme and the page (WCAG 2.x, level AA). Telegram's colors are chosen by
 * Telegram's clients and by people who make their own themes; nobody checked them against how this
 * application uses them. Before they replace the nine base tokens, each pair the application draws with
 * them is measured, and a pair that falls below its threshold is replaced by the design system's own
 * colors for the theme's scheme (tokens.css). What Telegram's theme does well is kept as it is.
 *
 * Pure: no document, no bridge. `telegram.ts` applies the result.
 */

export type Scheme = "light" | "dark";

/** The nine base tokens Telegram's theme replaces. */
export const BASE_TOKENS = [
  "--qd-bg",
  "--qd-surface",
  "--qd-text",
  "--qd-muted",
  "--qd-link",
  "--qd-accent",
  "--qd-on-accent",
  "--qd-border",
  "--qd-focus",
] as const;
export type BaseToken = (typeof BASE_TOKENS)[number];
export type BaseColors = Readonly<Record<BaseToken, string>>;

/**
 * The design system's values of the nine tokens, as tokens.css has them. A copy, because the guard must
 * know the color it falls back to in order to measure the next pair against it;
 * telegramContrast.test.ts fails when the two differ.
 */
export const DESIGN: Readonly<Record<Scheme, BaseColors>> = {
  light: {
    "--qd-bg": "#ffffff",
    "--qd-surface": "#f1f3f6",
    "--qd-text": "#0f172a",
    "--qd-muted": "#55627a",
    "--qd-link": "#0b5cc4",
    "--qd-accent": "#0b63ce",
    "--qd-on-accent": "#ffffff",
    "--qd-border": "#76829a",
    "--qd-focus": "#0b63ce",
  },
  dark: {
    "--qd-bg": "#171c26",
    "--qd-surface": "#0e121a",
    "--qd-text": "#e8ecf3",
    "--qd-muted": "#a3adbf",
    "--qd-link": "#7db5ff",
    "--qd-accent": "#5aa2ff",
    "--qd-on-accent": "#061a33",
    "--qd-border": "#7e8aa0",
    "--qd-focus": "#7db5ff",
  },
};

/** Text against its ground. */
export const AA_TEXT = 4.5;
/** A boundary or a shape that is not text (a field's border, the focus ring, a button against the page). */
export const AA_NON_TEXT = 3;

/** Below this luminance a background reads as dark: light text on it has the better contrast. */
export const DARK_BACKGROUND = 0.18;

/** Relative luminance (WCAG) of a six-digit hex color. */
export function luminance(hex: string): number {
  const channels = [1, 3, 5].map((start) => {
    const value = Number.parseInt(hex.slice(start, start + 2), 16) / 255;
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * (channels[0] ?? 0) + 0.7152 * (channels[1] ?? 0) + 0.0722 * (channels[2] ?? 0);
}

/** Contrast ratio (WCAG) of two six-digit hex colors: 1 for the same color, 21 for black on white. */
export function contrast(one: string, other: string): number {
  const a = luminance(one);
  const b = luminance(other);
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}

/** The first candidate that reaches `threshold` against every ground; `last` when none of them does. */
function firstThatReads(candidates: readonly string[], last: string, grounds: readonly string[], threshold: number): string {
  const reads = (candidate: string) => grounds.every((ground) => contrast(candidate, ground) >= threshold);
  return candidates.find(reads) ?? last;
}

/**
 * Telegram's nine colors, with every pair that would be hard to read replaced.
 *
 *   text on bg, text on surface            4.5:1
 *   muted on bg and on surface             4.5:1
 *   link on bg and on surface              4.5:1
 *   on-accent on accent                    4.5:1   (and accent against bg 3:1: a button must show)
 *   border against bg                      3:1
 *   focus against bg                       3:1
 *
 * The page ground and its text are one pair: when Telegram's fail, both come from the design system,
 * and so does the second ground. Each of the others falls back alone, to the design system's color for
 * the scheme; when the theme's ground is so unusual that this color does not reach the threshold on it
 * either, the text color is used, which the first rule has already made readable there.
 *
 * `scheme` is what Telegram says its theme is. Without it the ground's own lightness decides.
 */
export function guardContrast(colors: BaseColors, scheme: Scheme | null = null): BaseColors {
  const design = DESIGN[scheme ?? (luminance(colors["--qd-bg"]) < DARK_BACKGROUND ? "dark" : "light")];

  const ownGround = contrast(colors["--qd-text"], colors["--qd-bg"]) >= AA_TEXT;
  const bg = ownGround ? colors["--qd-bg"] : design["--qd-bg"];
  const text = ownGround ? colors["--qd-text"] : design["--qd-text"];
  // The second ground carries the same text: Telegram's, or else the design system's, or else the first.
  const surface = firstThatReads(
    ownGround ? [colors["--qd-surface"], design["--qd-surface"]] : [design["--qd-surface"]],
    bg,
    [text],
    AA_TEXT,
  );
  const grounds = [bg, surface];

  const muted = firstThatReads([colors["--qd-muted"], design["--qd-muted"]], text, grounds, AA_TEXT);
  const link = firstThatReads([colors["--qd-link"], design["--qd-link"]], text, grounds, AA_TEXT);
  const border = firstThatReads([colors["--qd-border"], design["--qd-border"]], text, [bg], AA_NON_TEXT);
  const focus = firstThatReads([colors["--qd-focus"], design["--qd-focus"]], text, [bg], AA_NON_TEXT);

  // A button: its label on it, and the button itself against the page. The last resort is the page's
  // own text and ground the other way round, which the first rule made readable.
  const buttons: readonly (readonly [accent: string, onAccent: string])[] = [
    [colors["--qd-accent"], colors["--qd-on-accent"]],
    [design["--qd-accent"], design["--qd-on-accent"]],
  ];
  const [accent, onAccent] = buttons.find(
    ([ground, label]) => contrast(label, ground) >= AA_TEXT && contrast(ground, bg) >= AA_NON_TEXT,
  ) ?? [text, bg];

  return {
    "--qd-bg": bg,
    "--qd-surface": surface,
    "--qd-text": text,
    "--qd-muted": muted,
    "--qd-link": link,
    "--qd-accent": accent,
    "--qd-on-accent": onAccent,
    "--qd-border": border,
    "--qd-focus": focus,
  };
}

/** The pairs `guardContrast` promises and a set of colors does not keep (the tests ask). */
export function failingPairs(colors: BaseColors): string[] {
  const pairs: readonly (readonly [BaseToken, BaseToken, number])[] = [
    ["--qd-text", "--qd-bg", AA_TEXT],
    ["--qd-text", "--qd-surface", AA_TEXT],
    ["--qd-muted", "--qd-bg", AA_TEXT],
    ["--qd-muted", "--qd-surface", AA_TEXT],
    ["--qd-link", "--qd-bg", AA_TEXT],
    ["--qd-link", "--qd-surface", AA_TEXT],
    ["--qd-on-accent", "--qd-accent", AA_TEXT],
    ["--qd-accent", "--qd-bg", AA_NON_TEXT],
    ["--qd-border", "--qd-bg", AA_NON_TEXT],
    ["--qd-focus", "--qd-bg", AA_NON_TEXT],
  ];
  return pairs
    .filter(([foreground, background, threshold]) => contrast(colors[foreground], colors[background]) < threshold)
    .map(([foreground, background]) => `${foreground} on ${background}`);
}

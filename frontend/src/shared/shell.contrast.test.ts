import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

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

/** Color variables of the default theme, read from the first `:root` block of the style sheet. */
function defaultTheme(): Record<string, string> {
  const css = readFileSync(resolve(import.meta.dirname, "shell.css"), "utf8");
  const root = /:root\s*\{([^}]*)\}/.exec(css)?.[1] ?? "";
  return Object.fromEntries([...root.matchAll(/(--qd-[\w-]+):\s*(#[0-9a-f]{6})\s*;/gi)].map((m) => [m[1], m[2]]));
}

const AA_TEXT = 4.5;
const AA_NON_TEXT = 3;

describe("default theme contrast (WCAG AA)", () => {
  const theme = defaultTheme();

  // Every foreground/background pair the style sheet actually combines for text.
  it.each([
    ["--qd-text", "--qd-bg"],
    ["--qd-text", "--qd-surface"],
    ["--qd-muted", "--qd-bg"],
    ["--qd-muted", "--qd-surface"],
    ["--qd-link", "--qd-bg"],
    ["--qd-link", "--qd-surface"],
    ["--qd-on-accent", "--qd-accent"],
  ])("%s on %s is at least 4.5:1", (foreground, background) => {
    const fg = theme[foreground];
    const bg = theme[background];
    expect(fg, foreground).toBeDefined();
    expect(bg, background).toBeDefined();
    expect(contrast(fg ?? "", bg ?? "")).toBeGreaterThanOrEqual(AA_TEXT);
  });

  it.each([
    ["--qd-focus", "--qd-bg"],
    ["--qd-focus", "--qd-surface"],
    ["--qd-border", "--qd-bg"],
    ["--qd-border", "--qd-surface"],
  ])("%s against %s is at least 3:1 (focus ring, control borders)", (foreground, background) => {
    expect(contrast(theme[foreground] ?? "", theme[background] ?? "")).toBeGreaterThanOrEqual(AA_NON_TEXT);
  });

  it("measures contrast correctly, so a weak pair would be caught", () => {
    expect(contrast("#000000", "#ffffff")).toBeCloseTo(21, 5);
    expect(contrast("#777777", "#ffffff")).toBeCloseTo(4.48, 2);
    expect(contrast("#777777", "#ffffff")).toBeLessThan(AA_TEXT);
    expect(contrast("#999999", "#ffffff")).toBeLessThan(AA_NON_TEXT);
  });
});

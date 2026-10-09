import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const SOURCE = resolve(import.meta.dirname, "..");

export function withoutComments(css: string): string {
  return css.replace(/\/\*[\s\S]*?\*\//g, "");
}

/** A style sheet under src/, without its comments. */
export function readStyleSheet(...path: string[]): string {
  return withoutComments(readFileSync(resolve(SOURCE, ...path), "utf8"));
}

/** Every custom property tokens.css defines: the only names a style sheet may use. */
export function definedTokens(): Set<string> {
  const css = readStyleSheet("shared", "tokens.css");
  return new Set([...css.matchAll(/(--qd-[\w-]+)\s*:/g)].map((match) => match[1] ?? ""));
}

export function usedTokens(css: string): string[] {
  return [...new Set([...css.matchAll(/var\((--[\w-]+)/g)].map((match) => match[1] ?? ""))].sort();
}

export type StyleGuardOptions = {
  /** shell.css draws the focus ring itself; no other style sheet may touch it. */
  ownsFocusRing?: boolean;
};

/**
 * What a style sheet does that only tokens.css or shell.css may do. Empty when it is clean:
 *  - a color written out (hex, rgb(), hsl() and the like) instead of a token, which neither theme nor
 *    the Telegram theme would replace and no contrast check would see;
 *  - a variable tokens.css does not define;
 *  - an outline or a :focus rule, which could remove the ring every control gets from shell.css.
 */
export function styleProblems(css: string, tokens: ReadonlySet<string>, options: StyleGuardOptions = {}): string[] {
  const source = withoutComments(css);
  const problems: string[] = [];
  for (const [color] of source.matchAll(/#[0-9a-f]{3,8}\b/gi)) {
    problems.push("color written out: " + color);
  }
  for (const [call] of source.matchAll(/\b(?:rgb|hsl|hwb|lab|lch|oklab|oklch|color)a?\(/gi)) {
    problems.push("color written out: " + call);
  }
  for (const name of usedTokens(source)) {
    if (!tokens.has(name)) {
      problems.push("unknown variable: " + name);
    }
  }
  if (!options.ownsFocusRing) {
    if (/outline(?:-[a-z]+)?\s*:/.test(source)) {
      problems.push("outline changed");
    }
    if (/:focus/.test(source)) {
      problems.push(":focus rule");
    }
  }
  return problems;
}

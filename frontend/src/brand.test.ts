import { readdirSync, readFileSync, statSync } from "node:fs";
import { relative, resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { translate } from "./i18n/catalog";
import { LANGUAGES } from "./i18n/types";
import { uz } from "./i18n/uz";
import { toCyrillic } from "./i18n/uzCyrillic";
import { BRAND_MARK, BRAND_NAME, BRAND_PLACEHOLDER, BRAND_SHORT_NAME, BRAND_SOURCE, BRAND_TAGLINE } from "./shared/brand";

/**
 * The product's name is written in one place, `backend/src/qarz/domain/brand.json`, and the front end
 * reads that very file. Nothing under `frontend/` or `e2e/` writes the name or a former name out: a text
 * says `{brand}`. The server's twin of this file is `backend/tests/test_brand.py`, which searches the
 * whole repository; the last tests here plant a literal and show this search finds it.
 *
 * Technical identifiers are not the brand and are not looked for: `QD_*` variables, `X-Qarz-*` headers,
 * `qd.*` storage keys, `--qd-*` style variables, the package's name (docs/08-technical-spec, "Brand").
 */
const REPO = resolve(import.meta.dirname, "..", "..");

type Definition = {
  name: string;
  short_name: string;
  tagline: Record<string, string>;
  mark: unknown;
  former: { name: string; initials: string }[];
};
const definition = JSON.parse(readFileSync(resolve(REPO, BRAND_SOURCE), "utf8")) as Definition;

describe("the one definition", () => {
  it("is the server's file, read as it is", () => {
    expect(BRAND_SOURCE).toBe("backend/src/qarz/domain/brand.json");
    expect([BRAND_NAME, BRAND_SHORT_NAME, BRAND_TAGLINE]).toEqual([definition.name, definition.short_name, definition.tagline["uz"]]);
    expect(BRAND_MARK).toEqual(definition.mark);
    // No copy on this side: the only import of a definition is that path.
    const module = readFileSync(resolve(import.meta.dirname, "shared", "brand.ts"), "utf8");
    expect([...module.matchAll(/from "([^"]+)"/g)].map((found) => found[1])).toEqual([`../../../${BRAND_SOURCE}`]);
  });

  it("fills the name into a text in every language, and Uzbek Cyrillic leaves it in Latin letters", () => {
    expect(uz["app.name"]).toBe(BRAND_PLACEHOLDER);
    for (const language of LANGUAGES) {
      expect(translate(language, "app.name"), language).toBe(BRAND_NAME);
    }
    expect(toCyrillic(`${BRAND_PLACEHOLDER} xizmati`)).toBe(`${BRAND_PLACEHOLDER} хизмати`);
    expect(toCyrillic(`${BRAND_NAME} xizmati`)).toBe(`${BRAND_NAME} хизмати`);
    for (const old of definition.former) {
      expect(toCyrillic(old.name), "a former name is ordinary Uzbek now").not.toBe(old.name);
    }
  });
});

// --- nothing else writes it ---------------------------------------------------------------------------

const SEARCHED = ["frontend", "e2e"];
const NOT_ENTERED = new Set(["node_modules", "dist", "playwright-report", "test-results", ".vite"]);
/** Pictures, and the lock files, whose hashes hold every pair of letters. */
const NOT_SEARCHED = /\.png$|(^|\/)package-lock\.json$/;
/** Made from the definition and held to it by a check of its own: today's name may stand there, a former one may not. */
const MADE_FROM_IT = [/^frontend\/public\/panel\/manifest\.webmanifest$/, /^frontend\/src\/app\/__snapshots__\/[^/]+\.snap$/];
/** Single lines: [file, what the line holds, why]. */
const ALLOWED_LINES: [string, string, string][] = [["frontend/src/shared/qr.test.ts", 'readBotUsername(" @', "a bot's username is configuration"]];

const escaped = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
/**
 * A name in any capitals, with its spaces, without them, or with them as an address writes them (`%20`,
 * `+`); former initials as a word of their own, in capitals.
 */
function pattern(names: string[], initials: string[] = []): RegExp | null {
  const anyCase = (word: string) => [...word].map((char) => (/[a-z]/i.test(char) ? `[${char.toLowerCase()}${char.toUpperCase()}]` : escaped(char))).join("");
  const parts = [
    ...names.map((name) => name.split(/\s+/).map(anyCase).join("(?:\\s|%20|\\+)*")),
    ...initials.map((letters) => `(?:^|[^A-Za-z0-9_-])${escaped(letters)}(?![A-Za-z0-9_-])`),
  ];
  return parts.length === 0 ? null : new RegExp(parts.join("|"));
}
const CURRENT = pattern([definition.name, definition.short_name]);
const FORMER = pattern(
  definition.former.map((old) => old.name),
  definition.former.map((old) => old.initials),
);

function literals(files: Iterable<[string, string]>): string[] {
  const found: string[] = [];
  for (const [path, text] of files) {
    if (NOT_SEARCHED.test(path) || path === BRAND_SOURCE) {
      continue;
    }
    const made = MADE_FROM_IT.some((allowed) => allowed.test(path));
    const allowed = ALLOWED_LINES.filter(([file]) => file === path).map(([, needle]) => needle);
    text.split("\n").forEach((line, index) => {
      if (allowed.some((needle) => line.includes(needle))) {
        return;
      }
      const hit = (made ? null : CURRENT?.exec(line)) ?? FORMER?.exec(line);
      if (hit) {
        found.push(`${path}:${index + 1}: ${hit[0].trim()}`);
      }
    });
  }
  return found;
}

function* walk(directory: string): Generator<[string, string]> {
  for (const name of readdirSync(directory).sort()) {
    const path = resolve(directory, name);
    if (statSync(path).isDirectory()) {
      if (!NOT_ENTERED.has(name)) {
        yield* walk(path);
      }
    } else {
      const shown = relative(REPO, path).replaceAll("\\", "/");
      if (!NOT_SEARCHED.test(shown)) {
        yield [shown, readFileSync(path, "utf8")];
      }
    }
  }
}

describe("the name is written nowhere else", () => {
  it("not in a catalog, a page, a script, a test or a journey", () => {
    const files = SEARCHED.flatMap((directory) => [...walk(resolve(REPO, directory))]);
    expect(files.length).toBeGreaterThan(300);
    expect(literals(files)).toEqual([]);
  });

  it("every exception is still needed", () => {
    for (const [path, needle] of ALLOWED_LINES) {
      expect(readFileSync(resolve(REPO, path), "utf8").split("\n").filter((line) => line.includes(needle)), path).toHaveLength(1);
    }
  });

  it.each([
    `"app.name": "${definition.name}",`,
    `<title>${definition.name.toUpperCase()} — panel</title>`,
    `const BOT = "${definition.name.replace(/\s+/g, "").toLowerCase()}_bot";`,
  ])("a planted %s is found", (planted) => {
    const clean = 'const KEY = "qd.language";\nheaders["X-Qarz-Shop"] = id;\nconst name = t("app.name");\n';
    expect(literals([["frontend/src/i18n/uz.ts", clean]])).toEqual([]);
    const found = literals([["frontend/src/i18n/uz.ts", clean + planted]]);
    expect(found).toHaveLength(1);
    expect(found[0]?.startsWith("frontend/src/i18n/uz.ts:4: ")).toBe(true);
    expect(literals([["frontend/panel/index.html", planted]])).toHaveLength(1);
    expect(literals([["e2e/tests/01-first.spec.ts", planted]])).toHaveLength(1);
    // A file made from the definition may hold today's name.
    expect(literals([["frontend/public/panel/manifest.webmanifest", planted]])).toEqual([]);
  });

  it("a former name is found everywhere, a file made from the definition included", () => {
    const old = pattern(["Old Name"], ["ON"]);
    expect(old?.test('"name": "Old Name"')).toBe(true);
    expect(old?.test("@oldname_bot")).toBe(true);
    expect(old?.test("otpauth://totp/Old%20Name%3Aadmin-1")).toBe(true);
    expect(old?.test('initials: "ON"')).toBe(true);
    expect(old?.test("ON_SECRET --on-accent SEASON ON-1")).toBe(false);
    for (const former of definition.former) {
      expect(literals([["frontend/public/panel/manifest.webmanifest", `"name": "${former.name}"`]])).toHaveLength(1);
      expect(literals([["frontend/src/shared/network/testing.ts", `const CODE = "${former.initials}";`]])).toHaveLength(1);
    }
    for (const technical of ["qarz-daftari-frontend", "QD_TEST", "X-Qarz-Shop", "qd.theme", "--qd-accent"]) {
      expect(literals([["frontend/src/x.ts", technical]]), technical).toEqual([]);
    }
  });
});

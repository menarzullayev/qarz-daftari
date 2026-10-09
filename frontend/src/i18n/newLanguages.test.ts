import { describe, expect, it } from "vitest";

import { checkPartialCatalog, hasNoProblems, missingKeys } from "./check";
import type { Message } from "./types";

/**
 * Tajik, Karakalpak and English may trail behind Uzbek (see `check.ts`): while other work adds Uzbek and
 * Russian text, a key that one of them lacks reads Uzbek and is not a mistake. So this test checks what
 * they have, in every catalog there is, and does not ask that they have everything. `npm run
 * i18n:missing` lists what is absent; a final pass fills it and then makes completeness part of this
 * test (docs/10-operations/translation-review.md).
 *
 * The catalogs are found, not listed: a new catalog directory is checked the day it gets a file in one
 * of these languages, without a line being added here.
 */
type Catalog = Readonly<Record<string, Message>>;
type Module = Record<string, Catalog>;

const PLURAL_FORMS = { tg: ["other"], kaa: ["other"], en: ["one", "other"] } as const;
type Trailing = keyof typeof PLURAL_FORMS;
const TRAILING = Object.keys(PLURAL_FORMS) as Trailing[];

const modules = import.meta.glob<Module>(["./{uz,ru,tg,kaa,en}.ts", "./*/{uz,ru,tg,kaa,en}.ts"], { eager: true });

/** "./panel/tg.ts" → ["panel", "tg"]; the main catalog's directory is "". */
function place(path: string): [string, string] {
  const parts = path.replace(/^\.\//, "").replace(/\.ts$/, "").split("/");
  return parts.length === 1 ? ["", parts[0] ?? ""] : [parts[0] ?? "", parts[1] ?? ""];
}

function only(module: Module, path: string): Catalog {
  const exported = Object.values(module);
  if (exported.length !== 1 || exported[0] === undefined) {
    throw new Error(`${path} must export exactly one catalog`);
  }
  return exported[0];
}

const catalogs = new Map<string, Map<string, Catalog>>();
for (const [path, module] of Object.entries(modules)) {
  const [directory, language] = place(path);
  const ofDirectory = catalogs.get(directory) ?? new Map<string, Catalog>();
  ofDirectory.set(language, only(module, path));
  catalogs.set(directory, ofDirectory);
}

const cases = [...catalogs.entries()].flatMap(([directory, byLanguage]) =>
  TRAILING.filter((language) => byLanguage.has(language)).map((language) => ({
    name: `${directory || "main"} in ${language}`,
    source: byLanguage.get("uz"),
    partial: byLanguage.get(language) as Catalog,
    forms: PLURAL_FORMS[language],
  })),
);

describe("the catalogs that may trail behind Uzbek", () => {
  it("finds the catalogs", () => {
    expect([...catalogs.keys()].sort()).toEqual(
      expect.arrayContaining(["", "admin", "exports", "imports", "panel", "receipts", "reports", "share", "support"]),
    );
    // Today each of them exists in all three languages; that is 27 files to check.
    expect(cases.length).toBeGreaterThanOrEqual(27);
    for (const [directory, byLanguage] of catalogs) {
      expect(byLanguage.has("uz"), `${directory || "main"} has an Uzbek catalog`).toBe(true);
    }
  });

  it.each(cases)("$name has no unknown key, the right kind of entry, Uzbek's placeholders and no empty text", ({ source, partial, forms }) => {
    expect(checkPartialCatalog(source ?? {}, partial, forms)).toEqual({ unknown: [], kind: [], placeholders: [], empty: [] });
  });

  it.each(cases)("$name copies the names of the languages as they are", ({ source, partial }) => {
    for (const [key, name] of Object.entries(source ?? {})) {
      if (key.startsWith("lang.") && partial[key] !== undefined) {
        expect(partial[key], key).toBe(name);
      }
    }
  });

  it.each(cases)("$name leaves bot commands and the product's name as Uzbek has them", ({ source, partial }) => {
    const kept = (text: string) => [...text.matchAll(/(?:^|[\s(])(\/[a-z_]+)|Qarz Daftari/g)].map((found) => found[1] ?? found[0]).sort();
    for (const [key, entry] of Object.entries(partial)) {
      const original = (source ?? {})[key];
      if (typeof entry === "string" && typeof original === "string") {
        expect(kept(entry), key).toEqual(kept(original));
      }
    }
  });
});

describe("the check itself", () => {
  const uz: Catalog = {
    "a.plain": "Salom, {name}",
    "a.count": { other: "{count} ta mijoz" },
    "a.two": "{first} va {second}",
  };

  it("passes a catalog that has less than Uzbek", () => {
    const problems = checkPartialCatalog(uz, { "a.plain": "Салом, {name}" }, ["other"]);
    expect(hasNoProblems(problems)).toBe(true);
    expect(missingKeys(uz, { "a.plain": "Салом, {name}" })).toEqual(["a.count", "a.two"]);
    expect(missingKeys(uz, uz)).toEqual([]);
  });

  it("names a key that Uzbek does not have", () => {
    const problems = checkPartialCatalog(uz, { "a.plain": "Салом, {name}", "a.unknown": "Нест" }, ["other"]);
    expect(problems.unknown).toEqual(["a.unknown"]);
    expect(hasNoProblems(problems)).toBe(false);
  });

  it("names a placeholder that was renamed, dropped or added", () => {
    expect(checkPartialCatalog(uz, { "a.plain": "Салом, {ном}" }, ["other"]).placeholders).toEqual(["a.plain"]);
    expect(checkPartialCatalog(uz, { "a.plain": "Салом" }, ["other"]).placeholders).toEqual(["a.plain"]);
    expect(checkPartialCatalog(uz, { "a.two": "{first}, {second}, {third}" }, ["other"]).placeholders).toEqual(["a.two"]);
    expect(checkPartialCatalog(uz, { "a.two": "{second} ва {first}" }, ["other"]).placeholders).toEqual([]);
  });

  it("names a plural entry written as a plain string, and the reverse", () => {
    expect(checkPartialCatalog(uz, { "a.count": "{count} мизоҷ" }, ["other"]).kind).toEqual(["a.count"]);
    expect(checkPartialCatalog(uz, { "a.plain": { other: "Салом, {name}" } }, ["other"]).kind).toEqual(["a.plain"]);
  });

  it("names plural forms that are not the language's own", () => {
    const english = { "a.count": { one: "{count} customer", other: "{count} customers" } };
    expect(checkPartialCatalog(uz, english, ["one", "other"]).kind).toEqual([]);
    expect(checkPartialCatalog(uz, english, ["other"]).kind).toEqual(["a.count"]);
    expect(checkPartialCatalog(uz, { "a.count": { other: "{count} customers" } }, ["one", "other"]).kind).toEqual(["a.count"]);
    expect(checkPartialCatalog(uz, { "a.count": { one: "a customer", other: "{count} customers" } }, ["one", "other"]).placeholders).toEqual([
      "a.count",
    ]);
  });

  it("names empty text", () => {
    expect(checkPartialCatalog(uz, { "a.plain": "" }, ["other"]).empty).toEqual(["a.plain"]);
    expect(checkPartialCatalog(uz, { "a.plain": "   " }, ["other"]).empty).toEqual(["a.plain"]);
    expect(checkPartialCatalog(uz, { "a.count": { other: " " } }, ["other"]).empty).toEqual(["a.count"]);
  });
});

import { describe, expect, it } from "vitest";

import { checkPartialCatalog, hasNoProblems, missingKeys } from "./check";
import type { Message } from "./types";

/**
 * Tajik, Karakalpak and English against Uzbek (see `check.ts`): what each has is right (no unknown key,
 * the right kind of entry, Uzbek's placeholders, no empty text), and, since the last module of the
 * expansion was merged, each has everything: a key Uzbek has and one of them lacks fails this test and
 * CI's `npm run i18n:missing -- --strict` (docs/10-operations/translation-review.md). At run time such a
 * key still reads Uzbek, so a slip is the wrong language and never a blank.
 *
 * The catalogs are found, not listed: a new catalog directory is held to this the day it gets its
 * Uzbek file, without a line being added here.
 */
type Catalog = Readonly<Record<string, Message>>;
type Module = Record<string, Catalog>;

const PLURAL_FORMS = { tg: ["other"], kaa: ["other"], en: ["one", "other"] } as const;
type Trailing = keyof typeof PLURAL_FORMS;
const TRAILING = Object.keys(PLURAL_FORMS) as Trailing[];

const modules = import.meta.glob<Module>(["./{uz,ru,tg,kaa,en}.ts", "./*/{uz,ru,tg,kaa,en}.ts"], { eager: true });
/** The customer's page has a catalog of its own, with its own test of what it says; here it is only asked to be whole. */
const pageModules = import.meta.glob<Module>("../k/{uz,tg,kaa,en}.ts", { eager: true });

/** "./panel/tg.ts" → ["panel", "tg"]; the main catalog's directory is "". */
function place(path: string): [string, string] {
  const parts = path.replace(/^\.\.?\//, "").replace(/\.ts$/, "").split("/");
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

const whole = new Map(catalogs);
for (const [path, module] of Object.entries(pageModules)) {
  const [directory, language] = place(path);
  const ofDirectory = whole.get(directory) ?? new Map<string, Catalog>();
  ofDirectory.set(language, only(module, path));
  whole.set(directory, ofDirectory);
}

/** Every catalog there is, in each of the three languages: the file may be absent, which is "lacks everything". */
const complete = [...whole.entries()].flatMap(([directory, byLanguage]) =>
  TRAILING.map((language) => ({
    name: `${directory || "main"} in ${language}`,
    source: byLanguage.get("uz") ?? {},
    partial: byLanguage.get(language) ?? {},
  })),
);

describe("completeness: every text exists in Tajik, Karakalpak and English", () => {
  it("looks at every catalog, the network's and the customer's page among them", () => {
    expect([...whole.keys()]).toEqual(expect.arrayContaining(["", "network", "stock", "cash", "panel", "k"]));
    expect(complete.length).toBe(whole.size * TRAILING.length);
    for (const [directory, byLanguage] of whole) {
      expect(Object.keys(byLanguage.get("uz") ?? {}).length, `${directory || "main"} has Uzbek text`).toBeGreaterThan(0);
    }
  });

  it.each(complete)("$name lacks no key that Uzbek has", ({ source, partial }) => {
    expect(missingKeys(source, partial)).toEqual([]);
  });

  it("would fail for a key that only Uzbek and Russian were given, and for a catalog with no file", () => {
    const network = catalogs.get("network");
    const uz = network?.get("uz") ?? {};
    const tg = network?.get("tg") ?? {};
    expect(Object.keys(uz).length).toBeGreaterThan(200);
    expect(missingKeys({ ...uz, "net.added.later": "Yangi matn" }, tg)).toEqual(["net.added.later"]);
    expect(missingKeys(uz, {}).length).toBe(Object.keys(uz).length);
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

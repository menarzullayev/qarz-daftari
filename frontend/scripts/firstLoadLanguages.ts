/**
 * Only Uzbek is part of the first load (NFR-010): the text of every other language is a file of its
 * own, fetched when that language is the one in use.
 *
 * After a build this script reads what each page asks for on first load (the same list the bundle
 * budget measures, `size.ts`) and looks in it for sentences of each language's catalog. Russian, Tajik,
 * Karakalpak and English must not be there, and the rules that write Uzbek Cyrillic must not be either;
 * each must be somewhere else in the build, or the check would pass by not finding what it looks for.
 * Uzbek must be there: every language falls back to it. It prints the size of each language's file.
 *
 * The page behind a customer's read-only link (/k/) is not checked: it carries its few dozen messages
 * in every language on purpose, so that the reader can switch without the network, and has a budget of
 * its own.
 *
 * Usage: `npm run size` (builds first, then the budget, then this). `--pretend-first-load=ru` treats the
 * Russian file as part of the first load and shows the check failing; that is its negative check.
 */
import { readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { gzipSync } from "node:zlib";

import { en } from "../src/i18n/en.ts";
import { kaa } from "../src/i18n/kaa.ts";
import { ru } from "../src/i18n/ru.ts";
import { tg } from "../src/i18n/tg.ts";
import { uz } from "../src/i18n/uz.ts";
import { collectInitialAssets } from "./size.ts";

type Entry = string | Readonly<Record<string, string>>;
type Catalog = Readonly<Record<string, Entry | undefined>>;

const MARKERS = 40;
const MIN_LENGTH = 24;
/** What only the module that writes Uzbek Cyrillic contains: three of its exceptions, side by side. */
export const CYRILLIC_RULES = ["мўъжиза", "калькулятор", "квитанция"] as const;

function texts(catalog: Catalog): string[] {
  return Object.values(catalog).flatMap((entry) => (entry === undefined ? [] : typeof entry === "string" ? [entry] : Object.values(entry)));
}

/**
 * Sentences that tell a language's catalog from every other: long, without a placeholder, a quote or a
 * line break (so that a minifier writes them as they are), and not in any other catalog.
 */
export function markersOf(catalog: Catalog, others: readonly Catalog[], count = MARKERS): string[] {
  const elsewhere = new Set(others.flatMap(texts));
  return texts(catalog)
    .filter((text) => text.length >= MIN_LENGTH && !/[{}"'`\\\n]/.test(text) && !elsewhere.has(text))
    .slice(0, count);
}

function escaped(text: string): string {
  return [...text].map((char) => (char.charCodeAt(0) > 126 ? `\\u${char.charCodeAt(0).toString(16).padStart(4, "0")}` : char)).join("");
}

/** How many of the sentences a script contains, written as they are or with `\uXXXX` escapes. */
export function found(script: string, markers: readonly string[]): number {
  const lower = script.toLowerCase();
  return markers.filter((marker) => script.includes(marker) || lower.includes(escaped(marker).toLowerCase())).length;
}

export type Languages = Readonly<Record<string, readonly string[]>>;

export type Verdict = {
  /** Languages other than Uzbek with text in the first load. */
  inFirstLoad: string[];
  /** Languages whose text is nowhere in the build: the check could not have caught them. */
  nowhere: string[];
  /** Whether the first load has the Uzbek text every language falls back to. */
  uzbekThere: boolean;
};

/**
 * `first` are the scripts of the first load and `later` every other script of the build. A language is
 * "in" a set of scripts when a third of its sentences are found there: one shared word is not a catalog.
 */
export function judge(first: readonly string[], later: readonly string[], languages: Languages, uzbek: readonly string[]): Verdict {
  const isIn = (scripts: readonly string[], markers: readonly string[]) =>
    scripts.reduce((sum, script) => sum + found(script, markers), 0) * 3 >= markers.length && markers.length > 0;
  const names = Object.keys(languages);
  return {
    inFirstLoad: names.filter((name) => isIn(first, languages[name] ?? [])),
    nowhere: names.filter((name) => !isIn(later, languages[name] ?? []) && !isIn(first, languages[name] ?? [])),
    uzbekThere: isIn(first, uzbek),
  };
}

function main(): void {
  const pretend = process.argv.find((arg) => arg.startsWith("--pretend-first-load="))?.split("=")[1];
  const dist = resolve(import.meta.dirname, "..", "dist");
  const languages: Languages = {
    ru: markersOf(ru, [uz, tg, kaa, en]),
    tg: markersOf(tg, [uz, ru, kaa, en]),
    kaa: markersOf(kaa, [uz, ru, tg, en]),
    en: markersOf(en, [uz, ru, tg, kaa]),
    "uz-Cyrl": CYRILLIC_RULES,
  };
  const uzbek = markersOf(uz, [ru, tg, kaa, en]);
  const assets = readdirSync(resolve(dist, "assets")).filter((name) => name.endsWith(".js"));
  const script = (name: string) => readFileSync(resolve(dist, "assets", name), "utf8");

  const chunkOf: Record<string, string> = {};
  for (const [language, markers] of Object.entries(languages)) {
    // The main catalog's file: the one script that holds most of the language's sentences.
    const best = assets.map((name) => ({ name, hits: found(script(name), markers) })).sort((a, b) => b.hits - a.hits)[0];
    if (best && best.hits > 0) {
      chunkOf[language] = best.name;
      const size = gzipSync(readFileSync(resolve(dist, "assets", best.name)), { level: 9 }).length;
      console.log(`  ${language.padEnd(8)} ${(size / 1024).toFixed(2).padStart(7)} KB gzip  /assets/${best.name}`);
    }
  }

  let failed = false;
  for (const entry of ["app", "panel", "admin"]) {
    const html = readFileSync(resolve(dist, entry, "index.html"), "utf8");
    const firstNames = collectInitialAssets(html)
      .local.filter((url) => url.endsWith(".js"))
      .map((url) => url.replace(/^\/assets\//, ""));
    if (pretend !== undefined && chunkOf[pretend] !== undefined) {
      firstNames.push(chunkOf[pretend]);
    }
    const first = firstNames.map(script);
    const later = assets.filter((name) => !firstNames.includes(name)).map(script);
    const verdict = judge(first, later, languages, uzbek);
    if (verdict.inFirstLoad.length > 0) {
      console.error(`FAIL: ${entry}: the first load carries the text of ${verdict.inFirstLoad.join(", ")}; only Uzbek may be there`);
      failed = true;
    }
    if (verdict.nowhere.length > 0) {
      console.error(`FAIL: ${entry}: no file of the build holds the text of ${verdict.nowhere.join(", ")}: the check cannot see it`);
      failed = true;
    }
    if (!verdict.uzbekThere) {
      console.error(`FAIL: ${entry}: the first load does not hold the Uzbek text that every language falls back to`);
      failed = true;
    }
    if (verdict.inFirstLoad.length === 0 && verdict.nowhere.length === 0 && verdict.uzbekThere) {
      console.log(`OK: ${entry}: the first load holds Uzbek and no other language; each of the other five is a file of its own`);
    }
  }
  if (failed) {
    process.exit(1);
  }
}

if (import.meta.main) {
  main();
}

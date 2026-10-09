/**
 * Lists what each language still lacks of the front end's text. Not part of CI.
 *
 * Uzbek and Russian are complete by test. Tajik, Karakalpak and English may trail behind while other
 * work adds Uzbek and Russian text: a key they lack reads Uzbek at run time, and the tests check only
 * what they have (`src/i18n/check.ts`). This script is how the gap is seen and, in the end, closed: the
 * final pass translates what it lists and then makes completeness part of the tests
 * (docs/10-operations/translation-review.md).
 *
 * For Uzbek Cyrillic, which is made from the Uzbek text, it lists the Latin words left in what the
 * rules write: each is a brand or a code that is meant to stay, or a word for a reviewer to look at.
 *
 * Usage: `npm run i18n:missing` (exit 0 whatever it finds). `-- --strict` exits 1 when a key is missing.
 * `-- --keys` prints every missing key, not only the counts. The server's texts have a twin:
 * `python scripts/i18n_missing.py` in `backend/`.
 */
import { existsSync, readdirSync } from "node:fs";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

import { checkPartialCatalog, hasNoProblems, missingKeys } from "../src/i18n/check.ts";
import { toCyrillic } from "../src/i18n/uzCyrillic.ts";

type Entry = string | Readonly<Record<string, string>>;
type Catalog = Readonly<Record<string, Entry>>;

const TRAILING = { tg: ["other"], kaa: ["other"], en: ["one", "other"] } as const;
const SRC = resolve(import.meta.dirname, "..", "src");

async function catalogAt(path: string): Promise<Catalog | null> {
  if (!existsSync(path)) {
    return null;
  }
  const module = (await import(pathToFileURL(path).href)) as Record<string, Catalog>;
  const exported = Object.values(module);
  if (exported.length !== 1 || exported[0] === undefined) {
    throw new Error(`${path} must export exactly one catalog`);
  }
  return exported[0];
}

/** The catalog directories: the main one, each directory beside it that has an Uzbek file, and the customer's page. */
function directories(): { name: string; path: string }[] {
  const i18n = resolve(SRC, "i18n");
  const nested = readdirSync(i18n, { withFileTypes: true })
    .filter((entry) => entry.isDirectory() && existsSync(resolve(i18n, entry.name, "uz.ts")))
    .map((entry) => ({ name: entry.name, path: resolve(i18n, entry.name) }));
  return [{ name: "main", path: i18n }, ...nested, { name: "k (customer's page)", path: resolve(SRC, "k") }];
}

function texts(catalog: Catalog): string[] {
  return Object.values(catalog).flatMap((entry) => (typeof entry === "string" ? [entry] : Object.values(entry)));
}

async function main(): Promise<void> {
  const strict = process.argv.includes("--strict");
  const showKeys = process.argv.includes("--keys") || strict;
  let missingTotal = 0;
  let problemsTotal = 0;
  const latinLeft = new Map<string, string>();

  for (const language of Object.keys(TRAILING) as (keyof typeof TRAILING)[]) {
    console.log(`\n${language}`);
    for (const directory of directories()) {
      const source = await catalogAt(resolve(directory.path, "uz.ts"));
      if (source === null) {
        continue;
      }
      const partial = (await catalogAt(resolve(directory.path, `${language}.ts`))) ?? {};
      const missing = missingKeys(source, partial);
      const problems = checkPartialCatalog(source, partial, TRAILING[language]);
      missingTotal += missing.length;
      const total = Object.keys(source).length;
      console.log(`  ${directory.name.padEnd(20)} ${String(total - missing.length).padStart(4)} of ${String(total).padStart(4)}  missing ${missing.length}`);
      if (showKeys) {
        for (const key of missing) {
          console.log(`      ${key}`);
        }
      }
      if (!hasNoProblems(problems)) {
        problemsTotal += 1;
        console.log(`      PROBLEMS (the tests fail on these): ${JSON.stringify(problems)}`);
      }
    }
  }

  for (const directory of directories()) {
    const source = await catalogAt(resolve(directory.path, "uz.ts"));
    for (const [key, entry] of Object.entries(source ?? {})) {
      if (key.startsWith("lang.")) {
        continue;
      }
      for (const text of texts({ [key]: entry })) {
        const made = toCyrillic(text).replace(/\{[^{}]*\}/g, " ");
        for (const word of made.match(/[A-Za-z][A-Za-z0-9_'./@:?=-]*/g) ?? []) {
          if (!latinLeft.has(word)) {
            latinLeft.set(word, key);
          }
        }
      }
    }
  }
  console.log(`\nuz-Cyrl: made from the Uzbek text. Latin left in it (${latinLeft.size} different; first key each is in):`);
  for (const [word, key] of [...latinLeft.entries()].sort(([a], [b]) => a.localeCompare(b))) {
    console.log(`  ${word.padEnd(28)} ${key}`);
  }

  console.log(`\nmissing keys in all: ${missingTotal}; catalogs with problems: ${problemsTotal}`);
  if (strict && (missingTotal > 0 || problemsTotal > 0)) {
    process.exit(1);
  }
}

if (import.meta.main) {
  await main();
}

/**
 * Writes the Uzbek Cyrillic text of the page behind a customer's read-only link: `src/k/uzCyrl.ts`.
 *
 * Everywhere else Uzbek Cyrillic is made when it is needed, from the Uzbek text and the rules of
 * `src/i18n/uzCyrillic.ts`. This page is the exception: it must stay a few kilobytes and share no code
 * with the staff application, so it carries its few dozen messages already written. They are still not
 * typed: this script writes them with the same rules, and `src/k/page.test.ts` fails when a message in
 * the file is not what the rules give for today's Uzbek text.
 *
 * Usage: `npm run i18n:k` after changing an Uzbek message of the page (`src/k/uz.ts`) or the rules.
 * `node scripts/kCyrillic.ts --check` changes nothing and exits 1 when the file is out of date.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

import { toCyrillic } from "../src/i18n/uzCyrillic.ts";
import { uz } from "../src/k/uz.ts";

/** A language is named in its own script whatever the reader's, so the names are copied, not rewritten. */
export function isLanguageName(key: string): boolean {
  return key.startsWith("lang.") && key !== "lang.choose";
}

export function cyrillicOf(source: Readonly<Record<string, string>>): Record<string, string> {
  return Object.fromEntries(Object.entries(source).map(([key, text]) => [key, isLanguageName(key) ? text : toCyrillic(text)]));
}

export function fileText(source: Readonly<Record<string, string>>): string {
  const lines = Object.entries(cyrillicOf(source)).map(([key, text]) => `  ${JSON.stringify(key)}: ${JSON.stringify(text)},`);
  return [
    'import type { MessageKey } from "./messages";',
    "",
    "/**",
    " * Uzbek Cyrillic text of the page behind a customer's read-only link.",
    " *",
    " * Written by `npm run i18n:k` from `uz.ts` and the rules of `../i18n/uzCyrillic.ts`: do not edit it by",
    " * hand. A word that comes out wrong is corrected in the rules' tables and this file written again. A key",
    " * that is absent here reads Uzbek in Latin script.",
    " */",
    "export const uzCyrl: Partial<Record<MessageKey, string>> = {",
    ...lines,
    "};",
    "",
  ].join("\n");
}

function main(): void {
  const path = resolve(import.meta.dirname, "..", "src", "k", "uzCyrl.ts");
  const wanted = fileText(uz);
  if (process.argv.includes("--check")) {
    let current = "";
    try {
      current = readFileSync(path, "utf8");
    } catch {
      // No file yet: out of date.
    }
    if (current !== wanted) {
      console.error("src/k/uzCyrl.ts is out of date: run `npm run i18n:k`");
      process.exit(1);
    }
    console.log("src/k/uzCyrl.ts is up to date");
    return;
  }
  writeFileSync(path, wanted);
  console.log(`wrote ${path}`);
}

if (import.meta.main) {
  main();
}

/**
 * What a catalog that may trail behind Uzbek is held to, and what it still lacks.
 *
 * Tajik, Karakalpak and English are checked for what they have, not for what they lack: while other
 * work keeps adding Uzbek and Russian text, a key that one of them does not have yet reads Uzbek and is
 * not a mistake. What is a mistake: a key Uzbek does not have, a plain string where Uzbek has plural
 * forms (or the reverse), the wrong set of plural forms, placeholders that differ from Uzbek's, and
 * empty text. `npm run i18n:missing` lists the keys that are absent.
 *
 * This file imports nothing, so that the script can run it without a bundler.
 */

type Plural = Readonly<Record<string, string>>;
type Entry = string | Plural;
type Entries = Readonly<Record<string, Entry | undefined>>;

const PLACEHOLDER = /\{(\w+)\}/g;

function placeholders(template: string): string {
  return [...new Set([...template.matchAll(PLACEHOLDER)].map((match) => match[1] ?? ""))].sort().join(",");
}

function forms(entry: Entry): string[] {
  return typeof entry === "string" ? [entry] : Object.values(entry);
}

export type PartialProblems = {
  /** Keys the Uzbek catalog does not have. */
  unknown: string[];
  /** A plain string where Uzbek has plural forms, the reverse, or plural forms that are not the language's. */
  kind: string[];
  /** Keys whose `{param}` placeholders differ from Uzbek's, in any form. */
  placeholders: string[];
  /** Keys with text that is empty or only spaces. */
  empty: string[];
};

/**
 * Checks what a partial catalog has against the Uzbek source. `pluralForms` are the names of the forms
 * a plural entry of this language has: `["other"]` for Tajik and Karakalpak, `["one", "other"]` for
 * English.
 */
export function checkPartialCatalog(source: Entries, partial: Entries, pluralForms: readonly string[]): PartialProblems {
  const problems: PartialProblems = { unknown: [], kind: [], placeholders: [], empty: [] };
  const wanted = [...pluralForms].sort().join(",");
  for (const [key, entry] of Object.entries(partial)) {
    if (entry === undefined) {
      continue;
    }
    const original = source[key];
    if (original === undefined) {
      problems.unknown.push(key);
      continue;
    }
    if (forms(entry).some((text) => typeof text !== "string" || text.trim() === "")) {
      problems.empty.push(key);
      continue;
    }
    const sameKind = (typeof entry === "string") === (typeof original === "string");
    if (!sameKind || (typeof entry !== "string" && Object.keys(entry).sort().join(",") !== wanted)) {
      problems.kind.push(key);
      continue;
    }
    const expected = placeholders(forms(original)[0] ?? "");
    if (forms(entry).some((text) => placeholders(text) !== expected)) {
      problems.placeholders.push(key);
    }
  }
  return problems;
}

export function hasNoProblems(problems: PartialProblems): boolean {
  return Object.values(problems).every((keys) => keys.length === 0);
}

/** Keys of the Uzbek source that the partial catalog does not have, in the source's order. */
export function missingKeys(source: Entries, partial: Entries): string[] {
  return Object.keys(source).filter((key) => partial[key] === undefined);
}

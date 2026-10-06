import { ru } from "./ru";
import type { Language, Message, MessageKey, MessageParams, RuPlural } from "./types";
import { uz } from "./uz";

export const catalogs: Readonly<Record<Language, Readonly<Record<MessageKey, Message>>>> = { uz, ru };

export type RuPluralCategory = keyof RuPlural;

/**
 * Russian plural category for a count. Whole numbers follow the usual rule (1, 21, 101 → one; 2-4, 22-24
 * → few; everything else, including 11-14, → many). A fraction takes the "few" form, which is the
 * genitive singular Russian uses there ("1,5 дня").
 */
export function ruPluralCategory(count: number): RuPluralCategory {
  if (!Number.isFinite(count)) {
    throw new RangeError("count must be a finite number");
  }
  const n = Math.abs(count);
  if (!Number.isInteger(n)) {
    return "few";
  }
  const lastTwo = n % 100;
  const last = n % 10;
  if (last === 1 && lastTwo !== 11) {
    return "one";
  }
  if (last >= 2 && last <= 4 && (lastTwo < 12 || lastTwo > 14)) {
    return "few";
  }
  return "many";
}

const PLACEHOLDER = /\{(\w+)\}/g;

/** Names of the `{param}` placeholders in a template, without duplicates, sorted. */
export function placeholdersOf(template: string): string[] {
  return [...new Set([...template.matchAll(PLACEHOLDER)].map((match) => match[1] ?? ""))].sort();
}

function interpolate(key: string, template: string, params: MessageParams): string {
  return template.replace(PLACEHOLDER, (_whole, name: string) => {
    const value = params[name];
    if (value === undefined) {
      throw new Error(`message "${key}" needs the parameter "${name}"`);
    }
    return String(value);
  });
}

function templateFor(lang: Language, key: MessageKey, message: Message, params: MessageParams): string {
  if (typeof message === "string") {
    return message;
  }
  const count = params["count"];
  if (typeof count !== "number") {
    throw new Error(`plural message "${key}" needs a numeric "count" parameter`);
  }
  if ("other" in message) {
    return message.other;
  }
  if (lang !== "ru") {
    throw new Error(`message "${key}" has Russian plural forms in the "${lang}" catalog`);
  }
  return message[ruPluralCategory(count)];
}

/**
 * Resolves a message in the given language. A missing parameter throws instead of printing a broken
 * sentence: a seller must never see "{count} kun kechikkan", and the mistake surfaces in the first test.
 */
export function translate(lang: Language, key: MessageKey, params: MessageParams = {}): string {
  const message = catalogs[lang][key];
  return interpolate(key, templateFor(lang, key, message, params), params);
}

export type CatalogDifference = {
  /** Keys present in the first catalog only. */
  missingInSecond: string[];
  /** Keys present in the second catalog only. */
  missingInFirst: string[];
  /** Keys that are a plain string in one catalog and a plural entry in the other. */
  kindMismatch: string[];
  /** Keys whose `{param}` placeholders differ between the two catalogs or between plural forms. */
  placeholderMismatch: string[];
};

function templatesOf(message: Message): string[] {
  return typeof message === "string" ? [message] : Object.values(message);
}

/** Runtime twin of the compile-time check, so a unit test fails too when the catalogs drift apart. */
export function compareCatalogs(
  first: Readonly<Record<string, Message>>,
  second: Readonly<Record<string, Message>>,
): CatalogDifference {
  const difference: CatalogDifference = {
    missingInSecond: [],
    missingInFirst: Object.keys(second).filter((key) => !(key in first)),
    kindMismatch: [],
    placeholderMismatch: [],
  };
  for (const [key, a] of Object.entries(first)) {
    const b = second[key];
    if (b === undefined) {
      difference.missingInSecond.push(key);
      continue;
    }
    if ((typeof a === "string") !== (typeof b === "string")) {
      difference.kindMismatch.push(key);
      continue;
    }
    const expected = placeholdersOf(templatesOf(a)[0] ?? "").join(",");
    const all = [...templatesOf(a), ...templatesOf(b)];
    if (all.some((template) => placeholdersOf(template).join(",") !== expected)) {
      difference.placeholderMismatch.push(key);
    }
  }
  return difference;
}

export function isSameCatalogShape(difference: CatalogDifference): boolean {
  return Object.values(difference).every((keys) => keys.length === 0);
}

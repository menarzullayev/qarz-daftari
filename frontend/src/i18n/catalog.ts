import { BRAND_NAME } from "../shared/brand";
import type { EnPlural, Language, Message, MessageKey, MessageParams, RuPlural } from "./types";
import { LANGUAGES } from "./types";
import { uz } from "./uz";

type Messages = Record<string, Message>;

/** The languages whose text is fetched when it is first needed; Uzbek alone is part of the first load. */
export type FetchedLanguage = Exclude<Language, "uz" | "uz-Cyrl">;

/** A catalog's text in one language: there already, or a function that fetches it. */
export type Part = Readonly<Messages> | (() => Promise<Readonly<Messages>>);

/**
 * One catalog in its languages. Uzbek is the source and is always there. Every other language is
 * optional: a part that is absent, or a key that a part does not have, reads Uzbek. Uzbek Cyrillic has
 * no part: it is made from the Uzbek text.
 */
export type Section = { readonly uz: Readonly<Messages> } & { readonly [L in FetchedLanguage]?: Part };

/** What turns Uzbek text into Uzbek Cyrillic, and the few messages written by hand; fetched on demand. */
type Cyrillic = { toCyrillic: (text: string) => string; overrides: Readonly<Messages> };

const core: Section = {
  uz,
  ru: () => import("./ru").then((module) => module.ru),
  tg: () => import("./tg").then((module) => module.tg),
  kaa: () => import("./kaa").then((module) => module.kaa),
  en: () => import("./en").then((module) => module.en),
};

const loaded: Record<Language, Messages> = { uz: { ...uz }, "uz-Cyrl": {}, ru: {}, tg: {}, kaa: {}, en: {} };
const sections: Section[] = [core];
/** The sections whose part for a language is in `loaded`. */
const arrived: Record<Language, Set<Section>> = {
  uz: new Set(),
  "uz-Cyrl": new Set(),
  ru: new Set(),
  tg: new Set(),
  kaa: new Set(),
  en: new Set(),
};
let cyrillic: Cyrillic | null = null;
let active: Language = "uz";
let version = 0;
const listeners = new Set<() => void>();

function changed(): void {
  version += 1;
  for (const listener of listeners) {
    listener();
  }
}

/** Tells the caller when text has arrived that was not there before; returns how to stop being told. */
export function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** A number that changes whenever `subscribe` would have told. */
export function catalogVersion(): number {
  return version;
}

/**
 * The messages this entry point has loaded: the main catalog, and for the web panel its own as well.
 * The type names every key. Uzbek has each key of a catalog that was added; another language has the
 * keys that have arrived for it and may lack some, which is why text is read through `translate`.
 */
export const catalogs: Readonly<Record<Language, Readonly<Record<MessageKey, Message>>>> = loaded as Record<
  Language,
  Record<MessageKey, Message>
>;

/** Parts that are there before they are asked for, by the Uzbek catalog they belong to (see `preloadPart`). */
const preloaded = new Map<Readonly<Messages>, Partial<Record<FetchedLanguage, Readonly<Messages>>>>();

function install(language: FetchedLanguage, section: Section, messages: Readonly<Messages>): void {
  Object.assign(loaded[language], messages);
  arrived[language].add(section);
}

/**
 * Says that a catalog's text in a language is already at hand, so that it is there the moment the
 * catalog is added and nothing has to be fetched. `source` is the catalog's Uzbek object. The tests use
 * it to draw a screen in Russian without waiting (`scripts/testLanguages.ts`); the application itself
 * fetches (`loadLanguage`).
 */
export function preloadPart(source: Readonly<Messages>, language: FetchedLanguage, messages: Readonly<Messages>): void {
  preloaded.set(source, { ...preloaded.get(source), [language]: messages });
  for (const section of sections) {
    if (section.uz === source) {
      install(language, section, messages);
    }
  }
}

async function fetchCyrillic(): Promise<void> {
  if (cyrillic === null) {
    const [rules, own] = await Promise.all([import("./uzCyrillic"), import("./uzCyrlOverrides")]);
    cyrillic = { toCyrillic: rules.toCyrillic, overrides: own.uzCyrlOverrides };
  }
}

async function fetchPart(language: FetchedLanguage, section: Section): Promise<void> {
  const part = section[language];
  install(language, section, typeof part === "function" ? await part() : (part ?? {}));
}

/** Whether every catalog added so far has its text for the language. */
export function hasLanguage(language: Language): boolean {
  if (language === "uz") {
    return true;
  }
  if (language === "uz-Cyrl") {
    return cyrillic !== null;
  }
  return sections.every((section) => arrived[language].has(section));
}

/**
 * Fetches what the language still lacks of the catalogs added so far. Rejects when any of it could not
 * be fetched; what did arrive is kept, and a later call asks only for the rest. Text that has not
 * arrived is read in Uzbek, so nothing waits on this that does not choose to.
 */
export async function loadLanguage(language: Language): Promise<void> {
  if (hasLanguage(language)) {
    return;
  }
  const waiting =
    language === "uz" || language === "uz-Cyrl"
      ? [fetchCyrillic()]
      : sections.filter((section) => !arrived[language].has(section)).map((section) => fetchPart(language, section));
  const results = await Promise.allSettled(waiting);
  changed();
  const failed = results.find((result) => result.status === "rejected");
  if (failed) {
    throw new Error(`the text of "${language}" could not be fetched`, { cause: failed.reason });
  }
}

/** The language the interface is in now: a catalog added later fetches its text for it at once. */
export function setActiveLanguage(language: Language): void {
  active = language;
}

/** The language the interface is in now, for the one reader that has no language context above it. */
export function activeLanguage(): Language {
  return active;
}

/**
 * Resolves when the catalogs added so far have their text for the language in use, or when that text
 * could not be fetched: never rejects. A screen that is fetched on demand waits for this before it is
 * drawn, so that it is not drawn in Uzbek first and in its reader's language a moment later.
 */
export function languageSettled(): Promise<void> {
  return loadLanguage(active).catch(() => undefined);
}

/** A module that brings a catalog of its own, once that catalog's text for the language in use is there. */
export async function withMessages<T>(module: Promise<T>): Promise<T> {
  const fetched = await module;
  await languageSettled();
  return fetched;
}

/**
 * Adds an entry point's or a screen's own catalog to the loaded messages. The web panel calls it once,
 * before it renders; the Mini App never does, so the panel's text is not part of its download (NFR-010).
 * The Uzbek text is added at once. A language given as a function is fetched when it is the one in use.
 */
export function addMessages(section: Section): void {
  Object.assign(loaded.uz, section.uz);
  sections.push(section);
  for (const language of LANGUAGES) {
    if (language === "uz" || language === "uz-Cyrl") {
      continue;
    }
    const part = preloaded.get(section.uz)?.[language] ?? section[language];
    if (typeof part !== "function") {
      // Text that is at hand, or a language this catalog does not have at all: nothing to fetch.
      install(language, section, part ?? {});
    }
  }
  if (!hasLanguage(active)) {
    void languageSettled();
  }
}

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

/** English plural category for a count: "one" for exactly 1 (and -1), "other" for everything else. */
export function enPluralCategory(count: number): keyof EnPlural {
  return Math.abs(count) === 1 ? "one" : "other";
}

const PLACEHOLDER = /\{(\w+)\}/g;

/**
 * What a text may name without being given it. No catalog writes the product's name: a text says
 * `{brand}`, in every language, and it is filled in here from the one place the name is written. Uzbek
 * Cyrillic is made from the Uzbek text before this, and its rules never touch a placeholder, so the
 * name stays in Latin letters without the rules being told.
 */
const BUILT_IN: Readonly<Record<string, string>> = { brand: BRAND_NAME };

/** Names of the `{param}` placeholders in a template, without duplicates, sorted. */
export function placeholdersOf(template: string): string[] {
  return [...new Set([...template.matchAll(PLACEHOLDER)].map((match) => match[1] ?? ""))].sort();
}

function interpolate(key: string, template: string, params: MessageParams): string {
  return template.replace(PLACEHOLDER, (_whole, name: string) => {
    const value = params[name] ?? BUILT_IN[name];
    if (value === undefined) {
      throw new Error(`message "${key}" needs the parameter "${name}"`);
    }
    return String(value);
  });
}

/** The forms a plural entry has decide its rule: three are Russian, two are English, one is everyone else's. */
function templateFor(key: MessageKey, message: Message, params: MessageParams): string {
  if (typeof message === "string") {
    return message;
  }
  const count = params["count"];
  if (typeof count !== "number") {
    throw new Error(`plural message "${key}" needs a numeric "count" parameter`);
  }
  if ("few" in message) {
    return message[ruPluralCategory(count)];
  }
  if ("one" in message) {
    return message[enPluralCategory(count)];
  }
  return message.other;
}

function inCyrillic(message: Message, toCyrillic: (text: string) => string): Message {
  if (typeof message === "string") {
    return toCyrillic(message);
  }
  return Object.fromEntries(Object.entries(message).map(([form, text]) => [form, toCyrillic(text)])) as Message;
}

/**
 * The message a language reads for a key, given the Uzbek one. Its own when it has one of the same kind
 * (a plain string where Uzbek has a plain string, plural forms where Uzbek has them); otherwise Uzbek,
 * transliterated for Uzbek Cyrillic. Never nothing and never the key.
 */
function messageIn(language: Language, key: MessageKey, source: Message): Message {
  if (language === "uz") {
    return source;
  }
  const own = loaded[language][key];
  if (own !== undefined && (typeof own === "string") === (typeof source === "string")) {
    return own;
  }
  if (language !== "uz-Cyrl" || cyrillic === null) {
    return source;
  }
  const made = cyrillic.overrides[key] ?? inCyrillic(source, cyrillic.toCyrillic);
  loaded[language][key] = made;
  return made;
}

/**
 * Resolves a message in the given language. A key that the language does not have is read in Uzbek. A
 * missing parameter throws instead of printing a broken sentence: a seller must never see "{count} kun
 * kechikkan", and the mistake surfaces in the first test. So does a key that no loaded catalog has.
 */
export function translate(lang: Language, key: MessageKey, params: MessageParams = {}): string {
  const source: Message | undefined = loaded.uz[key];
  if (source === undefined) {
    throw new Error(`message "${key}" is not loaded: it belongs to a catalog this entry point did not add`);
  }
  return interpolate(key, templateFor(key, messageIn(lang, key, source), params), params);
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

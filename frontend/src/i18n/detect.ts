import { LANGUAGES, type Language } from "./types";

export const DEFAULT_LANGUAGE: Language = "uz";
export const LANGUAGE_STORAGE_KEY = "qd.language";

/** Maps "ru", "RU", "ru-RU", "uz-Latn" to a supported language; anything else is not supported. */
export function normalizeLanguage(code: string | null | undefined): Language | null {
  if (typeof code !== "string") {
    return null;
  }
  const primary = code.trim().toLowerCase().split(/[-_]/)[0];
  return LANGUAGES.find((language) => language === primary) ?? null;
}

export type LanguageSources = {
  /** The user's own earlier choice, as persisted by `writeStoredLanguage`. */
  stored?: string | null | undefined;
  /** `initDataUnsafe.user.language_code` from the Telegram Mini App, when there is one. */
  telegramLanguageCode?: string | null | undefined;
};

/** Order: explicit choice, then the Telegram interface language, then Uzbek. */
export function detectLanguage(sources: LanguageSources): Language {
  return (
    normalizeLanguage(sources.stored) ?? normalizeLanguage(sources.telegramLanguageCode) ?? DEFAULT_LANGUAGE
  );
}

type ReadableStorage = Pick<Storage, "getItem">;
type WritableStorage = Pick<Storage, "setItem">;

function browserStorage(): Storage | null {
  try {
    return globalThis.localStorage ?? null;
  } catch {
    // Access itself can throw when storage is blocked (private mode, some in-app browsers).
    return null;
  }
}

export function readStoredLanguage(storage: ReadableStorage | null = browserStorage()): Language | null {
  try {
    return normalizeLanguage(storage?.getItem(LANGUAGE_STORAGE_KEY));
  } catch {
    return null;
  }
}

/** Returns false when the choice could not be persisted; the choice still applies to this session. */
export function writeStoredLanguage(
  language: Language,
  storage: WritableStorage | null = browserStorage(),
): boolean {
  if (!storage) {
    return false;
  }
  try {
    storage.setItem(LANGUAGE_STORAGE_KEY, language);
    return true;
  } catch {
    return false;
  }
}

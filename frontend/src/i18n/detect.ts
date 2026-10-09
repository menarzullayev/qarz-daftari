import { LANGUAGES, type Language } from "./types";

export const DEFAULT_LANGUAGE: Language = "uz";
export const LANGUAGE_STORAGE_KEY = "qd.language";

/**
 * Maps a language tag to a supported language: "ru", "RU", "ru-RU" and "uz-Latn" by their first part,
 * "uz-Cyrl", "uz_Cyrl" and "uz-Cyrl-UZ" to Uzbek in Cyrillic script. Anything else is not supported.
 */
export function normalizeLanguage(code: string | null | undefined): Language | null {
  if (typeof code !== "string") {
    return null;
  }
  const [primary, ...rest] = code.trim().toLowerCase().split(/[-_]/);
  if (primary === "uz" && rest.includes("cyrl")) {
    return "uz-Cyrl";
  }
  return LANGUAGES.find((language) => language === primary) ?? null;
}

/**
 * The language a person starts in, from the language of their Telegram interface. Telegram reports
 * Kazakh ("kk") but has no Karakalpak: a Kazakh interface starts in Karakalpak, its closest relative
 * here. The same table as the server's (`qarz.domain.languages.from_telegram`). A language with no
 * counterpart is not guessed: the caller falls back to Uzbek.
 */
export function languageFromTelegram(code: string | null | undefined): Language | null {
  const known = normalizeLanguage(code);
  if (known !== null) {
    return known;
  }
  return typeof code === "string" && code.trim().toLowerCase().split(/[-_]/)[0] === "kk" ? "kaa" : null;
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
    normalizeLanguage(sources.stored) ?? languageFromTelegram(sources.telegramLanguageCode) ?? DEFAULT_LANGUAGE
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

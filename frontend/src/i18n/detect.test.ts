import { describe, expect, it } from "vitest";

import { LANGUAGES } from "./types";
import {
  LANGUAGE_STORAGE_KEY,
  detectLanguage,
  languageFromTelegram,
  normalizeLanguage,
  readStoredLanguage,
  writeStoredLanguage,
} from "./detect";

describe("detectLanguage", () => {
  it("prefers the stored choice over the Telegram language", () => {
    expect(detectLanguage({ stored: "uz", telegramLanguageCode: "ru" })).toBe("uz");
    expect(detectLanguage({ stored: "ru", telegramLanguageCode: "uz" })).toBe("ru");
  });

  it("uses the Telegram language when nothing is stored", () => {
    expect(detectLanguage({ stored: null, telegramLanguageCode: "ru" })).toBe("ru");
    expect(detectLanguage({ telegramLanguageCode: "ru-RU" })).toBe("ru");
  });

  it("falls back to Uzbek", () => {
    expect(detectLanguage({})).toBe("uz");
    expect(detectLanguage({ stored: null, telegramLanguageCode: null })).toBe("uz");
  });

  it("ignores an unsupported stored value and moves to the next source", () => {
    expect(detectLanguage({ stored: "de", telegramLanguageCode: "ru" })).toBe("ru");
    expect(detectLanguage({ stored: "", telegramLanguageCode: "ru" })).toBe("ru");
    // A stored value is the person's own choice among the six; "kk" is only ever Telegram's hint.
    expect(detectLanguage({ stored: "kk", telegramLanguageCode: "ru" })).toBe("ru");
  });

  it("does not let the Telegram language override an explicit choice", () => {
    expect(detectLanguage({ stored: "uz", telegramLanguageCode: "ru" })).not.toBe("ru");
  });

  it("does not guess Russian for other languages", () => {
    expect(detectLanguage({ telegramLanguageCode: "de" })).toBe("uz");
    expect(detectLanguage({ telegramLanguageCode: "tr" })).toBe("uz");
    expect(detectLanguage({ telegramLanguageCode: "uk" })).toBe("uz");
  });

  it.each([
    ["uz", "uz"],
    ["uz-Cyrl", "uz-Cyrl"],
    ["uz_Cyrl", "uz-Cyrl"],
    ["ru", "ru"],
    ["tg", "tg"],
    ["kaa", "kaa"],
    ["kk", "kaa"],
    ["kk-KZ", "kaa"],
    ["en", "en"],
    ["en-US", "en"],
    ["EN_gb", "en"],
  ])("starts a person whose Telegram speaks %s in %s", (telegramLanguageCode, expected) => {
    expect(detectLanguage({ telegramLanguageCode })).toBe(expected);
    expect(languageFromTelegram(telegramLanguageCode)).toBe(expected);
  });

  it.each(["de", "fa", "ky", "kkx", "", null, undefined])("has no language for Telegram's %s", (code) => {
    expect(languageFromTelegram(code)).toBeNull();
    expect(detectLanguage({ telegramLanguageCode: code })).toBe("uz");
  });

  it.each(LANGUAGES)("keeps the choice of %s whatever Telegram says", (stored) => {
    for (const telegramLanguageCode of ["uz", "ru", "en", "kk", "tg", "de", null]) {
      expect(detectLanguage({ stored, telegramLanguageCode })).toBe(stored);
    }
  });
});

describe("normalizeLanguage", () => {
  it.each([
    ["ru", "ru"],
    ["RU", "ru"],
    ["ru-RU", "ru"],
    ["uz", "uz"],
    ["uz-Latn", "uz"],
    ["uz_UZ", "uz"],
    ["uz-Cyrl", "uz-Cyrl"],
    ["uz-cyrl", "uz-Cyrl"],
    ["uz_Cyrl", "uz-Cyrl"],
    ["uz-Cyrl-UZ", "uz-Cyrl"],
    ["tg", "tg"],
    ["tg-TJ", "tg"],
    ["kaa", "kaa"],
    ["kaa-Latn-UZ", "kaa"],
    ["en", "en"],
    ["en-GB", "en"],
  ])("maps %s to %s", (code, expected) => {
    expect(normalizeLanguage(code)).toBe(expected);
  });

  it.each(["de", "kk", "cyrl", "uzb", "", "rus", "russian", null, undefined])("rejects %s", (code) => {
    expect(normalizeLanguage(code)).toBeNull();
  });
});

describe("stored language", () => {
  it.each(LANGUAGES)("round-trips %s through storage under its own tag", (language) => {
    const data = new Map<string, string>();
    const storage = {
      getItem: (key: string) => data.get(key) ?? null,
      setItem: (key: string, value: string) => void data.set(key, value),
    };
    expect(writeStoredLanguage(language, storage)).toBe(true);
    expect(data.get(LANGUAGE_STORAGE_KEY)).toBe(language);
    expect(readStoredLanguage(storage)).toBe(language);
  });

  it("round-trips through storage", () => {
    const data = new Map<string, string>();
    const storage = {
      getItem: (key: string) => data.get(key) ?? null,
      setItem: (key: string, value: string) => void data.set(key, value),
    };
    expect(readStoredLanguage(storage)).toBeNull();
    expect(writeStoredLanguage("ru", storage)).toBe(true);
    expect(data.get(LANGUAGE_STORAGE_KEY)).toBe("ru");
    expect(readStoredLanguage(storage)).toBe("ru");
  });

  it("survives storage that is missing or throws", () => {
    const broken = {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
    };
    expect(readStoredLanguage(broken)).toBeNull();
    expect(writeStoredLanguage("ru", broken)).toBe(false);
    expect(readStoredLanguage(null)).toBeNull();
    expect(writeStoredLanguage("ru", null)).toBe(false);
  });
});

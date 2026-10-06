import { describe, expect, it } from "vitest";

import {
  LANGUAGE_STORAGE_KEY,
  detectLanguage,
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
    expect(detectLanguage({ stored: "en", telegramLanguageCode: "ru" })).toBe("ru");
    expect(detectLanguage({ stored: "", telegramLanguageCode: "ru" })).toBe("ru");
  });

  it("does not let the Telegram language override an explicit choice", () => {
    expect(detectLanguage({ stored: "uz", telegramLanguageCode: "ru" })).not.toBe("ru");
  });

  it("does not guess Russian for other languages", () => {
    expect(detectLanguage({ telegramLanguageCode: "en" })).toBe("uz");
    expect(detectLanguage({ telegramLanguageCode: "kk" })).toBe("uz");
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
  ])("maps %s to %s", (code, expected) => {
    expect(normalizeLanguage(code)).toBe(expected);
  });

  it.each(["en", "", "rus", "russian", null, undefined])("rejects %s", (code) => {
    expect(normalizeLanguage(code)).toBeNull();
  });
});

describe("stored language", () => {
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

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { translate } from "./catalog";
import { writeStoredLanguage } from "./detect";
import type { Language, MessageKey, MessageParams } from "./types";

export type Translate = (key: MessageKey, params?: MessageParams) => string;

type I18nValue = {
  language: Language;
  setLanguage: (language: Language) => void;
  t: Translate;
};

const I18nContext = createContext<I18nValue | null>(null);

type I18nProviderProps = {
  initialLanguage: Language;
  children: ReactNode;
};

/** Holds the chosen language, persists an explicit choice, and keeps `<html lang>` in step with it. */
export function I18nProvider({ initialLanguage, children }: I18nProviderProps) {
  const [language, setLanguageState] = useState<Language>(initialLanguage);

  const setLanguage = useCallback((next: Language) => {
    writeStoredLanguage(next);
    setLanguageState(next);
  }, []);

  useEffect(() => {
    document.documentElement.lang = language;
  }, [language]);

  const value = useMemo<I18nValue>(
    () => ({ language, setLanguage, t: (key, params) => translate(language, key, params) }),
    [language, setLanguage],
  );

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nValue {
  const value = useContext(I18nContext);
  if (!value) {
    throw new Error("useI18n must be used inside I18nProvider");
  }
  return value;
}

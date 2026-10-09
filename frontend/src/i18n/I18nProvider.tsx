import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";

import { catalogVersion, hasLanguage, loadLanguage, setActiveLanguage, subscribe, translate } from "./catalog";
import { writeStoredLanguage } from "./detect";
import { DIRECTIONS, type Language, type MessageKey, type MessageParams } from "./types";

export type Translate = (key: MessageKey, params?: MessageParams) => string;

type I18nValue = {
  language: Language;
  /**
   * Changes the language. Its text is fetched first: the interface stays in the language it is in until
   * the new one has arrived, and stays there if it cannot be fetched.
   */
  setLanguage: (language: Language) => void;
  t: Translate;
};

const I18nContext = createContext<I18nValue | null>(null);

type I18nProviderProps = {
  initialLanguage: Language;
  children: ReactNode;
};

/**
 * Holds the chosen language, persists an explicit choice, and keeps `<html lang>` and `<html dir>` in
 * step with it. The entry point fetches the first language's text before it renders (`startLanguage`);
 * a language chosen later is fetched here.
 */
export function I18nProvider({ initialLanguage, children }: I18nProviderProps) {
  const [language, setLanguageState] = useState<Language>(initialLanguage);
  // Text that arrives after a screen was drawn (a catalog added late, a language fetched late) draws it again.
  const version = useSyncExternalStore(subscribe, catalogVersion);
  const wanted = useRef<Language>(initialLanguage);
  setActiveLanguage(language);

  const setLanguage = useCallback((next: Language) => {
    const apply = () => {
      // A later choice made while this one was on its way wins.
      if (wanted.current === next) {
        writeStoredLanguage(next);
        setActiveLanguage(next);
        setLanguageState(next);
      }
    };
    wanted.current = next;
    if (hasLanguage(next)) {
      apply();
    } else {
      loadLanguage(next).then(apply, () => undefined);
    }
  }, []);

  useEffect(() => {
    document.documentElement.lang = language;
    document.documentElement.dir = DIRECTIONS[language];
  }, [language]);

  const value = useMemo<I18nValue>(
    () => ({ language, setLanguage, t: (key, params) => translate(language, key, params) }),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `version` makes `t` a new function, so every reader of it draws again, when text arrives
    [language, setLanguage, version],
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

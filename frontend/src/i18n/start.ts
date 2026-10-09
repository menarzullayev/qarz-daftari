import { loadLanguage, setActiveLanguage } from "./catalog";
import type { Language } from "./types";

/**
 * What an entry point does before it renders: fetches the text of the language the person starts in.
 * Only Uzbek is part of the first load; any other language is one more file, fetched here. Resolves in
 * every case: if the text cannot be fetched the interface is drawn in Uzbek, and is drawn again in the
 * person's language when a later attempt brings it.
 */
export function startLanguage(language: Language): Promise<void> {
  setActiveLanguage(language);
  return loadLanguage(language).catch(() => undefined);
}

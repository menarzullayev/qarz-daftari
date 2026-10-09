import type { Message, MessageKey } from "./types";

/**
 * Uzbek Cyrillic messages that are written by hand, because the transliteration of the Uzbek message
 * would be wrong. Everything else in Uzbek Cyrillic is the Uzbek text put through `toCyrillic`.
 *
 * The names of the languages are here: each language is named in its own script, whatever the reader's.
 * A single word that the rules get wrong belongs in the transliterator's own tables (`uzCyrillic.ts`),
 * where it is corrected everywhere at once; this table is for whole messages.
 */
export const uzCyrlOverrides: Readonly<Partial<Record<MessageKey, Message>>> = {
  "lang.uz": "O'zbekcha",
  "lang.kaa": "Qaraqalpaqsha",
  "lang.en": "English",
};

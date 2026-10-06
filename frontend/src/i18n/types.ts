import type { uz } from "./uz";

/** Uzbek nouns do not change after a number, so one form is enough. */
export type UzPlural = { other: string };

/** Russian needs three forms: 1 клиент, 2 клиента, 5 клиентов. */
export type RuPlural = { one: string; few: string; many: string };

export type Message = string | UzPlural | RuPlural;

/** The Uzbek catalog defines the key set; every other catalog must match it exactly (ADR-021, NFR-007). */
export type MessageKey = keyof typeof uz;

/** Shape the Russian catalog must have: the same keys, and a plural entry wherever Uzbek has one. */
export type RuCatalog = {
  [K in MessageKey]: (typeof uz)[K] extends string ? string : RuPlural;
};

export const LANGUAGES = ["uz", "ru"] as const;
export type Language = (typeof LANGUAGES)[number];

export type MessageParams = Readonly<Record<string, string | number>>;

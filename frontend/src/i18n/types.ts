import type { uzPanel } from "./panel/uz";
import type { uzReports } from "./reports/uz";
import type { uz } from "./uz";

/** Uzbek nouns do not change after a number, so one form is enough. */
export type UzPlural = { other: string };

/** Russian needs three forms: 1 клиент, 2 клиента, 5 клиентов. */
export type RuPlural = { one: string; few: string; many: string };

export type Message = string | UzPlural | RuPlural;

/** Keys of the main catalog, which every entry point loads. */
export type CoreMessageKey = keyof typeof uz;

/** Keys of the web panel's own catalog, loaded by the panel entry only (see `addMessages`). */
export type PanelMessageKey = keyof typeof uzPanel;

/** Keys of the reports' own catalog, added when the reports screen is first opened (see `addMessages`). */
export type ReportsMessageKey = keyof typeof uzReports;

/** The Uzbek catalogs define the key set; every other catalog must match it exactly (ADR-021, NFR-007). */
export type MessageKey = CoreMessageKey | PanelMessageKey | ReportsMessageKey;

/** Shape the Russian catalog must have: the same keys, and a plural entry wherever Uzbek has one. */
export type RuCatalog = {
  [K in CoreMessageKey]: (typeof uz)[K] extends string ? string : RuPlural;
};

/** The same rule for the web panel's catalog. */
export type RuPanelCatalog = {
  [K in PanelMessageKey]: (typeof uzPanel)[K] extends string ? string : RuPlural;
};

/** And for the reports' catalog. */
export type RuReportsCatalog = {
  [K in ReportsMessageKey]: (typeof uzReports)[K] extends string ? string : RuPlural;
};

export const LANGUAGES = ["uz", "ru"] as const;
export type Language = (typeof LANGUAGES)[number];

export type MessageParams = Readonly<Record<string, string | number>>;

import type { uzAdmin } from "./admin/uz";
import type { uzCash } from "./cash/uz";
import type { uzExports } from "./exports/uz";
import type { uzImports } from "./imports/uz";
import type { uzPanel } from "./panel/uz";
import type { uzReceipts } from "./receipts/uz";
import type { uzReports } from "./reports/uz";
import type { uzShare } from "./share/uz";
import type { uzSupport } from "./support/uz";
import type { uz } from "./uz";

/** Uzbek nouns do not change after a number, so one form is enough. */
export type UzPlural = { other: string };

/** Russian needs three forms: 1 клиент, 2 клиента, 5 клиентов. */
export type RuPlural = { one: string; few: string; many: string };

export type Message = string | UzPlural | RuPlural | EnPlural;

/** Keys of the main catalog, which every entry point loads. */
export type CoreMessageKey = keyof typeof uz;

/** Keys of the web panel's own catalog, loaded by the panel entry only (see `addMessages`). */
export type PanelMessageKey = keyof typeof uzPanel;

/** Keys of the reports' own catalog, added when the reports screen is first opened (see `addMessages`). */
export type ReportsMessageKey = keyof typeof uzReports;

/** Keys of the export screen's own catalog, added when that screen is first opened (see `addMessages`). */
export type ExportsMessageKey = keyof typeof uzExports;

/** Keys of the import screen's own catalog, added when that screen is first opened (see `addMessages`). */
export type ImportsMessageKey = keyof typeof uzImports;

/** Keys of the owner's subscription receipts, added when the subscription screen asks for them. */
export type ReceiptsMessageKey = keyof typeof uzReceipts;

/** Keys of the owner's view of support access: added by the web panel, and by the Mini App on demand. */
export type SupportMessageKey = keyof typeof uzSupport;

/** Keys of a customer's read-only link as staff see it: added when one of its sections is first shown. */
export type ShareMessageKey = keyof typeof uzShare;

/** Keys of the cash book, added when the cash screen is first opened (see `addMessages`). */
export type CashMessageKey = keyof typeof uzCash;

/** Keys of the administrator's panel, added by the admin entry only (see `addMessages`). */
export type AdminMessageKey = keyof typeof uzAdmin;

/** The Uzbek catalogs define the key set; every other catalog must match it exactly (ADR-021, NFR-007). */
export type MessageKey =
  | CoreMessageKey
  | PanelMessageKey
  | ReportsMessageKey
  | ExportsMessageKey
  | ImportsMessageKey
  | ReceiptsMessageKey
  | SupportMessageKey
  | ShareMessageKey
  | CashMessageKey
  | AdminMessageKey;

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

/** And for the export screen's catalog. */
export type RuExportsCatalog = {
  [K in ExportsMessageKey]: (typeof uzExports)[K] extends string ? string : RuPlural;
};

/** And for the import screen's catalog. */
export type RuImportsCatalog = {
  [K in ImportsMessageKey]: (typeof uzImports)[K] extends string ? string : RuPlural;
};

/** And for the owner's subscription receipts. */
export type RuReceiptsCatalog = {
  [K in ReceiptsMessageKey]: (typeof uzReceipts)[K] extends string ? string : RuPlural;
};

/** And for the owner's view of support access. */
export type RuSupportCatalog = {
  [K in SupportMessageKey]: (typeof uzSupport)[K] extends string ? string : RuPlural;
};

/** And for a customer's read-only link. */
export type RuShareCatalog = {
  [K in ShareMessageKey]: (typeof uzShare)[K] extends string ? string : RuPlural;
};

/** And for the cash book. */
export type RuCashCatalog = {
  [K in CashMessageKey]: (typeof uzCash)[K] extends string ? string : RuPlural;
};

/** And for the administrator's catalog. */
export type RuAdminCatalog = {
  [K in AdminMessageKey]: (typeof uzAdmin)[K] extends string ? string : RuPlural;
};

/**
 * The languages of the interface, as BCP 47 tags, in the order the picker shows them. `uz` (Uzbek in
 * Latin script) is the source: it defines the keys, and every other language falls back to it. `uz-Cyrl`
 * has no catalog of its own: it is `uz` transliterated (see `uzCyrillic.ts`).
 */
export const LANGUAGES = ["uz", "uz-Cyrl", "ru", "tg", "kaa", "en"] as const;
export type Language = (typeof LANGUAGES)[number];

/** The direction each language is written in, for `<html dir>`. All six are written left to right. */
export const DIRECTIONS: Readonly<Record<Language, "ltr" | "rtl">> = {
  uz: "ltr",
  "uz-Cyrl": "ltr",
  ru: "ltr",
  tg: "ltr",
  kaa: "ltr",
  en: "ltr",
};

/** English has two forms: 1 customer, 5 customers. */
export type EnPlural = { one: string; other: string };

/**
 * Shape of a catalog that may trail behind Uzbek: Tajik, Karakalpak and English. No key that Uzbek does
 * not have, and a plural entry wherever Uzbek has one; a key that is absent reads Uzbek at run time
 * (`translate`). `Plural` is the language's own set of forms: one form for Tajik and Karakalpak, whose
 * nouns do not change after a number, and two for English.
 */
export type PartialCatalog<Source, Plural = UzPlural> = {
  readonly [K in keyof Source]?: Source[K] extends string ? string : Plural;
};

export type MessageParams = Readonly<Record<string, string | number>>;

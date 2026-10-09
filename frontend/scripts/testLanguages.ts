import { ruAdmin } from "../src/i18n/admin/ru";
import { uzAdmin } from "../src/i18n/admin/uz";
import { preloadPart } from "../src/i18n/catalog";
import { ruExports } from "../src/i18n/exports/ru";
import { uzExports } from "../src/i18n/exports/uz";
import { ruImports } from "../src/i18n/imports/ru";
import { uzImports } from "../src/i18n/imports/uz";
import { ruPanel } from "../src/i18n/panel/ru";
import { uzPanel } from "../src/i18n/panel/uz";
import { ruReceipts } from "../src/i18n/receipts/ru";
import { uzReceipts } from "../src/i18n/receipts/uz";
import { ruReports } from "../src/i18n/reports/ru";
import { uzReports } from "../src/i18n/reports/uz";
import { ru } from "../src/i18n/ru";
import { ruShare } from "../src/i18n/share/ru";
import { uzShare } from "../src/i18n/share/uz";
import { ruSupport } from "../src/i18n/support/ru";
import { uzSupport } from "../src/i18n/support/uz";
import { uz } from "../src/i18n/uz";

/**
 * Run before every test file (vite.config.ts, `setupFiles`): Russian is at hand for every catalog.
 *
 * In the application only Uzbek is part of the first load and every other language is fetched before
 * the first render (`startLanguage`) or when it is chosen. A test that draws a screen in Russian would
 * otherwise have to wait for a file before each render; this makes Russian text arrive with the catalog
 * it belongs to, as it did when both languages were in one bundle. A catalog that a test file does not
 * add still has no text in either language (`addMessages.test.ts`).
 *
 * Only Russian: the other languages are reached the way the application reaches them, by fetching, and
 * `languages.test.tsx` tests exactly that.
 */
const RUSSIAN = [
  [uz, ru],
  [uzAdmin, ruAdmin],
  [uzExports, ruExports],
  [uzImports, ruImports],
  [uzPanel, ruPanel],
  [uzReceipts, ruReceipts],
  [uzReports, ruReports],
  [uzShare, ruShare],
  [uzSupport, ruSupport],
] as const;

for (const [source, russian] of RUSSIAN) {
  preloadPart(source, "ru", russian);
}

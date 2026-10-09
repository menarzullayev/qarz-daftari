import { addMessages } from "../../i18n/catalog";
import { uzReceipts } from "../../i18n/receipts/uz";

// The text of the owner's subscription receipts, added when this module is loaded: with the section, on demand.
// Uzbek is part of this module; each other language is fetched when it is the one in use.
addMessages({
  uz: uzReceipts,
  ru: () => import("../../i18n/receipts/ru").then((module) => module.ruReceipts),
  tg: () => import("../../i18n/receipts/tg").then((module) => module.tgReceipts),
  kaa: () => import("../../i18n/receipts/kaa").then((module) => module.kaaReceipts),
  en: () => import("../../i18n/receipts/en").then((module) => module.enReceipts),
});

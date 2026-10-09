import { addMessages } from "../../i18n/catalog";
import { ruStock } from "../../i18n/stock/ru";
import { uzStock } from "../../i18n/stock/uz";

// The text of the stock, its documents and the suppliers, added when one of their screens is loaded:
// on demand, and never for a shop whose platform has the stock switched off. Tajik, Karakalpak and
// English are fetched when one of them is the language in use.
addMessages({
  uz: uzStock,
  ru: ruStock,
  tg: () => import("../../i18n/stock/tg").then((module) => module.tgStock),
  kaa: () => import("../../i18n/stock/kaa").then((module) => module.kaaStock),
  en: () => import("../../i18n/stock/en").then((module) => module.enStock),
});

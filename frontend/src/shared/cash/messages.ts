import { addMessages } from "../../i18n/catalog";
import { ruCash } from "../../i18n/cash/ru";
import { uzCash } from "../../i18n/cash/uz";

// The cash book's own text, added when this module is loaded: that is, with the cash screen, on demand,
// and only while the platform switch is on. Tajik, Karakalpak and English are fetched when one of them
// is the language in use.
addMessages({
  uz: uzCash,
  ru: ruCash,
  tg: () => import("../../i18n/cash/tg").then((module) => module.tgCash),
  kaa: () => import("../../i18n/cash/kaa").then((module) => module.kaaCash),
  en: () => import("../../i18n/cash/en").then((module) => module.enCash),
});

import { addMessages } from "../../i18n/catalog";
import { uzExports } from "../../i18n/exports/uz";

// The export screen's own text, added when this module is loaded: that is, with the screen, on demand.
// Uzbek is part of this module; each other language is fetched when it is the one in use.
addMessages({
  uz: uzExports,
  ru: () => import("../../i18n/exports/ru").then((module) => module.ruExports),
  tg: () => import("../../i18n/exports/tg").then((module) => module.tgExports),
  kaa: () => import("../../i18n/exports/kaa").then((module) => module.kaaExports),
  en: () => import("../../i18n/exports/en").then((module) => module.enExports),
});

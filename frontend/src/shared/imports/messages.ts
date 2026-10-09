import { addMessages } from "../../i18n/catalog";
import { uzImports } from "../../i18n/imports/uz";

// The import screen's own text, added when this module is loaded: that is, with the screen, on demand.
// Uzbek is part of this module; each other language is fetched when it is the one in use.
addMessages({
  uz: uzImports,
  ru: () => import("../../i18n/imports/ru").then((module) => module.ruImports),
  tg: () => import("../../i18n/imports/tg").then((module) => module.tgImports),
  kaa: () => import("../../i18n/imports/kaa").then((module) => module.kaaImports),
  en: () => import("../../i18n/imports/en").then((module) => module.enImports),
});

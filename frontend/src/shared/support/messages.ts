import { addMessages } from "../../i18n/catalog";
import { uzSupport } from "../../i18n/support/uz";

// What the owner reads about support access. The web panel loads it when it starts (its notice is on
// every screen); the Mini App loads it with the section under the settings, on demand.
// Uzbek is part of this module; each other language is fetched when it is the one in use.
addMessages({
  uz: uzSupport,
  ru: () => import("../../i18n/support/ru").then((module) => module.ruSupport),
  tg: () => import("../../i18n/support/tg").then((module) => module.tgSupport),
  kaa: () => import("../../i18n/support/kaa").then((module) => module.kaaSupport),
  en: () => import("../../i18n/support/en").then((module) => module.enSupport),
});

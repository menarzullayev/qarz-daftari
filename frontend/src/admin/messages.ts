import { addMessages } from "../i18n/catalog";
import { uzAdmin } from "../i18n/admin/uz";

// The administrator's own text. Every module of the admin entry that shows text imports this file for
// its effect, so the messages are loaded before the first render whichever module is imported first.
// Uzbek is part of this module; each other language is fetched when it is the one in use.
addMessages({
  uz: uzAdmin,
  ru: () => import("../i18n/admin/ru").then((module) => module.ruAdmin),
  tg: () => import("../i18n/admin/tg").then((module) => module.tgAdmin),
  kaa: () => import("../i18n/admin/kaa").then((module) => module.kaaAdmin),
  en: () => import("../i18n/admin/en").then((module) => module.enAdmin),
});

import { addMessages } from "../i18n/catalog";
import { uzPanel } from "../i18n/panel/uz";

// The web panel's own text. Every module of the panel that shows text imports this file for its effect,
// so the messages are loaded before the first render whichever module is imported first.
// Uzbek is part of this module; each other language is fetched when it is the one in use.
addMessages({
  uz: uzPanel,
  ru: () => import("../i18n/panel/ru").then((module) => module.ruPanel),
  tg: () => import("../i18n/panel/tg").then((module) => module.tgPanel),
  kaa: () => import("../i18n/panel/kaa").then((module) => module.kaaPanel),
  en: () => import("../i18n/panel/en").then((module) => module.enPanel),
});

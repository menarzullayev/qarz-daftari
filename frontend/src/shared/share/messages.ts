import { addMessages } from "../../i18n/catalog";
import { uzShare } from "../../i18n/share/uz";

// The text of a customer's read-only link, added when one of its sections is loaded: on demand, and
// only for a manager or an owner.
// Uzbek is part of this module; each other language is fetched when it is the one in use.
addMessages({
  uz: uzShare,
  ru: () => import("../../i18n/share/ru").then((module) => module.ruShare),
  tg: () => import("../../i18n/share/tg").then((module) => module.tgShare),
  kaa: () => import("../../i18n/share/kaa").then((module) => module.kaaShare),
  en: () => import("../../i18n/share/en").then((module) => module.enShare),
});

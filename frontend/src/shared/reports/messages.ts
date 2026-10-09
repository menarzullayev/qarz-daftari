import { addMessages } from "../../i18n/catalog";
import { uzReports } from "../../i18n/reports/uz";

// The reports' own text, added when this module is loaded: that is, with the reports screen, on demand.
// Uzbek is part of this module; each other language is fetched when it is the one in use.
addMessages({
  uz: uzReports,
  ru: () => import("../../i18n/reports/ru").then((module) => module.ruReports),
  tg: () => import("../../i18n/reports/tg").then((module) => module.tgReports),
  kaa: () => import("../../i18n/reports/kaa").then((module) => module.kaaReports),
  en: () => import("../../i18n/reports/en").then((module) => module.enReports),
});

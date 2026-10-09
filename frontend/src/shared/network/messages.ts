import { addMessages } from "../../i18n/catalog";
import { ruNetwork } from "../../i18n/network/ru";
import { uzNetwork } from "../../i18n/network/uz";

// The text of the network between shops, added when one of its screens is loaded: on demand, and never
// for a shop whose platform has the network switched off. Tajik, Karakalpak and English are fetched
// when one of them is the language in use; Uzbek Cyrillic is made from the Uzbek text.
addMessages({
  uz: uzNetwork,
  ru: ruNetwork,
  tg: () => import("../../i18n/network/tg").then((module) => module.tgNetwork),
  kaa: () => import("../../i18n/network/kaa").then((module) => module.kaaNetwork),
  en: () => import("../../i18n/network/en").then((module) => module.enNetwork),
});

import { addMessages } from "../../i18n/catalog";
import { ruNetwork } from "../../i18n/network/ru";
import { uzNetwork } from "../../i18n/network/uz";

// The text of the network between shops, added when one of its screens is loaded: on demand, and never
// for a shop whose platform has the network switched off. Uzbek and Russian only; a reader of another
// language is shown the Uzbek text (Uzbek Cyrillic is made from it), until theirs is written.
addMessages({ uz: uzNetwork, ru: ruNetwork });

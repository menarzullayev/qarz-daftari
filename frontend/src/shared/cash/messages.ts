import { addMessages } from "../../i18n/catalog";
import { ruCash } from "../../i18n/cash/ru";
import { uzCash } from "../../i18n/cash/uz";

// The cash book's own text, added when this module is loaded: that is, with the cash screen, on demand,
// and only while the platform switch is on.
addMessages({ uz: uzCash, ru: ruCash });

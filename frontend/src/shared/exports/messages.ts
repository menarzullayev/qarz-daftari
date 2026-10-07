import { addMessages } from "../../i18n/catalog";
import { ruExports } from "../../i18n/exports/ru";
import { uzExports } from "../../i18n/exports/uz";

// The export screen's own text, added when this module is loaded: that is, with the screen, on demand.
addMessages({ uz: uzExports, ru: ruExports });

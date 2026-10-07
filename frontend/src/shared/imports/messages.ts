import { addMessages } from "../../i18n/catalog";
import { ruImports } from "../../i18n/imports/ru";
import { uzImports } from "../../i18n/imports/uz";

// The import screen's own text, added when this module is loaded: that is, with the screen, on demand.
addMessages({ uz: uzImports, ru: ruImports });

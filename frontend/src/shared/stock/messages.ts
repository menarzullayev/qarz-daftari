import { addMessages } from "../../i18n/catalog";
import { ruStock } from "../../i18n/stock/ru";
import { uzStock } from "../../i18n/stock/uz";

// The text of the stock, its documents and the suppliers, added when one of their screens is loaded:
// on demand, and never for a shop whose platform has the stock switched off.
addMessages({ uz: uzStock, ru: ruStock });

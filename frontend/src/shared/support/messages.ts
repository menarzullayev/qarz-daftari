import { addMessages } from "../../i18n/catalog";
import { ruSupport } from "../../i18n/support/ru";
import { uzSupport } from "../../i18n/support/uz";

// What the owner reads about support access. The web panel loads it when it starts (its notice is on
// every screen); the Mini App loads it with the section under the settings, on demand.
addMessages({ uz: uzSupport, ru: ruSupport });

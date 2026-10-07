import { addMessages } from "../i18n/catalog";
import { ruAdmin } from "../i18n/admin/ru";
import { uzAdmin } from "../i18n/admin/uz";

// The administrator's own text. Every module of the admin entry that shows text imports this file for
// its effect, so the messages are loaded before the first render whichever module is imported first.
addMessages({ uz: uzAdmin, ru: ruAdmin });

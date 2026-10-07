import { addMessages } from "../i18n/catalog";
import { ruPanel } from "../i18n/panel/ru";
import { uzPanel } from "../i18n/panel/uz";

// The web panel's own text. Every module of the panel that shows text imports this file for its effect,
// so the messages are loaded before the first render whichever module is imported first.
addMessages({ uz: uzPanel, ru: ruPanel });

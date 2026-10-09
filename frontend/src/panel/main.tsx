import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { detectLanguage, readStoredLanguage } from "../i18n/detect";
import { previewStaffSession } from "../shared/session";
import { StaffApp } from "../shared/StaffApp";
import { initTheme } from "../shared/theme";
import { takeLoginReturn } from "./loginReturn";
import { PanelRoot } from "./PanelRoot";
import { registerPanelWorker } from "./pwa/register";

const root = document.getElementById("root");
if (!root) {
  throw new Error("root element is missing");
}

// Web panel: the same staff screens on a desktop layout, with the owner's back office. It runs outside
// Telegram, so there is no Telegram language to read; Telegram's sign-in script is added by the sign-in
// screen alone, not by the page.
// First of all, before anything is drawn or requested: if Telegram has just sent the browser back here,
// the signed fields are taken out of the address.
const loginReturn = takeLoginReturn();
initTheme();

const initialLanguage = detectLanguage({ stored: readStoredLanguage() });
const preview = import.meta.env.DEV ? previewStaffSession(window.location.search) : null;

// The panel can be installed, and an installed panel must open without a connection: its service
// worker keeps the page and its scripts, and nothing else (pwa/worker.ts). Registered once the page has
// loaded, so it never competes with the first load.
window.addEventListener("load", () => {
  void registerPanelWorker({ container: navigator.serviceWorker, production: import.meta.env.PROD });
});

createRoot(root).render(
  <StrictMode>
    {preview ? (
      <StaffApp entryKey="entry.panel" session={preview} initialLanguage={initialLanguage} />
    ) : (
      <PanelRoot initialLanguage={initialLanguage} loginReturn={loginReturn} />
    )}
  </StrictMode>,
);

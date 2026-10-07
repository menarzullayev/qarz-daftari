import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { detectLanguage, readStoredLanguage } from "../i18n/detect";
import { previewStaffSession } from "../shared/session";
import { StaffApp } from "../shared/StaffApp";
import { PanelRoot } from "./PanelRoot";

const root = document.getElementById("root");
if (!root) {
  throw new Error("root element is missing");
}

// Web panel: the same staff screens on a desktop layout, with the owner's back office. It runs outside
// Telegram, so there is no Telegram language to read; Telegram's sign-in script is added by the sign-in
// screen alone, not by the page.
const initialLanguage = detectLanguage({ stored: readStoredLanguage() });
const preview = import.meta.env.DEV ? previewStaffSession(window.location.search) : null;

createRoot(root).render(
  <StrictMode>
    {preview ? (
      <StaffApp entryKey="entry.panel" session={preview} initialLanguage={initialLanguage} />
    ) : (
      <PanelRoot initialLanguage={initialLanguage} />
    )}
  </StrictMode>,
);

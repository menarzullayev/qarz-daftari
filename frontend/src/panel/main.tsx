import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { detectLanguage, readStoredLanguage } from "../i18n/detect";
import { previewStaffSession } from "../shared/session";
import { StaffApp } from "../shared/StaffApp";

const root = document.getElementById("root");
if (!root) {
  throw new Error("root element is missing");
}

// Web panel: the same staff screens on a desktop layout. It runs outside Telegram, so there is no
// Telegram language to read and no Telegram script on the page.
const initialLanguage = detectLanguage({ stored: readStoredLanguage() });
const session = import.meta.env.DEV ? previewStaffSession(window.location.search) : null;

createRoot(root).render(
  <StrictMode>
    <StaffApp entryKey="entry.panel" session={session} initialLanguage={initialLanguage} />
  </StrictMode>,
);

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { detectLanguage, readStoredLanguage } from "../i18n/detect";
import { previewStaffSession } from "../shared/session";
import { StaffApp } from "../shared/StaffApp";
import { initTelegram } from "../shared/telegram";

const root = document.getElementById("root");
if (!root) {
  throw new Error("root element is missing");
}

// Staff workspace inside the Telegram Mini App. Sign-in with the launch data arrives with the API client.
const launch = initTelegram();
const initialLanguage = detectLanguage({
  stored: readStoredLanguage(),
  telegramLanguageCode: launch.languageCode,
});
const session = import.meta.env.DEV ? previewStaffSession(window.location.search) : null;

createRoot(root).render(
  <StrictMode>
    <StaffApp entryKey="entry.app" session={session} initialLanguage={initialLanguage} />
  </StrictMode>,
);

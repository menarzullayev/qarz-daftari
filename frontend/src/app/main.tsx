import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { detectLanguage, readStoredLanguage } from "../i18n/detect";
import { startLanguage } from "../i18n/start";
import { ErrorBoundary } from "../shared/ErrorBoundary";
import { previewStaffSession } from "../shared/session";
import { StaffApp } from "../shared/StaffApp";
import { StaffRoot } from "../shared/StaffRoot";
import { initTelegram } from "../shared/telegram";
import { initTheme } from "../shared/theme";
import { webAppConnector, webViewStore } from "../shared/webAppSession";

const root = document.getElementById("root");
if (!root) {
  throw new Error("root element is missing");
}

// The Telegram Mini App: the staff workspace, and for a shop's customer their own account.
const launch = initTelegram();
// Inside Telegram its theme decides the colors; anywhere else the person's own choice does.
if (!launch.insideTelegram) {
  initTheme();
}
const initialLanguage = detectLanguage({
  stored: readStoredLanguage(),
  telegramLanguageCode: launch.languageCode,
});
const preview = import.meta.env.DEV ? previewStaffSession(window.location.search) : null;

// The signed launch string is exchanged once for a session token. The token is kept for the life of this
// web view (see webAppSession.ts) and never put in a URL. Outside Telegram there is nothing to sign in with.
const connect = webAppConnector((input, init) => window.fetch(input, init), launch.initData, webViewStore());

// The text of the language the person starts in is fetched before anything is drawn.
void startLanguage(initialLanguage).then(() => {
  createRoot(root).render(
    <StrictMode>
      <ErrorBoundary scope="page">
        {preview ? (
          <StaffApp entryKey="entry.app" session={preview} initialLanguage={initialLanguage} />
        ) : (
          <StaffRoot entryKey="entry.app" initialLanguage={initialLanguage} connect={connect} customerPage />
        )}
      </ErrorBoundary>
    </StrictMode>,
  );
});

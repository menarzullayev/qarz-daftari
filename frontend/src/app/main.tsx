import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { detectLanguage, readStoredLanguage } from "../i18n/detect";
import { type ApiAuth, signInWebApp } from "../shared/api";
import { previewStaffSession } from "../shared/session";
import { StaffApp } from "../shared/StaffApp";
import { StaffRoot } from "../shared/StaffRoot";
import { initTelegram } from "../shared/telegram";

const root = document.getElementById("root");
if (!root) {
  throw new Error("root element is missing");
}

// Staff workspace inside the Telegram Mini App.
const launch = initTelegram();
const initialLanguage = detectLanguage({
  stored: readStoredLanguage(),
  telegramLanguageCode: launch.languageCode,
});
const preview = import.meta.env.DEV ? previewStaffSession(window.location.search) : null;

// The signed launch string is exchanged once for a session token, which lives in memory only: it is
// never written to storage or to a URL. Outside Telegram there is nothing to sign in with.
const connect = async (): Promise<ApiAuth | null> => {
  if (launch.initData === null) {
    return null;
  }
  const token = await signInWebApp((input, init) => window.fetch(input, init), launch.initData);
  return { kind: "bearer", token };
};

createRoot(root).render(
  <StrictMode>
    {preview ? (
      <StaffApp entryKey="entry.app" session={preview} initialLanguage={initialLanguage} />
    ) : (
      <StaffRoot entryKey="entry.app" initialLanguage={initialLanguage} connect={connect} />
    )}
  </StrictMode>,
);

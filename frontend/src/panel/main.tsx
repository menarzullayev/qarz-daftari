import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { detectLanguage, readStoredLanguage } from "../i18n/detect";
import type { ApiAuth } from "../shared/api";
import { previewStaffSession } from "../shared/session";
import { StaffApp } from "../shared/StaffApp";
import { StaffRoot } from "../shared/StaffRoot";

const root = document.getElementById("root");
if (!root) {
  throw new Error("root element is missing");
}

// Web panel: the same staff screens on a desktop layout. It runs outside Telegram, so there is no
// Telegram language to read and no Telegram script on the page.
const initialLanguage = detectLanguage({ stored: readStoredLanguage() });
const preview = import.meta.env.DEV ? previewStaffSession(window.location.search) : null;

// The panel's session is an HTTP-only cookie set by POST /api/v1/auth/telegram-login. The sign-in form
// for it is a later story; until then there is no cookie and the panel shows the sign-in notice. The
// CSRF token that sign-in returns must be passed here when that form arrives: without it the server
// refuses every write from a cookie session.
const connect = async (): Promise<ApiAuth | null> => ({ kind: "cookie", csrfToken: null });

createRoot(root).render(
  <StrictMode>
    {preview ? (
      <StaffApp entryKey="entry.panel" session={preview} initialLanguage={initialLanguage} />
    ) : (
      <StaffRoot entryKey="entry.panel" initialLanguage={initialLanguage} connect={connect} />
    )}
  </StrictMode>,
);

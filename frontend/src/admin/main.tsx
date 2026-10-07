import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { detectLanguage, readStoredLanguage } from "../i18n/detect";
import { takeLoginReturn } from "../panel/loginReturn";
import { previewAdminSession } from "../shared/session";
import { AdminApp } from "./AdminApp";
import { AdminRoot } from "./AdminRoot";

const root = document.getElementById("root");
if (!root) {
  throw new Error("root element is missing");
}

// The administrator's panel: Telegram sign-in, the second factor, then the panel. Telegram's sign-in
// script is added by the sign-in screen alone, not by the page.
// First of all, before anything is drawn or requested: if Telegram has just sent the browser back here,
// the signed fields are taken out of the address.
const loginReturn = takeLoginReturn();

const initialLanguage = detectLanguage({ stored: readStoredLanguage() });
const preview = import.meta.env.DEV ? previewAdminSession(window.location.search) : false;

createRoot(root).render(
  <StrictMode>
    {preview ? <AdminApp signedIn initialLanguage={initialLanguage} /> : <AdminRoot initialLanguage={initialLanguage} loginReturn={loginReturn} />}
  </StrictMode>,
);

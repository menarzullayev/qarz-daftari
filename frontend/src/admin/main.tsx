import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { detectLanguage, readStoredLanguage } from "../i18n/detect";
import { previewAdminSession } from "../shared/session";
import { AdminApp } from "./AdminApp";

const root = document.getElementById("root");
if (!root) {
  throw new Error("root element is missing");
}

const initialLanguage = detectLanguage({ stored: readStoredLanguage() });
const signedIn = import.meta.env.DEV ? previewAdminSession(window.location.search) : false;

createRoot(root).render(
  <StrictMode>
    <AdminApp signedIn={signedIn} initialLanguage={initialLanguage} />
  </StrictMode>,
);

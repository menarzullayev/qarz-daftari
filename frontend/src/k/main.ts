import "../shared/tokens.css";
import "./k.css";
import { startPage } from "./page";

// The page behind a customer's read-only link (/k/#<secret>). No sign-in, no framework, no storage but
// the reader's choice of language; the color theme follows the device (tokens.css, "System").
const root = document.getElementById("root");
if (!root) {
  throw new Error("root element is missing");
}

function browserStorage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    // Access itself can throw when storage is blocked (private mode, some in-app browsers).
    return null;
  }
}

void startPage({
  root,
  hash: window.location.hash,
  fetch: (input, init) => window.fetch(input, init),
  storage: browserStorage(),
  browserLanguages: navigator.languages ?? [navigator.language],
});

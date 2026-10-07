import { readLoginData, type TelegramLoginData } from "./signIn";

/**
 * Coming back from Telegram (ADR-017). The Login widget runs in its redirect mode: once the person has
 * confirmed in Telegram, Telegram's script sends this browser to the page's own address with the signed
 * fields in the query string. The fields are a credential for the next hour, so the page that receives
 * them does three things before anything is drawn or requested:
 *
 *   1. reads them out of the address;
 *   2. replaces the address, in the same history entry, with one that has no query string: the fields
 *      are not left in the address bar, and no step back or forward leads to an address with them;
 *   3. accepts them only when this page was reached from a page of this same site.
 *
 * The third is what a callback gave for free. A link to this page with somebody else's valid fields in
 * it, followed from a message or from another site, would otherwise sign the reader in to the sender's
 * account. Telegram's script navigates from the sign-in page itself, so the browser names this site as
 * where the navigation came from; a link from anywhere else names another site or nothing. Nothing is
 * kept in storage for this.
 *
 * The server accepts each set of signed fields once, so an address that was seen is useless afterwards.
 */
export type LoginReturn =
  /** An ordinary load of the page: the address carried no signed fields. */
  | { status: "none" }
  /** Fields arrived but are not taken: not from this site, or not what Telegram sends. */
  | { status: "refused" }
  /** What Telegram signed, every field as it came. */
  | { status: "returned"; data: TelegramLoginData };

export const NO_RETURN: LoginReturn = { status: "none" };

type Page = {
  location: Pick<Location, "search" | "pathname" | "hash" | "origin">;
  history: Pick<History, "replaceState" | "state">;
  document: Pick<Document, "referrer">;
};

function fromThisSite(referrer: string, origin: string): boolean {
  try {
    return new URL(referrer).origin === origin;
  } catch {
    return false;
  }
}

/**
 * Takes the signed fields out of the address, if it carries any. Call once, when the page's script
 * starts: before rendering and before any request.
 */
export function takeLoginReturn(page: Page = window): LoginReturn {
  const query = new URLSearchParams(page.location.search);
  // Telegram always sends the signature; without it this is not a return, and the address is left alone.
  if (!query.has("hash")) {
    return NO_RETURN;
  }
  page.history.replaceState(page.history.state, "", page.location.pathname + page.location.hash);

  if (!fromThisSite(page.document.referrer, page.location.origin)) {
    return { status: "refused" };
  }
  const fields: Record<string, string> = {};
  for (const [name, value] of query) {
    // A field given twice has no single signed value.
    if (name in fields) {
      return { status: "refused" };
    }
    fields[name] = value;
  }
  const data = readLoginData(fields);
  return data === null ? { status: "refused" } : { status: "returned", data };
}

import { type ApiAuth, type Fetch, signInWebApp } from "./api";

/**
 * The Mini App's session for as long as its web view lives.
 *
 * Telegram signs the launch data once, when it opens the Mini App, and the server accepts one signed
 * launch string once (security review, finding 9). But a page can be asked for its session more than
 * once on a single launch: the workspace connects again after a failed start, and Telegram's "Reload
 * Page", or a phone that restores the web view, loads the page again with the very same launch data
 * (Telegram's bridge script keeps it in `sessionStorage` for exactly that). Sending the launch string a
 * second time is refused, and the person would be told to sign in with no way of doing so short of
 * closing the Mini App.
 *
 * So the launch string is exchanged once, and the token it bought is remembered: in memory for this
 * page, and in `sessionStorage` for a reload. `sessionStorage` belongs to this one web view and is gone
 * when the Mini App is closed; nothing is written to `localStorage`, and nothing to an address. The
 * token is kept beside the signature of the launch it came from, so a new launch never reuses it.
 */
export const WEBAPP_SESSION_KEY = "qd.webapp.session";

export type SessionStore = Pick<Storage, "getItem" | "setItem" | "removeItem">;

type Kept = { launch: string; token: string };

/** What tells one launch from another: Telegram's signature over it. */
function launchSignature(initData: string): string {
  return new URLSearchParams(initData).get("hash") ?? "";
}

function readKept(store: SessionStore | null): Kept | null {
  try {
    const raw: unknown = JSON.parse(store?.getItem(WEBAPP_SESSION_KEY) ?? "null");
    if (typeof raw !== "object" || raw === null) {
      return null;
    }
    const { launch, token } = raw as Record<string, unknown>;
    return typeof launch === "string" && typeof token === "string" && launch !== "" && token !== "" ? { launch, token } : null;
  } catch {
    // Storage that cannot be read, or holds something else, is storage with nothing in it.
    return null;
  }
}

function keep(store: SessionStore | null, kept: Kept): void {
  try {
    store?.setItem(WEBAPP_SESSION_KEY, JSON.stringify(kept));
  } catch {
    // A web view without storage still works until it is reloaded.
  }
}

/** The web view's own storage, or null where the browser refuses access to it. */
export function webViewStore(): SessionStore | null {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

/**
 * How the Mini App proves who is using it: null outside Telegram (there is nothing to sign in with),
 * otherwise a session token, bought with the launch string the first time and remembered after that.
 */
export function webAppConnector(fetch: Fetch, initData: string | null, store: SessionStore | null): () => Promise<ApiAuth | null> {
  let signingIn: Promise<string> | null = null;
  return async () => {
    if (initData === null) {
      return null;
    }
    const launch = launchSignature(initData);
    if (signingIn === null) {
      const kept = readKept(store);
      signingIn =
        kept !== null && launch !== "" && kept.launch === launch
          ? Promise.resolve(kept.token)
          : signInWebApp(fetch, initData).then((token) => {
              keep(store, { launch, token });
              return token;
            });
      // A sign-in that failed before the server answered may be tried again; one that was refused
      // will be refused again, and the caller shows that.
      signingIn.catch(() => {
        signingIn = null;
      });
    }
    return { kind: "bearer", token: await signingIn };
  };
}

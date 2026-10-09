import { Component, type ErrorInfo, type ReactNode } from "react";

import { activeLanguage, translate } from "../i18n/catalog";
import "./tokens.css";
import "./shell.css";
import "./workspace/workspace.css";

/**
 * What is shown in place of a part of the page that could not be drawn, so that the person never looks
 * at an empty screen. Two things end up here:
 *
 *  - a part of the application that is fetched when it is first opened (a screen, a section) and could
 *    not be fetched. After a deployment the files have new names and the old ones are gone, so a page
 *    that was opened before it asks for a file that no longer exists. The cure is to load the page
 *    again, and that is done once without asking;
 *  - an error while drawing. The message says so and offers to load the page again.
 *
 * There is no error-reporting service. What happened is written to the browser's console and goes
 * nowhere else: the error's name and message and the names of the components around it, never what a
 * screen was showing.
 */

/** When the page last loaded itself again because of a file it could not fetch (milliseconds, as text). */
export const RELOAD_MARK = "qd.reloadedAt";
/** One automatic reload in this long: a file that is still missing after it is shown as a message. */
export const RELOAD_EVERY_MS = 10 * 60 * 1000;

const CHUNK_MESSAGES: readonly RegExp[] = [
  // Chromium, Firefox and Safari, each in its own words, for `import()` of a file that is not there.
  /Failed to fetch dynamically imported module/i,
  /error loading dynamically imported module/i,
  /Importing a module script failed/i,
  // Vite's own, for a style sheet or a script the fetched part needs.
  /Unable to preload CSS/i,
  /Failed to load module script/i,
];

/** Whether an error is "a part of the application could not be fetched", in any browser's words. */
export function isChunkLoadError(error: unknown): boolean {
  if (!(error instanceof Error)) {
    return false;
  }
  return error.name === "ChunkLoadError" || CHUNK_MESSAGES.some((pattern) => pattern.test(error.message));
}

/** The page as the boundary uses it; the tests pass their own. */
export type Page = {
  reload: () => void;
  /** Kept for this tab only. Null when the browser refuses storage. */
  storage: Pick<Storage, "getItem" | "setItem"> | null;
  now: () => number;
  /** Whether the device says it has a connection: without one a reload would lose the page as well. */
  online: () => boolean;
};

function tabStorage(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    // Reading the property itself throws where storage is blocked.
    return null;
  }
}

const browserPage: Page = {
  reload: () => window.location.reload(),
  get storage() {
    return tabStorage();
  },
  now: () => Date.now(),
  online: () => navigator.onLine !== false,
};

/**
 * Loads the page again by itself, at most once in `RELOAD_EVERY_MS`: the mark of the last time is kept
 * for the tab, and a second failure inside that time is left to the person. Without storage to keep the
 * mark in, or without a connection, nothing is done. Returns whether the page is being loaded again.
 */
export function reloadOnce(page: Page = browserPage): boolean {
  try {
    const storage = page.storage;
    if (storage === null || !page.online()) {
      return false;
    }
    const last = Number(storage.getItem(RELOAD_MARK));
    const now = page.now();
    if (Number.isFinite(last) && last > 0 && now - last < RELOAD_EVERY_MS) {
      return false;
    }
    storage.setItem(RELOAD_MARK, String(now));
    // Read back: a store that takes the mark and does not keep it could not stop a second reload.
    if (storage.getItem(RELOAD_MARK) !== String(now)) {
      return false;
    }
    page.reload();
    return true;
  } catch {
    return false;
  }
}

type Failure = "crash" | "outdated";

type Props = {
  children: ReactNode;
  /**
   * "page" stands in for the whole page (the root of an entry); "screen" for one screen inside the
   * shell, whose header and navigation stay.
   */
  scope: "page" | "screen";
  page?: Page;
};

type State = { failure: Failure | null; reloading: boolean };

export class ErrorBoundary extends Component<Props, State> {
  override state: State = { failure: null, reloading: false };

  static getDerivedStateFromError(error: unknown): Partial<State> {
    return { failure: isChunkLoadError(error) ? "outdated" : "crash" };
  }

  override componentDidCatch(error: unknown, info: ErrorInfo): void {
    const outdated = isChunkLoadError(error);
    // The console only. The name, the message and the component names: nothing a screen was showing.
    console.error(
      outdated ? "[qd] a part of the application could not be fetched" : "[qd] a screen could not be drawn",
      error instanceof Error ? `${error.name}: ${error.message}` : "(not an Error)",
      info.componentStack ?? "",
    );
    if (outdated && reloadOnce(this.props.page)) {
      this.setState({ reloading: true });
    }
  }

  override render(): ReactNode {
    const { failure, reloading } = this.state;
    if (failure === null) {
      return this.props.children;
    }
    const page = this.props.page ?? browserPage;
    // The catalog itself, not the language context: at the root of an entry there is none above this.
    const t = (key: "crash.title" | "crash.body" | "crash.outdated" | "crash.reload") => translate(activeLanguage(), key);
    const message = (
      <div className="notice notice--error" role="alert">
        <p>{t(failure === "outdated" ? "crash.outdated" : "crash.body")}</p>
        <button type="button" className="button button--primary" onClick={() => page.reload()} disabled={reloading}>
          {t("crash.reload")}
        </button>
      </div>
    );
    if (this.props.scope === "screen") {
      return message;
    }
    return (
      <main className="crash">
        <h1 className="crash__title">{t("crash.title")}</h1>
        {message}
      </main>
    );
  }
}

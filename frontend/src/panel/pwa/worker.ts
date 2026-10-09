/**
 * The web panel's service worker, as a function of the worker's global scope so that it can be tested.
 *
 * What it is for: the panel can be installed, and an installed panel that is opened without a
 * connection must still draw itself and say that there is no connection, instead of the browser's
 * error page. So the worker keeps the panel's static shell: its page and the scripts and style sheets
 * that page names. Nothing else.
 *
 * What it never does:
 *  - it never answers, reads or stores a request to the API or to anything else that carries a
 *    person's data: `plan` lets it handle a GET for the panel's own page or for a built file under
 *    /assets/, and passes everything else to the network untouched, as if the worker were not there;
 *  - it never stores what a page asked for. The page's address may carry Telegram's signed sign-in
 *    fields in its query string; the stored shell is fetched by the worker itself, from the bare
 *    address, when the worker is installed;
 *  - it never serves a stored page while the network answers: the page is always asked from the
 *    network first, and the stored copy is for when that fails.
 *
 * Updating. Each build writes its own list of shell files and its own build mark into the worker's
 * script (vite.config.ts), so a deployment changes the script, and a changed script is installed by
 * the browser the next time the panel is opened. The new worker takes over at once (`skipWaiting`,
 * `clients.claim`) and removes the stores of earlier builds. Taking over at once is safe here, where it
 * would not be for a worker that serves pages from its store: an open page keeps getting its page from
 * the network and its built files by their content-hashed names, whichever worker is in charge, so no
 * page can be handed a mix of two builds. Waiting for every tab to close would only keep an old offline
 * shell alive longer.
 */

export type WorkerManifest = {
  /** Changes whenever any file of the shell changes. */
  build: string;
  /** The panel's page and the built files it names, as paths. */
  shell: readonly string[];
};

/** The panel's page: what an installed panel opens, and the only page the worker ever answers for. */
export const SHELL_PAGE = "/panel/";
export const CACHE_PREFIX = "qd-panel-";

/** Paths the worker must leave alone, whatever else is true of the request. */
export const NEVER_HANDLED = ["/api/", "/k/", "/admin/", "/app/", "/files/", "/pay/", "/tg/", "/metrics", "/healthz"] as const;

// A file Vite built: under /assets/, named with the hash of its content, a script or a style sheet.
const BUILT_FILE = /^\/assets\/[A-Za-z0-9._-]+-[A-Za-z0-9_-]{8}\.(?:js|css)$/;

export type Plan = "page" | "file" | "pass";

type RequestLike = { method: string; url: string; mode: string };

/** What the worker does with a request: answer for the panel's page, for a built file, or nothing at all. */
export function plan(request: RequestLike, origin: string): Plan {
  if (request.method !== "GET") {
    return "pass";
  }
  let url: URL;
  try {
    url = new URL(request.url);
  } catch {
    return "pass";
  }
  if (url.origin !== origin) {
    return "pass";
  }
  const path = url.pathname;
  if (NEVER_HANDLED.some((prefix) => path === prefix || path.startsWith(prefix.endsWith("/") ? prefix : `${prefix}/`))) {
    return "pass";
  }
  if (BUILT_FILE.test(path) && url.search === "") {
    return "file";
  }
  // A person opening the panel, not a script of the page asking for something under it.
  if (request.mode === "navigate" && (path === SHELL_PAGE || path.startsWith(SHELL_PAGE))) {
    return "page";
  }
  return "pass";
}

type Waiting = { waitUntil(work: Promise<unknown>): void };
type FetchEventLike = { request: Request; respondWith(answer: Promise<Response>): void };

/** The part of a service worker's global scope this worker uses. */
export type WorkerScope = {
  location: { origin: string };
  caches: Pick<CacheStorage, "open" | "keys" | "delete">;
  fetch: (input: Request | string, init?: RequestInit) => Promise<Response>;
  skipWaiting(): Promise<void>;
  clients: { claim(): Promise<void> };
  addEventListener(type: "install" | "activate", listener: (event: Waiting) => void): void;
  addEventListener(type: "fetch", listener: (event: FetchEventLike) => void): void;
};

function storable(response: Response): boolean {
  // A complete, same-origin answer: never an error page, a redirect or an opaque answer.
  return response.status === 200 && response.type === "basic";
}

export function startWorker(scope: WorkerScope, manifest: WorkerManifest): void {
  const cacheName = CACHE_PREFIX + manifest.build;

  scope.addEventListener("install", (event) => {
    event.waitUntil(
      (async () => {
        const cache = await scope.caches.open(cacheName);
        // All of the shell or none of it: one file that cannot be fetched fails the installation, and
        // the browser tries again later. Each file is asked from the server, past the browser's own
        // store, and from its bare address.
        const answers = await Promise.all(
          manifest.shell.map(async (path) => {
            const response = await scope.fetch(path, { cache: "reload", credentials: "omit" });
            if (!storable(response)) {
              throw new Error(`the shell file ${path} could not be fetched`);
            }
            return [path, response] as const;
          }),
        );
        await Promise.all(answers.map(([path, response]) => cache.put(path, response)));
        await scope.skipWaiting();
      })(),
    );
  });

  scope.addEventListener("activate", (event) => {
    event.waitUntil(
      (async () => {
        const names = await scope.caches.keys();
        // Only this worker's own stores of other builds; nothing else of the origin is touched.
        await Promise.all(
          names.filter((name) => name.startsWith(CACHE_PREFIX) && name !== cacheName).map((name) => scope.caches.delete(name)),
        );
        await scope.clients.claim();
      })(),
    );
  });

  scope.addEventListener("fetch", (event) => {
    const { request } = event;
    const what = plan(request, scope.location.origin);
    if (what === "pass") {
      // No answer from the worker: the browser sends the request itself, exactly as without one.
      return;
    }
    if (what === "page") {
      event.respondWith(
        (async () => {
          try {
            return await scope.fetch(request);
          } catch (failure) {
            const cache = await scope.caches.open(cacheName);
            const stored = await cache.match(SHELL_PAGE);
            if (stored) {
              return stored;
            }
            throw failure;
          }
        })(),
      );
      return;
    }
    event.respondWith(
      (async () => {
        const cache = await scope.caches.open(cacheName);
        const path = new URL(request.url).pathname;
        // A built file never changes under its name, so the stored copy is the file.
        const stored = await cache.match(path);
        if (stored) {
          return stored;
        }
        const response = await scope.fetch(request);
        if (storable(response)) {
          // A screen that is loaded on demand: kept, so that it too opens without a connection.
          await cache.put(path, response.clone());
        }
        return response;
      })(),
    );
  });
}

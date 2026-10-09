/**
 * Registers the web panel's service worker (worker.ts), so that an installed panel opens to its own
 * "no connection" notice instead of the browser's error page.
 *
 * The worker's script is /panel/sw.js and its scope is /panel/: the Mini App (/app/), the
 * administrators' entry (/admin/) and a customer's page (/k/) are outside it and are never controlled.
 */
export const WORKER_URL = "/panel/sw.js";
export const WORKER_SCOPE = "/panel/";

type Registrar = Pick<ServiceWorkerContainer, "register">;

export type RegistrationResult = "registered" | "unsupported" | "skipped" | "failed";

export async function registerPanelWorker(options: {
  /** `navigator.serviceWorker`; absent in a browser without service workers and outside a secure context. */
  container: Registrar | undefined;
  /** A built page. The development server has no built worker to register. */
  production: boolean;
}): Promise<RegistrationResult> {
  if (!options.production) {
    return "skipped";
  }
  if (!options.container) {
    return "unsupported";
  }
  try {
    // `updateViaCache: "none"`: the browser asks the server for the script and never trusts a stored
    // copy, so a deployment's new worker is found the next time the panel is opened.
    await options.container.register(WORKER_URL, { scope: WORKER_SCOPE, updateViaCache: "none" });
    return "registered";
  } catch {
    // Without the worker the panel works exactly as before; it only cannot open without a connection.
    return "failed";
  }
}

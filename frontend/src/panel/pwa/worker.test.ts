import { describe, expect, it, vi } from "vitest";

import { registerPanelWorker, WORKER_SCOPE, WORKER_URL } from "./register";
import { CACHE_PREFIX, NEVER_HANDLED, plan, SHELL_PAGE, startWorker, type WorkerManifest, type WorkerScope } from "./worker";

const ORIGIN = "https://qarz.example.uz";
const MANIFEST: WorkerManifest = {
  build: "0123456789abcdef",
  shell: ["/panel/", "/assets/panel-CZMCdhnq.js", "/assets/workspace-C8A9U79g.css"],
};
const SCREEN = "/assets/ReportsScreen-DUlOIMOD.js";

const get = (path: string, mode = "cors") => ({ method: "GET", url: path.startsWith("http") ? path : ORIGIN + path, mode });

describe("which requests the worker answers", () => {
  it.each([
    ["/panel/", "navigate"],
    ["/panel/?id=1&hash=abc", "navigate"],
    ["/panel/no/such/page", "navigate"],
  ])("answers for the panel's page: %s", (path, mode) => {
    expect(plan(get(path, mode), ORIGIN)).toBe("page");
  });

  it.each(["/assets/panel-CZMCdhnq.js", "/assets/workspace-C8A9U79g.css", "/assets/tokens-BLZDwiO_.css", SCREEN])(
    "answers for a built file: %s",
    (path) => {
      expect(plan(get(path), ORIGIN)).toBe("file");
      expect(plan(get(path, "no-cors"), ORIGIN)).toBe("file");
    },
  );

  it.each([
    // The API, in every form: a read, a customer's account, the administrators' side, a file link.
    "/api/v1/me",
    "/api/v1/shops/5a0c6d3e-0000-4000-8000-00000000aaaa/customers",
    "/api/v1/customer-share",
    "/api/admin/v1/auth",
    "/api/",
    "/files/abc?sig=def",
    "/pay/payme",
    "/tg/webhook",
    "/metrics",
    "/healthz",
    // The other entries, which are not installable and not the worker's.
    "/k/",
    "/k/index.html",
    "/admin/",
    "/admin/settings",
    "/app/",
    "/",
  ])("never touches %s, as a request of a page or as a page being opened", (path) => {
    expect(plan(get(path), ORIGIN)).toBe("pass");
    expect(plan(get(path, "navigate"), ORIGIN)).toBe("pass");
  });

  it.each([
    ["a request that changes something", { method: "POST", url: `${ORIGIN}/panel/`, mode: "navigate" }],
    ["a write to a built file's address", { method: "PUT", url: `${ORIGIN}/assets/panel-CZMCdhnq.js`, mode: "cors" }],
    ["a script of the page asking for the page", get("/panel/")],
    ["the manifest", get("/panel/manifest.webmanifest")],
    ["an icon", get("/panel/icons/icon-192.png", "no-cors")],
    ["the worker's own script", get("/panel/sw.js")],
    ["a built file with a query string", get("/assets/panel-CZMCdhnq.js?token=abc")],
    ["a file under /assets/ that was not built", get("/assets/data.json")],
    ["a file under /assets/ without a content hash", get("/assets/panel.js")],
    ["a path that only looks like /assets/", get("/panel/assets/panel-CZMCdhnq.js")],
    ["Telegram's sign-in script", get("https://telegram.org/js/telegram-widget.js?22", "no-cors")],
    ["another origin's page", get("https://example.org/panel/", "navigate")],
    ["the same host on another scheme", get("http://qarz.example.uz/panel/", "navigate")],
    ["an address that is not one", { method: "GET", url: "not a url", mode: "navigate" }],
  ])("leaves %s to the network", (_what, request) => {
    expect(plan(request, ORIGIN)).toBe("pass");
  });

  it("names every part of the service that carries a person's data among what it never touches", () => {
    expect(NEVER_HANDLED).toEqual(expect.arrayContaining(["/api/", "/k/", "/admin/", "/app/", "/files/"]));
  });
});

// --- the worker itself, against a stand-in for its global scope ------------------------------------------

type Store = Map<string, Response>;

function scopeWith(network: (path: string, init?: RequestInit) => Response | Promise<Response>) {
  const stores = new Map<string, Store>();
  const listeners = new Map<string, (event: never) => void>();
  const fetched: { path: string; init: RequestInit | undefined }[] = [];
  const calls = { skipWaiting: 0, claim: 0 };
  const scope: WorkerScope = {
    location: { origin: ORIGIN },
    caches: {
      open: async (name) => {
        const store = stores.get(name) ?? new Map<string, Response>();
        stores.set(name, store);
        return {
          match: async (key: string) => store.get(key)?.clone(),
          put: async (key: string, response: Response) => void store.set(key, response),
        } as unknown as Cache;
      },
      keys: async () => [...stores.keys()],
      delete: async (name) => stores.delete(name),
    },
    fetch: async (input, init) => {
      const url = new URL(typeof input === "string" ? input : input.url, ORIGIN);
      fetched.push({ path: url.pathname + url.search, init });
      return network(url.pathname + url.search, init);
    },
    skipWaiting: async () => void (calls.skipWaiting += 1),
    clients: { claim: async () => void (calls.claim += 1) },
    addEventListener: (type: string, listener: (event: never) => void) => void listeners.set(type, listener),
  };

  const lifecycle = async (type: "install" | "activate") => {
    let work: Promise<unknown> = Promise.resolve();
    (listeners.get(type) as unknown as (event: { waitUntil(p: Promise<unknown>): void }) => void)({
      waitUntil: (promise) => void (work = promise),
    });
    await work;
  };
  /** Dispatches a fetch event; null when the worker did not answer (the browser would). */
  const ask = (path: string, init: { method?: string; mode?: string } = {}): Promise<Response> | null => {
    let answer: Promise<Response> | null = null;
    const request = { method: init.method ?? "GET", url: ORIGIN + path, mode: init.mode ?? "cors" } as unknown as Request;
    (listeners.get("fetch") as unknown as (event: { request: Request; respondWith(a: Promise<Response>): void }) => void)({
      request,
      respondWith: (promise) => void (answer = promise),
    });
    return answer;
  };
  return { scope, stores, fetched, calls, lifecycle, ask };
}

const basic = (body: string, status = 200) => {
  const response = new Response(body, { status });
  Object.defineProperty(response, "type", { value: "basic" });
  return response;
};
const online = (path: string) => basic(`content of ${path}`);
const down = () => Promise.reject(new TypeError("Failed to fetch"));

describe("installing", () => {
  it("keeps exactly the shell of its build, fetched from the bare addresses, and takes over at once", async () => {
    const worker = scopeWith(online);
    startWorker(worker.scope, MANIFEST);
    await worker.lifecycle("install");

    expect([...worker.stores.keys()]).toEqual([`${CACHE_PREFIX}${MANIFEST.build}`]);
    expect([...(worker.stores.get(`${CACHE_PREFIX}${MANIFEST.build}`) as Store).keys()]).toEqual([...MANIFEST.shell]);
    expect(worker.fetched.map((request) => request.path)).toEqual([...MANIFEST.shell]);
    // Past the browser's own store, and without the session cookie: the shell is the same for everyone.
    expect(worker.fetched.every((request) => request.init?.cache === "reload" && request.init.credentials === "omit")).toBe(true);
    expect(worker.calls.skipWaiting).toBe(1);
  });

  it.each([
    ["cannot be fetched", (path: string) => (path === MANIFEST.shell[1] ? down() : online(path))],
    ["is answered with an error page", (path: string) => (path === MANIFEST.shell[2] ? basic("no", 404) : online(path))],
    ["is answered by another origin", (path: string) => (path === SHELL_PAGE ? new Response("x") : online(path))],
  ])("keeps nothing and does not take over when one file of the shell %s", async (_what, network) => {
    const worker = scopeWith(network);
    startWorker(worker.scope, MANIFEST);
    await expect(worker.lifecycle("install")).rejects.toThrow();
    expect([...(worker.stores.get(`${CACHE_PREFIX}${MANIFEST.build}`) ?? new Map()).keys()]).toEqual([]);
    expect(worker.calls.skipWaiting).toBe(0);
  });
});

describe("a new build", () => {
  it("removes the stores of earlier builds, keeps its own, and touches nothing that is not the worker's", async () => {
    const worker = scopeWith(online);
    worker.stores.set(`${CACHE_PREFIX}older-build`, new Map([["/panel/", basic("old page")]]));
    worker.stores.set("someone-elses-store", new Map());
    startWorker(worker.scope, MANIFEST);
    await worker.lifecycle("install");
    await worker.lifecycle("activate");
    expect([...worker.stores.keys()].sort()).toEqual([`${CACHE_PREFIX}${MANIFEST.build}`, "someone-elses-store"].sort());
    expect(worker.calls.claim).toBe(1);
  });

  it("has a store of its own name, so the shell of one build is never mixed with another's", async () => {
    const first = scopeWith(online);
    startWorker(first.scope, MANIFEST);
    await first.lifecycle("install");
    const second = scopeWith(online);
    startWorker(second.scope, { ...MANIFEST, build: "fedcba9876543210" });
    await second.lifecycle("install");
    expect([...first.stores.keys()]).not.toEqual([...second.stores.keys()]);
  });
});

describe("opening the panel", () => {
  async function installed(network: (path: string) => Response | Promise<Response>) {
    let live: (path: string) => Response | Promise<Response> = online;
    const worker = scopeWith((path) => live(path));
    startWorker(worker.scope, MANIFEST);
    await worker.lifecycle("install");
    await worker.lifecycle("activate");
    live = network;
    worker.fetched.length = 0;
    return worker;
  }
  const shellOf = (worker: Awaited<ReturnType<typeof installed>>) =>
    worker.stores.get(`${CACHE_PREFIX}${MANIFEST.build}`) as Store;

  it("asks the network for the page first, so a deployment's page is never hidden by the stored one", async () => {
    const worker = await installed((path) => basic(`the new page at ${path}`));
    const answer = await worker.ask("/panel/", { mode: "navigate" });
    expect(await answer?.text()).toBe("the new page at /panel/");
  });

  it("stores nothing of the page that was asked for: its address may carry Telegram's signed fields", async () => {
    const worker = await installed(online);
    const before = [...shellOf(worker).keys()];
    await worker.ask("/panel/?id=42&first_name=Ali&hash=0f0f", { mode: "navigate" });
    expect([...shellOf(worker).keys()]).toEqual(before);
    expect(JSON.stringify([...shellOf(worker).keys()])).not.toContain("hash");
    expect(await shellOf(worker).get(SHELL_PAGE)?.clone().text()).toBe("content of /panel/");
  });

  it("answers with the stored page when the network does not, whatever page of the panel was asked for", async () => {
    const worker = await installed(down);
    for (const path of ["/panel/", "/panel/?id=42&hash=0f0f", "/panel/customers"]) {
      const answer = await worker.ask(path, { mode: "navigate" });
      expect(await answer?.text()).toBe("content of /panel/");
    }
  });

  it("fails as the network failed when there is no stored page to fall back on", async () => {
    const worker = scopeWith(down);
    startWorker(worker.scope, MANIFEST);
    await expect(worker.ask("/panel/", { mode: "navigate" })).rejects.toThrow("Failed to fetch");
  });

  it("answers a built file from the store without asking the network", async () => {
    const worker = await installed(down);
    const answer = await worker.ask("/assets/panel-CZMCdhnq.js");
    expect(await answer?.text()).toBe("content of /assets/panel-CZMCdhnq.js");
    expect(worker.fetched).toEqual([]);
  });

  it("keeps a screen loaded on demand once it was fetched, and only a complete answer", async () => {
    const worker = await installed(online);
    expect(await (await worker.ask(SCREEN))?.text()).toBe(`content of ${SCREEN}`);
    expect(shellOf(worker).has(SCREEN)).toBe(true);
    worker.fetched.length = 0;
    await worker.ask(SCREEN);
    expect(worker.fetched).toEqual([]);

    const failing = await installed(() => basic("gone", 404));
    expect((await failing.ask(SCREEN))?.status).toBe(404);
    expect(shellOf(failing).has(SCREEN)).toBe(false);
  });

  it.each([
    ["/api/v1/me", "cors"],
    ["/api/v1/shops/5a0c6d3e-0000-4000-8000-00000000aaaa/customers", "cors"],
    ["/api/v1/customer-share", "cors"],
    ["/k/", "navigate"],
    ["/admin/", "navigate"],
    ["/app/", "navigate"],
    ["/files/abc", "navigate"],
  ])("gives no answer of its own for %s, online or not, and stores nothing", async (path, mode) => {
    for (const network of [online, down]) {
      const worker = await installed(network);
      const before = [...shellOf(worker).keys()];
      expect(worker.ask(path, { mode })).toBeNull();
      expect(worker.fetched).toEqual([]);
      expect([...shellOf(worker).keys()]).toEqual(before);
    }
  });

  it("gives no answer for a request that changes something", async () => {
    const worker = await installed(online);
    expect(worker.ask("/api/v1/auth/telegram-login", { method: "POST" })).toBeNull();
    expect(worker.ask("/panel/", { method: "POST", mode: "navigate" })).toBeNull();
  });

  it("the check above would notice an answer: the worker does answer for its own page", async () => {
    const worker = await installed(online);
    expect(worker.ask("/panel/", { mode: "navigate" })).not.toBeNull();
  });
});

describe("registering the worker", () => {
  it("registers the panel's script for the panel's scope, never trusting a stored copy of it", async () => {
    const register = vi.fn(() => Promise.resolve({} as ServiceWorkerRegistration));
    expect(await registerPanelWorker({ container: { register }, production: true })).toBe("registered");
    expect(register).toHaveBeenCalledWith("/panel/sw.js", { scope: "/panel/", updateViaCache: "none" });
    expect([WORKER_URL, WORKER_SCOPE, SHELL_PAGE]).toEqual(["/panel/sw.js", "/panel/", "/panel/"]);
  });

  it("registers nothing on the development server or where the browser has no service workers", async () => {
    const register = vi.fn(() => Promise.resolve({} as ServiceWorkerRegistration));
    expect(await registerPanelWorker({ container: { register }, production: false })).toBe("skipped");
    expect(await registerPanelWorker({ container: undefined, production: true })).toBe("unsupported");
    expect(register).not.toHaveBeenCalled();
  });

  it("leaves the panel working when the registration is refused", async () => {
    const register = vi.fn(() => Promise.reject(new Error("refused")));
    expect(await registerPanelWorker({ container: { register }, production: true })).toBe("failed");
  });
});

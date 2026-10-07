import { describe, expect, it } from "vitest";

import { fakeServer, ok, refusal, type Reply } from "../testing/fakeServer";
import { type SessionStore, WEBAPP_SESSION_KEY, webAppConnector } from "./webAppSession";

const LAUNCH = "query_id=AAE&user=%7B%22id%22%3A1%7D&auth_date=1791270000&hash=abc123";
const NEXT_LAUNCH = "query_id=AAF&user=%7B%22id%22%3A1%7D&auth_date=1791273600&hash=def456";

/** The web view's storage: lives across page loads until the Mini App is closed. */
function webView(): SessionStore & { items: Map<string, string> } {
  const items = new Map<string, string>();
  return {
    items,
    getItem: (key) => items.get(key) ?? null,
    setItem: (key, value) => void items.set(key, value),
    removeItem: (key) => void items.delete(key),
  };
}

/** A server that, like the real one, accepts one launch string once and refuses it after that. */
function server(extra: (index: number) => Reply | null = () => null) {
  const used = new Set<string>();
  let issued = 0;
  return fakeServer((sent, index) => {
    const reply = extra(index);
    if (reply !== null) {
      return reply;
    }
    const initData = (sent.body as { init_data: string }).init_data;
    if (used.has(initData)) {
      return refusal(401, "UNAUTHENTICATED", "Avval tizimga kiring.");
    }
    used.add(initData);
    issued += 1;
    return ok({ token: `token-${issued}`, expires_at: "2026-10-07T12:00:00+00:00" });
  });
}

describe("the Mini App's session", () => {
  it("is nothing outside Telegram, and nothing is asked of the server", async () => {
    const api = server();
    expect(await webAppConnector(api.fetch, null, webView())()).toBeNull();
    expect(api.sent).toHaveLength(0);
  });

  it("exchanges the launch string once, however often the workspace connects on one page", async () => {
    const api = server();
    const connect = webAppConnector(api.fetch, LAUNCH, webView());
    const [first, second] = await Promise.all([connect(), connect()]);
    expect(first).toEqual({ kind: "bearer", token: "token-1" });
    expect(second).toEqual(first);
    // A retry after a failed start: the string was used, so sending it again would be refused.
    expect(await connect()).toEqual(first);
    expect(api.sent.map((sent) => sent.path)).toEqual(["/api/v1/auth/telegram-webapp"]);
  });

  it("survives the page being loaded again with the same launch data", async () => {
    const api = server();
    const store = webView();
    const before = await webAppConnector(api.fetch, LAUNCH, store)();
    // A new page: nothing in memory, the same web view, the same launch string from Telegram's bridge.
    const after = await webAppConnector(api.fetch, LAUNCH, store)();
    expect(after).toEqual(before);
    expect(api.sent).toHaveLength(1);
  });

  it("signs in anew when Telegram opens the Mini App again, and forgets the earlier token", async () => {
    const api = server();
    const store = webView();
    await webAppConnector(api.fetch, LAUNCH, store)();
    expect(await webAppConnector(api.fetch, NEXT_LAUNCH, store)()).toEqual({ kind: "bearer", token: "token-2" });
    expect(api.sent.map((sent) => (sent.body as { init_data: string }).init_data)).toEqual([LAUNCH, NEXT_LAUNCH]);
    expect(store.items.get(WEBAPP_SESSION_KEY)).toBe(JSON.stringify({ launch: "def456", token: "token-2" }));
  });

  it("keeps the token and the launch's signature, never the launch string, and only in the store it was given", async () => {
    const api = server();
    const store = webView();
    await webAppConnector(api.fetch, LAUNCH, store)();
    expect([...store.items.keys()]).toEqual([WEBAPP_SESSION_KEY]);
    expect(store.items.get(WEBAPP_SESSION_KEY)).toBe(JSON.stringify({ launch: "abc123", token: "token-1" }));
    expect(store.items.get(WEBAPP_SESSION_KEY)).not.toContain("query_id");
  });

  it("does not trust what storage holds when it is not a kept session", async () => {
    for (const junk of ["", "null", "[]", "{", '{"launch":"abc123"}', '{"launch":"abc123","token":7}', '{"launch":"","token":"x"}']) {
      const api = server();
      const store = webView();
      store.items.set(WEBAPP_SESSION_KEY, junk);
      expect(await webAppConnector(api.fetch, LAUNCH, store)()).toEqual({ kind: "bearer", token: "token-1" });
      expect(api.sent).toHaveLength(1);
    }
  });

  it("works without storage, and with storage that throws", async () => {
    const api = server();
    expect(await webAppConnector(api.fetch, LAUNCH, null)()).toEqual({ kind: "bearer", token: "token-1" });
    const broken: SessionStore = {
      getItem: () => {
        throw new Error("denied");
      },
      setItem: () => {
        throw new Error("denied");
      },
      removeItem: () => undefined,
    };
    expect(await webAppConnector(api.fetch, NEXT_LAUNCH, broken)()).toEqual({ kind: "bearer", token: "token-2" });
  });

  it("tries again after a sign-in that never reached the server, and keeps nothing from the failure", async () => {
    const api = server((index) => (index === 0 ? "offline" : null));
    const store = webView();
    const connect = webAppConnector(api.fetch, LAUNCH, store);
    await expect(connect()).rejects.toMatchObject({ code: "NETWORK" });
    expect(store.items.size).toBe(0);
    expect(await connect()).toEqual({ kind: "bearer", token: "token-1" });
  });

  it("passes a refusal on: a used launch string is refused again, and nothing is kept", async () => {
    const api = server((index) => (index === 0 ? refusal(401, "UNAUTHENTICATED", "Avval tizimga kiring.") : null));
    const store = webView();
    await expect(webAppConnector(api.fetch, LAUNCH, store)()).rejects.toMatchObject({ status: 401 });
    expect(store.items.size).toBe(0);
  });
});

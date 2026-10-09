import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { translate } from "../i18n/catalog";
import type { Language } from "../i18n/types";
import { fakeServer, ok, SHOP_ID } from "../testing/fakeServer";
import { ApiError, call, createApi, defaultTimeoutMs, type Fetch, isAbort, NO_ANSWER, TIMEOUT, TIMEOUTS } from "./api";
import { errorText } from "./workspace/parts";

const KEY = "0123456789abcdef";

/** A server that takes the request and never answers; a real `fetch` rejects when its signal aborts. */
function silentServer() {
  const seen: { url: string; init: RequestInit }[] = [];
  const fetch: Fetch = (url, init) => {
    seen.push({ url, init });
    return new Promise<Response>((_resolve, reject) => {
      init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    });
  };
  return { fetch, seen };
}

/** The same, for a transport that does not listen to the signal at all. */
const deafFetch: Fetch = () => new Promise<Response>(() => undefined);

const read = (value: unknown) => value;

/** What a call ended with, held so that the test can move the clock first. */
function outcome(promise: Promise<unknown>): Promise<unknown> {
  return promise.then(
    (value) => ({ value }),
    (error: unknown) => error,
  );
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("the limit of a request that names none", () => {
  it("is 15 s for a read, 30 s for a write, and two minutes for a file either way", () => {
    expect(defaultTimeoutMs({ method: "GET" })).toBe(15_000);
    expect(defaultTimeoutMs({ method: "POST" })).toBe(30_000);
    expect(defaultTimeoutMs({ method: "PATCH" })).toBe(30_000);
    expect(defaultTimeoutMs({ method: "DELETE" })).toBe(30_000);
    expect(defaultTimeoutMs({ method: "POST", form: new FormData() })).toBe(120_000);
    expect(defaultTimeoutMs({ method: "PUT", raw: new Blob(["a"]) })).toBe(120_000);
    expect(defaultTimeoutMs({ method: "GET", binary: true })).toBe(120_000);
    expect(TIMEOUTS).toEqual({ read: 15_000, write: 30_000, transfer: 120_000 });
  });
});

describe("a request that hangs", () => {
  it("is stopped: a read ends as TIMEOUT after 15 s, and the request itself is aborted", async () => {
    const server = silentServer();
    const ended = outcome(call({ fetch: server.fetch, auth: null }, { method: "GET", path: "/api/v1/me/shops", read }));
    await vi.advanceTimersByTimeAsync(14_999);
    expect(server.seen[0]?.init.signal?.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    const error = await ended;
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 0, code: TIMEOUT, serverMessage: null });
    expect(server.seen[0]?.init.signal?.aborted).toBe(true);
    // Not an abort to the caller: a hook that ignores aborts must show this one.
    expect(isAbort(error)).toBe(false);
  });

  it("is not stopped a moment before its limit", async () => {
    const server = silentServer();
    let settled = false;
    void outcome(call({ fetch: server.fetch, auth: null }, { method: "GET", path: "/x", read })).then(() => {
      settled = true;
    });
    await vi.advanceTimersByTimeAsync(14_999);
    expect(settled).toBe(false);
  });

  it("a write waits 30 s and ends as NO_ANSWER, which does not claim that nothing was saved", async () => {
    const server = silentServer();
    const ended = outcome(
      call({ fetch: server.fetch, auth: null }, { method: "POST", path: "/x", body: { amount: 1 }, idempotencyKey: KEY, read }),
    );
    await vi.advanceTimersByTimeAsync(29_999);
    expect(server.seen[0]?.init.signal?.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    expect(await ended).toMatchObject({ status: 0, code: NO_ANSWER });
  });

  it("takes the limit a call names in place of the default", async () => {
    const server = silentServer();
    const ended = outcome(call({ fetch: server.fetch, auth: null }, { method: "GET", path: "/x", timeoutMs: 2_000, read }));
    await vi.advanceTimersByTimeAsync(2_000);
    expect(await ended).toMatchObject({ code: TIMEOUT });
  });

  it("has no limit when the call says zero", async () => {
    const server = silentServer();
    let settled = false;
    void outcome(call({ fetch: server.fetch, auth: null }, { method: "GET", path: "/x", timeoutMs: 0, read })).then(() => {
      settled = true;
    });
    await vi.advanceTimersByTimeAsync(10 * 60_000);
    expect(settled).toBe(false);
    expect(server.seen[0]?.init.signal?.aborted).toBe(false);
  });

  it("ends even when the transport does not listen to the signal", async () => {
    const ended = outcome(call({ fetch: deafFetch, auth: null }, { method: "GET", path: "/x", read }));
    await vi.advanceTimersByTimeAsync(15_000);
    expect(await ended).toMatchObject({ code: TIMEOUT });
  });

  it("ends when the answer's headers came and its body never does", async () => {
    const stalled = { ok: true, status: 200, headers: new Headers(), json: () => new Promise(() => undefined) };
    const fetch: Fetch = () => Promise.resolve(stalled as unknown as Response);
    const ended = outcome(call({ fetch, auth: null }, { method: "GET", path: "/x", read }));
    await vi.advanceTimersByTimeAsync(15_000);
    expect(await ended).toMatchObject({ status: 0, code: TIMEOUT });
  });

  it("an upload is given two minutes", async () => {
    const server = silentServer();
    const ended = outcome(call({ fetch: server.fetch, auth: null }, { method: "POST", path: "/x", raw: new Blob(["rows"]), read }));
    await vi.advanceTimersByTimeAsync(119_999);
    expect(server.seen[0]?.init.signal?.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    expect(await ended).toMatchObject({ code: NO_ANSWER });
  });
});

describe("a request that is answered", () => {
  it("leaves no timer behind and is not touched when the limit passes later", async () => {
    const server = fakeServer(() => ok({ items: [], active_shop: null }));
    const api = createApi({ fetch: server.fetch, auth: { kind: "bearer", token: "t" } });
    await expect(api.myShops()).resolves.toMatchObject({ items: [] });
    expect(vi.getTimerCount()).toBe(0);
    await vi.advanceTimersByTimeAsync(60_000);
  });

  it("leaves no timer behind when it is refused, or never arrives", async () => {
    const refused = fakeServer(() => ({ status: 403, body: { error: { code: "FORBIDDEN", message: "Ruxsat yo'q." } } }));
    await expect(call({ fetch: refused.fetch, auth: null }, { method: "GET", path: "/x", read })).rejects.toMatchObject({
      code: "FORBIDDEN",
    });
    const offline = fakeServer(() => "offline");
    await expect(call({ fetch: offline.fetch, auth: null }, { method: "GET", path: "/x", read })).rejects.toMatchObject({
      code: "NETWORK",
    });
    expect(vi.getTimerCount()).toBe(0);
  });
});

describe("a screen that was left", () => {
  it("still ends as an abort, which nothing shows, and not as a timeout", async () => {
    const server = silentServer();
    const leaving = new AbortController();
    const ended = outcome(
      call({ fetch: server.fetch, auth: null }, { method: "GET", path: "/x", signal: leaving.signal, read }),
    );
    await vi.advanceTimersByTimeAsync(1_000);
    leaving.abort();
    const error = await ended;
    expect(isAbort(error)).toBe(true);
    expect(error).not.toBeInstanceOf(ApiError);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("is aborted at once when the screen had been left before the call", async () => {
    const server = silentServer();
    const leaving = new AbortController();
    leaving.abort();
    const fetch: Fetch = (url, init) =>
      init.signal?.aborted ? Promise.reject(new DOMException("aborted", "AbortError")) : server.fetch(url, init);
    const error = await outcome(call({ fetch, auth: null }, { method: "GET", path: "/x", signal: leaving.signal, read }));
    expect(isAbort(error)).toBe(true);
  });
});

describe("a write that timed out and is sent again", () => {
  it("carries the same Idempotency-Key, so the server answers what it already applied", async () => {
    // The first request reaches the server, which applies it; the answer never reaches the page.
    const applied = new Map<string, { id: string }>();
    const keys: string[] = [];
    let attempt = 0;
    const fetch: Fetch = (_url, init) => {
      attempt += 1;
      const key = (init.headers as Record<string, string>)["Idempotency-Key"] ?? "";
      keys.push(key);
      // As the server does (application/idempotency.py): one result for a key, returned on a repeat.
      const stored = applied.get(key) ?? { id: `entry-${applied.size + 1}` };
      applied.set(key, stored);
      if (attempt === 1) {
        return new Promise<Response>(() => undefined);
      }
      return Promise.resolve(new Response(JSON.stringify(stored), { status: 200 }));
    };
    const send = () =>
      call({ fetch, auth: null }, { method: "POST", path: `/api/v1/shops/${SHOP_ID}/x`, body: { amount: 45000 }, idempotencyKey: KEY, read });

    const first = outcome(send());
    await vi.advanceTimersByTimeAsync(30_000);
    expect(await first).toMatchObject({ code: NO_ANSWER });

    await expect(send()).resolves.toEqual({ id: "entry-1" });
    expect(keys).toEqual([KEY, KEY]);
    expect(applied.size).toBe(1);
  });
});

describe("what the person reads", () => {
  const t = (language: Language) => (key: Parameters<typeof translate>[1], params?: Parameters<typeof translate>[2]) =>
    translate(language, key, params);

  it("a read that timed out: nothing was saved, try again", () => {
    expect(errorText(new ApiError(0, TIMEOUT, null), t("uz"))).toBe(
      "So'rov juda uzoq davom etdi va to'xtatildi. Hech narsa saqlanmadi. Qayta urinib ko'ring.",
    );
  });

  it("a write that timed out: it may have been saved, and the words do not deny it", () => {
    const uz = errorText(new ApiError(0, NO_ANSWER, null), t("uz"));
    expect(uz).toBe("Javob o'z vaqtida kelmadi. Saqlangan bo'lishi ham mumkin: avval tekshirib ko'ring, so'ng qayta urining.");
    expect(uz).not.toContain("saqlanmadi");
    expect(errorText(new ApiError(0, NO_ANSWER, null), t("ru"))).toBe(
      "Ответ не пришёл вовремя. Возможно, изменение уже сохранено: сначала проверьте, затем повторите попытку.",
    );
  });
});

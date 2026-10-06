import { describe, expect, it, vi } from "vitest";

import { customerBody, fakeServer, NO_OVERDUE, ok, refusal, SHOP_BASE, SHOP_ID } from "../testing/fakeServer";
import { ApiError, BAD_RESPONSE, createApi, NETWORK_ERROR, newIdempotencyKey, signInWebApp } from "./api";

const KEY = "0123456789abcdef";
const CUSTOMER_ID = "11111111-1111-4111-8111-111111111111";

function bearerShop(fetch: Parameters<typeof createApi>[0]["fetch"], onUnauthenticated?: () => void) {
  const options = { fetch, auth: { kind: "bearer", token: "session-token" } as const };
  return createApi(onUnauthenticated ? { ...options, onUnauthenticated } : options).shop(SHOP_ID);
}

async function failure(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
  } catch (error) {
    if (error instanceof ApiError) {
      return error;
    }
    throw error;
  }
  throw new Error("the call did not fail");
}

describe("requests", () => {
  it("sends the Mini App's token as a bearer header and no cookies", async () => {
    const server = fakeServer(() => ok({ items: [], next_cursor: null }));
    const seen: RequestInit[] = [];
    await bearerShop((input, init) => {
      seen.push(init);
      return server.fetch(input, init);
    }).listCustomers({});
    expect(server.sent[0]?.headers["Authorization"]).toBe("Bearer session-token");
    expect(seen[0]?.credentials).toBe("omit");
  });

  it("adds the CSRF token to a cookie session's writes only", async () => {
    const server = fakeServer((sent) => (sent.method === "GET" ? ok({ items: [], active_shop: null }) : ok({})));
    const api = createApi({ fetch: server.fetch, auth: { kind: "cookie", csrfToken: "csrf-1" } });
    await api.myShops();
    await api.setActiveShop(SHOP_ID);
    expect(server.sent[0]?.headers["X-CSRF-Token"]).toBeUndefined();
    expect(server.sent[0]?.headers["Authorization"]).toBeUndefined();
    expect(server.sent[1]?.headers["X-CSRF-Token"]).toBe("csrf-1");
    expect(server.sent[1]).toMatchObject({ method: "PUT", path: "/api/v1/me/active-shop", body: { shop_id: SHOP_ID } });
  });

  it("builds the customer search query and leaves out what is empty", async () => {
    const server = fakeServer(() => ok({ items: [customerBody()], next_cursor: "next-1" }));
    const page = await bearerShop(server.fetch).listCustomers({ q: "Ali aka", status: "archived", cursor: "abc" });
    expect(server.sent[0]).toMatchObject({
      method: "GET",
      path: `${SHOP_BASE}/customers`,
      query: { q: "Ali aka", status: "archived", cursor: "abc" },
    });
    expect(page.nextCursor).toBe("next-1");
    expect(page.items[0]).toEqual({
      id: CUSTOMER_ID,
      displayName: "Ali Valiyev",
      phone: "+998901234567",
      status: "active",
      remindersOff: false,
      balance: 120000,
    });

    await bearerShop(server.fetch).listCustomers({ q: "", cursor: null });
    expect(server.sent[1]?.query).toEqual({});
  });

  it("asks for debtors with the overdue flag spelled as the API reads it", async () => {
    const server = fakeServer(() => ok({ items: [{ ...customerBody(), overdue: NO_OVERDUE }], next_cursor: null }));
    await bearerShop(server.fetch).debtors({ overdue: true });
    await bearerShop(server.fetch).debtors({ overdue: false, cursor: "c2" });
    expect(server.sent.map((sent) => sent.query)).toEqual([{ overdue: "true" }, { overdue: "false", cursor: "c2" }]);
  });

  it("records an entry with a whole amount, the key, and only the fields that were given", async () => {
    const server = fakeServer(() =>
      ok({ entry: { id: "e1", kind: "credit", amount: 45000, promised_date: "2026-11-05" }, customer: customerBody() }, 201),
    );
    const api = bearerShop(server.fetch);
    const recorded = await api.recordEntry(CUSTOMER_ID, { kind: "credit", amount: 45000, note: null, promisedDate: null }, KEY);
    expect(server.sent[0]).toMatchObject({
      method: "POST",
      path: `${SHOP_BASE}/customers/${CUSTOMER_ID}/entries`,
      body: { kind: "credit", amount: 45000 },
    });
    expect(server.sent[0]?.body).toEqual({ kind: "credit", amount: 45000 });
    expect(server.sent[0]?.headers["Idempotency-Key"]).toBe(KEY);
    expect(recorded.customer.balance).toBe(120000);
    expect(recorded.entry.promisedDate).toBe("2026-11-05");

    await api.recordEntry(CUSTOMER_ID, { kind: "credit", amount: 100, note: "non", promisedDate: "2026-10-07" }, KEY);
    expect(server.sent[1]?.body).toEqual({ kind: "credit", amount: 100, note: "non", promised_date: "2026-10-07" });
  });

  it("never sends an amount that is not a whole number", () => {
    const server = fakeServer(() => ok({}));
    const api = bearerShop(server.fetch);
    for (const amount of [45000.5, Number.NaN, Number.POSITIVE_INFINITY, 2 ** 60]) {
      expect(() => api.recordEntry(CUSTOMER_ID, { kind: "credit", amount, note: null, promisedDate: null }, KEY)).toThrow(
        RangeError,
      );
    }
    expect(server.sent).toHaveLength(0);
  });

  it("refuses to send a write with a key the server would not accept", async () => {
    const server = fakeServer(() => ok(customerBody()));
    const api = bearerShop(server.fetch);
    for (const key of ["short", "has space 12345", "x".repeat(129), ""]) {
      await expect(api.setArchived(CUSTOMER_ID, true, key)).rejects.toThrow(RangeError);
    }
    expect(server.sent).toHaveLength(0);
  });

  it("removes a phone with null and leaves untouched fields out of a patch", async () => {
    const server = fakeServer(() => ok(customerBody()));
    const api = bearerShop(server.fetch);
    await api.updateCustomer(CUSTOMER_ID, { phone: null }, KEY);
    await api.updateCustomer(CUSTOMER_ID, { displayName: "Ali aka", remindersOff: true }, KEY);
    expect(server.sent[0]).toMatchObject({ method: "PATCH", path: `${SHOP_BASE}/customers/${CUSTOMER_ID}` });
    expect(server.sent[0]?.body).toEqual({ phone: null });
    expect(server.sent[1]?.body).toEqual({ display_name: "Ali aka", reminders_off: true });
  });

  it("posts archive, unarchive and reversal to their own routes with the key", async () => {
    const server = fakeServer((sent) =>
      sent.path.endsWith("/reversal") ? ok({ entry: {}, customer: customerBody({ balance: 75000 }) }, 201) : ok(customerBody()),
    );
    const api = bearerShop(server.fetch);
    await api.setArchived(CUSTOMER_ID, true, KEY);
    await api.setArchived(CUSTOMER_ID, false, KEY);
    const after = await api.reverseEntry("22222222-2222-4222-8222-222222222222", KEY);
    expect(server.sent.map((sent) => `${sent.method} ${sent.path}`)).toEqual([
      `POST ${SHOP_BASE}/customers/${CUSTOMER_ID}/archive`,
      `POST ${SHOP_BASE}/customers/${CUSTOMER_ID}/unarchive`,
      `POST ${SHOP_BASE}/entries/22222222-2222-4222-8222-222222222222/reversal`,
    ]);
    expect(server.sent.every((sent) => sent.headers["Idempotency-Key"] === KEY)).toBe(true);
    expect(after.balance).toBe(75000);
  });

  it("exchanges the launch string for a token without putting it in the address", async () => {
    const server = fakeServer(() => ok({ token: "issued-token", expires_at: "2026-10-07T00:00:00+00:00" }));
    expect(await signInWebApp(server.fetch, "query_id=AAE&hash=abc")).toBe("issued-token");
    expect(server.sent[0]).toMatchObject({
      method: "POST",
      path: "/api/v1/auth/telegram-webapp",
      query: {},
      body: { init_data: "query_id=AAE&hash=abc" },
    });
  });
});

describe("failures", () => {
  it("carries the server's code, message and fields", async () => {
    const server = fakeServer(() => refusal(422, "VALIDATION", "Ma'lumotlar noto'g'ri kiritilgan.", { amount: "a whole amount" }));
    const error = await failure(bearerShop(server.fetch).overview());
    expect(error).toMatchObject({
      status: 422,
      code: "VALIDATION",
      serverMessage: "Ma'lumotlar noto'g'ri kiritilgan.",
      fields: { amount: "a whole amount" },
    });
  });

  it("reports a request that never arrived as a network failure", async () => {
    const server = fakeServer(() => "offline");
    const error = await failure(bearerShop(server.fetch).overview());
    expect(error).toMatchObject({ status: 0, code: NETWORK_ERROR, serverMessage: null });
  });

  it("copes with an error page that is not the API's JSON", async () => {
    const error = await failure(
      bearerShop(async () => new Response("<html>Bad Gateway</html>", { status: 502 })).overview(),
    );
    expect(error).toMatchObject({ status: 502, code: "ERROR", serverMessage: null });
  });

  it("tells the application when the session is no longer valid", async () => {
    const onUnauthenticated = vi.fn();
    const server = fakeServer(() => refusal(401, "UNAUTHENTICATED", "Avval tizimga kiring."));
    const error = await failure(bearerShop(server.fetch, onUnauthenticated).overview());
    expect(error.code).toBe("UNAUTHENTICATED");
    expect(onUnauthenticated).toHaveBeenCalledTimes(1);
  });

  it("does not report other refusals as a lost session", async () => {
    const onUnauthenticated = vi.fn();
    const server = fakeServer(() => refusal(403, "SHOP_SUSPENDED", "Do'kon to'xtatilgan."));
    await failure(bearerShop(server.fetch, onUnauthenticated).overview());
    expect(onUnauthenticated).not.toHaveBeenCalled();
  });
});

describe("responses", () => {
  const overviewBody = { outstanding: 500000, debtors: 4, overdue: { amount: 120000, customers: 2 }, due_today: 45000 };

  it("reads the overview", async () => {
    const server = fakeServer(() => ok(overviewBody));
    expect(await bearerShop(server.fetch).overview()).toEqual({
      outstanding: 500000,
      debtors: 4,
      overdueAmount: 120000,
      overdueCustomers: 2,
      dueToday: 45000,
    });
  });

  it.each([
    ["a fraction", 500000.5],
    ["text", "500000"],
    ["nothing", null],
    ["a number too large to be exact", 2 ** 60],
  ])("refuses an amount that is %s instead of showing it", async (_name, outstanding) => {
    const server = fakeServer(() => ok({ ...overviewBody, outstanding }));
    const error = await failure(bearerShop(server.fetch).overview());
    expect(error.code).toBe(BAD_RESPONSE);
  });

  it("refuses a customer whose balance is a fraction", async () => {
    const server = fakeServer(() => ok({ items: [customerBody({ balance: 0.1 + 0.2 })], next_cursor: null }));
    const error = await failure(bearerShop(server.fetch).listCustomers({}));
    expect(error.code).toBe(BAD_RESPONSE);
  });

  it("reads a customer with entries newest first, as the server sent them", async () => {
    const server = fakeServer(() =>
      ok({
        ...customerBody(),
        overdue: { amount: 45000, since: "2026-09-20", days: 16, due_today: 0 },
        payment_history: { on_time_percent: 67, on_time_amount: 200000, due_amount: 300000, longest_delay_days: 9 },
        entries: [
          {
            id: "e2",
            seq: 2,
            kind: "payment",
            amount: 20000,
            note: null,
            created_at: "2026-10-01T05:00:00+00:00",
            promised_date: null,
            reverses_id: null,
            reversed: false,
            disputed: false,
            author_id: "m1",
          },
        ],
        entries_total: 1,
      }),
    );
    const detail = await bearerShop(server.fetch).readCustomer(CUSTOMER_ID);
    expect(server.sent[0]?.path).toBe(`${SHOP_BASE}/customers/${CUSTOMER_ID}`);
    expect(detail.overdue).toEqual({ amount: 45000, since: "2026-09-20", days: 16, dueToday: 0 });
    expect(detail.paymentHistory?.onTimePercent).toBe(67);
    expect(detail.entries).toHaveLength(1);
    expect(detail.entries[0]).toMatchObject({ id: "e2", kind: "payment", amount: 20000, reversed: false });
  });

  it("reads the user's shops and refuses a role it does not know", async () => {
    const shops = fakeServer(() => ok({ items: [{ shop_id: SHOP_ID, name: "Baraka", role: "manager" }], active_shop: SHOP_ID }));
    const auth = { kind: "bearer", token: "t" } as const;
    expect(await createApi({ fetch: shops.fetch, auth }).myShops()).toEqual({
      items: [{ shopId: SHOP_ID, name: "Baraka", role: "manager" }],
      activeShop: SHOP_ID,
    });
    const odd = fakeServer(() => ok({ items: [{ shop_id: SHOP_ID, name: "Baraka", role: "admin" }], active_shop: null }));
    expect((await failure(createApi({ fetch: odd.fetch, auth }).myShops())).code).toBe(BAD_RESPONSE);
  });
});

describe("newIdempotencyKey", () => {
  it("makes keys the server accepts, each one different", () => {
    const keys = Array.from({ length: 200 }, () => newIdempotencyKey());
    expect(keys.every((key) => /^[A-Za-z0-9_-]{8,128}$/.test(key))).toBe(true);
    expect(new Set(keys).size).toBe(keys.length);
  });

  it("still works where randomUUID is missing", () => {
    const original = crypto.randomUUID;
    try {
      // An insecure context has getRandomValues but no randomUUID.
      Object.defineProperty(crypto, "randomUUID", { value: undefined, configurable: true });
      const key = newIdempotencyKey();
      expect(key).toMatch(/^[0-9a-f]{32}$/);
      expect(newIdempotencyKey()).not.toBe(key);
    } finally {
      Object.defineProperty(crypto, "randomUUID", { value: original, configurable: true });
    }
  });
});

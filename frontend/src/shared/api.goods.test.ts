import { describe, expect, it } from "vitest";

import {
  customerBody,
  detailBody,
  entryBody,
  fakeServer,
  itemBody,
  lineBody,
  ok,
  refusal,
  settingsBody,
  SHOP_BASE,
  SHOP_ID,
} from "../testing/fakeServer";
import { ApiError, BAD_RESPONSE, createApi, type NewLine } from "./api";

const KEY = "0123456789abcdef";
const CUSTOMER_ID = "11111111-1111-4111-8111-111111111111";
const ENTRY_ID = "22222222-2222-4222-8222-222222222222";
const ITEM_ID = "44444444-4444-4444-8444-444444444441";
const OTHER_ITEM = "44444444-4444-4444-8444-444444444442";

const shopApi = (fetch: Parameters<typeof createApi>[0]["fetch"]) =>
  createApi({ fetch, auth: { kind: "bearer", token: "session-token" } }).shop(SHOP_ID);

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

const LINES: NewLine[] = [
  { catalogItemId: ITEM_ID, qty: 1500, unitPrice: 25000 },
  { name: "Qora choy", unit: "quti", qty: 1000, unitPrice: 18000 },
  { name: "Gugurt", unit: null, qty: 125, unitPrice: 4 },
];

describe("goods lines", () => {
  it("sends lines without an amount: quantities as decimal strings, prices as whole numbers", async () => {
    const server = fakeServer(() =>
      ok({ entry: { id: ENTRY_ID, kind: "credit", amount: 55501, promised_date: "2026-11-05", lines: [lineBody({ qty: "1.500" })] }, customer: customerBody() }, 201),
    );
    const recorded = await shopApi(server.fetch).recordEntry(CUSTOMER_ID, { kind: "credit", lines: LINES, note: null, promisedDate: null }, KEY);
    expect(server.sent[0]).toMatchObject({ method: "POST", path: `${SHOP_BASE}/customers/${CUSTOMER_ID}/entries` });
    expect(server.sent[0]?.body).toEqual({
      kind: "credit",
      lines: [
        { catalog_item_id: ITEM_ID, qty: "1.5", unit_price: 25000 },
        { name: "Qora choy", unit: "quti", qty: "1", unit_price: 18000 },
        { name: "Gugurt", qty: "0.125", unit_price: 4 },
      ],
    });
    expect(server.sent[0]?.headers["Idempotency-Key"]).toBe(KEY);
    expect(recorded.entry.lines).toEqual([
      { lineNo: 1, catalogItemId: ITEM_ID, name: "Non", qty: 1500, unit: "dona", unitPrice: 4000, lineTotal: 8000 },
    ]);
  });

  it("never sends a line the server would refuse, a payment with lines, or more than fifty lines", async () => {
    const server = fakeServer(() => ok({ entry: { id: ENTRY_ID, amount: 200000, lines: [] } }, 201));
    const api = shopApi(server.fetch);
    const one: NewLine = { catalogItemId: ITEM_ID, qty: 1000, unitPrice: 4000 };
    const bad: NewLine[][] = [
      [],
      Array<NewLine>(51).fill(one),
      [{ ...one, qty: 0 }],
      [{ ...one, qty: 1.5 }],
      [{ ...one, qty: -1000 }],
      [{ ...one, unitPrice: 0 }],
      [{ ...one, unitPrice: 4000.5 }],
      [{ ...one, unitPrice: 100_000_001 }],
      [{ ...one, qty: 1, unitPrice: 100 }], // 0.1 UZS
    ];
    for (const lines of bad) {
      expect(() => api.recordEntry(CUSTOMER_ID, { kind: "credit", lines, note: null, promisedDate: null }, KEY)).toThrow(RangeError);
      expect(() => api.addLines(ENTRY_ID, lines, KEY)).toThrow(RangeError);
    }
    expect(() => api.recordEntry(CUSTOMER_ID, { kind: "payment", lines: [one], note: null, promisedDate: null }, KEY)).toThrow(RangeError);
    expect(server.sent).toHaveLength(0);
    // Fifty is allowed.
    await api.addLines(ENTRY_ID, Array<NewLine>(50).fill(one), KEY);
    expect((server.sent[0]?.body as { lines: unknown[] }).lines).toHaveLength(50);
  });

  it("adds lines to an entry and reads what was saved", async () => {
    const server = fakeServer(() => ok({ entry: { id: ENTRY_ID, amount: 8000, lines: [lineBody()] } }, 201));
    const added = await shopApi(server.fetch).addLines(ENTRY_ID, [{ catalogItemId: ITEM_ID, qty: 2000, unitPrice: 4000 }], KEY);
    expect(server.sent[0]).toMatchObject({ method: "POST", path: `${SHOP_BASE}/entries/${ENTRY_ID}/lines` });
    expect(server.sent[0]?.body).toEqual({ lines: [{ catalog_item_id: ITEM_ID, qty: "2", unit_price: 4000 }] });
    expect(server.sent[0]?.headers["Idempotency-Key"]).toBe(KEY);
    expect(added).toEqual({
      id: ENTRY_ID,
      amount: 8000,
      lines: [{ lineNo: 1, catalogItemId: ITEM_ID, name: "Non", qty: 2000, unit: "dona", unitPrice: 4000, lineTotal: 8000 }],
    });
  });

  it("reads each entry's lines and author, and an entry without the field as one without goods", async () => {
    const old: Record<string, unknown> = entryBody({ id: "old" });
    delete old["lines"];
    delete old["author_id"];
    const server = fakeServer(() =>
      ok(detailBody({ entries: [entryBody({ lines: [lineBody({ qty: "0.125", catalog_item_id: null })] }), old], entries_total: 2 })),
    );
    const detail = await shopApi(server.fetch).readCustomer(CUSTOMER_ID);
    expect(detail.entries[0]?.authorId).toBe("33333333-3333-4333-8333-333333333333");
    expect(detail.entries[0]?.lines[0]).toMatchObject({ qty: 125, catalogItemId: null });
    expect(detail.entries[1]?.lines).toEqual([]);
    expect(detail.entries[1]?.authorId).toBeNull();
  });

  it.each([
    ["a quantity that is a number", { qty: 2 }],
    ["a quantity with four decimals", { qty: "1.2345" }],
    ["a quantity with a comma", { qty: "1,5" }],
    ["a zero quantity", { qty: "0" }],
    ["a fractional line total", { line_total: 8000.5 }],
    ["a fractional price", { unit_price: 4000.5 }],
    ["a missing name", { name: null }],
  ])("refuses a line with %s", async (_what, overrides) => {
    const server = fakeServer(() => ok(detailBody({ entries: [entryBody({ lines: [lineBody(overrides)] })] })));
    expect((await failure(shopApi(server.fetch).readCustomer(CUSTOMER_ID))).code).toBe(BAD_RESPONSE);
  });
});

describe("promise choice", () => {
  it("posts the chosen date with the key and reads the date the server kept", async () => {
    const server = fakeServer(() => ok({ entry: { id: ENTRY_ID, amount: 45000, promised_date: "2026-10-07" }, customer: customerBody() }));
    const chosen = await shopApi(server.fetch).choosePromise(ENTRY_ID, "2026-10-07", KEY);
    expect(server.sent[0]).toMatchObject({ method: "POST", path: `${SHOP_BASE}/entries/${ENTRY_ID}/promise-choice` });
    expect(server.sent[0]?.body).toEqual({ promised_date: "2026-10-07" });
    expect(server.sent[0]?.headers["Idempotency-Key"]).toBe(KEY);
    expect(chosen).toEqual({ id: ENTRY_ID, amount: 45000, promisedDate: "2026-10-07" });
  });

  it("passes on the server's refusal", async () => {
    const server = fakeServer(() => refusal(409, "PROMISE_ALREADY_SET", "Muddat allaqachon belgilangan."));
    const error = await failure(shopApi(server.fetch).choosePromise(ENTRY_ID, "2026-10-07", KEY));
    expect([error.status, error.code, error.serverMessage]).toEqual([409, "PROMISE_ALREADY_SET", "Muddat allaqachon belgilangan."]);
  });
});

describe("catalog", () => {
  it("builds the list query, keeping learned=false and leaving out what is empty", async () => {
    const server = fakeServer(() => ok({ items: [itemBody({ learned: true, status: "hidden", merged_into: OTHER_ITEM })], next_cursor: "n1" }));
    const api = shopApi(server.fetch);
    const page = await api.listCatalog({ q: "non", status: "hidden", learned: false, cursor: "c1", limit: 10 });
    await api.listCatalog({ q: "", learned: true, cursor: null });
    await api.listCatalog({});
    expect(server.sent.map((sent) => sent.query)).toEqual([
      { q: "non", status: "hidden", learned: "false", cursor: "c1", limit: "10" },
      { learned: "true" },
      {},
    ]);
    expect(server.sent[0]).toMatchObject({ method: "GET", path: `${SHOP_BASE}/catalog` });
    expect(page.nextCursor).toBe("n1");
    expect(page.items[0]).toEqual({ id: ITEM_ID, name: "Non", unit: "dona", price: 4000, learned: true, status: "hidden", mergedInto: OTHER_ITEM });
  });

  it("creates, changes, hides, shows, accepts, dismisses and merges, each with the key", async () => {
    const server = fakeServer(() => ok(itemBody()));
    const api = shopApi(server.fetch);
    await api.createCatalogItem({ name: "Non", unit: null, price: 4000 }, KEY);
    await api.createCatalogItem({ name: "Sut", unit: "l", price: 12000 }, KEY);
    await api.updateCatalogItem(ITEM_ID, { price: 4500 }, KEY);
    await api.updateCatalogItem(ITEM_ID, { name: "Buxanka", unit: "" }, KEY);
    for (const action of ["hide", "unhide", "accept", "dismiss"] as const) {
      await api.changeCatalogItem(ITEM_ID, action, KEY);
    }
    await api.mergeCatalogItem(ITEM_ID, OTHER_ITEM, KEY);
    const base = `${SHOP_BASE}/catalog`;
    expect(server.sent.map((sent) => [sent.method, sent.path, sent.body])).toEqual([
      ["POST", base, { name: "Non", price: 4000 }],
      ["POST", base, { name: "Sut", unit: "l", price: 12000 }],
      ["PATCH", `${base}/${ITEM_ID}`, { price: 4500 }],
      ["PATCH", `${base}/${ITEM_ID}`, { name: "Buxanka", unit: "" }],
      ["POST", `${base}/${ITEM_ID}/hide`, undefined],
      ["POST", `${base}/${ITEM_ID}/unhide`, undefined],
      ["POST", `${base}/${ITEM_ID}/accept`, undefined],
      ["POST", `${base}/${ITEM_ID}/dismiss`, undefined],
      ["POST", `${base}/${ITEM_ID}/merge`, { into: OTHER_ITEM }],
    ]);
    expect(server.sent.every((sent) => sent.headers["Idempotency-Key"] === KEY)).toBe(true);
  });

  it("never sends a price that is not a whole number", () => {
    const server = fakeServer(() => ok(itemBody()));
    const api = shopApi(server.fetch);
    for (const price of [4000.5, Number.NaN, 2 ** 60]) {
      expect(() => api.createCatalogItem({ name: "Non", unit: null, price }, KEY)).toThrow(RangeError);
      expect(() => api.updateCatalogItem(ITEM_ID, { price }, KEY)).toThrow(RangeError);
    }
    expect(server.sent).toHaveLength(0);
  });

  it.each([
    ["a fractional price", { price: 4000.5 }],
    ["a price written as text", { price: "4000" }],
    ["an unknown status", { status: "deleted" }],
    ["a learned flag that is not a flag", { learned: "yes" }],
  ])("refuses an item with %s", async (_what, overrides) => {
    const server = fakeServer(() => ok({ items: [itemBody(overrides)], next_cursor: null }));
    expect((await failure(shopApi(server.fetch).listCatalog({}))).code).toBe(BAD_RESPONSE);
  });
});

describe("shop settings", () => {
  it("reads the settings and patches only what was given", async () => {
    const server = fakeServer(() => ok(settingsBody()));
    const api = shopApi(server.fetch);
    expect(await api.readSettings()).toEqual({ id: SHOP_ID, name: "Baraka savdo", lang: "uz", defaultPromiseDays: 30 });
    await api.updateSettings({ defaultPromiseDays: 14 }, KEY);
    await api.updateSettings({ name: "Ziyo", lang: "ru" }, KEY);
    expect(server.sent.map((sent) => [sent.method, sent.path, sent.body])).toEqual([
      ["GET", SHOP_BASE, undefined],
      ["PATCH", SHOP_BASE, { default_promise_days: 14 }],
      ["PATCH", SHOP_BASE, { name: "Ziyo", lang: "ru" }],
    ]);
    expect(server.sent[1]?.headers["Idempotency-Key"]).toBe(KEY);
  });

  it("never sends a day count that is not a whole number, and refuses one in an answer", async () => {
    const server = fakeServer(() => ok(settingsBody({ default_promise_days: 30.5 })));
    const api = shopApi(server.fetch);
    expect(() => api.updateSettings({ defaultPromiseDays: 14.5 }, KEY)).toThrow(RangeError);
    expect(server.sent).toHaveLength(0);
    expect((await failure(api.readSettings())).code).toBe(BAD_RESPONSE);
  });
});

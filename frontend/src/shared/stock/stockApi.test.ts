import { describe, expect, it } from "vitest";

import { fakeServer, ok, refusal, SHOP_BASE, SHOP_ID } from "../../testing/fakeServer";
import { type ApiError, BAD_RESPONSE, createApi } from "../api";
import { documentBody as requestBody, stockOf } from "./stockApi";
import {
  costedItemBody,
  DOCUMENT_ID,
  documentBody,
  EAN,
  ENTRY_ID,
  ITEM_ID,
  STOCK,
  stockItemBody,
  stockSettingsBody,
  SUPPLIER_ID,
  supplierBody,
  supplierEntryBody,
  SUPPLIERS,
} from "./testing";

const auth = { kind: "bearer", token: "t" } as const;
const KEY = "key-0000-0000-0001";

function client(server: ReturnType<typeof fakeServer>) {
  const api = createApi({ fetch: server.fetch, auth }).shop(SHOP_ID);
  return { api, stock: stockOf(api) };
}

async function failure(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
  } catch (error) {
    return error as ApiError;
  }
  throw new Error("expected a failure");
}

describe("whether the stock exists", () => {
  const body = { items: [], active_shop: null };

  it("is learned from a header of the person's shops, and only from the word 'on'", async () => {
    const on = fakeServer(() => ({ status: 200, body, headers: { "X-Qarz-Stock": "on" } }));
    expect((await createApi({ fetch: on.fetch, auth }).myShops()).stockOn).toBe(true);
    for (const headers of [{}, { "X-Qarz-Stock": "off" }, { "X-Qarz-Stock": "true" }, { "X-Qarz-Stock": "" }, { "X-Qarz-Cash-Book": "on" }]) {
      const other = fakeServer(() => ({ status: 200, body, headers }));
      expect((await createApi({ fetch: other.fetch, auth }).myShops()).stockOn, JSON.stringify(headers)).toBe(false);
    }
  });

  it("is told apart from the cash book: each has its own header", async () => {
    const both = fakeServer(() => ({ status: 200, body, headers: { "X-Qarz-Stock": "on", "X-Qarz-Cash-Book": "on" } }));
    expect(await createApi({ fetch: both.fetch, auth }).myShops()).toMatchObject({ stockOn: true, cashBookOn: true });
  });

  it("reads whether the shop keeps a cash book from the stock's settings, and takes silence as no", async () => {
    expect((await client(fakeServer(() => ok(stockSettingsBody()))).stock.settings()).cashBook).toBe(false);
    expect((await client(fakeServer(() => ok(stockSettingsBody({ cash_book: true })))).stock.settings()).cashBook).toBe(true);
    expect((await client(fakeServer(() => ok(stockSettingsBody({ cash_book: "yes" })))).stock.settings()).cashBook).toBe(false);
  });
});

describe("reading the stock", () => {
  it("reads an item without cost as having none: the key is absent, not a zero", async () => {
    const { stock } = client(fakeServer(() => ok(stockItemBody({ on_hand: "-2.5", low_stock: "5", low: true }))));
    const item = await stock.item(ITEM_ID);
    expect(item).toEqual({
      id: ITEM_ID,
      name: "Shakar",
      unit: "kg",
      price: 15000,
      status: "active",
      learned: false,
      tracked: true,
      onHand: "-2.5",
      lowStock: "5",
      low: true,
      barcodes: [EAN],
      lastSaleAt: null,
    });
    expect("cost" in item).toBe(false);
  });

  it("reads the cost when the server sent it", async () => {
    const { stock } = client(fakeServer(() => ok(costedItemBody())));
    expect((await stock.item(ITEM_ID)).cost).toEqual({ currency: "UZS", average: 12000, value: 90000, margin: 3000 });
  });

  it.each([
    ["a quantity that is a number, not the API's decimal string", stockItemBody({ on_hand: 7.5 })],
    ["a quantity with four decimals", stockItemBody({ on_hand: "7.5001" })],
    ["a price that is not whole", stockItemBody({ price: 15000.5 })],
    ["a cost in a currency it does not know", costedItemBody({ cost: { currency: "EUR", average: 1, value: 1, margin: 1 } })],
  ])("refuses %s rather than showing it", async (_name, body) => {
    const { stock } = client(fakeServer(() => ok(body)));
    expect((await failure(stock.item(ITEM_ID))).code).toBe(BAD_RESPONSE);
  });

  it("looks a code up as a query parameter and passes a 404 on as it is", async () => {
    const server = fakeServer((sent) => (sent.query["code"] === EAN ? ok(stockItemBody()) : refusal(404, "NOT_FOUND", "Topilmadi.")));
    const { stock } = client(server);
    expect((await stock.lookup(EAN)).id).toBe(ITEM_ID);
    expect(server.sent[0]).toMatchObject({ method: "GET", path: `${STOCK}/lookup`, query: { code: EAN } });
    expect((await failure(stock.lookup("73513537"))).status).toBe(404);
  });

  it("lists items by filter, words and cursor", async () => {
    const server = fakeServer(() => ok({ items: [stockItemBody()], next_cursor: "c2" }));
    const page = await client(server).stock.items({ q: "sha", filter: "low", cursor: "c1" });
    expect(page.nextCursor).toBe("c2");
    expect(server.sent[0]?.query).toEqual({ q: "sha", filter: "low", cursor: "c1" });
  });

  it("reads a document's money only when it is there", async () => {
    const withMoney = await client(fakeServer(() => ok(documentBody()))).stock.document(DOCUMENT_ID);
    expect(withMoney.money).toEqual({ currency: "UZS", total: 24000, paid: 24000 });
    expect(withMoney.lines[0]).toEqual({ lineNo: 1, item: { id: ITEM_ID, name: "Shakar", unit: "kg" }, qty: "2", unitCost: 12000, lineTotal: 24000 });
    const hidden = documentBody({ currency: undefined, total: undefined, paid: undefined, lines: [{ line_no: 1, item: { id: ITEM_ID, name: "Shakar", unit: "kg" }, qty: "2" }] });
    const without = await client(fakeServer(() => ok(hidden))).stock.document(DOCUMENT_ID);
    expect("money" in without).toBe(false);
    expect("unitCost" in (without.lines[0] ?? {})).toBe(false);
  });

  it("refuses a document of a kind or a state it does not know", async () => {
    for (const body of [documentBody({ kind: "gift" }), documentBody({ status: "lost" })]) {
      expect((await failure(client(fakeServer(() => ok(body))).stock.document(DOCUMENT_ID))).code).toBe(BAD_RESPONSE);
    }
  });

  it("reads the suppliers with a balance for each currency and the totals apart", async () => {
    const server = fakeServer(() =>
      ok({
        suppliers: [supplierBody({ balances: [{ currency: "UZS", balance: 500000 }, { currency: "USD", balance: -2500 }] })],
        totals: [{ currency: "UZS", owed: 500000 }, { currency: "USD", owed: 0 }],
        next_cursor: null,
      }),
    );
    const list = await client(server).stock.suppliers({ status: "active" });
    expect(list.suppliers[0]?.balances).toEqual([
      { currency: "UZS", balance: 500000 },
      { currency: "USD", balance: -2500 },
    ]);
    expect(list.totals).toEqual([
      { currency: "UZS", owed: 500000 },
      { currency: "USD", owed: 0 },
    ]);
  });
});

describe("writing", () => {
  it("builds a document's body without the keys that do not apply: the server refuses extras", () => {
    expect(
      requestBody({ kind: "write_off", docDate: "2026-10-06", reason: "expired", note: null, supplierId: null, lines: [{ itemId: ITEM_ID, qty: "1.5" }] }),
    ).toEqual({ kind: "write_off", doc_date: "2026-10-06", reason: "expired", lines: [{ item_id: ITEM_ID, qty: "1.5" }] });
    expect(
      requestBody({
        kind: "receipt",
        paid: 0,
        currency: "USD",
        lines: [{ qty: "2", unitCost: 0, newItem: { name: "Choy", unit: "dona", price: 15000, barcode: null } }],
      }),
    ).toEqual({
      kind: "receipt",
      // Nothing paid is said, not left out: with a supplier it means "on credit".
      paid: 0,
      currency: "USD",
      lines: [{ qty: "2", unit_cost: 0, new_item: { name: "Choy", unit: "dona", price: 15000 } }],
    });
    // How it was paid is said only when the caller names it.
    expect(requestBody({ kind: "receipt", method: "card", lines: [] })).toEqual({ kind: "receipt", method: "card", lines: [] });
    expect("method" in requestBody({ kind: "receipt", method: null, lines: [] })).toBe(false);
  });

  it("sends every write with its idempotency key", async () => {
    const server = fakeServer((sent) => {
      if (sent.path.includes("/suppliers")) {
        return sent.path.includes("/entries") ? ok({ entry: supplierEntryBody(), supplier: supplierBody() }) : ok(supplierBody());
      }
      if (sent.path.endsWith("/settings")) {
        return ok(stockSettingsBody());
      }
      return sent.path.includes("/documents") ? ok(documentBody()) : ok(stockItemBody());
    });
    const { stock } = client(server);
    await stock.updateSettings(true, KEY);
    await stock.updateItem(ITEM_ID, { tracked: false, lowStock: null }, KEY);
    await stock.createDocument({ kind: "stocktake", lines: [{ itemId: ITEM_ID, qty: "0" }] }, false, KEY);
    await stock.updateDocument(DOCUMENT_ID, { kind: "stocktake", lines: [{ itemId: ITEM_ID, qty: "3" }] }, KEY);
    await stock.postDocument(DOCUMENT_ID, KEY);
    await stock.cancelDocument(DOCUMENT_ID, "xato", KEY);
    await stock.createSupplier({ name: "Baraka", phone: null, note: null }, KEY);
    await stock.updateSupplier(SUPPLIER_ID, { name: "Baraka", phone: "+998901112233", note: "ulgurji" }, KEY);
    await stock.setArchived(SUPPLIER_ID, true, KEY);
    await stock.setArchived(SUPPLIER_ID, false, KEY);
    await stock.addEntry(SUPPLIER_ID, { kind: "opening", amount: 5, currency: "UZS", note: null }, KEY);
    await stock.cancelEntry(SUPPLIER_ID, ENTRY_ID, "xato", KEY);
    expect(server.sent.map((sent) => [sent.method, sent.path.replace(SHOP_BASE, ""), sent.body])).toEqual([
      ["PUT", "/stock/settings", { refuse_negative: true }],
      ["PATCH", `/stock/items/${ITEM_ID}`, { tracked: false, clear_low_stock: true }],
      ["POST", "/stock/documents", { kind: "stocktake", lines: [{ item_id: ITEM_ID, qty: "0" }] }],
      ["PUT", `/stock/documents/${DOCUMENT_ID}`, { kind: "stocktake", lines: [{ item_id: ITEM_ID, qty: "3" }] }],
      ["POST", `/stock/documents/${DOCUMENT_ID}/post`, undefined],
      ["POST", `/stock/documents/${DOCUMENT_ID}/cancel`, { reason: "xato" }],
      ["POST", "/suppliers", { name: "Baraka" }],
      ["PUT", `/suppliers/${SUPPLIER_ID}`, { name: "Baraka", phone: "+998901112233", note: "ulgurji" }],
      ["POST", `/suppliers/${SUPPLIER_ID}/archive`, undefined],
      ["POST", `/suppliers/${SUPPLIER_ID}/unarchive`, undefined],
      ["POST", `/suppliers/${SUPPLIER_ID}/entries`, { kind: "opening", amount: 5, currency: "UZS" }],
      ["POST", `/suppliers/${SUPPLIER_ID}/entries/${ENTRY_ID}/cancel`, { reason: "xato" }],
    ]);
    expect(server.sent.every((sent) => sent.headers["Idempotency-Key"] === KEY)).toBe(true);
    expect(SUPPLIERS).toBe(`${SHOP_BASE}/suppliers`);
  });

  it("sends no key with a read", async () => {
    const server = fakeServer(() => ok(stockSettingsBody()));
    await client(server).stock.settings();
    expect("Idempotency-Key" in (server.sent[0]?.headers ?? {})).toBe(false);
  });
});

describe("a sale's answer", () => {
  const recorded = (extra: Record<string, unknown>) => ({
    entry: { id: "e1", kind: "credit", amount: 45000, promised_date: null, lines: [] },
    customer: { id: "c1", display_name: "Ali", phone: null, status: "active", reminders_off: false, credit_limit: null, balance: 45000 },
    ...extra,
  });

  it("carries the stock's warnings when the server sent some, and no key at all when it did not", async () => {
    const warned = fakeServer(() =>
      ok(recorded({ stock_warnings: [{ kind: "negative", item: ITEM_ID, name: "Shakar", on_hand: "-1" }, { kind: "unit", item: ITEM_ID, name: "Shakar", unit: "kg" }] }), 201),
    );
    const answer = await client(warned).api.recordEntry("c1", { kind: "credit", amount: 45000, note: null, promisedDate: null }, KEY);
    expect(answer.stockWarnings).toEqual([
      { kind: "negative", itemId: ITEM_ID, name: "Shakar", onHand: "-1" },
      { kind: "unit", itemId: ITEM_ID, name: "Shakar", unit: "kg" },
    ]);
    const plain = await client(fakeServer(() => ok(recorded({}), 201))).api.recordEntry(
      "c1",
      { kind: "credit", amount: 45000, note: null, promisedDate: null },
      KEY,
    );
    expect("stockWarnings" in plain).toBe(false);
  });

  it("refuses a warning whose quantity is not one", async () => {
    const odd = fakeServer(() => ok(recorded({ stock_warnings: [{ kind: "negative", item: ITEM_ID, name: "Shakar", on_hand: "few" }] }), 201));
    const error = await failure(client(odd).api.recordEntry("c1", { kind: "credit", amount: 45000, note: null, promisedDate: null }, KEY));
    expect(error.code).toBe(BAD_RESPONSE);
  });
});

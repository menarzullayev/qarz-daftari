import { describe, expect, it } from "vitest";

import { fakeServer, ok, SHOP_BASE, SHOP_ID } from "../../testing/fakeServer";
import { createApi } from "../api";
import { cashOf } from "./cashApi";
import { category, dayBody, entry, line, summaryBody, total } from "./testing";

const KEY = "cash-test-key-0001";

function calls(reply: (path: string) => unknown) {
  const server = fakeServer((sent) => ok(reply(sent.path), sent.method === "POST" ? 201 : 200));
  const api = createApi({ fetch: server.fetch, auth: { kind: "bearer", token: "test-session" } }).shop(SHOP_ID);
  return { server, cash: cashOf(api) };
}

describe("reading the cash book", () => {
  it("reads a day: the balances of each method, the totals of each currency, the entries", async () => {
    const { server, cash } = calls(() => dayBody());
    const day = await cash.day("2026-10-06", null);
    expect(server.sent[0]).toMatchObject({ method: "GET", path: `${SHOP_BASE}/cash/day`, query: { date: "2026-10-06" } });
    expect(day.balances[0]).toEqual({
      currency: "UZS",
      method: "cash",
      opening: 100000,
      income: 500000,
      expense: 300000,
      closing: 300000,
      count: 2,
    });
    expect(day.totals).toEqual([
      { currency: "UZS", opening: 100000, income: 620000, expense: 300000, closing: 420000, count: 3 },
    ]);
    expect(day.entries[0]).toMatchObject({
      direction: "income",
      method: "card",
      amount: 120000,
      source: "manual",
      customer: null,
      cancelled: null,
      category: { name: "Savdo" },
    });
    expect(day.nextCursor).toBeNull();
  });

  it("asks for today when no day is named, and passes a cursor on", async () => {
    const { server, cash } = calls(() => dayBody());
    await cash.day(null, null);
    await cash.day("2026-10-05", "abc");
    expect(server.sent[0]?.query).toEqual({});
    expect(server.sent[1]?.query).toEqual({ date: "2026-10-05", cursor: "abc" });
  });

  it("reads a balance below zero: the book records what was written into it", async () => {
    const below = line("cash", { opening: 0, income: 0, expense: 50000, closing: -50000, count: 1 });
    const { cash } = calls(() => dayBody({ balances: [below], totals: [total({ ...below })] }));
    expect((await cash.day(null, null)).balances[0]?.closing).toBe(-50000);
  });

  it("reads a customer's payment with whose it is, and a cancellation with its reason", async () => {
    const paid = entry({
      source: "ledger",
      customer: { id: "c-1", display_name: "Ali Valiyev" },
      cancelled: { at: "2026-10-06T08:00:00+00:00", by: "m-2", reason: null },
    });
    const { cash } = calls(() => dayBody({ entries: [paid] }));
    const read = (await cash.day(null, null)).entries[0];
    expect(read).toMatchObject({
      source: "ledger",
      customer: { id: "c-1", displayName: "Ali Valiyev" },
      cancelled: { by: "m-2", reason: null },
    });
  });

  it.each([
    ["a balance that does not add up", dayBody({ balances: [line("cash", { closing: 1 })] })],
    ["a total that does not add up", dayBody({ totals: [total({ closing: 7 })] })],
    ["a method the contract does not name", dayBody({ balances: [line("cheque")] })],
    ["a currency the contract does not name", dayBody({ entries: [entry({ currency: "EUR" })] })],
    ["an amount that is not whole", dayBody({ entries: [entry({ amount: 10.5 })] })],
    ["a direction the contract does not name", dayBody({ entries: [entry({ direction: "both" })] })],
    ["a source the contract does not name", dayBody({ entries: [entry({ source: "bank" })] })],
    ["no entries at all", { ...dayBody(), entries: undefined }],
  ])("refuses %s instead of showing it", async (_name, body) => {
    const { cash } = calls(() => body);
    await expect(cash.day(null, null)).rejects.toMatchObject({ code: "BAD_RESPONSE" });
  });

  it("reads a period: by category and by day, each figure with its currency", async () => {
    const { server, cash } = calls(() => summaryBody());
    const summary = await cash.summary("2026-10-01", "2026-10-06");
    expect(server.sent[0]?.query).toEqual({ from: "2026-10-01", to: "2026-10-06" });
    expect(summary.categories.map((row) => [row.category.name, row.currency, row.amount, row.count])).toEqual([
      ["Savdo", "UZS", 700000, 2],
      ["Ijara", "UZS", 300000, 1],
    ]);
    expect(summary.days).toEqual([{ date: "2026-10-05", currency: "UZS", income: 700000, expense: 300000 }]);
  });

  it("reads the categories and the currencies an entry may be written in", async () => {
    const { cash } = calls(() => ({ items: [category("income", "Savdo")], currencies: ["UZS", "USD"] }));
    expect(await cash.categories()).toEqual({
      items: [{ id: "cat-income-Savdo", direction: "income", name: "Savdo", archived: false, fixed: false }],
      currencies: ["UZS", "USD"],
    });
  });

  it("refuses a list of currencies it does not know", async () => {
    const { cash } = calls(() => ({ items: [], currencies: ["UZS", "EUR"] }));
    await expect(cash.categories()).rejects.toMatchObject({ code: "BAD_RESPONSE" });
  });
});

describe("writing to the cash book", () => {
  it("sends an entry as the server takes it: so'm and today are the absence of their fields", async () => {
    const { server, cash } = calls(() => ({ entry: entry() }));
    await cash.record(
      { direction: "income", method: "card", currency: "UZS", amount: 120000, categoryId: "cat-1", note: null, day: null },
      KEY,
    );
    expect(server.sent[0]).toMatchObject({ method: "POST", path: `${SHOP_BASE}/cash/entries` });
    expect(server.sent[0]?.headers["Idempotency-Key"]).toBe(KEY);
    expect(server.sent[0]?.body).toEqual({ direction: "income", method: "card", amount: 120000, category_id: "cat-1" });
  });

  it("names the currency, the note and the day when they are given", async () => {
    const { server, cash } = calls(() => ({ entry: entry({ currency: "USD" }) }));
    await cash.record(
      {
        direction: "expense",
        method: "cash",
        currency: "USD",
        amount: 125050,
        categoryId: "cat-2",
        note: "Ijara",
        day: "2026-10-01",
      },
      KEY,
    );
    expect(server.sent[0]?.body).toEqual({
      direction: "expense",
      method: "cash",
      amount: 125050,
      category_id: "cat-2",
      currency: "USD",
      note: "Ijara",
      day: "2026-10-01",
    });
  });

  it.each([0, -5, 10.5, Number.NaN])("sends nothing for the amount %s", (amount) => {
    const { server, cash } = calls(() => ({ entry: entry() }));
    expect(() =>
      cash.record(
        { direction: "income", method: "cash", currency: "UZS", amount, categoryId: "cat-1", note: null, day: null },
        KEY,
      ),
    ).toThrow(RangeError);
    expect(server.sent).toHaveLength(0);
  });

  it("cancels with a reason, and arranges the categories", async () => {
    const { server, cash } = calls((path) =>
      path.includes("/entries/") ? { entry: entry() } : path.endsWith("/backfill") ? { written: 4 } : category("expense", "Soliq"),
    );
    await cash.cancel("e-1", "Xato yozilgan", KEY);
    await cash.createCategory("expense", "Soliq", KEY);
    await cash.updateCategory("cat-9", { name: "Soliqlar" }, KEY);
    await cash.updateCategory("cat-9", { archived: true }, KEY);
    await cash.deleteCategory("cat-9", KEY);
    expect(await cash.backfill("2026-09-01", KEY)).toBe(4);
    expect(server.sent.map((sent) => [sent.method, sent.path.replace(`${SHOP_BASE}/cash`, ""), sent.body])).toEqual([
      ["POST", "/entries/e-1/cancellation", { reason: "Xato yozilgan" }],
      ["POST", "/categories", { direction: "expense", name: "Soliq" }],
      ["PATCH", "/categories/cat-9", { name: "Soliqlar" }],
      ["PATCH", "/categories/cat-9", { archived: true }],
      ["DELETE", "/categories/cat-9", undefined],
      ["POST", "/backfill", { since: "2026-09-01" }],
    ]);
  });
});

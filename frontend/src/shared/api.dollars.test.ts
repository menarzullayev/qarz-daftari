import { describe, expect, it } from "vitest";

import {
  accountBody,
  accountEntryBody,
  creditSettingsBody,
  CUSTOMER_ID,
  customerBody,
  detailBody,
  entryBody,
  fakeServer,
  LINK_ID,
  ME_BASE,
  NO_OVERDUE,
  noticeBody,
  ok,
  openDateRequestBody,
  openDisputeBody,
  openNoticeBody,
  overdueReportBody,
  periodReportBody,
  type Reply,
  type Sent,
  settingsBody,
  SHOP_BASE,
  SHOP_ID,
} from "../testing/fakeServer";
import { ApiError, BAD_RESPONSE, createApi } from "./api";
import { reports } from "./reports/reportsApi";

/**
 * US dollars beside so'm, as the API carries them (backend/src/qarz/application/currencies.py): `usd`
 * objects in whole cents beside the so'm figures, and `currency: "USD"` beside an amount in dollars. An
 * answer of a shop without dollars has neither, and is read into exactly what it was read into before.
 */

const KEY = "key-for-tests-0001";

function client(handler: (sent: Sent) => Reply) {
  const server = fakeServer(handler);
  const api = createApi({ fetch: server.fetch, auth: { kind: "bearer", token: "test-session" } });
  return { server, api, shop: api.shop(SHOP_ID), account: api.account(LINK_ID) };
}

const OVERDUE_USD = { amount: 500, since: "2026-10-01", days: 5, due_today: 250 };
const HISTORY = { on_time_percent: 75, on_time_amount: 1500, due_amount: 2000, longest_delay_days: 3 };

describe("reading dollars", () => {
  it("reads a customer's dollar balance and dollar limit beside the so'm ones", async () => {
    const { shop } = client(() =>
      ok({ items: [customerBody({ usd: { balance: 1250, credit_limit: 50000 } }), customerBody({ usd: { balance: 0, credit_limit: null } })], next_cursor: null }),
    );
    const { items } = await shop.listCustomers({});
    expect(items.map((customer) => [customer.balance, customer.usd])).toEqual([
      [120000, { balance: 1250, creditLimit: 50000 }],
      [120000, { balance: 0, creditLimit: null }],
    ]);
  });

  it("reads a customer without dollars into exactly what it always was: no `usd` at all", async () => {
    const { shop } = client((sent) => (sent.path.endsWith(CUSTOMER_ID) ? ok(detailBody()) : ok({ items: [customerBody()], next_cursor: null })));
    const [listed] = (await shop.listCustomers({})).items;
    expect(listed).toEqual({
      id: CUSTOMER_ID,
      displayName: "Ali Valiyev",
      phone: "+998901234567",
      status: "active",
      remindersOff: false,
      creditLimit: null,
      balance: 120000,
    });
    const detail = await shop.readCustomer(CUSTOMER_ID);
    expect("usd" in detail).toBe(false);
    expect(detail.entries.map((entry) => "currency" in entry)).toEqual([false]);
    // A null in place of the object is the same as its absence.
    const { shop: other } = client(() => ok({ items: [customerBody({ usd: null })], next_cursor: null }));
    expect("usd" in ((await other.listCustomers({})).items[0] ?? {})).toBe(false);
  });

  it("reads the page of a customer: the dollar overdue, the dollar history, and which entries are in dollars", async () => {
    const { shop } = client(() =>
      ok(
        detailBody({
          usd: { balance: 1250, credit_limit: null, overdue: OVERDUE_USD, payment_history: HISTORY },
          entries: [entryBody({ id: "22222222-2222-4222-8222-22222222aaaa", amount: 1250, currency: "USD" }), entryBody()],
          entries_total: 2,
          payment_notices: [{ ...noticeBody({ amount: 500, currency: "USD" }), receipt_seen_before: false }],
        }),
      ),
    );
    const detail = await shop.readCustomer(CUSTOMER_ID);
    expect(detail.usd).toEqual({
      balance: 1250,
      creditLimit: null,
      overdue: { amount: 500, since: "2026-10-01", days: 5, dueToday: 250 },
      paymentHistory: { onTimePercent: 75, onTimeAmount: 1500, dueAmount: 2000, longestDelayDays: 3 },
    });
    expect(detail.entries.map((entry) => [entry.amount, entry.currency])).toEqual([
      [1250, "USD"],
      [45000, undefined],
    ]);
    expect(detail.paymentNotices.map((notice) => notice.currency)).toEqual(["USD"]);
    // A dollar history of null says that no dollar debt has fallen due yet; it is kept as null.
    const { shop: young } = client(() => ok(detailBody({ usd: { balance: 0, credit_limit: null, overdue: NO_OVERDUE, payment_history: null } })));
    expect((await young.readCustomer(CUSTOMER_ID)).usd?.paymentHistory).toBeNull();
  });

  it("refuses a currency it does not know, a dollar balance that is not whole cents, and a malformed `usd`", async () => {
    const bad = async (body: unknown) => {
      const { shop } = client(() => ok(body));
      return shop.readCustomer(CUSTOMER_ID).then(
        () => "read",
        (error: unknown) => (error instanceof ApiError ? error.code : "other"),
      );
    };
    expect(await bad(detailBody({ entries: [entryBody({ currency: "EUR" })] }))).toBe(BAD_RESPONSE);
    expect(await bad(detailBody({ entries: [entryBody({ currency: 840 })] }))).toBe(BAD_RESPONSE);
    expect(await bad(detailBody({ usd: { balance: 12.5, credit_limit: null } }))).toBe(BAD_RESPONSE);
    expect(await bad(detailBody({ usd: { credit_limit: null } }))).toBe(BAD_RESPONSE);
    expect(await bad(detailBody({ usd: 1250 }))).toBe(BAD_RESPONSE);
    // The same answers with the dollars well formed are read.
    expect(await bad(detailBody({ entries: [entryBody({ currency: "USD" })], usd: { balance: 1250, credit_limit: null } }))).toBe("read");
    expect(await bad(detailBody({ entries: [entryBody({ currency: "UZS" })] }))).toBe("read");
  });

  it("reads the overview's dollar figures, and none for a shop without dollars", async () => {
    const figures = { outstanding: 1250000, debtors: 5, overdue: { amount: 320000, customers: 2 }, due_today: 45000 };
    const usd = { outstanding: 125050, debtors: 2, overdue: { amount: 5000, customers: 1 }, due_today: 1250 };
    const { shop } = client(() => ok({ ...figures, usd }));
    expect(await shop.overview()).toEqual({
      outstanding: 1250000,
      debtors: 5,
      overdueAmount: 320000,
      overdueCustomers: 2,
      dueToday: 45000,
      usd: { outstanding: 125050, debtors: 2, overdueAmount: 5000, overdueCustomers: 1, dueToday: 1250 },
    });
    const { shop: plain } = client(() => ok(figures));
    expect(await plain.overview()).toEqual({ outstanding: 1250000, debtors: 5, overdueAmount: 320000, overdueCustomers: 2, dueToday: 45000 });
  });

  it("reads whether the shop works in dollars only when the platform offers them", async () => {
    const read = async (overrides: Record<string, unknown>) => (await client(() => ok(settingsBody(overrides))).shop.readSettings()).usdOn;
    expect(await read({ usd_on: true })).toBe(true);
    expect(await read({ usd_on: false })).toBe(false);
    // No key, or a null in its place: the shop has no such setting, which is not the same as "off".
    expect(await read({})).toBeUndefined();
    expect(await read({ usd_on: null })).toBeUndefined();
    const settings = await client(() => ok(settingsBody())).shop.readSettings();
    expect(settings).toEqual({ id: SHOP_ID, name: "Baraka savdo", lang: "uz", defaultPromiseDays: 30 });
  });

  it("reads the default dollar limit with its own bounds", async () => {
    const { shop } = client(() => ok(creditSettingsBody({ usd: { default_credit_limit: 50000, limit_bounds: [100, 100000000] } })));
    expect(await shop.readCreditSettings()).toEqual({
      defaultLimit: null,
      sellersMayExceed: false,
      bounds: { min: 1000, max: 10000000000 },
      usd: { defaultLimit: 50000, bounds: { min: 100, max: 100000000 } },
    });
    const { shop: plain } = client(() => ok(creditSettingsBody()));
    expect(await plain.readCreditSettings()).toEqual({ defaultLimit: null, sellersMayExceed: false, bounds: { min: 1000, max: 10000000000 } });
  });

  it("reads the currency of a notice, a dispute and a date request, and so'm from its absence", async () => {
    const { shop } = client((sent) => {
      if (sent.path.endsWith("/payment-notices")) {
        return ok({ items: [openNoticeBody({ amount: 500, customer_balance: 1250, currency: "USD" }), openNoticeBody()] });
      }
      return sent.path.endsWith("/disputes")
        ? ok({ items: [openDisputeBody({ amount: 1250, currency: "USD" }), openDisputeBody()] })
        : ok({ items: [openDateRequestBody({ amount: 1250, currency: "USD" }), openDateRequestBody()] });
    });
    expect((await shop.listPaymentNotices()).map((notice) => [notice.amount, notice.customerBalance, notice.currency])).toEqual([
      [500, 1250, "USD"],
      [50000, 120000, undefined],
    ]);
    expect((await shop.listDisputes()).map((dispute) => [dispute.amount, dispute.currency])).toEqual([
      [1250, "USD"],
      [45000, undefined],
    ]);
    expect((await shop.listDateRequests()).map((request) => [request.amount, request.currency])).toEqual([
      [1250, "USD"],
      [45000, undefined],
    ]);
  });

  it("reads the dollars a reminder stated, and the dollars due of a customer nobody can reach", async () => {
    const { shop } = client((sent) =>
      sent.method === "POST"
        ? ok({ sent: true, channel: "telegram", amount: 45000, usd: { amount: 1250 } })
        : ok({ items: [{ customer_id: CUSTOMER_ID, display_name: "Ali Valiyev", phone: null, amount: 0, usd: { amount: 1250 } }] }),
    );
    expect(await shop.sendReminder(CUSTOMER_ID, KEY)).toEqual({ channel: "telegram", amount: 45000, usdAmount: 1250 });
    expect(await shop.listUnreachable()).toEqual([{ customerId: CUSTOMER_ID, displayName: "Ali Valiyev", phone: null, amount: 0, usdAmount: 1250 }]);
    const { shop: plain } = client(() => ok({ sent: true, channel: "sms", amount: 45000 }));
    expect(await plain.sendReminder(CUSTOMER_ID, KEY)).toEqual({ channel: "sms", amount: 45000 });
  });

  it("reads a customer's own accounts and account with their dollar book", async () => {
    const usd = { balance: 1250, overdue: { amount: 500, due_today: 250 }, payment_history: HISTORY };
    const { api, account } = client((sent) =>
      sent.path === "/api/v1/me/accounts"
        ? ok({ items: [{ link_id: LINK_ID, shop_name: "Baraka savdo", display_name: "Ali", balance: 120000, usd: { balance: 1250 } }] })
        : ok(accountBody({ usd, entries: [accountEntryBody({ amount: 1250, currency: "USD" }), accountEntryBody()], entries_total: 2 })),
    );
    expect(await api.myAccounts()).toEqual([{ linkId: LINK_ID, shopName: "Baraka savdo", displayName: "Ali", balance: 120000, usd: { balance: 1250 } }]);
    const detail = await account.read();
    expect(detail.usd).toEqual({
      balance: 1250,
      overdueAmount: 500,
      dueToday: 250,
      paymentHistory: { onTimePercent: 75, onTimeAmount: 1500, dueAmount: 2000, longestDelayDays: 3 },
    });
    expect(detail.entries.map((entry) => entry.currency)).toEqual(["USD", undefined]);
    const { api: plain, account: own } = client((sent) =>
      sent.path === "/api/v1/me/accounts"
        ? ok({ items: [{ link_id: LINK_ID, shop_name: "Baraka savdo", display_name: "Ali", balance: 120000 }] })
        : ok(accountBody()),
    );
    expect(await plain.myAccounts()).toEqual([{ linkId: LINK_ID, shopName: "Baraka savdo", displayName: "Ali", balance: 120000 }]);
    expect("usd" in (await own.read())).toBe(false);
  });

  it("reads the dollar debt a removal waits for", async () => {
    const { account } = client(() => ok({ removed: false, waiting_for_balance: 0, usd: { waiting_for_balance: 1250 } }));
    expect(await account.requestRemoval()).toEqual({ removed: false, waitingForBalance: 0, waitingForUsd: 1250 });
    const { account: plain } = client(() => ok({ removed: false, waiting_for_balance: 120000 }));
    expect(await plain.requestRemoval()).toEqual({ removed: false, waitingForBalance: 120000 });
  });

  it("reads the reports' dollar sections, each from the dollar book only", async () => {
    const dollars = {
      outstanding: { start: 10000, end: 12500 },
      credit: { amount: 5000, count: 2, customers: 1 },
      payments: { amount: 2500, count: 1, customers: 1 },
      opening: { amount: 0, count: 0 },
      net_change: 2500,
      reversals: { amount: 0, count: 0 },
      on_time: { due_amount: 0, on_time_amount: 0, percent: null },
      days: [{ date: "2026-10-01", credit: 5000, payments: 2500 }],
      top_debtors: [{ customer_id: CUSTOMER_ID, display_name: "Ali Valiyev", balance: 12500 }],
      staff: [],
    };
    const { shop } = client((sent) =>
      sent.path.endsWith("/period")
        ? ok(periodReportBody({ usd: dollars }))
        : ok(overdueReportBody({ usd: { total: { amount: 500, customers: 1 }, bands: [{ band: "1_7", from_days: 1, to_days: 7, amount: 500, customers: 1 }] } })),
    );
    const period = await reports(shop).period("2026-10-01", "2026-10-06");
    expect(period.outstanding).toEqual({ start: 500000, end: 600000 });
    expect(period.usd?.outstanding).toEqual({ start: 10000, end: 12500 });
    expect(period.usd?.netChange).toBe(2500);
    expect(period.usd?.topDebtors).toEqual([{ customerId: CUSTOMER_ID, displayName: "Ali Valiyev", balance: 12500 }]);
    // The counts of customers and disputes are the period's own, not a currency's.
    expect([period.newCustomers, period.disputesOpened]).toEqual([2, 1]);
    expect(period.usd && "newCustomers" in period.usd).toBe(false);
    const late = await reports(shop).overdue();
    expect(late.total).toEqual({ amount: 180000, customers: 3 });
    expect(late.usd).toEqual({ total: { amount: 500, customers: 1 }, bands: [{ band: "1_7", fromDays: 1, toDays: 7, amount: 500, customers: 1 }] });

    const { shop: plain } = client((sent) => (sent.path.endsWith("/period") ? ok(periodReportBody()) : ok(overdueReportBody())));
    expect("usd" in (await reports(plain).period("2026-10-01", "2026-10-06"))).toBe(false);
    expect("usd" in (await reports(plain).overdue())).toBe(false);
  });
});

describe("writing dollars", () => {
  const recorded = (entry: Record<string, unknown>, customer: Record<string, unknown> = {}) =>
    ok({ entry: { id: "e1", kind: "credit", promised_date: null, lines: [], ...entry }, customer: customerBody(customer), limit_warning: null }, 201);

  it("sends a dollar entry as cents with its currency, and reads the answer's currency", async () => {
    const { server, shop } = client(() => recorded({ amount: 1250, currency: "USD" }, { usd: { balance: 1250, credit_limit: null } }));
    const made = await shop.recordEntry(CUSTOMER_ID, { kind: "credit", amount: 1250, currency: "USD", note: null, promisedDate: null }, KEY);
    expect(server.sent[0]?.body).toEqual({ kind: "credit", amount: 1250, currency: "USD" });
    expect(made.entry.currency).toBe("USD");
    expect(made.customer.usd).toEqual({ balance: 1250, creditLimit: null });
  });

  it("sends a so'm entry exactly as before: no currency beside the amount", async () => {
    const { server, shop } = client(() => recorded({ amount: 45000 }));
    const plain = await shop.recordEntry(CUSTOMER_ID, { kind: "credit", amount: 45000, note: null, promisedDate: null }, KEY);
    await shop.recordEntry(CUSTOMER_ID, { kind: "credit", amount: 45000, currency: "UZS", note: null, promisedDate: null }, KEY);
    expect(server.sent.map((sent) => sent.body)).toEqual([
      { kind: "credit", amount: 45000 },
      { kind: "credit", amount: 45000 },
    ]);
    expect("currency" in plain.entry).toBe(false);
  });

  it("never sends goods lines as a sale in dollars, nor a dollar amount that is not whole cents", () => {
    const { server, shop } = client(() => recorded({ amount: 1 }));
    const line = { name: "Non", unit: null, qty: 1000, unitPrice: 4000 };
    // @ts-expect-error -- a sale with goods lines has no currency: goods are priced in so'm
    const withLines = () => shop.recordEntry(CUSTOMER_ID, { kind: "credit", lines: [line], currency: "USD", note: null, promisedDate: null }, KEY);
    expect(withLines).toThrow(RangeError);
    expect(() => shop.recordEntry(CUSTOMER_ID, { kind: "credit", amount: 12.5, currency: "USD", note: null, promisedDate: null }, KEY)).toThrow(RangeError);
    expect(server.sent).toHaveLength(0);
  });

  it("sets and removes the dollar limit apart from the so'm one", async () => {
    const { server, shop } = client(() => ok(customerBody({ usd: { balance: 0, credit_limit: 50000 } })));
    await shop.updateCustomer(CUSTOMER_ID, { creditLimitUsd: 50000 }, KEY);
    await shop.updateCustomer(CUSTOMER_ID, { creditLimitUsd: null }, KEY);
    await shop.updateCustomer(CUSTOMER_ID, { creditLimit: 500000 }, KEY);
    expect(server.sent.map((sent) => sent.body)).toEqual([{ credit_limit_usd: 50000 }, { credit_limit_usd: null }, { credit_limit: 500000 }]);
    expect(() => shop.updateCustomer(CUSTOMER_ID, { creditLimitUsd: 500.5 }, KEY)).toThrow(RangeError);
    expect(server.sent).toHaveLength(3);
  });

  it("sets the default dollar limit and the shop's dollar switch in their own fields", async () => {
    const { server, shop } = client((sent) => (sent.path.endsWith("/credit-settings") ? ok(creditSettingsBody()) : ok(settingsBody({ usd_on: true }))));
    await shop.updateCreditSettings({ defaultLimitUsd: 50000 }, KEY);
    await shop.updateCreditSettings({ defaultLimitUsd: null }, KEY);
    await shop.updateCreditSettings({ defaultLimit: 500000 }, KEY);
    const saved = await shop.updateSettings({ usdOn: true }, KEY);
    await shop.updateSettings({ name: "Ziyo" }, KEY);
    expect(server.sent.map((sent) => sent.body)).toEqual([
      { default_credit_limit_usd: 50000 },
      { default_credit_limit_usd: null },
      { default_credit_limit: 500000 },
      { usd_on: true },
      { name: "Ziyo" },
    ]);
    expect(saved.usdOn).toBe(true);
    expect(() => shop.updateCreditSettings({ defaultLimitUsd: 0.5 }, KEY)).toThrow(RangeError);
  });

  it("asks for the debtors by the dollar debt only when dollars are named", async () => {
    const { server, shop } = client(() => ok({ items: [], next_cursor: null }));
    await shop.debtors({ overdue: false, currency: "USD" });
    await shop.debtors({ overdue: true, currency: "UZS" });
    await shop.debtors({ overdue: false });
    expect(server.sent.map((sent) => [sent.path, sent.query])).toEqual([
      [`${SHOP_BASE}/overview/debtors`, { overdue: "false", currency: "USD" }],
      [`${SHOP_BASE}/overview/debtors`, { overdue: "true" }],
      [`${SHOP_BASE}/overview/debtors`, { overdue: "false" }],
    ]);
  });

  it("sends a customer's notice of dollars with its currency, as JSON and as a form", async () => {
    const { server, account } = client(() => ok(noticeBody({ amount: 500, currency: "USD" }), 201));
    const sent = await account.sendPaymentNotice(500, null, KEY, "USD");
    await account.sendPaymentNotice(500, new Blob([new Uint8Array(4)], { type: "image/png" }), KEY, "USD");
    expect(sent.currency).toBe("USD");
    expect(server.sent[0]).toMatchObject({ method: "POST", path: `${ME_BASE}/payment-notices`, body: { amount: 500, currency: "USD" } });
    expect(server.sent[1]?.form).toMatchObject({ amount: "500", currency: "USD" });
  });

  it("sends a notice of so'm exactly as before: the amount alone", async () => {
    const { server, account } = client(() => ok(noticeBody(), 201));
    const sent = await account.sendPaymentNotice(50000, null, KEY);
    await account.sendPaymentNotice(50000, null, KEY, "UZS");
    await account.sendPaymentNotice(50000, new Blob([new Uint8Array(4)], { type: "image/png" }), KEY);
    expect(server.sent.slice(0, 2).map((request) => request.body)).toEqual([{ amount: 50000 }, { amount: 50000 }]);
    expect(Object.keys(server.sent[2]?.form ?? {}).sort()).toEqual(["amount", "receipt"]);
    expect("currency" in sent).toBe(false);
  });
});

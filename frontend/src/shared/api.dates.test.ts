import { describe, expect, it } from "vitest";

import {
  accountBody,
  accountEntryBody,
  DATE_REQUEST_ID,
  dateRequestBody,
  detailBody,
  entryBody,
  fakeServer,
  LINK_ID,
  ME_BASE,
  ok,
  openDateRequestBody,
  overdueReportBody,
  periodReportBody,
  promiseBody,
  refusal,
  SHOP_BASE,
  SHOP_ID,
} from "../testing/fakeServer";
import { createApi } from "./api";
import { reports } from "./reports/reportsApi";

const ENTRY_ID = "22222222-2222-4222-8222-222222222222";
const KEY = "key-0000-0001";

function client(reply: Parameters<typeof fakeServer>[0]) {
  const server = fakeServer(reply);
  const api = createApi({ fetch: server.fetch, auth: { kind: "bearer", token: "session-token" } });
  return { server, shop: api.shop(SHOP_ID), account: api.account(LINK_ID) };
}

describe("an entry's promised dates and date request", () => {
  const HISTORY = [promiseBody(), promiseBody({ promised_date: "2026-11-20", actor: "customer_request", reason: "Oylik kechikdi", created_at: "2026-10-07T05:00:00+00:00" })];

  it("are read for staff and for the customer in the same shape", async () => {
    const staff = client(() => ok(detailBody({ entries: [entryBody({ promises: HISTORY, date_request: dateRequestBody({ status: "accepted", closed_at: "2026-10-07T05:00:00+00:00" }) })] })));
    const [entry] = (await staff.shop.readCustomer("11111111-1111-4111-8111-111111111111")).entries;
    expect(entry?.promises).toEqual([
      { promisedDate: "2026-11-05", actor: "default", reason: null, createdAt: "2026-10-05T19:30:00+00:00" },
      { promisedDate: "2026-11-20", actor: "customer_request", reason: "Oylik kechikdi", createdAt: "2026-10-07T05:00:00+00:00" },
    ]);
    expect(entry?.dateRequest).toEqual({
      id: DATE_REQUEST_ID,
      entryId: ENTRY_ID,
      status: "accepted",
      requestedDate: "2026-11-20",
      reason: "Oylik kechikdi",
      declineReason: null,
      createdAt: "2026-10-06T05:10:00+00:00",
      closedAt: "2026-10-07T05:00:00+00:00",
    });

    const mine = client(() => ok(accountBody({ entries: [accountEntryBody({ promises: HISTORY, date_request: dateRequestBody() })] })));
    const [own] = (await mine.account.read()).entries;
    expect(own?.promises).toHaveLength(2);
    expect(own?.dateRequest?.status).toBe("open");
  });

  it("are empty for a server that does not send them", async () => {
    const { shop } = client(() => ok(detailBody()));
    const [entry] = (await shop.readCustomer("11111111-1111-4111-8111-111111111111")).entries;
    expect(entry?.promises).toEqual([]);
    expect(entry?.dateRequest).toBeNull();
  });

  it("are refused when they are not the contract", async () => {
    const { shop } = client(() => ok(detailBody({ entries: [entryBody({ promises: [{ promised_date: 20261105 }] })] })));
    await expect(shop.readCustomer("11111111-1111-4111-8111-111111111111")).rejects.toMatchObject({ code: "BAD_RESPONSE" });
  });
});

describe("asking for a later date", () => {
  it("posts the entry, the date and the reason to the customer's own account, with no idempotency key", async () => {
    const { server, account } = client(() => ok(dateRequestBody(), 201));
    const made = await account.openDateRequest(ENTRY_ID, "2026-11-20", "Oylik kechikdi");
    expect(made.status).toBe("open");
    expect(server.sent[0]).toMatchObject({
      method: "POST",
      path: `${ME_BASE}/date-requests`,
      body: { entry_id: ENTRY_ID, requested_date: "2026-11-20", reason: "Oylik kechikdi" },
    });
    expect(server.sent[0]?.headers["Idempotency-Key"]).toBeUndefined();
  });

  it("leaves the reason out when there is none", async () => {
    const { server, account } = client(() => ok(dateRequestBody({ reason: null }), 201));
    await account.openDateRequest(ENTRY_ID, "2026-11-20", null);
    expect(server.sent[0]?.body).toEqual({ entry_id: ENTRY_ID, requested_date: "2026-11-20" });
  });

  it("carries the server's reason for a refusal", async () => {
    const { account } = client(() => refusal(409, "DATE_REQUEST_NOT_ALLOWED", "Hozir bo'lmaydi.", { reason: "fully_paid" }));
    await expect(account.openDateRequest(ENTRY_ID, "2026-11-20", null)).rejects.toMatchObject({
      code: "DATE_REQUEST_NOT_ALLOWED",
      fields: { reason: "fully_paid" },
    });
  });
});

describe("the shop's side of date requests", () => {
  it("lists open requests with the customer, the amount and the date each would replace", async () => {
    const { server, shop } = client(() => ok({ items: [openDateRequestBody()] }));
    expect(await shop.listDateRequests()).toEqual([
      {
        id: DATE_REQUEST_ID,
        entryId: ENTRY_ID,
        status: "open",
        requestedDate: "2026-11-20",
        reason: "Oylik kechikdi",
        declineReason: null,
        createdAt: "2026-10-06T05:10:00+00:00",
        closedAt: null,
        customerId: "11111111-1111-4111-8111-111111111111",
        customerName: "Ali Valiyev",
        amount: 45000,
        promisedDate: "2026-11-05",
      },
    ]);
    expect(server.sent[0]).toMatchObject({ method: "GET", path: `${SHOP_BASE}/date-requests` });
  });

  it("refuses a request whose amount is not a whole number of UZS", async () => {
    const { shop } = client(() => ok({ items: [openDateRequestBody({ amount: 45000.5 })] }));
    await expect(shop.listDateRequests()).rejects.toMatchObject({ code: "BAD_RESPONSE" });
  });

  it("accepts and declines with an idempotency key; a decline sends its reason only when there is one", async () => {
    const { server, shop } = client(() => ok(openDateRequestBody({ status: "accepted" })));
    await shop.acceptDateRequest(DATE_REQUEST_ID, KEY);
    await shop.declineDateRequest(DATE_REQUEST_ID, "Muddat juda uzoq", KEY);
    await shop.declineDateRequest(DATE_REQUEST_ID, null, KEY);
    expect(server.sent.map((sent) => [sent.method, sent.path, sent.body, sent.headers["Idempotency-Key"]])).toEqual([
      ["POST", `${SHOP_BASE}/date-requests/${DATE_REQUEST_ID}/accept`, undefined, KEY],
      ["POST", `${SHOP_BASE}/date-requests/${DATE_REQUEST_ID}/decline`, { reason: "Muddat juda uzoq" }, KEY],
      ["POST", `${SHOP_BASE}/date-requests/${DATE_REQUEST_ID}/decline`, {}, KEY],
    ]);
  });

  it("changes a promised date and reads both dates and the request the change closed", async () => {
    const { server, shop } = client(() =>
      ok({
        entry: { id: ENTRY_ID, amount: 45000, promised_date: "2026-11-25", previous_date: "2026-11-05" },
        customer: {},
        date_request: dateRequestBody({ status: "accepted", closed_at: "2026-10-06T07:00:00+00:00" }),
      }),
    );
    const changed = await shop.changePromise(ENTRY_ID, "2026-11-25", "Kelishildi", KEY);
    expect(changed).toMatchObject({ entryId: ENTRY_ID, promisedDate: "2026-11-25", previousDate: "2026-11-05" });
    expect(changed.dateRequest?.status).toBe("accepted");
    expect(server.sent[0]).toMatchObject({
      method: "POST",
      path: `${SHOP_BASE}/entries/${ENTRY_ID}/promise`,
      body: { promised_date: "2026-11-25", reason: "Kelishildi" },
    });
    expect(server.sent[0]?.headers["Idempotency-Key"]).toBe(KEY);
  });

  it("reads a change that closed no request, and sends no reason when there is none", async () => {
    const { server, shop } = client(() =>
      ok({ entry: { id: ENTRY_ID, amount: 45000, promised_date: "2026-10-20", previous_date: "2026-11-05" }, date_request: null }),
    );
    expect((await shop.changePromise(ENTRY_ID, "2026-10-20", null, KEY)).dateRequest).toBeNull();
    expect(server.sent[0]?.body).toEqual({ promised_date: "2026-10-20" });
  });
});

describe("the reports", () => {
  it("asks for a period by its two days and reads every figure as a whole number", async () => {
    const { server, shop } = client(() => ok(periodReportBody()));
    const report = await reports(shop).period("2026-10-01", "2026-10-06");
    expect(server.sent[0]).toMatchObject({
      method: "GET",
      path: `${SHOP_BASE}/reports/period`,
      query: { from: "2026-10-01", to: "2026-10-06" },
    });
    expect(report.outstanding).toEqual({ start: 500000, end: 600000 });
    expect(report.credit).toEqual({ amount: 300000, count: 4, customers: 3 });
    expect(report.payments).toEqual({ amount: 250000, count: 2, customers: 2 });
    expect(report.opening).toEqual({ amount: 50000, count: 1 });
    expect(report.reversals).toEqual({ amount: 20000, count: 1 });
    expect([report.netChange, report.newCustomers, report.disputesOpened]).toEqual([100000, 2, 1]);
    expect(report.onTime).toEqual({ dueAmount: 200000, onTimeAmount: 150000, percent: 75 });
    expect(report.days).toHaveLength(6);
    expect(report.topDebtors[0]).toEqual({ customerId: "11111111-1111-4111-8111-111111111111", displayName: "Ali Valiyev", balance: 400000 });
    expect(report.staff[1]).toMatchObject({ role: "seller", credit: { amount: 200000, count: 3 } });
  });

  it("keeps a null on-time share as null: nothing fell due", async () => {
    const { shop } = client(() => ok(periodReportBody({ on_time: { due_amount: 0, on_time_amount: 0, percent: null } })));
    expect((await reports(shop).period("2026-10-01", "2026-10-06")).onTime.percent).toBeNull();
  });

  it.each([
    ["a fraction of a so'm", { outstanding: { start: 500000.5, end: 600000 } }],
    ["an amount as text", { credit: { amount: "300000", count: 4, customers: 3 } }],
    ["a missing list", { days: undefined }],
  ])("refuses a period report with %s", async (_name, overrides) => {
    const { shop } = client(() => ok(periodReportBody(overrides)));
    await expect(reports(shop).period("2026-10-01", "2026-10-06")).rejects.toMatchObject({ code: "BAD_RESPONSE" });
  });

  it("carries the server's word for what is wrong with the period", async () => {
    const { shop } = client(() => refusal(422, "VALIDATION", "Ma'lumotlar noto'g'ri kiritilgan.", { to: "IN_FUTURE" }));
    await expect(reports(shop).period("2026-10-01", "2026-12-01")).rejects.toMatchObject({ fields: { to: "IN_FUTURE" } });
  });

  it("reads overdue debt by age, with a last band that has no end", async () => {
    const { server, shop } = client(() => ok(overdueReportBody()));
    const report = await reports(shop).overdue();
    expect(server.sent[0]).toMatchObject({ method: "GET", path: `${SHOP_BASE}/reports/overdue`, query: {} });
    expect(report.asOf).toBe("2026-10-06");
    expect(report.total).toEqual({ amount: 180000, customers: 3 });
    expect(report.bands.map((band) => [band.band, band.fromDays, band.toDays, band.amount, band.customers])).toEqual([
      ["1_7", 1, 7, 45000, 1],
      ["8_30", 8, 30, 100000, 2],
      ["31_90", 31, 90, 0, 0],
      ["over_90", 91, null, 35000, 1],
    ]);
  });
});

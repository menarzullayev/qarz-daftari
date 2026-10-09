import { describe, expect, it } from "vitest";

import {
  accountBody,
  creditSettingsBody,
  CUSTOMER_ID,
  customerBody,
  fakeServer,
  LINK_ID,
  NO_OVERDUE,
  ok,
  periodReportBody,
  type Reply,
  type Sent,
  SHOP_BASE,
  SHOP_ID,
} from "../testing/fakeServer";
import { ApiError, BAD_RESPONSE, createApi } from "./api";
import { reports } from "./reports/reportsApi";

/**
 * Advances as the API carries them: a balance below zero, `advances` beside what is owed, the shop's
 * `accept_advances`, and `advance: true` with a confirmed payment. What a shop without advances sends
 * and what is sent to it is what it always was.
 */

const KEY = "key-for-tests-0001";
const TOTALS = { outstanding: 50000, debtors: 1, overdue: { amount: 0, customers: 0 }, due_today: 0 };

function client(handler: (sent: Sent) => Reply) {
  const server = fakeServer(handler);
  const api = createApi({ fetch: server.fetch, auth: { kind: "bearer", token: "test-session" } });
  return { server, api, shop: api.shop(SHOP_ID) };
}

describe("a balance below zero", () => {
  it("is read as it is for a customer, a debtor and the customer's own account", async () => {
    const { shop, api } = client((sent) =>
      sent.path.endsWith("/debtors")
        ? ok({ items: [{ ...customerBody({ balance: -30000 }), overdue: NO_OVERDUE }], next_cursor: null })
        : sent.path.includes("/me/")
          ? ok(accountBody({ balance: -15000, usd: { balance: -500, overdue: { amount: 0, due_today: 0 } } }))
          : ok({ items: [customerBody({ balance: -30000, usd: { balance: -500, credit_limit: null } })], next_cursor: null }),
    );
    const listed = await shop.listCustomers({});
    expect([listed.items[0]?.balance, listed.items[0]?.usd?.balance]).toEqual([-30000, -500]);
    expect((await shop.debtors({ overdue: false, inCredit: true })).items[0]?.balance).toBe(-30000);
    const mine = await api.account(LINK_ID).read();
    expect([mine.balance, mine.usd?.balance]).toEqual([-15000, -500]);
  });

  it("is still refused when it is not a whole amount", async () => {
    const { shop } = client(() => ok({ items: [customerBody({ balance: -30000.5 })], next_cursor: null }));
    await expect(shop.listCustomers({})).rejects.toMatchObject({ code: BAD_RESPONSE });
  });
});

describe("the list of customers in credit", () => {
  it("is asked for by name, and the list of debtors is asked for exactly as before", async () => {
    const { shop, server } = client(() => ok({ items: [], next_cursor: null }));
    await shop.debtors({ overdue: false, inCredit: true, currency: "USD", cursor: "c1" });
    await shop.debtors({ overdue: true });
    await shop.debtors({ overdue: false, inCredit: false });
    expect(server.sent.map((sent) => sent.query)).toEqual([
      { overdue: "false", in_credit: "true", currency: "USD", cursor: "c1" },
      { overdue: "true" },
      { overdue: "false" },
    ]);
  });
});

describe("the overview", () => {
  it("reads what the shop holds, in so'm and in dollars, beside what it is owed", async () => {
    const usd = { ...TOTALS, outstanding: 12000, advances: { amount: 500, customers: 1 } };
    const { shop } = client(() => ok({ ...TOTALS, advances: { amount: 30000, customers: 2 }, usd }));
    const totals = await shop.overview();
    expect(totals.outstanding).toBe(50000);
    expect(totals.advances).toEqual({ amount: 30000, customers: 2 });
    expect(totals.usd?.advances).toEqual({ amount: 500, customers: 1 });
  });

  it("has no such figure for a shop that holds none: the answer is read into what it always was", async () => {
    const { shop } = client(() => ok({ ...TOTALS, advances: null }));
    expect(await shop.overview()).toEqual({ outstanding: 50000, debtors: 1, overdueAmount: 0, overdueCustomers: 0, dueToday: 0 });
  });

  it.each([{ amount: "30000", customers: 1 }, { amount: 30000 }, { amount: 1.5, customers: 1 }, []])(
    "refuses advances that are not two whole numbers: %j",
    async (advances) => {
      const { shop } = client(() => ok({ ...TOTALS, advances }));
      await expect(shop.overview()).rejects.toMatchObject({ code: BAD_RESPONSE });
    },
  );
});

describe("the period report", () => {
  it("reads the advances at both ends, in each currency's own section", async () => {
    const body = periodReportBody({ advances: { start: 0, end: 40000 } });
    const { shop } = client(() => ok({ ...body, usd: periodReportBody({ advances: { start: 100, end: 0 } }) }));
    const report = await reports(shop).period("2026-10-01", "2026-10-06");
    expect(report.advances).toEqual({ start: 0, end: 40000 });
    expect(report.usd?.advances).toEqual({ start: 100, end: 0 });
    expect(report.outstanding).toEqual({ start: 500000, end: 600000 });
  });

  it("has none for a shop that held none, and refuses ones that are not whole", async () => {
    const plain = client(() => ok(periodReportBody()));
    expect("advances" in (await reports(plain.shop).period("2026-10-01", "2026-10-06"))).toBe(false);
    const broken = client(() => ok(periodReportBody({ advances: { start: 0, end: "40000" } })));
    await expect(reports(broken.shop).period("2026-10-01", "2026-10-06")).rejects.toMatchObject({ code: BAD_RESPONSE });
  });
});

describe("the shop's setting", () => {
  it("is read as on only when the server says so", async () => {
    const answers = [creditSettingsBody({ accept_advances: true }), creditSettingsBody({ accept_advances: false }), creditSettingsBody()];
    const { shop } = client(() => ok(answers.shift()));
    expect((await shop.readCreditSettings()).acceptAdvances).toBe(true);
    // Off, or not named at all: the settings are the object they always were, with no such field.
    const plain = { defaultLimit: null, sellersMayExceed: false, bounds: { min: 1000, max: 10000000000 } };
    expect(await shop.readCreditSettings()).toEqual(plain);
    expect(await shop.readCreditSettings()).toEqual(plain);
  });

  it("is sent only when it is the change", async () => {
    const { shop, server } = client(() => ok(creditSettingsBody({ accept_advances: true })));
    await shop.updateCreditSettings({ acceptAdvances: true }, KEY);
    await shop.updateCreditSettings({ acceptAdvances: false }, KEY);
    await shop.updateCreditSettings({ sellersMayExceed: true }, KEY);
    expect(server.writes().map((sent) => sent.body)).toEqual([
      { accept_advances: true },
      { accept_advances: false },
      { sellers_may_exceed: true },
    ]);
  });
});

describe("a payment kept as an advance", () => {
  const recorded = () =>
    ok(
      {
        entry: { id: "e1", seq: 2, kind: "payment", amount: 65000, note: null, created_at: "2026-10-06T07:00:00+00:00", promised_date: null },
        customer: customerBody({ balance: -15000 }),
      },
      201,
    );

  it("says so in the body, and the customer comes back in credit", async () => {
    const { shop, server } = client(recorded);
    const saved = await shop.recordEntry(CUSTOMER_ID, { kind: "payment", amount: 65000, note: null, promisedDate: null, advance: true }, KEY);
    expect(server.writes()[0]).toMatchObject({ method: "POST", path: `${SHOP_BASE}/customers/${CUSTOMER_ID}/entries` });
    expect(server.writes()[0]?.body).toEqual({ kind: "payment", amount: 65000, advance: true });
    expect(saved.customer.balance).toBe(-15000);
  });

  it("is not named in a payment nobody confirmed: the request is the one it always was", async () => {
    const { shop, server } = client(recorded);
    await shop.recordEntry(CUSTOMER_ID, { kind: "payment", amount: 65000, note: null, promisedDate: null }, KEY);
    expect(server.writes()[0]?.body).toEqual({ kind: "payment", amount: 65000 });
  });

  it("is never sent with a credit sale", async () => {
    const { shop, server } = client(recorded);
    expect(() => shop.recordEntry(CUSTOMER_ID, { kind: "credit", amount: 65000, note: null, promisedDate: null, advance: true }, KEY)).toThrow(
      RangeError,
    );
    expect(server.sent).toHaveLength(0);
  });

  it("keeps the figures of the server's question on the error", async () => {
    const fields = { debt: "50000", over: "15000", advance: "15000" };
    const { shop } = client(() => ({ status: 409, body: { error: { code: "ADVANCE_NOT_CONFIRMED", message: "Tasdiqlang.", fields } } }));
    const error = await shop
      .recordEntry(CUSTOMER_ID, { kind: "payment", amount: 65000, note: null, promisedDate: null }, KEY)
      .catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 409, code: "ADVANCE_NOT_CONFIRMED", serverMessage: "Tasdiqlang.", fields });
  });
});

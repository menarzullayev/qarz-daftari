import { describe, expect, it } from "vitest";

import {
  accountBody,
  accountEntryBody,
  COUNTER_START,
  CUSTOMER_ID,
  DISPUTE_ID,
  disputeBody,
  fakeServer,
  lineBody,
  linkBody,
  LINK_ID,
  ME_BASE,
  ok,
  openDisputeBody,
  SHOP_BASE,
  SHOP_ID,
  START,
} from "../testing/fakeServer";
import { ApiError, BAD_RESPONSE, cleanReason, createApi, REASON_MAX, REASON_MIN } from "./api";

const auth = { kind: "bearer", token: "t" } as const;
const KEY = "key-12345678";
const WAITING_ID = "99999999-9999-4999-8999-999999999999";
const ENTRY_ID = "22222222-2222-4222-8222-222222222222";

function client(body: unknown, status = 200) {
  const server = fakeServer(() => ok(body, status));
  const api = createApi({ fetch: server.fetch, auth });
  return { server, api, shop: api.shop(SHOP_ID), account: api.account(LINK_ID) };
}

const failure = (promise: Promise<unknown>) =>
  promise.then(
    () => {
      throw new Error("expected a failure");
    },
    (error: unknown) => error as ApiError,
  );

describe("a reason", () => {
  it("is 3 to 300 characters once white space is tidied, as the server counts it", () => {
    expect([REASON_MIN, REASON_MAX]).toEqual([3, 300]);
    expect(cleanReason("  bu   mening\n qarzim emas ")).toBe("bu mening qarzim emas");
    expect(cleanReason("abc")).toBe("abc");
    expect(cleanReason("ab")).toBeNull();
    expect(cleanReason(" a  b ")).toBe("a b");
    expect(cleanReason("   ")).toBeNull();
    expect(cleanReason("x".repeat(300))).toHaveLength(300);
    expect(cleanReason("x".repeat(301))).toBeNull();
    // 301 characters typed, 300 once the doubled space is one.
    expect(cleanReason(`${"x".repeat(150)}  ${"y".repeat(149)}`)).toHaveLength(300);
  });
});

describe("connecting customers (staff)", () => {
  it("reads the link state", async () => {
    const { server, shop } = client(linkBody({ linked: true, status: "active", since: "2026-10-01T05:00:00+00:00" }));
    expect(await shop.readLink(CUSTOMER_ID)).toEqual({ linked: true, status: "active", since: "2026-10-01T05:00:00+00:00" });
    expect(server.sent[0]).toMatchObject({ method: "GET", path: `${SHOP_BASE}/customers/${CUSTOMER_ID}/link` });
    expect(server.sent[0]?.headers["Idempotency-Key"]).toBeUndefined();
  });

  it("issues a personal link with a key and no body, and keeps the start code out of the address", async () => {
    const { server, shop } = client({ token: START.slice(2), start: START, expires_at: "2026-10-13T07:00:00+00:00" }, 201);
    expect(await shop.createLink(CUSTOMER_ID, KEY)).toEqual({ start: START, expiresAt: "2026-10-13T07:00:00+00:00" });
    expect(server.sent[0]).toMatchObject({ method: "POST", path: `${SHOP_BASE}/customers/${CUSTOMER_ID}/link`, query: {} });
    expect(server.sent[0]?.headers["Idempotency-Key"]).toBe(KEY);
    expect(server.sent[0]?.body).toBeUndefined();
  });

  it("reads a repeated answer whose code the server no longer has", async () => {
    const { shop } = client({ token: null, start: null, expires_at: "2026-10-13T07:00:00+00:00" }, 201);
    expect((await shop.createLink(CUSTOMER_ID, KEY)).start).toBeNull();
  });

  it("reads and rotates the counter code", async () => {
    const read = client({ exists: true, since: "2026-09-01T05:00:00+00:00" });
    expect(await read.shop.readCounterCode()).toEqual({ exists: true, since: "2026-09-01T05:00:00+00:00" });
    expect(read.server.sent[0]).toMatchObject({ method: "GET", path: `${SHOP_BASE}/counter-code` });

    const rotate = client({ token: COUNTER_START.slice(2), start: COUNTER_START }, 201);
    expect(await rotate.shop.rotateCounterCode(KEY)).toEqual({ start: COUNTER_START, expiresAt: null });
    expect(rotate.server.sent[0]).toMatchObject({ method: "POST", path: `${SHOP_BASE}/counter-code` });
    expect(rotate.server.sent[0]?.headers["Idempotency-Key"]).toBe(KEY);
  });

  it("lists, attaches and dismisses the people who wait", async () => {
    const list = client({ items: [{ id: WAITING_ID, name: "Vali", since: "2026-10-06T05:00:00+00:00" }] });
    expect(await list.shop.listWaiting()).toEqual([{ id: WAITING_ID, name: "Vali", since: "2026-10-06T05:00:00+00:00" }]);
    expect(list.server.sent[0]).toMatchObject({ method: "GET", path: `${SHOP_BASE}/waiting` });

    const attach = client({ customer_id: CUSTOMER_ID, linked: true });
    await attach.shop.attachWaiting(WAITING_ID, CUSTOMER_ID, KEY);
    expect(attach.server.sent[0]).toMatchObject({
      method: "POST",
      path: `${SHOP_BASE}/waiting/${WAITING_ID}/attach`,
      body: { customer_id: CUSTOMER_ID },
    });
    expect(attach.server.sent[0]?.headers["Idempotency-Key"]).toBe(KEY);

    const dismiss = client({ dismissed: true });
    await dismiss.shop.dismissWaiting(WAITING_ID, KEY);
    expect(dismiss.server.sent[0]).toMatchObject({ method: "POST", path: `${SHOP_BASE}/waiting/${WAITING_ID}/dismiss` });
    expect(dismiss.server.sent[0]?.body).toBeUndefined();
    expect(dismiss.server.sent[0]?.headers["Idempotency-Key"]).toBe(KEY);
  });

  it("refuses a staff write without a usable key before anything is sent", async () => {
    const { server, shop } = client({});
    await expect(shop.createLink(CUSTOMER_ID, "short")).rejects.toBeInstanceOf(RangeError);
    await expect(shop.rotateCounterCode("")).rejects.toBeInstanceOf(RangeError);
    await expect(shop.dismissWaiting(WAITING_ID, "has space 123")).rejects.toBeInstanceOf(RangeError);
    expect(server.sent).toHaveLength(0);
  });
});

describe("disputes (staff)", () => {
  it("lists open disputes with whole amounts", async () => {
    const { server, shop } = client({ items: [openDisputeBody()] });
    expect(await shop.listDisputes()).toEqual([
      {
        id: DISPUTE_ID,
        status: "open",
        reason: "Men bu tovarni olmaganman",
        declineReason: null,
        entryId: ENTRY_ID,
        createdAt: "2026-10-06T05:10:00+00:00",
        customerId: CUSTOMER_ID,
        customerName: "Ali Valiyev",
        amount: 45000,
      },
    ]);
    expect(server.sent[0]).toMatchObject({ method: "GET", path: `${SHOP_BASE}/disputes` });

    const odd = client({ items: [openDisputeBody({ amount: 45000.5 })] });
    expect((await failure(odd.shop.listDisputes())).code).toBe(BAD_RESPONSE);
  });

  it("declines with a tidied reason and a key", async () => {
    const { server, shop } = client(openDisputeBody({ status: "declined" }));
    await shop.declineDispute(DISPUTE_ID, "  Tovar   berilgan, imzo bor ", KEY);
    expect(server.sent[0]).toMatchObject({
      method: "POST",
      path: `${SHOP_BASE}/disputes/${DISPUTE_ID}/decline`,
      body: { reason: "Tovar berilgan, imzo bor" },
    });
    expect(server.sent[0]?.headers["Idempotency-Key"]).toBe(KEY);
  });

  it("sends no reason the server would refuse", async () => {
    const { server, shop, account } = client({});
    expect(() => shop.declineDispute(DISPUTE_ID, "yo", KEY)).toThrow(RangeError);
    expect(() => shop.declineDispute(DISPUTE_ID, "x".repeat(301), KEY)).toThrow(RangeError);
    expect(() => account.openDispute(ENTRY_ID, "  a ")).toThrow(RangeError);
    expect(server.sent).toHaveLength(0);
  });
});

describe("a person's own accounts", () => {
  it("lists the accounts", async () => {
    const { server, api } = client({
      items: [{ link_id: LINK_ID, shop_name: "Baraka savdo", display_name: "Ali Valiyev", balance: 120000 }],
    });
    expect(await api.myAccounts()).toEqual([
      { linkId: LINK_ID, shopName: "Baraka savdo", displayName: "Ali Valiyev", balance: 120000 },
    ]);
    expect(server.sent[0]).toMatchObject({ method: "GET", path: "/api/v1/me/accounts" });
  });

  it("reads one account with its entries, disputes and goods lines", async () => {
    const { server, account } = client(
      accountBody({
        overdue: { amount: 45000, due_today: 10000 },
        removal_requested: true,
        entries: [
          accountEntryBody({
            disputed: true,
            dispute: disputeBody({ status: "declined", decline_reason: "Imzo bor" }),
            lines: [lineBody({ qty: "1.500", unit: "kg", line_total: 6000 })],
          }),
        ],
        entries_total: 7,
      }),
    );
    const detail = await account.read();
    expect(server.sent[0]).toMatchObject({ method: "GET", path: ME_BASE });
    expect(detail).toMatchObject({
      linkId: LINK_ID,
      shopName: "Baraka savdo",
      displayName: "Ali Valiyev",
      balance: 120000,
      overdueAmount: 45000,
      dueToday: 10000,
      removalRequested: true,
      entriesTotal: 7,
    });
    expect(detail.entries[0]).toMatchObject({
      id: ENTRY_ID,
      kind: "credit",
      amount: 45000,
      promisedDate: "2026-11-05",
      reversed: false,
      disputed: true,
      dispute: { id: DISPUTE_ID, status: "declined", reason: "Men bu tovarni olmaganman", declineReason: "Imzo bor" },
    });
    expect(detail.entries[0]?.lines).toEqual([
      { lineNo: 1, catalogItemId: expect.any(String), name: "Non", qty: 1500, unit: "kg", unitPrice: 4000, lineTotal: 6000 },
    ]);
  });

  it("treats an entry without the lines field as an entry without goods", async () => {
    const entry: Record<string, unknown> = accountEntryBody();
    delete entry["lines"];
    const { account } = client(accountBody({ entries: [entry] }));
    expect((await account.read()).entries[0]?.lines).toEqual([]);
  });

  it.each([
    ["a balance with a fraction", accountBody({ balance: 120000.5 })],
    ["an overdue amount as text", accountBody({ overdue: { amount: "45000", due_today: 0 } })],
    ["an entry amount with a fraction", accountBody({ entries: [accountEntryBody({ amount: 0.1 })] })],
    ["a dispute without a status", accountBody({ entries: [accountEntryBody({ dispute: { id: DISPUTE_ID, reason: "x" } })] })],
  ])("refuses %s", async (_what, body) => {
    expect((await failure(client(body).account.read())).code).toBe(BAD_RESPONSE);
  });

  it("sends the customer's writes without an idempotency key", async () => {
    const disconnect = client({ disconnected: true });
    await disconnect.account.disconnect();
    expect(disconnect.server.sent[0]).toMatchObject({ method: "POST", path: `${ME_BASE}/disconnect` });

    const removal = client({ removed: false, waiting_for_balance: 120000 });
    expect(await removal.account.requestRemoval()).toEqual({ removed: false, waitingForBalance: 120000 });
    expect(removal.server.sent[0]).toMatchObject({ method: "POST", path: `${ME_BASE}/removal` });

    const gone = client({ removed: true, waiting_for_balance: null });
    expect(await gone.account.requestRemoval()).toEqual({ removed: true, waitingForBalance: null });

    const open = client({ ...disputeBody(), entry_id: ENTRY_ID, created_at: "2026-10-06T05:10:00+00:00" }, 201);
    expect(await open.account.openDispute(ENTRY_ID, " Men  olmaganman ")).toMatchObject({ id: DISPUTE_ID, status: "open" });
    expect(open.server.sent[0]).toMatchObject({
      method: "POST",
      path: `${ME_BASE}/disputes`,
      body: { entry_id: ENTRY_ID, reason: "Men olmaganman" },
    });

    const withdraw = client(disputeBody({ status: "withdrawn" }));
    expect((await withdraw.account.withdrawDispute(DISPUTE_ID)).status).toBe("withdrawn");
    expect(withdraw.server.sent[0]).toMatchObject({ method: "POST", path: `${ME_BASE}/disputes/${DISPUTE_ID}/withdraw` });

    for (const { server } of [disconnect, removal, gone, open, withdraw]) {
      expect(server.sent[0]?.headers["Idempotency-Key"]).toBeUndefined();
    }
  });

  it("has no call that confirms an entry: a customer is never asked to (BR-10)", () => {
    const { account } = client({});
    expect(Object.keys(account).sort()).toEqual([
      "disconnect",
      "openDateRequest",
      "openDispute",
      "read",
      "requestRemoval",
      "sendPaymentNotice",
      "withdrawDispute",
    ]);
  });
});

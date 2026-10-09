import { describe, expect, it } from "vitest";

import { fakeServer, ok, refusal, SHOP_ID } from "../../testing/fakeServer";
import { type ApiError, BAD_RESPONSE, createApi } from "../api";
import { draftBody as requestBody, networkOf } from "./networkApi";
import {
  CODE,
  COUNTERPART_ID,
  draftBody,
  inviteBody,
  INVITE_ID,
  linkBody,
  NET_LINK_ID,
  NETWORK,
  NOTE_ID,
  noteBody,
  ORDER_ID,
  orderBody,
  overviewBody,
  OWN_ITEM_ID,
  PAYMENT_ID,
  paymentBody,
  reconciliationBody,
  STOCK_DOCUMENT_ID,
  waitingBody,
} from "./testing";

const auth = { kind: "bearer", token: "t" } as const;
const KEY = "key-0000-0000-0001";

function client(server: ReturnType<typeof fakeServer>) {
  return networkOf(createApi({ fetch: server.fetch, auth }).shop(SHOP_ID));
}

async function failure(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
  } catch (error) {
    return error as ApiError;
  }
  throw new Error("expected a failure");
}

describe("whether the network exists", () => {
  const body = { items: [], active_shop: null };

  it("is learned from a header of the person's shops, and only from the word 'on'", async () => {
    const on = fakeServer(() => ({ status: 200, body, headers: { "X-Qarz-Network": "on" } }));
    expect((await createApi({ fetch: on.fetch, auth }).myShops()).networkOn).toBe(true);
    for (const headers of [{}, { "X-Qarz-Network": "off" }, { "X-Qarz-Network": "true" }, { "X-Qarz-Network": "" }, { "X-Qarz-Stock": "on" }]) {
      const other = fakeServer(() => ({ status: 200, body, headers }));
      expect((await createApi({ fetch: other.fetch, auth }).myShops()).networkOn, JSON.stringify(headers)).toBe(false);
    }
  });

  it("is told apart from the stock and the cash book: each has its own header", async () => {
    const all = fakeServer(() => ({ status: 200, body, headers: { "X-Qarz-Network": "on", "X-Qarz-Stock": "on" } }));
    expect(await createApi({ fetch: all.fetch, auth }).myShops()).toMatchObject({ networkOn: true, stockOn: true, cashBookOn: false });
  });
});

describe("reading the network", () => {
  it("reads the overview: the links, what awaits this shop, and the invitations when they are sent", async () => {
    const server = fakeServer(() => ok(overviewBody({ waiting: waitingBody({ notes: 2, rejected_notes: 1 }), invites: [inviteBody()] })));
    const overview = await client(server).overview();
    expect(server.sent[0]).toMatchObject({ method: "GET", path: NETWORK });
    expect(overview.waiting).toEqual({ links: 0, orders: 0, deliveries: 0, notes: 2, rejectedNotes: 1, payments: 0 });
    expect(overview.links[0]).toMatchObject({ id: NET_LINK_ID, role: "buyer", state: "active", invited: false, counterpart: { kind: "supplier", id: COUNTERPART_ID } });
    expect(overview.invites).toEqual([{ id: INVITE_ID, as: "supplier", expiresAt: "2026-10-08T07:00:00+00:00" }]);
    // A member who may not manage the links is sent no invitations: absent, not an empty list.
    expect("invites" in (await client(fakeServer(() => ok(overviewBody()))).overview())).toBe(false);
  });

  it("keeps the shop's own account apart from the agreed one, and absent when the server leaves it out", async () => {
    const row = reconciliationBody({ awaiting: { notes: 1, payments_own: 2, payments_partner: 3, payments_declined: 4 }, own_balance: 480000, difference: -20000 });
    const hidden = reconciliationBody();
    delete (hidden as Record<string, unknown>)["own_balance"];
    delete (hidden as Record<string, unknown>)["difference"];
    const server = fakeServer(() => ok({ link: linkBody(), reconciliation: [row, { ...hidden, currency: "USD" }], events: [] }));
    const page = await client(server).link(NET_LINK_ID);
    expect(server.sent[0]?.path).toBe(`${NETWORK}/links/${NET_LINK_ID}`);
    expect(page.reconciliation[0]).toEqual({
      currency: "UZS",
      agreed: { delivered: 900000, paid: 400000, balance: 500000 },
      awaiting: { notes: 1, paymentsOwn: 2, paymentsPartner: 3, paymentsDeclined: 4 },
      ownBalance: 480000,
      difference: -20000,
    });
    expect(page.reconciliation[1]?.currency).toBe("USD");
    expect("ownBalance" in (page.reconciliation[1] ?? {})).toBe(false);
    expect("difference" in (page.reconciliation[1] ?? {})).toBe(false);
  });

  it("reads an order with its lines, and marks the ones the supplier changed", async () => {
    const lines = [
      { line_no: 1, name: "Shakar", unit: "kg", qty: "50", item_id: OWN_ITEM_ID, accepted_qty: "40.5", unit_price: 12000, line_total: 486000, changed: true },
    ];
    const order = await client(fakeServer(() => ok(orderBody({ status: "accepted", currency: "UZS", total: 486000, lines })))).order(ORDER_ID);
    expect(order.lines).toEqual([
      { lineNo: 1, name: "Shakar", unit: "kg", qty: "50", itemId: OWN_ITEM_ID, acceptedQty: "40.5", unitPrice: 12000, lineTotal: 486000, changed: true },
    ]);
    expect(order).toMatchObject({ number: 12, role: "supplier", status: "accepted", partnerName: "Ziyo market", total: 486000, deliveryNote: null });
  });

  it("reads a note, with the stock document it became once it is confirmed", async () => {
    const issued = await client(fakeServer(() => ok(noteBody()))).note(NOTE_ID);
    expect(issued).toMatchObject({ number: 5, orderNumber: 12, status: "issued", terms: "credit", total: 600000, paid: 0 });
    expect("stockDocumentId" in issued).toBe(false);
    const received = await client(fakeServer(() => ok(noteBody({ status: "received", stock_document_id: STOCK_DOCUMENT_ID })))).note(NOTE_ID);
    expect(received.stockDocumentId).toBe(STOCK_DOCUMENT_ID);
  });

  it("asks the lists with their filters, and leaves out the ones that were not given", async () => {
    const server = fakeServer((sent) =>
      ok(sent.path.endsWith("/orders") ? { orders: [orderBody({ lines: undefined })], next_cursor: "next" } : sent.path.endsWith("/notes") ? { notes: [], next_cursor: null } : { payments: [paymentBody()], next_cursor: null }),
    );
    const network = client(server);
    const orders = await network.orders({ role: "supplier", status: "sent", cursor: null });
    expect(orders.nextCursor).toBe("next");
    expect(server.sent[0]).toMatchObject({ path: `${NETWORK}/orders`, query: { role: "supplier", status: "sent" } });
    await network.notes({ link: NET_LINK_ID, limit: 20 });
    expect(server.sent[1]).toMatchObject({ path: `${NETWORK}/notes`, query: { link: NET_LINK_ID, limit: "20" } });
    const payments = await network.payments({ status: "awaiting" });
    expect(server.sent[2]).toMatchObject({ path: `${NETWORK}/payments`, query: { status: "awaiting" } });
    expect(payments.items[0]).toMatchObject({ recordedBy: "partner", status: "awaiting", amount: 250000, inOwnBooks: false });
  });

  it("refuses an answer that is not the contract instead of showing it", async () => {
    for (const body of [overviewBody({ links: [linkBody({ role: "middleman" })] }), overviewBody({ waiting: { links: "1" } }), { links: [] }]) {
      expect((await failure(client(fakeServer(() => ok(body))).overview())).code).toBe(BAD_RESPONSE);
    }
    expect((await failure(client(fakeServer(() => ok(noteBody({ lines: [{ line_no: 1, name: "x", unit: "kg", qty: "1.2345", unit_price: 1, line_total: 1 }] })))).note(NOTE_ID))).code).toBe(BAD_RESPONSE);
  });

  it("passes the server's refusal on, with its code, its words and its fields", async () => {
    const server = fakeServer(() => refusal(404, "NETWORK_INVITE_INVALID", "Kod noto'g'ri yoki muddati o'tgan."));
    const error = await failure(client(server).requestLink(CODE, "buyer", KEY));
    expect(error).toMatchObject({ status: 404, code: "NETWORK_INVITE_INVALID", serverMessage: "Kod noto'g'ri yoki muddati o'tgan." });
    const taken = fakeServer(() => refusal(409, "CATALOG_NAME_TAKEN", "Bunday tovar bor.", { existing_id: OWN_ITEM_ID }));
    expect((await failure(client(taken).confirmNote(NOTE_ID, { lines: [], method: null }, KEY))).fields).toEqual({ existing_id: OWN_ITEM_ID });
  });
});

describe("writing to the network", () => {
  /** Every write: where it goes, what it says, and what it answers with. */
  const WRITES: readonly [string, (network: ReturnType<typeof client>) => Promise<unknown>, string, string, unknown, unknown][] = [
    ["an invitation", (n) => n.createInvite("supplier", KEY), "POST", "/invites", { as: "supplier" }, { ...inviteBody(), code: CODE }],
    ["an invitation taken back", (n) => n.withdrawInvite(INVITE_ID, KEY), "DELETE", `/invites/${INVITE_ID}`, undefined, null],
    ["a request to link", (n) => n.requestLink(CODE, "buyer", KEY), "POST", "/links", { code: CODE, as: "buyer" }, { link: linkBody({ state: "requested" }) }],
    ["an acceptance that makes a new row", (n) => n.acceptLink(NET_LINK_ID, null, KEY), "POST", `/links/${NET_LINK_ID}/accept`, undefined, { link: linkBody() }],
    [
      "an acceptance onto a row of the books",
      (n) => n.acceptLink(NET_LINK_ID, COUNTERPART_ID, KEY),
      "POST",
      `/links/${NET_LINK_ID}/accept`,
      { counterpart_id: COUNTERPART_ID },
      { link: linkBody() },
    ],
    ["a refusal to link", (n) => n.declineLink(NET_LINK_ID, KEY), "POST", `/links/${NET_LINK_ID}/decline`, undefined, { link: linkBody({ state: "declined" }) }],
    ["the end of a link", (n) => n.endLink(NET_LINK_ID, KEY), "POST", `/links/${NET_LINK_ID}/end`, undefined, { link: linkBody({ state: "ended", ended_by: "own" }) }],
    [
      "the row of the books a link stands for",
      (n) => n.setCounterpart(NET_LINK_ID, COUNTERPART_ID, KEY),
      "PUT",
      `/links/${NET_LINK_ID}/counterpart`,
      { counterpart_id: COUNTERPART_ID },
      { link: linkBody() },
    ],
    [
      "a draft",
      (n) => n.createDraft({ linkId: NET_LINK_ID, note: null, wantedDate: "2026-10-09", lines: [{ name: "Shakar", unit: "kg", qty: "50", itemId: null }] }, KEY),
      "POST",
      "/drafts",
      { link_id: NET_LINK_ID, wanted_date: "2026-10-09", lines: [{ name: "Shakar", unit: "kg", qty: "50" }] },
      draftBody(),
    ],
    [
      "a draft written again",
      (n) => n.updateDraft(ORDER_ID, { linkId: NET_LINK_ID, note: "Ertalab", wantedDate: null, lines: [{ name: "Shakar", unit: "kg", qty: "1.5", itemId: OWN_ITEM_ID }] }, KEY),
      "PUT",
      `/drafts/${ORDER_ID}`,
      { link_id: NET_LINK_ID, note: "Ertalab", lines: [{ name: "Shakar", unit: "kg", qty: "1.5", item_id: OWN_ITEM_ID }] },
      draftBody(),
    ],
    ["a draft thrown away", (n) => n.deleteDraft(ORDER_ID, KEY), "DELETE", `/drafts/${ORDER_ID}`, undefined, null],
    ["a draft sent", (n) => n.sendDraft(ORDER_ID, KEY), "POST", `/drafts/${ORDER_ID}/send`, undefined, orderBody({ role: "buyer" })],
    [
      "the answer to an order",
      (n) =>
        n.acceptOrder(
          ORDER_ID,
          { currency: "USD", lines: [{ lineNo: 1, qty: "40.5", unitPrice: 1250, itemId: OWN_ITEM_ID }, { lineNo: 2, qty: "0", unitPrice: 0, itemId: null }] },
          KEY,
        ),
      "POST",
      `/orders/${ORDER_ID}/accept`,
      { currency: "USD", lines: [{ line_no: 1, qty: "40.5", unit_price: 1250, item_id: OWN_ITEM_ID }, { line_no: 2, qty: "0", unit_price: 0 }] },
      orderBody({ status: "accepted" }),
    ],
    ["an order declined", (n) => n.declineOrder(ORDER_ID, "Omborda yo'q", KEY), "POST", `/orders/${ORDER_ID}/decline`, { reason: "Omborda yo'q" }, orderBody({ status: "declined" })],
    ["an order cancelled", (n) => n.cancelOrder(ORDER_ID, "Kerak emas", KEY), "POST", `/orders/${ORDER_ID}/cancel`, { reason: "Kerak emas" }, orderBody({ status: "cancelled" })],
    ["a delivery all on credit", (n) => n.deliver(ORDER_ID, null, KEY), "POST", `/orders/${ORDER_ID}/deliver`, {}, noteBody({ role: "supplier" })],
    ["a delivery partly paid", (n) => n.deliver(ORDER_ID, 100000, KEY), "POST", `/orders/${ORDER_ID}/deliver`, { paid: 100000 }, noteBody({ role: "supplier" })],
    [
      "a note corrected",
      (n) => n.correctNote(NOTE_ID, { reason: "Narx xato", paid: 0, lines: [{ lineNo: 1, qty: "48", unitPrice: 12000 }] }, KEY),
      "POST",
      `/notes/${NOTE_ID}/correct`,
      { reason: "Narx xato", paid: 0, lines: [{ line_no: 1, qty: "48", unit_price: 12000 }] },
      noteBody({ role: "supplier" }),
    ],
    [
      "a note corrected in its reason alone",
      (n) => n.correctNote(NOTE_ID, { reason: "Sana xato", paid: null, lines: null }, KEY),
      "POST",
      `/notes/${NOTE_ID}/correct`,
      { reason: "Sana xato" },
      noteBody({ role: "supplier" }),
    ],
    [
      "a note confirmed",
      (n) =>
        n.confirmNote(
          NOTE_ID,
          { lines: [{ lineNo: 1, itemId: OWN_ITEM_ID }, { lineNo: 2, newPrice: 15000, newBarcode: null }, { lineNo: 3, newPrice: 9000, newBarcode: "4006381333931" }], method: "cash" },
          KEY,
        ),
      "POST",
      `/notes/${NOTE_ID}/confirm`,
      { lines: [{ line_no: 1, item_id: OWN_ITEM_ID }, { line_no: 2, new_price: 15000 }, { line_no: 3, new_price: 9000, new_barcode: "4006381333931" }], method: "cash" },
      noteBody({ status: "received" }),
    ],
    [
      "a note rejected",
      (n) => n.rejectNote(NOTE_ID, { reason: "Kam keldi", lines: [{ lineNo: 1, receivedQty: "45" }] }, KEY),
      "POST",
      `/notes/${NOTE_ID}/reject`,
      { reason: "Kam keldi", lines: [{ line_no: 1, received_qty: "45" }] },
      noteBody({ status: "rejected" }),
    ],
    [
      "a payment",
      (n) => n.recordPayment({ linkId: NET_LINK_ID, amount: 250000, currency: "UZS", method: "transfer", note: null }, KEY),
      "POST",
      "/payments",
      { link_id: NET_LINK_ID, amount: 250000, currency: "UZS", method: "transfer" },
      paymentBody({ recorded_by: "own", in_own_books: true }),
    ],
    ["a payment confirmed", (n) => n.confirmPayment(PAYMENT_ID, null, KEY), "POST", `/payments/${PAYMENT_ID}/confirm`, {}, paymentBody({ status: "confirmed" })],
    ["a payment confirmed with its method", (n) => n.confirmPayment(PAYMENT_ID, "card", KEY), "POST", `/payments/${PAYMENT_ID}/confirm`, { method: "card" }, paymentBody({ status: "confirmed" })],
    ["a payment declined", (n) => n.declinePayment(PAYMENT_ID, "Pul kelmadi", KEY), "POST", `/payments/${PAYMENT_ID}/decline`, { reason: "Pul kelmadi" }, paymentBody({ status: "declined" })],
    ["a payment taken back", (n) => n.withdrawPayment(PAYMENT_ID, KEY), "POST", `/payments/${PAYMENT_ID}/withdraw`, undefined, paymentBody({ status: "withdrawn" })],
  ];

  it.each(WRITES)("sends %s once, to its own address, with the key of the action", async (_name, write, method, path, body, answer) => {
    const server = fakeServer(() => (ok(answer, 201)));
    await write(client(server));
    expect(server.sent).toHaveLength(1);
    expect(server.sent[0]).toMatchObject({ method, path: `${NETWORK}${path}` });
    expect(server.sent[0]?.body).toEqual(body);
    expect(server.sent[0]?.headers["Idempotency-Key"]).toBe(KEY);
  });

  it("answers an invitation with its code, which no later reading carries", async () => {
    const made = await client(fakeServer(() => ok({ ...inviteBody(), code: CODE }, 201))).createInvite("supplier", KEY);
    expect(made).toEqual({ id: INVITE_ID, as: "supplier", expiresAt: "2026-10-08T07:00:00+00:00", code: CODE });
    const listed = await client(fakeServer(() => ok(overviewBody({ invites: [inviteBody()] })))).overview();
    expect(JSON.stringify(listed)).not.toContain("code");
  });

  it("leaves out of a draft what does not apply: the server refuses extras", () => {
    expect(requestBody({ linkId: NET_LINK_ID, note: "", wantedDate: "", lines: [] })).toEqual({ link_id: NET_LINK_ID, lines: [] });
    expect(requestBody({ linkId: NET_LINK_ID, note: null, wantedDate: null, lines: [{ name: "Un", unit: "qop", qty: "2", itemId: null }] })).toEqual({
      link_id: NET_LINK_ID,
      lines: [{ name: "Un", unit: "qop", qty: "2" }],
    });
  });
});

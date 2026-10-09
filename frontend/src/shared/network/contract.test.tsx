// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { customerBody, fakeServer, ok, refusal, type Reply, type Sent, SHOP_BASE } from "../../testing/fakeServer";
import { renderScreen } from "../../testing/renderScreen";
import { manySuppliers, stockSettingsBody, supplierListBody } from "../stock/testing";
import { HomeScreen, LinkScreen } from "./LinkScreens";
import { NoteScreen } from "./NoteScreens";
import { ComposeScreen } from "./OrderComposer";
import { OrderScreen } from "./OrderScreens";
import { PaymentsScreen } from "./PaymentScreens";
import {
  eventBody,
  linkBody,
  NET_LINK_ID,
  NETWORK,
  NOTE_ID,
  noteBody,
  ORDER_ID,
  orderBody,
  overviewBody,
  OWN_ITEM_ID,
  paymentBody,
  reconciliationBody,
  STOCK_DOCUMENT_ID,
} from "./testing";

/**
 * The places where the screens of the network were written against a described contract and guessed.
 * Each was checked against the server (backend/tests/api/test_network_contract.py holds the server's
 * side of the same points) and is held here to what the server does.
 */

beforeEach(() => {
  window.location.hash = "";
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const NOT_FOUND = refusal(404, "NOT_FOUND", "Topilmadi.");
const ON = { network: true, stock: true } as const;

function backend(reads: Record<string, unknown>, write: (sent: Sent, index: number) => Reply = () => NOT_FOUND) {
  let writes = 0;
  return fakeServer((sent) => {
    if (sent.method !== "GET") {
      writes += 1;
      return write(sent, writes - 1);
    }
    if (sent.path === `${SHOP_BASE}/stock/settings`) {
      return ok(stockSettingsBody());
    }
    return sent.path in reads ? ok(reads[sent.path]) : NOT_FOUND;
  });
}

const rows = (name: string) => within(screen.getByRole("list", { name })).getAllByRole("listitem");
const ORDER_PATH = `${NETWORK}/orders/${ORDER_ID}`;
const NOTE_PATH = `${NETWORK}/notes/${NOTE_ID}`;

describe("closing an order (the server's may_close_order)", () => {
  const delivered = (note: string, role: "buyer" | "supplier") =>
    orderBody({ role, status: "delivered", currency: "UZS", total: 600000, delivery_note: noteBody({ role, status: note }) });

  it("lets the buyer cancel after a delivery whose note it rejected, and not while the note awaits its answer", async () => {
    const server = backend({ [ORDER_PATH]: delivered("rejected", "buyer") }, () => ok(delivered("rejected", "buyer")));
    renderScreen(<OrderScreen orderId={ORDER_ID} />, { fetch: server.fetch, role: "manager", features: ON });
    fireEvent.click(await screen.findByRole("button", { name: "Buyurtmani bekor qilish" }));
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "Kerak emas" } });
    fireEvent.click(screen.getByRole("button", { name: "Buyurtmani bekor qilish" }));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]).toMatchObject({ method: "POST", path: `${ORDER_PATH}/cancel`, body: { reason: "Kerak emas" } });
    cleanup();
    renderScreen(<OrderScreen orderId={ORDER_ID} />, { fetch: backend({ [ORDER_PATH]: delivered("issued", "buyer") }).fetch, role: "manager", features: ON });
    await screen.findByRole("heading", { name: "Buyurtma № 12" });
    expect(screen.queryByRole("button", { name: "Buyurtmani bekor qilish" })).toBeNull();
    cleanup();
    // Once received, nothing closes it.
    renderScreen(<OrderScreen orderId={ORDER_ID} />, { fetch: backend({ [ORDER_PATH]: orderBody({ role: "buyer", status: "received" }) }).fetch, role: "manager", features: ON });
    await screen.findByRole("heading", { name: "Buyurtma № 12" });
    expect(screen.queryByRole("button", { name: "Buyurtmani bekor qilish" })).toBeNull();
  });

  it("lets the supplier decline what it accepted, and after a rejected note, but offers accepting only once", async () => {
    const accepted = orderBody({ status: "accepted", currency: "UZS", total: 600000 });
    const server = backend({ [ORDER_PATH]: accepted }, () => ok(accepted));
    renderScreen(<OrderScreen orderId={ORDER_ID} />, { fetch: server.fetch, role: "manager", features: ON });
    fireEvent.click(await screen.findByRole("button", { name: "Buyurtmani rad etish" }));
    expect(screen.queryByRole("button", { name: "Buyurtmani qabul qilish" })).toBeNull();
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "Tovar tugadi" } });
    fireEvent.click(screen.getByRole("button", { name: "Buyurtmani rad etish" }));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]).toMatchObject({ path: `${ORDER_PATH}/decline`, body: { reason: "Tovar tugadi" } });
    cleanup();
    renderScreen(<OrderScreen orderId={ORDER_ID} />, { fetch: backend({ [ORDER_PATH]: delivered("rejected", "supplier") }).fetch, role: "manager", features: ON });
    expect(await screen.findByRole("button", { name: "Buyurtmani rad etish" })).toBeTruthy();
    cleanup();
    renderScreen(<OrderScreen orderId={ORDER_ID} />, { fetch: backend({ [ORDER_PATH]: delivered("issued", "supplier") }).fetch, role: "manager", features: ON });
    await screen.findByRole("heading", { name: "Buyurtma № 12" });
    expect(screen.queryByRole("button", { name: "Buyurtmani rad etish" })).toBeNull();
  });
});

describe("taking a payment back (the server's PaymentService.withdraw)", () => {
  const TAKE_BACK = "Qaytarib olish";
  const ours = (overrides: Record<string, unknown>) => paymentBody({ recorded_by: "own", in_own_books: true, ...overrides });
  const show = async (payment: Record<string, unknown>, permissions: string[]) => {
    const reads = { [NETWORK]: overviewBody(), [`${NETWORK}/payments`]: { payments: [payment], next_cursor: null } };
    renderScreen(<PaymentsScreen />, { fetch: backend(reads).fetch, role: "seller", permissions, features: ON });
    await waitFor(() => rows("To'lovlar"));
  };

  it("asks the supplier's side for the right to cancel an entry, not the right to record a payment", async () => {
    await show(ours({ role: "supplier" }), ["network.view", "network.confirm", "payments.record"]);
    expect(screen.queryByRole("button", { name: TAKE_BACK })).toBeNull();
    cleanup();
    await show(ours({ role: "supplier" }), ["network.view", "network.confirm", "entries.cancel"]);
    expect(screen.getByRole("button", { name: TAKE_BACK })).toBeTruthy();
  });

  it("asks the buyer's side for the right to pay a supplier", async () => {
    await show(ours({ role: "buyer" }), ["network.view", "network.confirm", "entries.cancel"]);
    expect(screen.queryByRole("button", { name: TAKE_BACK })).toBeNull();
    cleanup();
    await show(ours({ role: "buyer" }), ["network.view", "network.confirm", "suppliers.pay"]);
    expect(screen.getByRole("button", { name: TAKE_BACK })).toBeTruthy();
  });

  it("asks for nothing more once the shop's own entry was cancelled by hand, and never without network.confirm", async () => {
    await show(ours({ role: "supplier", in_own_books: false }), ["network.view", "network.confirm"]);
    expect(screen.getByRole("button", { name: TAKE_BACK })).toBeTruthy();
    cleanup();
    await show(ours({ role: "supplier", in_own_books: false }), ["network.view", "entries.cancel"]);
    expect(screen.queryByRole("button", { name: TAKE_BACK })).toBeNull();
    cleanup();
    // Only one that awaits an answer is taken back.
    await show(ours({ role: "buyer", status: "confirmed" }), ["network.view", "network.confirm", "suppliers.pay"]);
    expect(screen.queryByRole("button", { name: TAKE_BACK })).toBeNull();
  });
});

describe("correcting a delivery note (the server's CorrectNoteBody)", () => {
  const lines = [
    { line_no: 1, name: "Shakar", unit: "kg", qty: "45", unit_price: 12000, line_total: 540000, item_id: null, received_qty: null },
    // The order's third line: a note's line keeps the number it has on the order.
    { line_no: 3, name: "Choy", unit: "dona", qty: "10", unit_price: 6000, line_total: 60000, item_id: null, received_qty: null },
  ];
  // A note that was itself a correction: its lines are no longer the order's accepted ones.
  const corrected = noteBody({ role: "supplier", status: "rejected", total: 600000, paid: 100000, terms: "part", lines, supersedes_id: "d4d4d4d4-d4d4-4d4d-8d4d-d4d4d4d4d4d0" });
  const open = () => {
    const server = backend({ [NOTE_PATH]: corrected }, () => ok(noteBody({ role: "supplier", number: 7 }), 201));
    renderScreen(<NoteScreen noteId={NOTE_ID} office />, { fetch: server.fetch, role: "manager", features: ON });
    return server;
  };

  it("says the whole note again when nothing but the reason is typed: what was paid, and every line by the order's number", async () => {
    const server = open();
    fireEvent.click(await screen.findByRole("button", { name: "Yuk xatini tuzatish" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Tuzatish sababi" }), { target: { value: "Sana xato edi" } });
    fireEvent.click(screen.getByRole("button", { name: "Yangi yuk xatini yozish" }));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    // Left out, the server would take `paid` as 0 and the lines as the order's accepted ones.
    expect(server.writes()[0]?.body).toEqual({
      reason: "Sana xato edi",
      paid: 100000,
      lines: [
        { line_no: 1, qty: "45", unit_price: 12000 },
        { line_no: 3, qty: "10", unit_price: 6000 },
      ],
    });
  });

  it("leaves out a line of which nothing was delivered, and sends nothing when no line is left", async () => {
    const server = open();
    fireEvent.click(await screen.findByRole("button", { name: "Yuk xatini tuzatish" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Tuzatish sababi" }), { target: { value: "Choy kelmadi" } });
    fireEvent.change(screen.getByLabelText("Choy: miqdor, dona"), { target: { value: "0" } });
    fireEvent.change(screen.getByLabelText("Shakar: miqdor, kg"), { target: { value: "0" } });
    fireEvent.click(screen.getByRole("button", { name: "Yangi yuk xatini yozish" }));
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(server.writes()).toEqual([]);
    fireEvent.change(screen.getByLabelText("Shakar: miqdor, kg"), { target: { value: "45" } });
    fireEvent.change(screen.getByLabelText("Yetkazishda to'langan summa"), { target: { value: "0" } });
    fireEvent.click(screen.getByRole("button", { name: "Yangi yuk xatini yozish" }));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]?.body).toEqual({ reason: "Choy kelmadi", paid: 0, lines: [{ line_no: 1, qty: "45", unit_price: 12000 }] });
  });
});

describe("a name the catalogue already has (the server's CATALOG_NAME_TAKEN)", () => {
  const CONFIRM = "Tasdiqlash: tovar omborga kirim qilinadi, ta'minotchi daftariga nasiya savdo yoziladi";

  it("offers the existing item for the line the server names in fields.line, of two that were added as new", async () => {
    const lines = [
      { line_no: 2, name: "Shakar", unit: "kg", qty: "50", unit_price: 12000, line_total: 600000, item_id: null, received_qty: null },
      { line_no: 5, name: "Choy", unit: "dona", qty: "10", unit_price: 6000, line_total: 60000, item_id: null, received_qty: null },
    ];
    const server = backend({ [NOTE_PATH]: noteBody({ lines, total: 660000 }), [`${SHOP_BASE}/stock/items`]: { items: [], next_cursor: null } }, (_, index) =>
      // `line` is the place of the line in the receipt, from zero: the note's second line, numbered 5.
      index === 0 ? refusal(409, "CATALOG_NAME_TAKEN", "Bunday nomli tovar bor.", { line: "1", existing_id: OWN_ITEM_ID }) : ok(noteBody({ status: "received" })),
    );
    renderScreen(<NoteScreen noteId={NOTE_ID} office={false} />, { fetch: server.fetch, role: "manager", features: ON });
    fireEvent.click(await screen.findByRole("button", { name: "Yukni qabul qilish" }));
    for (const button of screen.getAllByRole("button", { name: "Yangi tovar sifatida qo'shish" })) {
      fireEvent.click(button);
    }
    const prices = screen.getAllByLabelText("Sotish narxi, so'm");
    fireEvent.change(prices[0] as HTMLElement, { target: { value: "15000" } });
    fireEvent.change(prices[1] as HTMLElement, { target: { value: "8000" } });
    fireEvent.click(screen.getByRole("button", { name: CONFIRM }));
    fireEvent.click(await screen.findByRole("button", { name: "Katalogdagi shu nomli tovardan foydalanish" }));
    expect(screen.getByText("O'z katalogimizdagi tovar: Choy")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: CONFIRM }));
    await waitFor(() => expect(server.writes()).toHaveLength(2));
    expect(server.writes()[1]?.body).toEqual({
      lines: [
        { line_no: 2, new_price: 15000 },
        { line_no: 5, item_id: OWN_ITEM_ID },
      ],
    });
  });
});

describe("sending a new order: two writes of one action", () => {
  const reads = { [NETWORK]: overviewBody() };
  const fill = async () => {
    fireEvent.change(await screen.findByLabelText("1-qator: tovar nomi"), { target: { value: "Shakar" } });
    fireEvent.change(screen.getByLabelText("Miqdor"), { target: { value: "50" } });
  };
  const SEND = "Ta'minotchiga yuborish";
  const sentAs = (server: { writes: () => Sent[] }) => server.writes().map((sent) => `${sent.method} ${sent.path} ${sent.headers["Idempotency-Key"] ?? ""}`);

  it("repeats both keys when the same order is sent again after the second write failed", async () => {
    let sends = 0;
    const server = backend(reads, (sent) => {
      if (!sent.path.endsWith("/send")) {
        return ok({ ...orderDraft(), id: ORDER_ID }, 201);
      }
      sends += 1;
      return sends === 1 ? "offline" : ok(orderBody({ role: "buyer" }));
    });
    renderScreen(<ComposeScreen draftId={null} />, { fetch: server.fetch, role: "manager", features: ON });
    await fill();
    fireEvent.click(screen.getByRole("button", { name: SEND }));
    await waitFor(() => expect(server.writes()).toHaveLength(2));
    await screen.findByRole("alert");
    fireEvent.click(screen.getByRole("button", { name: SEND }));
    await waitFor(() => expect(server.writes()).toHaveLength(4));
    const [create, send, createAgain, sendAgain] = sentAs(server);
    // The server answers the first from what it stored and applies the second once.
    expect(createAgain).toBe(create);
    expect(sendAgain).toBe(send);
    expect(send).toBe(`POST ${NETWORK}/drafts/${ORDER_ID}/send ${create?.split(" ")[2]}-send`);
    await waitFor(() => expect(window.location.hash).toBe(`#/network/orders/${ORDER_ID}`));
  });

  it("writes over the draft the failed attempt left when the order is changed and sent again, and makes no second one", async () => {
    let sends = 0;
    const server = backend(reads, (sent) => {
      if (!sent.path.endsWith("/send")) {
        return ok({ ...orderDraft(), id: ORDER_ID }, sent.method === "POST" ? 201 : 200);
      }
      sends += 1;
      return sends === 1 ? refusal(409, "NETWORK_STATE", "Bu amalni hozir bajarib bo'lmaydi.") : ok(orderBody({ role: "buyer" }));
    });
    renderScreen(<ComposeScreen draftId={null} />, { fetch: server.fetch, role: "manager", features: ON });
    await fill();
    fireEvent.click(screen.getByRole("button", { name: SEND }));
    expect(await screen.findByText("Bu amalni hozir bajarib bo'lmaydi.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Miqdor"), { target: { value: "40" } });
    fireEvent.click(screen.getByRole("button", { name: SEND }));
    await waitFor(() => expect(server.writes()).toHaveLength(4));
    expect(server.writes().map((sent) => `${sent.method} ${sent.path}`)).toEqual([
      `POST ${NETWORK}/drafts`,
      `POST ${NETWORK}/drafts/${ORDER_ID}/send`,
      `PUT ${NETWORK}/drafts/${ORDER_ID}`,
      `POST ${NETWORK}/drafts/${ORDER_ID}/send`,
    ]);
    const keys = server.writes().map((sent) => sent.headers["Idempotency-Key"]);
    expect(new Set(keys).size).toBe(4);
    expect(keys[3]).toBe(`${keys[2]}-send`);
    expect(server.writes()[2]?.body).toMatchObject({ lines: [{ name: "Shakar", unit: "dona", qty: "40" }] });
  });

  it("makes a new draft when the one it left is gone: it was sent after all and only the answer was lost", async () => {
    const NEW_ID = "c3c3c3c3-c3c3-4c3c-8c3c-c3c3c3c3c3c4";
    let creates = 0;
    const server = backend(reads, (sent) => {
      if (sent.path.endsWith(`${ORDER_ID}/send`)) {
        return "offline";
      }
      if (sent.path.endsWith("/send")) {
        return ok(orderBody({ id: NEW_ID, role: "buyer" }));
      }
      if (sent.method === "PUT") {
        return NOT_FOUND;
      }
      creates += 1;
      return ok({ ...orderDraft(), id: creates === 1 ? ORDER_ID : NEW_ID }, 201);
    });
    renderScreen(<ComposeScreen draftId={null} />, { fetch: server.fetch, role: "manager", features: ON });
    await fill();
    fireEvent.click(screen.getByRole("button", { name: SEND }));
    await screen.findByRole("alert");
    fireEvent.change(screen.getByLabelText("Miqdor"), { target: { value: "40" } });
    fireEvent.click(screen.getByRole("button", { name: SEND }));
    await waitFor(() => expect(window.location.hash).toBe(`#/network/orders/${NEW_ID}`));
    expect(server.writes().map((sent) => `${sent.method} ${sent.path}`).slice(2)).toEqual([
      `PUT ${NETWORK}/drafts/${ORDER_ID}`,
      `POST ${NETWORK}/drafts`,
      `POST ${NETWORK}/drafts/${NEW_ID}/send`,
    ]);
    const keys = server.writes().map((sent) => sent.headers["Idempotency-Key"]);
    // Three writes of one action, three keys made from one.
    expect(keys[3]).toBe(`${keys[2]}-new`);
    expect(keys[4]).toBe(`${keys[2]}-send`);
  });

  function orderDraft() {
    return {
      status: "draft",
      link_id: NET_LINK_ID,
      partner: { name: "Baraka ulgurji" },
      note: null,
      wanted_date: null,
      lines: [{ line_no: 1, name: "Shakar", unit: "dona", qty: "50", item_id: null }],
      updated_at: "2026-10-06T05:00:00+00:00",
    };
  }
});

describe("choosing the row of the books a partner is", () => {
  const requested = (role: "buyer" | "supplier") =>
    overviewBody({ links: [linkBody({ role, state: "requested", invited: true, counterpart: null })], invites: [] });
  const ACCEPT = `${NETWORK}/links/${NET_LINK_ID}/accept`;

  it("reaches every supplier of the shop, not the first fifty: by a part of the name, or a page at a time", async () => {
    // The choice was a list of the first fifty with a search beside it; the sixty-third is past that page.
    const suppliers = manySuppliers(70);
    const server = fakeServer((sent) => {
      if (sent.method !== "GET") {
        return ok({ link: linkBody() });
      }
      if (sent.path === `${SHOP_BASE}/suppliers`) {
        return ok(supplierListBody(suppliers, sent.query));
      }
      return sent.path === NETWORK ? ok(requested("buyer")) : sent.path === `${SHOP_BASE}/stock/settings` ? ok(stockSettingsBody()) : NOT_FOUND;
    });
    renderScreen(<HomeScreen />, { fetch: server.fetch, role: "owner", features: ON });
    fireEvent.click(await screen.findByRole("button", { name: "Qabul qilish" }));
    const form = within(screen.getByRole("group", { name: "Qabul qilish" }));
    const choice = form.getByRole("combobox", { name: "Bizning daftardagi ta'minotchi" }) as HTMLInputElement;
    const asked = () => server.sent.filter((sent) => sent.path === `${SHOP_BASE}/suppliers`).map((sent) => sent.query);
    // Nothing is read before the choice is opened; then one page, never the whole list.
    expect(asked()).toEqual([]);
    fireEvent.click(choice);
    await form.findByRole("option", { name: "Ta'minotchi 01" });
    const offered = () => form.getAllByRole("option").map((option) => option.textContent);
    expect(offered()).toHaveLength(22);
    expect(offered()[0]).toBe("Yangi yozuv yaratilsin");
    expect(offered()).not.toContain("Ta'minotchi 63");
    fireEvent.click(form.getByRole("option", { name: "Yana ko'rsatish" }));
    await form.findByRole("option", { name: "Ta'minotchi 21" });
    expect(asked()).toEqual([
      { status: "active", limit: "20" },
      { status: "active", limit: "20", cursor: "c20" },
    ]);
    // By name the server looks among all of them.
    fireEvent.change(choice, { target: { value: "63" } });
    fireEvent.click(await form.findByRole("option", { name: "Ta'minotchi 63" }));
    expect(asked().at(-1)).toEqual({ q: "63", status: "active", limit: "20" });
    expect(choice.value).toBe("Ta'minotchi 63");
    fireEvent.click(form.getByRole("button", { name: "Qabul qilish" }));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]).toMatchObject({ path: ACCEPT, body: { counterpart_id: suppliers[62]?.id } });
    // The old notice of a list cut short is gone with the cut.
    expect(screen.queryByText(/Faqat birinchi/)).toBeNull();
  });

  it("gives the supplier's side its customers the same way, with the server's own cursor", async () => {
    const customers = Array.from({ length: 45 }, (_, index) =>
      customerBody({ id: `11111111-1111-4111-8111-${String(index + 1).padStart(12, "0")}`, display_name: `Mijoz ${String(index + 1).padStart(2, "0")}`, phone: null }),
    );
    const server = fakeServer((sent) => {
      if (sent.method !== "GET") {
        return ok({ link: linkBody({ role: "supplier" }) });
      }
      if (sent.path === `${SHOP_BASE}/customers`) {
        const part = (sent.query["q"] ?? "").toLowerCase();
        const matching = customers.filter((customer) => customer.display_name.toLowerCase().includes(part));
        const start = sent.query["cursor"] === undefined ? 0 : Number(sent.query["cursor"].slice(1));
        const limit = Number(sent.query["limit"]);
        return ok({ items: matching.slice(start, start + limit), next_cursor: start + limit < matching.length ? `k${start + limit}` : null });
      }
      return sent.path === NETWORK ? ok(requested("supplier")) : sent.path === `${SHOP_BASE}/stock/settings` ? ok(stockSettingsBody()) : NOT_FOUND;
    });
    renderScreen(<HomeScreen />, { fetch: server.fetch, role: "owner", features: ON });
    fireEvent.click(await screen.findByRole("button", { name: "Qabul qilish" }));
    const form = within(screen.getByRole("group", { name: "Qabul qilish" }));
    const choice = form.getByRole("combobox", { name: "Bizning daftardagi mijoz" });
    // With the keyboard: open, up to the last row (the next page), Enter; then two rows down and Enter.
    choice.focus();
    fireEvent.keyDown(choice, { key: "ArrowDown" });
    await form.findByRole("option", { name: "Mijoz 01" });
    fireEvent.keyDown(choice, { key: "ArrowUp" });
    fireEvent.keyDown(choice, { key: "Enter" });
    await form.findByRole("option", { name: "Mijoz 21" });
    fireEvent.keyDown(choice, { key: "ArrowDown" });
    fireEvent.keyDown(choice, { key: "Enter" });
    expect((choice as HTMLInputElement).value).toBe("Mijoz 22");
    expect(server.sent.filter((sent) => sent.path === `${SHOP_BASE}/customers`).map((sent) => sent.query)).toEqual([
      { status: "active", limit: "20" },
      { status: "active", limit: "20", cursor: "k20" },
    ]);
    // No request of the suppliers was made for a link in which this shop is the supplier.
    expect(server.sent.filter((sent) => sent.path === `${SHOP_BASE}/suppliers`)).toEqual([]);
    fireEvent.click(form.getByRole("button", { name: "Qabul qilish" }));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]).toMatchObject({ path: ACCEPT, body: { counterpart_id: customers[21]?.id } });
  });
});

describe("the two books side by side, when this shop's own cannot be read", () => {
  const LINK_PATH = `${NETWORK}/links/${NET_LINK_ID}`;
  const withoutOwn = () => {
    const row: Record<string, unknown> = reconciliationBody({ awaiting: { notes: 150000, payments_own: 0, payments_partner: 0, payments_declined: 0 } });
    delete row["own_balance"];
    delete row["difference"];
    return row;
  };

  it("says that the partner is not attached to a row of the books yet, and not that the member may not look", async () => {
    const page = { link: linkBody({ counterpart: null }), reconciliation: [withoutOwn()], events: [] };
    renderScreen(<LinkScreen linkId={NET_LINK_ID} />, { fetch: backend({ [LINK_PATH]: page }).fetch, role: "owner", features: ON });
    expect(await screen.findByText("Bu hamkor hali daftaringizdagi yozuvga bog'lanmagan, shuning uchun daftarlarni solishtirib bo'lmaydi.")).toBeTruthy();
    expect(screen.queryByText(/ruxsatingiz yo'q/)).toBeNull();
    // What awaits is an amount of money, never a count, and is still said.
    expect(screen.getByText(/150.000 so'm\. Hech kimning/)).toBeTruthy();
    expect(screen.queryByText(/Daftarlar bir xil/)).toBeNull();
  });

  it("says that the member may not look when there is such a row", async () => {
    const page = { link: linkBody(), reconciliation: [withoutOwn()], events: [] };
    renderScreen(<LinkScreen linkId={NET_LINK_ID} />, { fetch: backend({ [LINK_PATH]: page }).fetch, role: "seller", permissions: ["network.view"], features: ON });
    expect(await screen.findByText(/ruxsatingiz yo'q/)).toBeTruthy();
    expect(screen.queryByText(/hali daftaringizdagi yozuvga bog'lanmagan/)).toBeNull();
  });
});

describe("the stock document a confirmed note became", () => {
  const received = noteBody({ status: "received", decided_at: "2026-10-06T07:00:00+00:00", stock_document_id: STOCK_DOCUMENT_ID });
  const LINK = "Ombor hujjatini ochish";

  it("is linked only in the web panel, and only for a member who may open a receipt", async () => {
    renderScreen(<NoteScreen noteId={NOTE_ID} office />, { fetch: backend({ [NOTE_PATH]: received }).fetch, role: "manager", features: ON });
    expect((await screen.findByRole("link", { name: LINK })).getAttribute("href")).toBe(`#/stock-documents/${STOCK_DOCUMENT_ID}`);
    cleanup();
    // The Mini App has no screen of stock documents.
    renderScreen(<NoteScreen noteId={NOTE_ID} office={false} />, { fetch: backend({ [NOTE_PATH]: received }).fetch, role: "manager", features: ON });
    await screen.findByText(/Yuk qabul qilindi|qabul qilingan/);
    expect(screen.queryByRole("link", { name: LINK })).toBeNull();
    cleanup();
    renderScreen(<NoteScreen noteId={NOTE_ID} office />, { fetch: backend({ [NOTE_PATH]: received }).fetch, role: "seller", permissions: ["network.view", "stock.view"], features: ON });
    await screen.findByRole("heading", { name: "Yuk xati № 5" });
    expect(screen.queryByRole("link", { name: LINK })).toBeNull();
  });
});

describe("what the partner typed is text, never markup", () => {
  const NAME = '<img src=x onerror="window.pwned=1"> Baraka';
  const WORDS = '<b>qalin</b> <a href="https://evil.example">bosing</a> {shop} &amp;';

  const marked = (container: HTMLElement) => container.querySelectorAll("img, b, a[href^='http'], script, iframe").length;

  it("on an order: the partner's name, its note, a line's name and the reason it gave", async () => {
    const order = orderBody({
      partner: { name: NAME },
      note: WORDS,
      status: "cancelled",
      closed_reason: WORDS,
      lines: [{ line_no: 1, name: WORDS, unit: "kg", qty: "50", item_id: null, accepted_qty: null, unit_price: null, line_total: null, changed: false }],
      events: [eventBody({ kind: "order_cancelled", detail: { number: 12, reason: WORDS } })],
    });
    const { container } = renderScreen(<OrderScreen orderId={ORDER_ID} />, { fetch: backend({ [ORDER_PATH]: order }).fetch, role: "manager", features: ON });
    expect(await screen.findByRole("link", { name: NAME })).toBeTruthy();
    expect(screen.getAllByText(WORDS).length).toBeGreaterThanOrEqual(4);
    expect(marked(container)).toBe(0);
    expect((window as unknown as { pwned?: number }).pwned).toBeUndefined();
  });

  it("on a delivery note: the partner's name, a line's name and both reasons", async () => {
    const note = noteBody({
      partner: { name: NAME },
      status: "rejected",
      reject_reason: WORDS,
      supersedes_id: "d4d4d4d4-d4d4-4d4d-8d4d-d4d4d4d4d4d0",
      supersede_reason: WORDS,
      lines: [{ line_no: 1, name: WORDS, unit: "kg", qty: "50", unit_price: 12000, line_total: 600000, item_id: null, received_qty: null }],
      events: [eventBody({ kind: "note_rejected", detail: { number: 5, reason: WORDS } })],
    });
    const { container } = renderScreen(<NoteScreen noteId={NOTE_ID} office />, { fetch: backend({ [NOTE_PATH]: note }).fetch, role: "manager", features: ON });
    expect(await screen.findByText(NAME)).toBeTruthy();
    expect(screen.getAllByText(WORDS, { exact: false }).length).toBeGreaterThanOrEqual(3);
    expect(marked(container)).toBe(0);
  });

  it("on a partner's page and among the payments: the name, the phone as text, a payment's note and why it was declined", async () => {
    const page = { link: linkBody({ partner: { name: NAME, phone: "<b>+998</b>", removed: false } }), reconciliation: [], events: [] };
    const first = renderScreen(<LinkScreen linkId={NET_LINK_ID} />, { fetch: backend({ [`${NETWORK}/links/${NET_LINK_ID}`]: page }).fetch, role: "owner", features: ON });
    expect(await screen.findByRole("heading", { name: NAME })).toBeTruthy();
    expect(screen.getByText("<b>+998</b>")).toBeTruthy();
    expect(marked(first.container)).toBe(0);
    cleanup();
    const declined = paymentBody({ partner: { name: NAME }, role: "buyer", recorded_by: "own", status: "declined", note: WORDS, decline_reason: WORDS, in_own_books: false });
    const reads = { [NETWORK]: overviewBody(), [`${NETWORK}/payments`]: { payments: [declined], next_cursor: null } };
    const second = renderScreen(<PaymentsScreen />, { fetch: backend(reads).fetch, role: "manager", features: ON });
    const [row] = await waitFor(() => rows("To'lovlar"));
    expect(row?.textContent).toContain(NAME);
    expect(row?.textContent).toContain(WORDS);
    expect(marked(second.container)).toBe(0);
  });
});

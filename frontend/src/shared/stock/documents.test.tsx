// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { CUSTOMER_ID, customerBody, fakeServer, ok, refusal, type Reply, type Sent, SHOP_BASE } from "../../testing/fakeServer";
import { renderScreen } from "../../testing/renderScreen";
import { DocumentScreen, DocumentsScreen, NewDocumentScreen, ReceiptScreen } from "./DocumentScreens";
import {
  costedItemBody,
  DOCUMENT_ID,
  documentBody,
  EAN,
  ITEM_ID,
  manySuppliers,
  OTHER_ITEM,
  STOCK,
  stockSettingsBody,
  SUPPLIER_ID,
  supplierBody,
  supplierListBody,
  SUPPLIERS,
  UNKNOWN_EAN,
} from "./testing";

afterEach(() => {
  cleanup();
  window.location.hash = "";
});

const FIND = "Tovar: shtrix-kodni skanerlang yoki nom yozing";
const NOT_FOUND = refusal(404, "NOT_FOUND", "Topilmadi.");
const MANAGER = { role: "manager" as const };
const ARCHIVED_SUPPLIER = "77777777-7777-4777-8777-777777777772";

function backend(write: (sent: Sent) => Reply = () => NOT_FOUND, document: () => unknown = () => documentBody(), settings = stockSettingsBody()) {
  return fakeServer((sent) => {
    if (sent.method !== "GET") {
      return write(sent);
    }
    switch (sent.path) {
      case `${STOCK}/settings`:
        return ok(settings);
      case SUPPLIERS:
        return ok({ suppliers: [supplierBody()], totals: [{ currency: "UZS", owed: 500000 }], next_cursor: null });
      case `${STOCK}/lookup`:
        return sent.query["code"] === EAN ? ok(costedItemBody()) : NOT_FOUND;
      case `${STOCK}/items`:
        return ok({ items: [costedItemBody({ id: OTHER_ITEM, name: "Choy", unit: "dona", on_hand: "4", barcodes: [] })], next_cursor: null });
      case `${STOCK}/documents`:
        return ok({
          documents: [documentBody({ lines: undefined }), documentBody({ id: "88888888-8888-4888-8888-888888888882", kind: "write_off", number: 2, status: "draft", reason: "expired", currency: undefined, total: undefined, paid: undefined, lines: undefined })],
          next_cursor: null,
        });
      case `${STOCK}/documents/${DOCUMENT_ID}`:
        return ok(document());
      case `${SHOP_BASE}/customers`:
        return ok({ items: [customerBody()], next_cursor: null });
      default:
        return NOT_FOUND;
    }
  });
}

/** Chooses a supplier as a person does: opens the choice, waits for the server's list, presses the name. */
async function chooseSupplier(name: string | RegExp = "Baraka ulgurji") {
  fireEvent.click(await screen.findByRole("combobox", { name: "Ta'minotchi" }));
  fireEvent.click(await screen.findByRole("option", { name }));
}

function find(text: string) {
  const input = screen.getByLabelText(FIND);
  fireEvent.change(input, { target: { value: text } });
  fireEvent.submit(input.closest("form") as HTMLFormElement);
}

describe("a quick receipt", () => {
  it("adds a scanned item as a line, one more for a second scan, and posts it on credit to a supplier", async () => {
    const server = backend((sent) =>
      sent.path === `${STOCK}/documents`
        ? ok(documentBody({ supplier: { id: SUPPLIER_ID, name: "Baraka ulgurji" }, paid: 0 }), 201)
        : NOT_FOUND,
    );
    renderScreen(<ReceiptScreen host={{}} />, { fetch: server.fetch, ...MANAGER });
    await screen.findByRole("heading", { name: "Kirim" });
    find(EAN);
    const qty = (await screen.findByLabelText("Miqdor (kg)")) as HTMLInputElement;
    expect(qty.value).toBe("1");
    find(EAN);
    await waitFor(() => expect(qty.value).toBe("2"));
    expect(within(screen.getByRole("list", { name: "Tovarlar" })).getAllByRole("listitem")).toHaveLength(1);

    fireEvent.change(screen.getByLabelText("Bir birlik narxi"), { target: { value: "12000" } });
    expect(screen.getByText("Jami").parentElement?.textContent).toBe("Jami 24 000 so'm");
    // Without a supplier there is nothing to choose: the receipt is paid.
    expect(screen.queryByRole("group", { name: "To'lov" })).toBeNull();
    // This shop keeps no cash book: nobody is asked how the money was paid.
    expect(screen.queryByLabelText("To'lov usuli")).toBeNull();
    expect(screen.getByText("Ta'minotchi tanlanmagan: kirim to'liq to'langan deb yoziladi.")).toBeTruthy();

    await chooseSupplier();
    fireEvent.click(screen.getByLabelText("Qarzga olindi"));
    fireEvent.click(screen.getByRole("button", { name: "O'tkazish" }));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    const write = server.writes()[0];
    expect(write?.method).toBe("POST");
    expect(write?.path).toBe(`${STOCK}/documents`);
    expect(write?.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
    expect(write?.body).toEqual({
      kind: "receipt",
      doc_date: "2026-10-06",
      supplier_id: SUPPLIER_ID,
      currency: "UZS",
      paid: 0,
      lines: [{ item_id: ITEM_ID, qty: "2", unit_cost: 12000 }],
      post: true,
    });
    expect(await screen.findByText("Kirim № 7 o'tkazildi.")).toBeTruthy();
    expect(screen.getByText("Ta'minotchiga qarz bo'lib qoldi").nextElementSibling?.textContent).toBe("24 000 so'm");
  });

  it("sends a part that was paid, and refuses one that is more than the total before anything is sent", async () => {
    const server = backend(() => ok(documentBody(), 201));
    renderScreen(<ReceiptScreen host={{}} />, { fetch: server.fetch, ...MANAGER });
    await screen.findByRole("heading", { name: "Kirim" });
    find(EAN);
    fireEvent.change(await screen.findByLabelText("Bir birlik narxi"), { target: { value: "12000" } });
    await chooseSupplier();
    fireEvent.click(screen.getByLabelText("Bir qismi to'landi"));
    fireEvent.change(screen.getByLabelText("Hozir to'langan summa"), { target: { value: "13000" } });
    fireEvent.click(screen.getByRole("button", { name: "O'tkazish" }));
    expect(await screen.findByText("To'langan summa noldan hujjat summasigacha bo'lsin.")).toBeTruthy();
    expect(server.writes()).toEqual([]);
    fireEvent.change(screen.getByLabelText("Hozir to'langan summa"), { target: { value: "5000" } });
    fireEvent.click(screen.getByRole("button", { name: "O'tkazish" }));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]?.body).toMatchObject({ paid: 5000, post: true });
  });

  it("offers a member who may not pay suppliers credit only, and sends nothing paid", async () => {
    const server = backend(() => ok(documentBody({ paid: 0 }), 201));
    renderScreen(<ReceiptScreen host={{}} />, {
      fetch: server.fetch,
      role: "seller",
      permissions: ["stock.view", "stock.receive", "suppliers.view"],
    });
    await screen.findByRole("heading", { name: "Kirim" });
    find(EAN);
    fireEvent.change(await screen.findByLabelText("Bir birlik narxi"), { target: { value: "12000" } });
    await chooseSupplier();
    const choices = within(screen.getByRole("group", { name: "To'lov" })).getAllByRole("radio");
    expect(choices).toHaveLength(1);
    expect(screen.queryByLabelText("Hozir to'liq to'landi")).toBeNull();
    expect(screen.getByText("Ta'minotchiga to'lov yozishga ruxsatingiz yo'q: tovar qarzga olingan deb yoziladi.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "O'tkazish" }));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]?.body).toMatchObject({ supplier_id: SUPPLIER_ID, paid: 0 });
  });

  it("adds a good met for the first time under the code that found nothing, and saves a draft", async () => {
    const server = backend(() => ok(documentBody({ status: "draft", posted_at: null }), 201));
    renderScreen(<ReceiptScreen host={{}} />, { fetch: server.fetch, ...MANAGER });
    await screen.findByRole("heading", { name: "Kirim" });
    find(UNKNOWN_EAN);
    expect((await screen.findByRole("alert")).textContent).toContain("73513537 shtrix-kodli tovar topilmadi.");
    fireEvent.click(screen.getByRole("button", { name: "Shu kod bilan yangi tovar qo'shish" }));
    expect((screen.getByLabelText("Shtrix-kod (ixtiyoriy)") as HTMLInputElement).value).toBe(UNKNOWN_EAN);
    // A name and a selling price are required before the line exists.
    fireEvent.click(screen.getByRole("button", { name: "Kirimga qo'shish" }));
    expect(screen.getByText("Nomni yozing: ko'pi bilan 80 ta belgi.")).toBeTruthy();
    expect(screen.queryByRole("list", { name: "Tovarlar" })?.children).toHaveLength(0);
    fireEvent.change(screen.getByLabelText("Nomi"), { target: { value: " Yangi  choy " } });
    fireEvent.change(screen.getByLabelText("O'lchov birligi"), { target: { value: "dona" } });
    fireEvent.change(screen.getByLabelText("Sotish narxi, so'm"), { target: { value: "15 000" } });
    fireEvent.click(screen.getByRole("button", { name: "Kirimga qo'shish" }));
    fireEvent.change(screen.getByLabelText("Miqdor (dona)"), { target: { value: "10" } });
    fireEvent.change(screen.getByLabelText("Bir birlik narxi"), { target: { value: "9000" } });
    fireEvent.click(screen.getByRole("button", { name: "Qoralama sifatida saqlash" }));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]?.body).toEqual({
      kind: "receipt",
      doc_date: "2026-10-06",
      currency: "UZS",
      lines: [{ qty: "10", unit_cost: 9000, new_item: { name: "Yangi choy", unit: "dona", price: 15000, barcode: UNKNOWN_EAN } }],
    });
    // The draft is shown where it was written, with the button that makes it take effect.
    expect(await screen.findByText("Kirim № 7 qoralama sifatida saqlandi. Kuchga kirishi uchun uni o'tkazing.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "O'tkazish" })).toBeTruthy();
  });

  it("offers dollars only in a shop that buys in them, and then sends cents", async () => {
    const plain = backend();
    const first = renderScreen(<ReceiptScreen host={{}} />, { fetch: plain.fetch, ...MANAGER });
    await screen.findByRole("heading", { name: "Kirim" });
    expect(screen.queryByRole("group", { name: "Valyuta" })).toBeNull();
    first.unmount();

    const server = backend(() => ok(documentBody({ currency: "USD" }), 201), undefined, stockSettingsBody({ currencies: ["UZS", "USD"] }));
    renderScreen(<ReceiptScreen host={{}} />, { fetch: server.fetch, ...MANAGER });
    fireEvent.click(await screen.findByRole("button", { name: "$" }));
    find(EAN);
    fireEvent.change(await screen.findByLabelText("Bir birlik narxi"), { target: { value: "1.25" } });
    fireEvent.click(screen.getByRole("button", { name: "O'tkazish" }));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]?.body).toMatchObject({ currency: "USD", lines: [{ item_id: ITEM_ID, qty: "1", unit_cost: 125 }] });
  });

  it("asks how it was paid only in a shop that keeps a cash book, and only when something is paid now", async () => {
    const server = backend(() => ok(documentBody(), 201), undefined, stockSettingsBody({ cash_book: true }));
    renderScreen(<ReceiptScreen host={{}} />, { fetch: server.fetch, ...MANAGER });
    await screen.findByRole("heading", { name: "Kirim" });
    find(EAN);
    fireEvent.change(await screen.findByLabelText("Bir birlik narxi"), { target: { value: "12000" } });
    fireEvent.change(screen.getByLabelText("To'lov usuli"), { target: { value: "card" } });
    // On credit nothing is paid, so there is no method to ask for.
    await chooseSupplier();
    fireEvent.click(screen.getByLabelText("Qarzga olindi"));
    expect(screen.queryByLabelText("To'lov usuli")).toBeNull();
    fireEvent.click(screen.getByLabelText("Hozir to'liq to'landi"));
    expect((screen.getByLabelText("To'lov usuli") as HTMLSelectElement).value).toBe("card");
    fireEvent.click(screen.getByRole("button", { name: "O'tkazish" }));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]?.body).toMatchObject({ paid: 12000, method: "card" });
  });

  it("is not there for a member who may not receive goods, and nothing is asked for them", async () => {
    const server = backend();
    renderScreen(<ReceiptScreen host={{}} />, { fetch: server.fetch, role: "seller" });
    expect(await screen.findByText("Bosh sahifaga qaytish")).toBeTruthy();
    expect(screen.queryByLabelText(FIND)).toBeNull();
    expect(server.sent).toEqual([]);
  });

  it("shows the server's refusal with the line it is about", async () => {
    const server = backend(() =>
      refusal(409, "COST_CURRENCY_MISMATCH", "Bu tovarning tannarxi boshqa valyutada yuritiladi.", { item: ITEM_ID, line: "0" }),
    );
    renderScreen(<ReceiptScreen host={{}} />, { fetch: server.fetch, ...MANAGER });
    await screen.findByRole("heading", { name: "Kirim" });
    find(EAN);
    fireEvent.change(await screen.findByLabelText("Bir birlik narxi"), { target: { value: "12000" } });
    fireEvent.click(screen.getByRole("button", { name: "O'tkazish" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("Bu tovarning tannarxi boshqa valyutada yuritiladi.1-qatorni tekshiring.");
  });
});

describe("the other kinds", () => {
  it("a write-off needs its reason and sends no price", async () => {
    const server = backend(() => ok(documentBody({ kind: "write_off", reason: "expired" }), 201));
    renderScreen(<NewDocumentScreen kind="write_off" host={{}} />, { fetch: server.fetch, ...MANAGER });
    await screen.findByRole("heading", { name: "Hisobdan chiqarish" });
    find(EAN);
    await screen.findByLabelText("Miqdor (kg)");
    expect(screen.queryByLabelText("Bir birlik narxi")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "O'tkazish" }));
    expect(await screen.findByText("Sababni tanlang.")).toBeTruthy();
    expect(server.writes()).toEqual([]);
    fireEvent.change(screen.getByLabelText("Sababi"), { target: { value: "expired" } });
    fireEvent.click(screen.getByRole("button", { name: "O'tkazish" }));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]?.body).toEqual({
      kind: "write_off",
      doc_date: "2026-10-06",
      reason: "expired",
      lines: [{ item_id: ITEM_ID, qty: "1" }],
      post: true,
    });
    await waitFor(() => expect(window.location.hash).toBe(`#/stock-documents/${DOCUMENT_ID}`));
  });

  it("a stocktake is saved first and cannot be posted from its form: the differences come before", async () => {
    const server = backend(() => ok(documentBody({ kind: "stocktake", status: "draft" }), 201));
    renderScreen(<NewDocumentScreen kind="stocktake" host={{}} />, { fetch: server.fetch, ...MANAGER });
    await screen.findByRole("heading", { name: "Inventarizatsiya" });
    expect(screen.queryByRole("button", { name: "O'tkazish" })).toBeNull();
    find(EAN);
    fireEvent.change(await screen.findByLabelText("Sanalgan miqdor (kg)"), { target: { value: "0" } });
    fireEvent.click(screen.getByRole("button", { name: "Saqlash va farqlarni ko'rish" }));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]?.body).toEqual({ kind: "stocktake", doc_date: "2026-10-06", lines: [{ item_id: ITEM_ID, qty: "0" }] });
  });

  it("a stocktake's draft shows each count beside the books, and posting sends one request", async () => {
    const draft = documentBody({
      kind: "stocktake",
      status: "draft",
      posted_at: null,
      currency: undefined,
      total: undefined,
      paid: undefined,
      lines: [{ line_no: 1, item: { id: ITEM_ID, name: "Shakar", unit: "kg" }, qty: "6", expected: "7.5", difference: "-1.5" }],
    });
    const server = backend(() => ok({ ...draft, status: "posted", posted_at: "2026-10-06T07:00:00+00:00" }), () => draft);
    renderScreen(<DocumentScreen documentId={DOCUMENT_ID} host={{}} />, { fetch: server.fetch, ...MANAGER });
    const lines = within(await screen.findByRole("list", { name: "Tovarlar" })).getAllByRole("listitem");
    expect(lines.map((row) => row.textContent)).toEqual(["ShakarSanaldi6 kgHisobda7,5 kgFarq-1,5 kg"]);
    expect(screen.getByText(/Farqlar hozirgi qoldiqqa nisbatan/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "O'tkazish" }));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]?.path).toBe(`${STOCK}/documents/${DOCUMENT_ID}/post`);
    expect(server.writes()[0]?.body).toBeUndefined();
    expect(await screen.findByText("O'tkazilgan")).toBeTruthy();
    // Posted: nothing to edit or post any more, only to cancel.
    expect(screen.queryByRole("button", { name: "O'tkazish" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Tahrirlash" })).toBeNull();
  });

  it("a customer's return needs the customer and says how much is handed back in money", async () => {
    const server = backend(() => ok(documentBody({ kind: "customer_return", customer: { id: CUSTOMER_ID, name: "Ali Valiyev" }, total: 15000, paid: 5000 }), 201));
    renderScreen(<NewDocumentScreen kind="customer_return" host={{}} />, { fetch: server.fetch, ...MANAGER });
    await screen.findByRole("heading", { name: "Mijozdan qaytarish" });
    find(EAN);
    // Goods come back at the selling price unless the person says otherwise.
    expect(((await screen.findByLabelText("Bir birlik uchun qaytariladi")) as HTMLInputElement).value).toBe("15 000");
    fireEvent.click(screen.getByRole("button", { name: "O'tkazish" }));
    expect(await screen.findByText("Mijozni tanlang.")).toBeTruthy();
    expect(server.writes()).toEqual([]);
    fireEvent.change(screen.getByLabelText("Mijoz"), { target: { value: "Ali" } });
    fireEvent.click(within(screen.getByLabelText("Mijoz").parentElement as HTMLElement).getByRole("button", { name: "Qidirish" }));
    fireEvent.click(await screen.findByRole("button", { name: /Ali Valiyev/ }));
    fireEvent.change(screen.getByLabelText("Mijozga pul bilan qaytarildi, so'm"), { target: { value: "5000" } });
    fireEvent.click(screen.getByRole("button", { name: "O'tkazish" }));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]?.body).toEqual({
      kind: "customer_return",
      doc_date: "2026-10-06",
      customer_id: CUSTOMER_ID,
      paid: 5000,
      lines: [{ item_id: ITEM_ID, qty: "1", unit_cost: 15000 }],
      post: true,
    });
  });

  it("a kind the member may not write is not opened", async () => {
    const server = backend();
    renderScreen(<NewDocumentScreen kind="write_off" host={{}} />, {
      fetch: server.fetch,
      role: "seller",
      permissions: ["stock.view", "stock.receive"],
    });
    expect(await screen.findByText("Bosh sahifaga qaytish")).toBeTruthy();
    expect(server.sent).toEqual([]);
  });
});

describe("a document's page", () => {
  it("cancels a posted document only with a reason", async () => {
    const server = backend(() => ok(documentBody({ status: "cancelled", cancelled_at: "2026-10-06T08:00:00+00:00", cancel_reason: "Xato kiritilgan" })));
    renderScreen(<DocumentScreen documentId={DOCUMENT_ID} host={{}} />, { fetch: server.fetch, ...MANAGER });
    fireEvent.click(await screen.findByRole("button", { name: "Hujjatni bekor qilish" }));
    const form = screen.getByRole("group", { name: "Nima uchun bekor qilinmoqda?" });
    fireEvent.click(within(form).getByRole("button", { name: "Hujjatni bekor qilish" }));
    expect(await screen.findByText("Sababni yozing: 1 dan 200 tagacha belgi.")).toBeTruthy();
    expect(server.writes()).toEqual([]);
    fireEvent.change(screen.getByRole("textbox", { name: "Nima uchun bekor qilinmoqda?" }), { target: { value: "  Xato   kiritilgan " } });
    fireEvent.click(within(form).getByRole("button", { name: "Hujjatni bekor qilish" }));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]?.path).toBe(`${STOCK}/documents/${DOCUMENT_ID}/cancel`);
    expect(server.writes()[0]?.body).toEqual({ reason: "Xato kiritilgan" });
    expect(await screen.findByText("Bekor qilingan")).toBeTruthy();
    expect(screen.getByText("Bekor qilish sababi").nextElementSibling?.textContent).toBe("Xato kiritilgan");
    // A cancelled document offers nothing.
    expect(screen.queryByRole("button", { name: "Hujjatni bekor qilish" })).toBeNull();
  });

  it("shows the server's refusal to cancel a receipt whose goods were sold", async () => {
    const server = backend(() =>
      refusal(409, "STOCK_ALREADY_USED", "Bu tovar allaqachon sotilgan yoki chiqarilgan.", { item: ITEM_ID, name: "Shakar", on_hand: "0.5" }),
    );
    renderScreen(<DocumentScreen documentId={DOCUMENT_ID} host={{}} />, { fetch: server.fetch, ...MANAGER });
    fireEvent.click(await screen.findByRole("button", { name: "Hujjatni bekor qilish" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Nima uchun bekor qilinmoqda?" }), { target: { value: "xato" } });
    fireEvent.click(within(screen.getByRole("group", { name: "Nima uchun bekor qilinmoqda?" })).getByRole("button", { name: "Hujjatni bekor qilish" }));
    expect(await screen.findByText("Bu tovar allaqachon sotilgan yoki chiqarilgan.")).toBeTruthy();
  });

  it("shows no price where the server sent none, and no action for a kind the member may not write", async () => {
    const hidden = documentBody({
      currency: undefined,
      total: undefined,
      paid: undefined,
      lines: [{ line_no: 1, item: { id: ITEM_ID, name: "Shakar", unit: "kg" }, qty: "2" }],
    });
    const server = backend(undefined, () => hidden);
    renderScreen(<DocumentScreen documentId={DOCUMENT_ID} host={{}} />, {
      fetch: server.fetch,
      role: "seller",
      permissions: ["stock.view", "stock.adjust"],
    });
    const lines = within(await screen.findByRole("list", { name: "Tovarlar" })).getAllByRole("listitem");
    expect(lines.map((row) => row.textContent)).toEqual(["ShakarMiqdor2 kg"]);
    expect(screen.queryByText("Jami")).toBeNull();
    expect(screen.queryByText("Bir birlik narxi")).toBeNull();
    // A receipt is for those who may receive goods; this member corrects the books only.
    expect(screen.queryByRole("button", { name: "Hujjatni bekor qilish" })).toBeNull();
  });
});

describe("the list of documents", () => {
  it("filters by kind and state, and offers a new document of each kind the member may write", async () => {
    const server = backend();
    renderScreen(<DocumentsScreen />, { fetch: server.fetch, role: "seller", permissions: ["stock.view", "stock.adjust"] });
    const rows = within(await screen.findByRole("list", { name: "Ombor hujjatlari" })).getAllByRole("listitem");
    expect(rows.map((row) => row.textContent)).toEqual([
      "Kirim № 7Sana2026-yil 6-oktabrHolatiO'tkazilganTa'minotchi yoki mijoz—Jami24 000 so'mKim yozdiSiz",
      "Hisobdan chiqarish № 2Sana2026-yil 6-oktabrHolatiQoralamaTa'minotchi yoki mijoz—Jami—Kim yozdiSiz",
    ]);
    const offered = within(screen.getByRole("navigation", { name: "Yangi hujjat" })).getAllByRole("link");
    expect(offered.map((link) => link.textContent)).toEqual(["Mijozdan qaytarish", "Hisobdan chiqarish", "Inventarizatsiya"]);
    expect(offered[1]?.getAttribute("href")).toBe("#/stock-documents/new/write_off");
    const asked = () => server.sent.filter((sent) => sent.path === `${STOCK}/documents`).map((sent) => sent.query);
    expect(asked()).toEqual([{}]);
    fireEvent.change(screen.getByLabelText("Hujjat turi"), { target: { value: "stocktake" } });
    fireEvent.change(screen.getByLabelText("Holati"), { target: { value: "draft" } });
    await waitFor(() => expect(asked().at(-1)).toEqual({ kind: "stocktake", status: "draft" }));
    // This member may not read the suppliers: no filter by one is drawn, and their list is not asked for.
    expect(screen.queryByLabelText("Ta'minotchi")).toBeNull();
    expect(server.sent.filter((sent) => sent.path === SUPPLIERS)).toEqual([]);
  });

  it("narrows to one supplier, working or archived, together with the other filters", async () => {
    const archived = supplierBody({ id: ARCHIVED_SUPPLIER, name: "Eski ulgurji", status: "archived" });
    const server = fakeServer((sent) => {
      if (sent.path === SUPPLIERS) {
        return ok(supplierListBody([supplierBody(), archived], sent.query));
      }
      return sent.path === `${STOCK}/documents` ? ok({ documents: [documentBody({ lines: undefined })], next_cursor: null }) : NOT_FOUND;
    });
    renderScreen(<DocumentsScreen />, { fetch: server.fetch, ...MANAGER });
    const asked = () => server.sent.filter((sent) => sent.path === `${STOCK}/documents`).map((sent) => sent.query);
    const choice = (await screen.findByRole("combobox", { name: "Ta'minotchi" })) as HTMLInputElement;
    await screen.findByRole("list", { name: "Ombor hujjatlari" });
    // The suppliers are asked for when the choice is opened, not with the screen.
    expect(server.sent.filter((sent) => sent.path === SUPPLIERS)).toEqual([]);
    expect(asked()).toEqual([{}]);
    fireEvent.click(choice);
    await screen.findByRole("option", { name: "Baraka ulgurji" });
    // The working one, then the archived one, said to be so: its documents are still in the books.
    const offered = within(screen.getByRole("listbox", { name: "Ta'minotchi" })).getAllByRole("option");
    expect(offered.map((option) => option.textContent)).toEqual(["Barcha ta'minotchilar", "Baraka ulgurji", "Eski ulgurjiArxivda"]);
    expect(server.sent.filter((sent) => sent.path === SUPPLIERS).map((sent) => sent.query)).toEqual([
      { status: "active", limit: "20" },
      { status: "archived", limit: "20" },
    ]);
    fireEvent.click(screen.getByRole("option", { name: "Baraka ulgurji" }));
    await waitFor(() => expect(asked().at(-1)).toEqual({ supplier_id: SUPPLIER_ID }));
    expect(choice.value).toBe("Baraka ulgurji");
    fireEvent.change(screen.getByLabelText("Hujjat turi"), { target: { value: "receipt" } });
    fireEvent.change(screen.getByLabelText("Holati"), { target: { value: "posted" } });
    await waitFor(() => expect(asked().at(-1)).toEqual({ kind: "receipt", status: "posted", supplier_id: SUPPLIER_ID }));
    await chooseSupplier(/Eski ulgurji/);
    await waitFor(() => expect(asked().at(-1)).toEqual({ kind: "receipt", status: "posted", supplier_id: ARCHIVED_SUPPLIER }));
    fireEvent.click(choice);
    fireEvent.click(await screen.findByRole("option", { name: "Barcha ta'minotchilar" }));
    await waitFor(() => expect(asked().at(-1)).toEqual({ kind: "receipt", status: "posted" }));
    expect(choice.value).toBe("");
  });

  it("narrows to a supplier who is not among the first hundred, found by a part of the name", async () => {
    // The list this filter was filled from before ended at a hundred suppliers: the 130th could not be chosen.
    const suppliers = manySuppliers(130);
    const server = fakeServer((sent) => {
      if (sent.path === SUPPLIERS) {
        return ok(supplierListBody(suppliers, sent.query));
      }
      return sent.path === `${STOCK}/documents` ? ok({ documents: [documentBody({ lines: undefined })], next_cursor: null }) : NOT_FOUND;
    });
    renderScreen(<DocumentsScreen />, { fetch: server.fetch, ...MANAGER });
    const choice = await screen.findByRole("combobox", { name: "Ta'minotchi" });
    fireEvent.change(choice, { target: { value: "130" } });
    fireEvent.click(await screen.findByRole("option", { name: "Ta'minotchi 130" }));
    await waitFor(() =>
      expect(server.sent.filter((sent) => sent.path === `${STOCK}/documents`).at(-1)?.query).toEqual({ supplier_id: suppliers[129]?.id }),
    );
    expect(server.sent.some((sent) => sent.path === SUPPLIERS && sent.query["q"] === "130")).toBe(true);
    // No request asked for more than a page: nothing is read whole.
    expect(server.sent.filter((sent) => sent.path === SUPPLIERS).every((sent) => sent.query["limit"] === "20")).toBe(true);
  });

  it("draws the supplier filter only for a member who holds suppliers.view, and asks nothing of the suppliers for anyone else", async () => {
    const server = backend();
    renderScreen(<DocumentsScreen />, { fetch: server.fetch, role: "seller", permissions: ["stock.view", "stock.receive", "stock.adjust", "suppliers.pay", "suppliers.manage"] });
    await screen.findByRole("list", { name: "Ombor hujjatlari" });
    expect(screen.queryByRole("combobox", { name: "Ta'minotchi" })).toBeNull();
    expect(screen.queryByLabelText("Ta'minotchi")).toBeNull();
    // The other two filters are there all the same.
    expect(screen.getByLabelText("Hujjat turi")).toBeTruthy();
    expect(server.sent.filter((sent) => sent.path === SUPPLIERS)).toEqual([]);
    cleanup();
    renderScreen(<DocumentsScreen />, { fetch: server.fetch, role: "seller", permissions: ["stock.view", "stock.receive", "suppliers.view"] });
    await screen.findByRole("list", { name: "Ombor hujjatlari" });
    expect(screen.getByRole("combobox", { name: "Ta'minotchi" })).toBeTruthy();
  });

  it("says that nothing matched, shows the server's refusal, and reads the next page with its cursor", async () => {
    let answer: "none" | "fail" | "pages" = "none";
    const server = fakeServer((sent) => {
      if (sent.path !== `${STOCK}/documents`) {
        return NOT_FOUND;
      }
      if (answer === "fail") {
        return refusal(500, "INTERNAL", "Serverda xatolik.");
      }
      if (answer === "none") {
        return ok({ documents: [], next_cursor: null });
      }
      return sent.query["cursor"] === "c2"
        ? ok({ documents: [documentBody({ id: ARCHIVED_SUPPLIER, number: 6, lines: undefined })], next_cursor: null })
        : ok({ documents: [documentBody({ lines: undefined })], next_cursor: "c2" });
    });
    renderScreen(<DocumentsScreen />, { fetch: server.fetch, role: "seller", permissions: ["stock.view", "stock.receive"] });
    expect(await screen.findByText("Bunday hujjat yo'q.")).toBeTruthy();
    answer = "fail";
    fireEvent.change(screen.getByLabelText("Holati"), { target: { value: "draft" } });
    expect((await screen.findByRole("alert")).textContent).toContain("Serverda xatolik.");
    expect(screen.queryByText("Bunday hujjat yo'q.")).toBeNull();
    answer = "pages";
    fireEvent.click(screen.getByRole("button", { name: "Qayta urinish" }));
    await screen.findByRole("link", { name: "Kirim № 7" });
    fireEvent.click(screen.getByRole("button", { name: "Yana ko'rsatish" }));
    await screen.findByRole("link", { name: "Kirim № 6" });
    expect(server.sent.at(-1)?.query).toEqual({ status: "draft", cursor: "c2" });
    expect(screen.queryByRole("button", { name: "Yana ko'rsatish" })).toBeNull();
  });
});

/**
 * The server opens the documents by "stock.receive" or "stock.adjust", and the items, a barcode and
 * the stock's settings by "stock.view" alone. A member who holds the first without the second reads,
 * posts and cancels documents; the form, which searches items and reads the settings, is not drawn
 * for them, and neither read is ever asked on their behalf.
 */
describe("for a member who writes documents and may not see the stock", () => {
  const RECEIVER = { role: "seller" as const, permissions: ["stock.receive"] };
  const HINT = /«Omborni ko'rish» ruxsati ham kerak/;
  const stockReads = (server: ReturnType<typeof backend>) =>
    server.sent.filter((sent) => [`${STOCK}/settings`, `${STOCK}/items`, `${STOCK}/lookup`, SUPPLIERS].includes(sent.path));

  it("the panel's list offers no new document and says why; with stock.view it offers them and says nothing", async () => {
    const server = backend();
    renderScreen(<DocumentsScreen />, { fetch: server.fetch, ...RECEIVER });
    await screen.findByRole("list", { name: "Ombor hujjatlari" });
    expect(within(screen.getByRole("navigation", { name: "Yangi hujjat" })).queryAllByRole("link")).toEqual([]);
    expect(screen.getByText(HINT)).toBeTruthy();
    expect(stockReads(server)).toEqual([]);
    cleanup();
    renderScreen(<DocumentsScreen />, { fetch: server.fetch, role: "seller", permissions: ["stock.view", "stock.receive"] });
    await screen.findByRole("list", { name: "Ombor hujjatlari" });
    expect(within(screen.getByRole("navigation", { name: "Yangi hujjat" })).getAllByRole("link").map((link) => link.textContent)).toEqual([
      "Kirim",
      "Ta'minotchiga qaytarish",
    ]);
    expect(screen.queryByText(HINT)).toBeNull();
  });

  it("a draft is shown as it stands, posted with one request, and never opened in the form", async () => {
    const draft = () => documentBody({ status: "draft", posted_at: null });
    const server = backend((sent) => (sent.path === `${STOCK}/documents/${DOCUMENT_ID}/post` ? ok(documentBody()) : NOT_FOUND), draft);
    // `counter`: where a member who may change the draft is taken straight to its form.
    renderScreen(<DocumentScreen documentId={DOCUMENT_ID} counter host={{}} />, { fetch: server.fetch, ...RECEIVER });
    expect(await screen.findByRole("heading", { name: /Kirim № 7/ })).toBeTruthy();
    expect(screen.queryByLabelText("Miqdor (kg)")).toBeNull();
    expect(screen.queryByRole("button", { name: "Tahrirlash" })).toBeNull();
    expect(screen.getByRole("button", { name: "Qoralamani o'chirish" })).toBeTruthy();
    // A unit the settings would have named is shown by the server's own word for it.
    expect(screen.getByRole("list", { name: "Tovarlar" }).textContent).toContain("2 kg");
    fireEvent.click(screen.getByRole("button", { name: "O'tkazish" }));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]).toMatchObject({ method: "POST", path: `${STOCK}/documents/${DOCUMENT_ID}/post` });
    expect(stockReads(server)).toEqual([]);
  });

  it("the form's own addresses are not screens: a new document and the quick receipt ask nothing", async () => {
    const server = backend();
    renderScreen(<NewDocumentScreen kind="receipt" host={{}} />, { fetch: server.fetch, ...RECEIVER });
    expect(await screen.findByText("Bosh sahifaga qaytish")).toBeTruthy();
    cleanup();
    renderScreen(<ReceiptScreen host={{}} />, { fetch: server.fetch, ...RECEIVER });
    expect(await screen.findByText("Bosh sahifaga qaytish")).toBeTruthy();
    expect(server.sent).toEqual([]);
  });
});

describe("the supplier of a document", () => {
  const suppliers = manySuppliers(130);
  const withMany = (write: (sent: Sent) => Reply, document: () => unknown = () => documentBody()) =>
    fakeServer((sent) => {
      if (sent.method !== "GET") {
        return write(sent);
      }
      switch (sent.path) {
        case `${STOCK}/settings`:
          return ok(stockSettingsBody());
        case SUPPLIERS:
          return ok(supplierListBody(suppliers, sent.query));
        case `${STOCK}/lookup`:
          return ok(costedItemBody());
        case `${STOCK}/documents/${DOCUMENT_ID}`:
          return ok(document());
        default:
          return NOT_FOUND;
      }
    });

  it("is any of the shop's suppliers: one past the first hundred is found by name and sent", async () => {
    // The form's list ended at a hundred suppliers before; the 117th could not be received from.
    const server = withMany(() => ok(documentBody({ supplier: { id: suppliers[116]?.id, name: "Ta'minotchi 117" }, paid: 0 }), 201));
    renderScreen(<ReceiptScreen host={{}} />, { fetch: server.fetch, ...MANAGER });
    await screen.findByRole("heading", { name: "Kirim" });
    find(EAN);
    fireEvent.change(await screen.findByLabelText("Bir birlik narxi"), { target: { value: "12000" } });
    const choice = screen.getByRole("combobox", { name: "Ta'minotchi" }) as HTMLInputElement;
    // A receipt may have no supplier, and the empty field says what that means.
    expect(choice.getAttribute("placeholder")).toBe("Ta'minotchisiz (naqdga olindi)");
    fireEvent.change(choice, { target: { value: "117" } });
    fireEvent.click(await screen.findByRole("option", { name: "Ta'minotchi 117" }));
    fireEvent.click(screen.getByLabelText("Qarzga olindi"));
    fireEvent.click(screen.getByRole("button", { name: "O'tkazish" }));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]?.body).toMatchObject({ supplier_id: suppliers[116]?.id, paid: 0 });
    expect(server.sent.filter((sent) => sent.path === SUPPLIERS).every((sent) => sent.query["status"] === "active" && sent.query["limit"] === "20")).toBe(true);
  });

  it("must be chosen for a return to a supplier: no row for 'none', and the field is marked when it is missing", async () => {
    const server = withMany(() => NOT_FOUND);
    renderScreen(<NewDocumentScreen kind="supplier_return" host={{}} />, { fetch: server.fetch, ...MANAGER });
    const choice = (await screen.findByRole("combobox", { name: "Ta'minotchi" })) as HTMLInputElement;
    expect(choice.getAttribute("placeholder")).toBe("Ta'minotchini tanlang");
    fireEvent.click(choice);
    await screen.findByRole("option", { name: "Ta'minotchi 01" });
    expect(within(screen.getByRole("listbox", { name: "Ta'minotchi" })).getAllByRole("option")[0]?.textContent).not.toMatch(/Ta'minotchisiz|tanlang/);
    fireEvent.keyDown(choice, { key: "Escape" });
    find(EAN);
    fireEvent.change(await screen.findByLabelText("Bir birlik narxi"), { target: { value: "12000" } });
    fireEvent.click(screen.getByRole("button", { name: "O'tkazish" }));
    expect(await screen.findByText("Ta'minotchini tanlang.")).toBeTruthy();
    expect(choice.getAttribute("aria-invalid")).toBe("true");
    expect(choice.getAttribute("aria-describedby")).toContain("stock-doc-supplier-error");
    expect(server.writes()).toEqual([]);
  });

  it("is kept and shown by name on a draft for a member who may not read the suppliers, and nothing of them is asked", async () => {
    const draft = () => documentBody({ status: "draft", posted_at: null, supplier: { id: SUPPLIER_ID, name: "Baraka ulgurji" }, paid: 0 });
    const server = withMany((sent) => (sent.method === "PUT" ? ok(draft()) : NOT_FOUND), draft);
    renderScreen(<DocumentScreen documentId={DOCUMENT_ID} counter host={{}} />, { fetch: server.fetch, role: "seller", permissions: ["stock.view", "stock.receive"] });
    await screen.findByLabelText("Miqdor (kg)");
    expect(screen.queryByRole("combobox", { name: "Ta'minotchi" })).toBeNull();
    expect(screen.getByText("Baraka ulgurji").closest("p")?.textContent).toBe("Ta'minotchi Baraka ulgurji");
    fireEvent.click(screen.getByRole("button", { name: "Qoralama sifatida saqlash" }));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]?.body).toMatchObject({ supplier_id: SUPPLIER_ID });
    expect(server.sent.filter((sent) => sent.path === SUPPLIERS)).toEqual([]);
  });
});

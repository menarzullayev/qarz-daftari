// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { fakeServer, ok, refusal, type Reply, type Sent } from "../../testing/fakeServer";
import { renderScreen } from "../../testing/renderScreen";
import { StockReportScreen } from "./StockReportScreen";
import { SupplierScreen, SuppliersScreen } from "./SupplierScreens";
import {
  costedItemBody,
  DOCUMENT_ID,
  ENTRY_ID,
  ITEM_ID,
  STOCK,
  stockSettingsBody,
  SUPPLIER_ID,
  supplierBody,
  supplierEntryBody,
  SUPPLIERS,
} from "./testing";

afterEach(cleanup);

const NOT_FOUND = refusal(404, "NOT_FOUND", "Topilmadi.");
const PURCHASE_ID = "99999999-9999-4999-8999-999999999992";
const BOTH = [
  { currency: "UZS", balance: 500000 },
  { currency: "USD", balance: -2500 },
];

function backend(write: (sent: Sent) => Reply = () => NOT_FOUND, settings = stockSettingsBody()) {
  return fakeServer((sent) => {
    if (sent.method !== "GET") {
      return write(sent);
    }
    switch (sent.path) {
      case `${STOCK}/settings`:
        return ok(settings);
      case SUPPLIERS:
        return ok({
          suppliers: [supplierBody({ balances: BOTH }), supplierBody({ id: "77777777-7777-4777-8777-777777777772", name: "Ziyo", phone: null, balances: [] })],
          totals: [
            { currency: "UZS", owed: 500000 },
            { currency: "USD", owed: 12000 },
          ],
          next_cursor: null,
        });
      case `${SUPPLIERS}/${SUPPLIER_ID}`:
        return ok({
          supplier: supplierBody({ balances: BOTH }),
          entries: [
            supplierEntryBody(),
            supplierEntryBody({ id: PURCHASE_ID, seq: 2, kind: "purchase", amount: 600000, document: { id: DOCUMENT_ID, kind: "receipt", number: 7 } }),
          ],
          next_cursor: null,
        });
      default:
        return NOT_FOUND;
    }
  });
}

describe("the suppliers", () => {
  it("lists what is owed to each, a line for each currency, an advance said in words", async () => {
    const server = backend();
    renderScreen(<SuppliersScreen />, { fetch: server.fetch, role: "manager" });
    const rows = within(await screen.findByRole("list", { name: "Ta'minotchilar" })).getAllByRole("listitem");
    expect(rows.map((row) => row.textContent)).toEqual([
      "Baraka ulgurjiTelefon+998901112233Hisob-kitobQarzimiz: 500 000 so'mOldindan to'langan: 25.00 $",
      "ZiyoTelefon—Hisob-kitobQarz yo'q",
    ]);
    expect(within(rows[0] as HTMLElement).getByRole("link", { name: "Baraka ulgurji" }).getAttribute("href")).toBe(`#/suppliers/${SUPPLIER_ID}`);
  });

  it("shows the totals side by side, one for each currency, and never their sum", async () => {
    const server = backend();
    const { container } = renderScreen(<SuppliersScreen />, { fetch: server.fetch, role: "manager" });
    await screen.findByRole("list", { name: "Ta'minotchilar" });
    const figures = [...container.querySelectorAll(".figure")].map((figure) => figure.textContent);
    expect(figures).toEqual(["Ta'minotchilarga jami qarzimiz500 000 so'm", "Ta'minotchilarga jami qarzimiz120.00 $"]);
    // 500 000 + 12 000 is 512 000, and 500 000 + 120.00 is nothing at all.
    expect(container.textContent).not.toContain("512");
    expect(container.textContent).not.toContain("620");
  });

  it("adds a supplier for one who may keep the list, with one request", async () => {
    const server = backend(() => ok(supplierBody({ name: "Yangi" }), 201));
    renderScreen(<SuppliersScreen />, { fetch: server.fetch, role: "manager" });
    fireEvent.click(await screen.findByRole("button", { name: "Ta'minotchi qo'shish" }));
    fireEvent.click(screen.getByRole("button", { name: "Saqlash" }));
    expect(await screen.findByText("Nomni yozing: ko'pi bilan 80 ta belgi.")).toBeTruthy();
    expect(server.writes()).toEqual([]);
    fireEvent.change(screen.getByRole("textbox", { name: "Ta'minotchi" }), { target: { value: "  Yangi  ulgurji " } });
    fireEvent.change(screen.getByLabelText("Telefon"), { target: { value: "+998 90 111 22 33" } });
    fireEvent.click(screen.getByRole("button", { name: "Saqlash" }));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]?.path).toBe(SUPPLIERS);
    expect(server.writes()[0]?.body).toEqual({ name: "Yangi ulgurji", phone: "+998 90 111 22 33" });
    expect(server.writes()[0]?.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
  });

  it("does not offer adding to a member who only reads the suppliers", async () => {
    const server = backend();
    renderScreen(<SuppliersScreen />, { fetch: server.fetch, role: "seller", permissions: ["suppliers.view"] });
    await screen.findByRole("list", { name: "Ta'minotchilar" });
    expect(screen.queryByRole("button", { name: "Ta'minotchi qo'shish" })).toBeNull();
  });

  it("searches by name and shows the archive when asked", async () => {
    const server = backend();
    renderScreen(<SuppliersScreen />, { fetch: server.fetch, role: "manager" });
    await screen.findByRole("list", { name: "Ta'minotchilar" });
    const asked = () => server.sent.filter((sent) => sent.path === SUPPLIERS).map((sent) => sent.query);
    expect(asked()).toEqual([{ status: "active" }]);
    fireEvent.change(screen.getByLabelText("Ta'minotchi nomi bo'yicha qidirish"), { target: { value: "bar" } });
    fireEvent.submit(screen.getByRole("search"));
    await waitFor(() => expect(asked().at(-1)).toEqual({ q: "bar", status: "active" }));
    fireEvent.click(screen.getByRole("button", { name: "Arxivda" }));
    await waitFor(() => expect(asked().at(-1)).toEqual({ q: "bar", status: "archived" }));
  });
});

describe("one supplier's account", () => {
  it("shows the entries, names the document of one that a document wrote, and offers to cancel only a direct one", async () => {
    const server = backend();
    renderScreen(<SupplierScreen supplierId={SUPPLIER_ID} office />, { fetch: server.fetch, role: "manager" });
    const rows = within(await screen.findByRole("list", { name: "Hisobdagi yozuvlar" })).getAllByRole("listitem");
    expect(rows.map((row) => row.textContent)).toEqual([
      "2026-yil 6-oktabr, 10:10Nima bo'ldiTo'lovSumma100 000 so'mAmallarYozuvni bekor qilish",
      "2026-yil 6-oktabr, 10:10Nima bo'ldiQarzga olingan tovarSumma600 000 so'mHujjatKirim № 7",
    ]);
    // The entry of a receipt is cancelled by cancelling the receipt: its page is linked, no button here.
    expect(within(rows[1] as HTMLElement).getByRole("link", { name: "Kirim № 7" }).getAttribute("href")).toBe(`#/stock-documents/${DOCUMENT_ID}`);
    expect(within(rows[1] as HTMLElement).queryByRole("button")).toBeNull();
  });

  it("names the document without a link where there are no document pages", async () => {
    const server = backend();
    renderScreen(<SupplierScreen supplierId={SUPPLIER_ID} office={false} />, { fetch: server.fetch, role: "manager" });
    const list = await screen.findByRole("list", { name: "Hisobdagi yozuvlar" });
    expect(within(list).getByText("Kirim № 7").tagName).not.toBe("A");
    expect(within(list).queryByRole("link")).toBeNull();
  });

  it("records a payment in the currency chosen, in its minor unit", async () => {
    const server = backend(
      () => ok({ entry: supplierEntryBody({ currency: "USD", amount: 1250 }), supplier: supplierBody() }, 201),
      stockSettingsBody({ currencies: ["UZS", "USD"] }),
    );
    renderScreen(<SupplierScreen supplierId={SUPPLIER_ID} office />, { fetch: server.fetch, role: "manager" });
    fireEvent.click(await screen.findByRole("button", { name: "To'lov yozish" }));
    fireEvent.click(screen.getByRole("button", { name: "To'lovni yozish" }));
    expect(await screen.findByText("Summani to'g'ri yozing: noldan katta bo'lsin.")).toBeTruthy();
    expect(server.writes()).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "$" }));
    fireEvent.change(screen.getByLabelText("To'langan summa"), { target: { value: "12.50" } });
    fireEvent.change(screen.getByLabelText("Izoh"), { target: { value: "naqd" } });
    fireEvent.click(screen.getByRole("button", { name: "To'lovni yozish" }));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]?.path).toBe(`${SUPPLIERS}/${SUPPLIER_ID}/entries`);
    expect(server.writes()[0]?.body).toEqual({ kind: "payment", amount: 1250, currency: "USD", note: "naqd" });
  });

  it("asks how a payment was paid only in a shop that keeps a cash book, and never for an old debt", async () => {
    const server = backend(
      () => ok({ entry: supplierEntryBody(), supplier: supplierBody() }, 201),
      stockSettingsBody({ cash_book: true }),
    );
    renderScreen(<SupplierScreen supplierId={SUPPLIER_ID} office />, { fetch: server.fetch, role: "manager" });
    fireEvent.click(await screen.findByRole("button", { name: "Boshlang'ich qarzni yozish" }));
    expect(screen.queryByLabelText("To'lov usuli")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Bekor qilish" }));
    fireEvent.click(screen.getByRole("button", { name: "To'lov yozish" }));
    fireEvent.change(screen.getByLabelText("To'lov usuli"), { target: { value: "transfer" } });
    fireEvent.change(screen.getByLabelText("To'langan summa"), { target: { value: "100000" } });
    fireEvent.click(screen.getByRole("button", { name: "To'lovni yozish" }));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]?.body).toEqual({ kind: "payment", amount: 100000, currency: "UZS", method: "transfer" });
  });

  it("does not offer dollars in a shop that keeps so'm only", async () => {
    const server = backend();
    renderScreen(<SupplierScreen supplierId={SUPPLIER_ID} office />, { fetch: server.fetch, role: "manager" });
    fireEvent.click(await screen.findByRole("button", { name: "To'lov yozish" }));
    expect(screen.queryByRole("group", { name: "Valyuta" })).toBeNull();
    // No cash book, no question about the method, and none is sent.
    expect(screen.queryByLabelText("To'lov usuli")).toBeNull();
    fireEvent.change(screen.getByLabelText("To'langan summa"), { target: { value: "100 000" } });
    fireEvent.click(screen.getByRole("button", { name: "To'lovni yozish" }));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]?.body).toEqual({ kind: "payment", amount: 100000, currency: "UZS" });
  });

  it("offers paying only to one who may pay, and the rest only to one who keeps the list", async () => {
    const server = backend();
    const payer = renderScreen(<SupplierScreen supplierId={SUPPLIER_ID} office />, {
      fetch: server.fetch,
      role: "seller",
      permissions: ["suppliers.view", "suppliers.pay"],
    });
    await screen.findByRole("button", { name: "To'lov yozish" });
    for (const name of ["Boshlang'ich qarzni yozish", "Ma'lumotlarini o'zgartirish", "Arxivga o'tkazish"]) {
      expect(screen.queryByRole("button", { name })).toBeNull();
    }
    payer.unmount();

    renderScreen(<SupplierScreen supplierId={SUPPLIER_ID} office />, {
      fetch: server.fetch,
      role: "seller",
      permissions: ["suppliers.view", "suppliers.manage"],
    });
    await screen.findByRole("button", { name: "Boshlang'ich qarzni yozish" });
    expect(screen.queryByRole("button", { name: "To'lov yozish" })).toBeNull();
    // The standing entry is a payment: cancelling it is the payer's, not this member's.
    expect(screen.queryByRole("button", { name: "Yozuvni bekor qilish" })).toBeNull();
  });

  it("offers a reader nothing but the account", async () => {
    const server = backend();
    renderScreen(<SupplierScreen supplierId={SUPPLIER_ID} office />, { fetch: server.fetch, role: "seller", permissions: ["suppliers.view"] });
    await screen.findByRole("list", { name: "Hisobdagi yozuvlar" });
    expect(screen.queryAllByRole("button")).toEqual([]);
  });

  it("cancels an entry only with a reason", async () => {
    const server = backend(() => ok({ entry: supplierEntryBody({ kind: "reversal", reverses_id: ENTRY_ID }), supplier: supplierBody() }));
    renderScreen(<SupplierScreen supplierId={SUPPLIER_ID} office />, { fetch: server.fetch, role: "manager" });
    fireEvent.click(await screen.findByRole("button", { name: "Yozuvni bekor qilish" }));
    const form = screen.getByRole("group", { name: "Nima uchun bekor qilinmoqda?" });
    fireEvent.click(within(form).getByRole("button", { name: "Yozuvni bekor qilish" }));
    expect(await screen.findByText("Sababni yozing: 1 dan 200 tagacha belgi.")).toBeTruthy();
    expect(server.writes()).toEqual([]);
    fireEvent.change(within(form).getByRole("textbox"), { target: { value: "ikki marta yozilgan" } });
    fireEvent.click(within(form).getByRole("button", { name: "Yozuvni bekor qilish" }));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]?.path).toBe(`${SUPPLIERS}/${SUPPLIER_ID}/entries/${ENTRY_ID}/cancel`);
    expect(server.writes()[0]?.body).toEqual({ reason: "ikki marta yozilgan" });
  });

  it("shows why a supplier with an open account cannot be archived", async () => {
    const server = backend(() => refusal(409, "SUPPLIER_HAS_BALANCE", "Hisob-kitobi yopilmagan ta'minotchini arxivlab bo'lmaydi."));
    renderScreen(<SupplierScreen supplierId={SUPPLIER_ID} office />, { fetch: server.fetch, role: "manager" });
    fireEvent.click(await screen.findByRole("button", { name: "Arxivga o'tkazish" }));
    // Nothing is sent until the question is answered.
    expect(server.writes()).toEqual([]);
    fireEvent.click(within(screen.getByRole("group")).getByRole("button", { name: "Arxivga o'tkazish" }));
    expect(await screen.findByText("Hisob-kitobi yopilmagan ta'minotchini arxivlab bo'lmaydi.")).toBeTruthy();
    expect(server.writes()[0]?.path).toBe(`${SUPPLIERS}/${SUPPLIER_ID}/archive`);
  });
});

function reportBody() {
  return {
    days: 30,
    totals: {
      items: 12,
      low: 2,
      cost: [
        { currency: "UZS", value: 900000 },
        { currency: "USD", value: 45000 },
      ],
      selling: 1500000,
      margin: { selling: 1200000, cost: 900000, margin: 300000 },
    },
    not_sold: { items: [costedItemBody({ last_sale_at: null })], more: true },
    sold_below_cost: {
      sales: [
        { item_id: ITEM_ID, name: "Shakar", unit: "kg", qty: "2", sale_total: 20000, cost_total: 24000, loss: 4000, created_at: "2026-10-05T06:00:00+00:00" },
      ],
      more: false,
    },
    low_stock: { items: [], more: false },
  };
}

describe("the stock report", () => {
  it("shows the cost of the stock as one figure for each currency, never added, and the three lists", async () => {
    const server = fakeServer((sent) => (sent.path === `${STOCK}/report` ? ok(reportBody()) : NOT_FOUND));
    const { container } = renderScreen(<StockReportScreen />, { fetch: server.fetch, role: "manager" });
    await screen.findByText("Ombor tannarxi, so'mda olinganlari");
    const figures = [...container.querySelectorAll(".figure")].map((figure) => figure.textContent);
    expect(figures).toEqual([
      "Hisobdagi tovarlar12",
      "Kam qolganlar2",
      "Ombor tannarxi, so'mda olinganlari900 000 so'm",
      "Ombor tannarxi, dollarda olinganlari450.00 $",
      "Sotish narxida qiymati1 500 000 so'm",
      "Kutilayotgan foyda300 000 so'mTannarxi so'mda yuritiladigan tovarlar: sotish narxida 1 200 000 so'm, tannarxda 900 000 so'm.",
    ]);
    // 900 000 so'm and 45 000 cents are not 945 000 of anything.
    expect(container.textContent).not.toContain("945");
    const idle = within(screen.getByRole("list", { name: "30 kun ichida sotilmaganlar" })).getAllByRole("listitem");
    expect(idle.map((row) => row.textContent)).toEqual(["ShakarQoldiq7,5 kgOxirgi sotuvHali sotilmaganTannarx bo'yicha qiymati90 000 so'm"]);
    expect(screen.getByText("Ro'yxatda faqat dastlabki 50 tasi ko'rsatilgan.")).toBeTruthy();
    const below = within(screen.getByRole("list", { name: "30 kun ichida tannarxdan arzon sotilganlar" })).getAllByRole("listitem");
    expect(below[0]?.textContent).toBe("ShakarVaqt2026-yil 5-oktabr, 11:00Miqdor2 kgSotildi20 000 so'mTannarx24 000 so'mZarar4 000 so'm");
    expect(screen.getByText("Kam qolgan tovar yo'q.")).toBeTruthy();
  });

  it("asks again for another period", async () => {
    const server = fakeServer((sent) => (sent.path === `${STOCK}/report` ? ok(reportBody()) : NOT_FOUND));
    renderScreen(<StockReportScreen />, { fetch: server.fetch, role: "manager" });
    fireEvent.change(await screen.findByLabelText("Davr"), { target: { value: "90" } });
    await waitFor(() => expect(server.sent.map((sent) => sent.query)).toEqual([{ days: "30" }, { days: "90" }]));
  });

  it("is not there for a member who may not see cost, and is not asked for them", async () => {
    const server = fakeServer(() => ok(reportBody()));
    renderScreen(<StockReportScreen />, { fetch: server.fetch, role: "seller" });
    expect(await screen.findByText("Bosh sahifaga qaytish")).toBeTruthy();
    expect(screen.queryByText("Kutilayotgan foyda")).toBeNull();
    expect(server.sent).toEqual([]);
  });
});

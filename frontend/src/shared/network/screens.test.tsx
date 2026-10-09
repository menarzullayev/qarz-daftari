// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fakeServer, ok, refusal, type Reply, type Sent, SHOP_BASE } from "../../testing/fakeServer";
import { renderScreen } from "../../testing/renderScreen";
import { stockSettingsBody, supplierBody } from "../stock/testing";
import { HomeScreen, LinkScreen } from "./LinkScreens";
import { NoteScreen, NotesScreen } from "./NoteScreens";
import { ComposeScreen } from "./OrderComposer";
import { OrderScreen } from "./OrderScreens";
import { PaymentsScreen } from "./PaymentScreens";
import {
  CODE,
  COUNTERPART_ID,
  draftBody,
  eventBody,
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

beforeEach(() => {
  window.location.hash = "";
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const NOT_FOUND = refusal(404, "NOT_FOUND", "Topilmadi.");
const ON = { network: true, stock: true } as const;

/** A server that reads from `reads` by path and answers every write with `write`. */
function backend(reads: Record<string, unknown>, write: (sent: Sent) => Reply = () => NOT_FOUND) {
  return fakeServer((sent) => {
    if (sent.method !== "GET") {
      return write(sent);
    }
    if (sent.path === `${SHOP_BASE}/stock/settings`) {
      return ok(stockSettingsBody());
    }
    return sent.path in reads ? ok(reads[sent.path]) : NOT_FOUND;
  });
}

/** Text as it reads: amounts keep their thousands together with spaces that do not break. */
const plain = (text: string | null | undefined) => (text ?? "").replace(/\u00a0/g, " ");
const rows = (name: string) => within(screen.getByRole("list", { name })).getAllByRole("listitem");

describe("the overview of the web panel", () => {
  it("puts what awaits this shop first, each figure with the way to its list", async () => {
    const server = backend({ [NETWORK]: overviewBody({ waiting: waitingBody({ links: 1, deliveries: 2, rejected_notes: 3 }) }) });
    const { container } = renderScreen(<HomeScreen />, { fetch: server.fetch, role: "manager", features: ON });
    const waiting = await screen.findByRole("region", { name: "Sizdan javob kutilmoqda" });
    expect([...waiting.querySelectorAll(".figure")].map((figure) => figure.textContent)).toEqual([
      "Ulanish so'rovlari1",
      "Yetkazilishi kerak buyurtmalar2",
      "Rad etilgan yuk xatlari3",
    ]);
    expect(within(waiting).getByRole("link", { name: "Yetkazilishi kerak buyurtmalar" }).getAttribute("href")).toBe("#/network/orders/in");
    // Before the links, the invitations and everything else of the screen but the section's own bar.
    expect(container.querySelector("section")).toBe(waiting);
    expect(waiting.compareDocumentPosition(screen.getByRole("list", { name: "Hamkorlar" })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("says that nothing awaits when nothing does, and names a partner whose shop is gone", async () => {
    const gone = linkBody({ id: "a1a1a1a1-a1a1-4a1a-8a1a-a1a1a1a1a1a2", state: "ended", ended_by: "partner", partner: { name: null, phone: null, removed: true } });
    const server = backend({ [NETWORK]: overviewBody({ links: [linkBody(), gone] }) });
    renderScreen(<HomeScreen />, { fetch: server.fetch, role: "manager", features: ON });
    expect(await screen.findByText("Hozircha sizdan hech narsa kutilmayapti.")).toBeTruthy();
    expect(rows("Hamkorlar").map((row) => row.textContent)).toEqual([
      "Baraka ulgurjiBiz kimmizBiz xaridormizHolatiFaol",
      "Hamkor o'chirilganBiz kimmizBiz xaridormizHolatiTugatilgan",
    ]);
    expect(screen.getByRole("link", { name: "Baraka ulgurji" }).getAttribute("href")).toBe(`#/network/links/${NET_LINK_ID}`);
  });

  it("offers invitations and linking to the one who manages the links, and to nobody else", async () => {
    const reads = { [NETWORK]: overviewBody({ links: [linkBody({ state: "requested", invited: true })], invites: [inviteBody()] }) };
    renderScreen(<HomeScreen />, { fetch: backend(reads).fetch, role: "manager", features: ON });
    await screen.findByRole("list", { name: "Hamkorlar" });
    for (const name of ["Taklif kodi yaratish", "Kod bilan ulanish", "Qabul qilish", "Rad etish", "Qaytarib olish"]) {
      expect(screen.queryByRole("button", { name }), name).toBeNull();
    }
    cleanup();
    renderScreen(<HomeScreen />, { fetch: backend(reads).fetch, role: "owner", features: ON });
    await screen.findByRole("list", { name: "Hamkorlar" });
    for (const name of ["Taklif kodi yaratish", "Kod bilan ulanish", "Qabul qilish", "Rad etish", "Qaytarib olish"]) {
      expect(screen.getByRole("button", { name }), name).toBeTruthy();
    }
  });

  it("makes an invitation and shows its code once, with a copy and the word to hand it over outside the app", async () => {
    const writeText = vi.fn(() => Promise.resolve());
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    const server = backend({ [NETWORK]: overviewBody({ invites: [] }) }, () => ok({ ...inviteBody({ as: "buyer" }), code: CODE }, 201));
    renderScreen(<HomeScreen />, { fetch: server.fetch, role: "owner", features: ON });
    fireEvent.click(await screen.findByRole("button", { name: "Taklif kodi yaratish" }));
    expect(server.writes()).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "Kod yaratish" }));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]).toMatchObject({ method: "POST", path: `${NETWORK}/invites`, body: { as: "buyer" } });
    expect(server.writes()[0]?.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
    expect(await screen.findByText(CODE)).toBeTruthy();
    expect(screen.getByText("Kodni hamkor do'konga ilovadan tashqarida bering: og'zaki, telefon yoki xabar orqali.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Koddan nusxa olish" }));
    expect(writeText).toHaveBeenCalledWith(CODE);
    expect(await screen.findByRole("button", { name: "Nusxa olindi" })).toBeTruthy();
    // Once closed it is gone: the list that was read again carries no code.
    fireEvent.click(screen.getByRole("button", { name: "Yopish" }));
    expect(screen.queryByText(CODE)).toBeNull();
  });

  it("links by a code with the role this shop will have, and takes an invitation back", async () => {
    const server = backend({ [NETWORK]: overviewBody({ invites: [inviteBody()] }) }, (sent) =>
      sent.method === "DELETE" ? ok(null) : ok({ link: linkBody({ state: "requested", role: "supplier" }) }, 201),
    );
    renderScreen(<HomeScreen />, { fetch: server.fetch, role: "owner", features: ON });
    fireEvent.click(await screen.findByRole("button", { name: "Kod bilan ulanish" }));
    fireEvent.click(screen.getByRole("button", { name: "So'rov yuborish" }));
    expect(await screen.findByText("Kodni kiriting.")).toBeTruthy();
    expect(server.writes()).toEqual([]);
    fireEvent.change(screen.getByLabelText("Hamkor bergan kod"), { target: { value: `  ${CODE} ` } });
    fireEvent.click(within(screen.getByRole("group", { name: "Bu do'kon kim bo'ladi" })).getByRole("button", { name: "Biz ta'minotchimiz" }));
    fireEvent.click(screen.getByRole("button", { name: "So'rov yuborish" }));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]).toMatchObject({ method: "POST", path: `${NETWORK}/links`, body: { code: CODE, as: "supplier" } });
    expect(await screen.findByText("So'rov yuborildi. Hamkor uni qabul qilgach, hamkorlik boshlanadi.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Qaytarib olish" }));
    await waitFor(() => expect(server.writes()).toHaveLength(2));
    expect(server.writes()[1]).toMatchObject({ method: "DELETE", path: `${NETWORK}/invites/${INVITE_ID}` });
  });

  it("shows the server's words when a code is refused", async () => {
    const server = backend({ [NETWORK]: overviewBody({ invites: [] }) }, () => refusal(404, "NETWORK_INVITE_INVALID", "Kod noto'g'ri yoki muddati o'tgan."));
    renderScreen(<HomeScreen />, { fetch: server.fetch, role: "owner", features: ON });
    fireEvent.click(await screen.findByRole("button", { name: "Kod bilan ulanish" }));
    fireEvent.change(screen.getByLabelText("Hamkor bergan kod"), { target: { value: "xato" } });
    fireEvent.click(screen.getByRole("button", { name: "So'rov yuborish" }));
    expect((await screen.findByRole("alert")).textContent).toBe("Kod noto'g'ri yoki muddati o'tgan.");
  });

  it("accepts a request onto one of the shop's own suppliers, or with none chosen onto a new one", async () => {
    const server = backend(
      { [NETWORK]: overviewBody({ links: [linkBody({ state: "requested", invited: true, counterpart: null })], invites: [] }), [`${SHOP_BASE}/suppliers`]: { suppliers: [supplierBody()], totals: [], next_cursor: null } },
      () => ok({ link: linkBody() }),
    );
    renderScreen(<HomeScreen />, { fetch: server.fetch, role: "owner", features: ON });
    fireEvent.click(await screen.findByRole("button", { name: "Qabul qilish" }));
    const form = within(screen.getByRole("group", { name: "Qabul qilish" }));
    await form.findByRole("option", { name: "Baraka ulgurji" });
    expect((form.getByLabelText("Bizning daftardagi ta'minotchi") as HTMLSelectElement).value).toBe("");
    expect(form.getByRole("option", { name: "Yangi yozuv yaratilsin" })).toBeTruthy();
    fireEvent.change(form.getByLabelText("Bizning daftardagi ta'minotchi"), { target: { value: COUNTERPART_ID } });
    fireEvent.click(form.getByRole("button", { name: "Qabul qilish" }));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]).toMatchObject({ method: "POST", path: `${NETWORK}/links/${NET_LINK_ID}/accept`, body: { counterpart_id: COUNTERPART_ID } });
  });
});

describe("one partner's page", () => {
  const page = (reconciliation: unknown[], link = linkBody()) => ({
    link,
    reconciliation,
    events: [eventBody(), eventBody({ kind: "link_accepted", by: "own", at: "2026-10-01T06:00:00+00:00" }), eventBody({ kind: "something_new", by: "partner" })],
  });
  const open = (reconciliation: unknown[], options: { role?: "manager" | "owner"; permissions?: string[]; link?: ReturnType<typeof linkBody> } = {}) => {
    const server = backend({ [`${NETWORK}/links/${NET_LINK_ID}`]: page(reconciliation, options.link) });
    const view = renderScreen(<LinkScreen linkId={NET_LINK_ID} />, {
      fetch: server.fetch,
      role: options.role ?? "manager",
      ...(options.permissions ? { permissions: options.permissions } : {}),
      features: ON,
    });
    return { server, ...view };
  };

  it("says plainly that the books differ, by how much, and lists what explains it", async () => {
    const row = reconciliationBody({
      awaiting: { notes: 300000, payments_own: 100000, payments_partner: 50000, payments_declined: 70000 },
      own_balance: 330000,
      difference: -170000,
    });
    open([row]);
    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText("Daftarlar farq qiladi")).toBeTruthy();
    expect(within(alert).getByText("Farq: 170 000 so'm")).toBeTruthy();
    expect(within(alert).getAllByRole("listitem").map((item) => plain(item.textContent))).toEqual([
      "Yetkazilgan, lekin hali tasdiqlanmagan yuk xatlari: 300 000 so'm. Hech kimning daftarida yo'q.",
      "Biz yozgan, hamkor hali tasdiqlamagan to'lovlar: 100 000 so'm. Faqat bizning daftarimizda bor.",
      "Hamkor yozgan, bizning tasdig'imizni kutayotgan to'lovlar: 50 000 so'm. Bizning daftarimizda yo'q.",
      "Hamkor rad etgan, lekin daftarimizda turgan to'lovlar: 70 000 so'm. Ta'minotchi hisobidagi o'z yozuvingizni bekor qiling.",
    ]);
    expect(within(alert).getByRole("link", { name: "Ta'minotchi hisobini ochish" }).getAttribute("href")).toBe(`#/suppliers/${COUNTERPART_ID}`);
    // The two balances stand side by side, each under its own name; nothing calls them equal.
    const block = screen.getByRole("region", { name: "Hisob: so'm" });
    expect(within(block).getByText("Biz qarzdormiz: 500 000 so'm")).toBeTruthy();
    expect(within(block).getByText("Biz qarzdormiz: 330 000 so'm")).toBeTruthy();
    expect(screen.queryByText(/Daftarlar bir xil/)).toBeNull();
    expect(screen.queryByText(/daftarlar teng/i)).toBeNull();
  });

  it("calls the books equal only when they are and nothing is on its way", async () => {
    open([reconciliationBody()]);
    expect((await screen.findByText("Daftarlar bir xil: kelishilgan qoldiq o'z daftaringizdagi bilan teng.")).getAttribute("role")).toBe("status");
    expect(screen.queryByText("Daftarlar farq qiladi")).toBeNull();
    cleanup();
    open([reconciliationBody({ awaiting: { notes: 300000, payments_own: 0, payments_partner: 0, payments_declined: 0 } })]);
    await screen.findByText("Hozir daftarlar teng, lekin hali tasdiqlanmagan narsalar bor:");
    expect(screen.queryByText(/Daftarlar bir xil/)).toBeNull();
    expect(screen.getByText("Yetkazilgan, lekin hali tasdiqlanmagan yuk xatlari: 300 000 so'm. Hech kimning daftarida yo'q.")).toBeTruthy();
  });

  it("does not compare for a member who may not see the shop's own account: no balance of it, and no verdict", async () => {
    const hidden: Record<string, unknown> = reconciliationBody();
    delete hidden["own_balance"];
    delete hidden["difference"];
    open([hidden], { permissions: ["network.view"] });
    expect(await screen.findByText("O'z daftaringizdagi hisobni ko'rishga ruxsatingiz yo'q, shuning uchun daftarlarni solishtirib bo'lmaydi.")).toBeTruthy();
    expect(screen.queryByText(/Daftarlar bir xil/)).toBeNull();
    expect(screen.queryByText("Daftarlar farq qiladi")).toBeNull();
    expect(screen.queryByText("O'z daftarimizda (ta'minotchi hisobi)")).toBeNull();
    expect(screen.getByText("Biz qarzdormiz: 500 000 so'm")).toBeTruthy();
  });

  it("keeps two currencies apart, and reads a supplier's side the other way round", async () => {
    const dollars = reconciliationBody({ currency: "USD", agreed: { delivered: 50000, paid: 60000, balance: -10000 }, own_balance: -10000, difference: 0 });
    open([reconciliationBody(), dollars], { link: linkBody({ role: "supplier", counterpart: { kind: "customer", id: COUNTERPART_ID } }) });
    const som = within(await screen.findByRole("region", { name: "Hisob: so'm" }));
    expect(som.getAllByText("Hamkor qarzdor: 500 000 so'm")).toHaveLength(2);
    // The buyer paid ahead: the supplier owes the difference.
    expect(within(screen.getByRole("region", { name: "Hisob: Dollarda" })).getAllByText("Biz qarzdormiz: 100.00 $")).toHaveLength(2);
    expect(screen.getByRole("link", { name: "Hisobini ochish" }).getAttribute("href")).toBe(`#/customers/${COUNTERPART_ID}`);
  });

  it("tells the history with 'we' and 'the partner', never a member's name, and keeps a kind it does not know", async () => {
    open([]);
    const history = within(await screen.findByRole("list", { name: "Tarix" })).getAllByRole("listitem");
    expect(history.map((item) => item.textContent)).toEqual([
      "Ulanish so'raldi2026-yil 1-oktabr, 10:00 · hamkor",
      "Ulanish qabul qilindi2026-yil 1-oktabr, 11:00 · biz",
      "something_new2026-yil 1-oktabr, 10:00 · hamkor",
    ]);
    expect(screen.getByText("Hali yetkazish ham, to'lov ham bo'lmagan.")).toBeTruthy();
  });

  it("lets the owner end the link after a question, and a manager only record a payment", async () => {
    const { server } = open([reconciliationBody()], { role: "manager" });
    await screen.findByRole("button", { name: "To'lov yozish" });
    expect(screen.queryByRole("button", { name: "Hamkorlikni tugatish" })).toBeNull();
    expect(server.writes()).toEqual([]);
    cleanup();
    const owner = backend({ [`${NETWORK}/links/${NET_LINK_ID}`]: page([]) }, () => ok({ link: linkBody({ state: "ended", ended_by: "own" }) }));
    renderScreen(<LinkScreen linkId={NET_LINK_ID} />, { fetch: owner.fetch, role: "owner", features: ON });
    fireEvent.click(await screen.findByRole("button", { name: "Hamkorlikni tugatish" }));
    expect(screen.getByText(/“Baraka ulgurji” bilan hamkorlik tugatilsinmi\?/)).toBeTruthy();
    expect(owner.writes()).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "Hamkorlikni tugatish" }));
    await waitFor(() => expect(owner.writes()).toHaveLength(1));
    expect(owner.writes()[0]).toMatchObject({ method: "POST", path: `${NETWORK}/links/${NET_LINK_ID}/end` });
  });
});

describe("an order", () => {
  it("is accepted only with every line answered: a quantity, zero too, and a price for each", async () => {
    const server = backend({ [`${NETWORK}/orders/${ORDER_ID}`]: orderBody() }, () => ok(orderBody({ status: "accepted", currency: "UZS", total: 480000 })));
    renderScreen(<OrderScreen orderId={ORDER_ID} />, { fetch: server.fetch, role: "manager", features: ON });
    fireEvent.click(await screen.findByRole("button", { name: "Buyurtmani qabul qilish" }));
    const form = within(screen.getByRole("group", { name: "Buyurtmani qabul qilish" }));
    // The asked quantities are there to begin with; the prices are the supplier's to say.
    expect((form.getByLabelText("Yetkaziladigan miqdor, kg") as HTMLInputElement).value).toBe("50");
    fireEvent.click(form.getByRole("button", { name: "Qabul qilish va javobni yuborish" }));
    expect(await form.findAllByText("Narxni to'g'ri yozing.")).toHaveLength(2);
    expect(server.writes()).toEqual([]);
    const prices = form.getAllByLabelText("Bir birlik narxi");
    fireEvent.change(form.getByLabelText("Yetkaziladigan miqdor, kg"), { target: { value: "40,5" } });
    fireEvent.change(prices[0] as HTMLElement, { target: { value: "12000" } });
    fireEvent.change(form.getByLabelText("Yetkaziladigan miqdor, dona"), { target: { value: "0" } });
    fireEvent.change(prices[1] as HTMLElement, { target: { value: "0" } });
    expect(form.getByText("Jami: 486 000 so'm")).toBeTruthy();
    fireEvent.click(form.getByRole("button", { name: "Qabul qilish va javobni yuborish" }));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]).toMatchObject({ method: "POST", path: `${NETWORK}/orders/${ORDER_ID}/accept` });
    expect(server.writes()[0]?.body).toEqual({ lines: [{ line_no: 1, qty: "40.5", unit_price: 12000 }, { line_no: 2, qty: "0", unit_price: 0 }] });
  });

  it("marks for the buyer the lines the supplier accepted in another quantity", async () => {
    const lines = [
      { line_no: 1, name: "Shakar", unit: "kg", qty: "50", item_id: null, accepted_qty: "40.5", unit_price: 12000, line_total: 486000, changed: true },
      { line_no: 2, name: "Choy", unit: "dona", qty: "10", item_id: null, accepted_qty: "10", unit_price: 9000, line_total: 90000, changed: false },
    ];
    const server = backend({ [`${NETWORK}/orders/${ORDER_ID}`]: orderBody({ role: "buyer", status: "accepted", currency: "UZS", total: 576000, lines }) });
    renderScreen(<OrderScreen orderId={ORDER_ID} />, { fetch: server.fetch, role: "manager", features: ON });
    expect(await screen.findByText(/so'ralganidan boshqa miqdorni qabul qildi/)).toBeTruthy();
    const [sugar, tea] = rows("Buyurtma qatorlari");
    expect(plain(sugar?.textContent)).toBe("ShakarSo'ralgan50 kgQabul qilingan40,5 kg O'zgarganNarxi12 000 so'mJami486 000 so'm");
    expect(tea?.textContent).not.toContain("O'zgargan");
    // The buyer may cancel; answering and delivering are the supplier's.
    expect(screen.getByRole("button", { name: "Buyurtmani bekor qilish" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Buyurtmani qabul qilish" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Yetkazish: yuk xati yozish" })).toBeNull();
  });

  it("is declined with a reason of three characters or more, and shows nothing to do to one who only reads", async () => {
    const server = backend({ [`${NETWORK}/orders/${ORDER_ID}`]: orderBody() }, () => ok(orderBody({ status: "declined", closed_reason: "Omborda yo'q" })));
    renderScreen(<OrderScreen orderId={ORDER_ID} />, { fetch: server.fetch, role: "manager", features: ON });
    fireEvent.click(await screen.findByRole("button", { name: "Buyurtmani rad etish" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Rad etish sababi" }), { target: { value: "yo" } });
    fireEvent.click(screen.getByRole("button", { name: "Buyurtmani rad etish" }));
    expect(await screen.findByText("Sababni yozing: 3 dan 200 belgigacha.")).toBeTruthy();
    expect(server.writes()).toEqual([]);
    fireEvent.change(screen.getByRole("textbox", { name: "Rad etish sababi" }), { target: { value: "  Omborda   yo'q " } });
    fireEvent.click(screen.getByRole("button", { name: "Buyurtmani rad etish" }));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]).toMatchObject({ path: `${NETWORK}/orders/${ORDER_ID}/decline`, body: { reason: "Omborda yo'q" } });
    cleanup();
    renderScreen(<OrderScreen orderId={ORDER_ID} />, { fetch: backend({ [`${NETWORK}/orders/${ORDER_ID}`]: orderBody() }).fetch, role: "seller", permissions: ["network.view"], features: ON });
    await screen.findByRole("list", { name: "Buyurtma qatorlari" });
    expect(screen.queryByRole("button", { name: /Buyurtmani/ })).toBeNull();
  });

  it("is delivered by a note that says what was paid on delivery, for one who may also record a sale on credit", async () => {
    const accepted = orderBody({ status: "accepted", currency: "UZS", total: 600000 });
    const server = backend({ [`${NETWORK}/orders/${ORDER_ID}`]: accepted }, () => ok(noteBody({ role: "supplier", paid: 100000, terms: "part" }), 201));
    renderScreen(<OrderScreen orderId={ORDER_ID} />, { fetch: server.fetch, role: "manager", features: ON });
    fireEvent.click(await screen.findByRole("button", { name: "Yetkazish: yuk xati yozish" }));
    fireEvent.change(screen.getByLabelText("Yetkazishda to'langan summa"), { target: { value: "700000" } });
    fireEvent.click(screen.getByRole("button", { name: "Yuk xatini yozish" }));
    expect(await screen.findByText("Summani to'g'ri yozing: jamidan oshmasin.")).toBeTruthy();
    expect(server.writes()).toEqual([]);
    fireEvent.change(screen.getByLabelText("Yetkazishda to'langan summa"), { target: { value: "100000" } });
    expect(screen.getByText("Nasiyaga qoladi: 500 000 so'm")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Yuk xatini yozish" }));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]).toMatchObject({ path: `${NETWORK}/orders/${ORDER_ID}/deliver`, body: { paid: 100000 } });
    await waitFor(() => expect(window.location.hash).toBe(`#/network/notes/${NOTE_ID}`));
    cleanup();
    // Fulfilling without the permission to record a sale on credit: the note is not theirs to issue.
    renderScreen(<OrderScreen orderId={ORDER_ID} />, {
      fetch: backend({ [`${NETWORK}/orders/${ORDER_ID}`]: accepted }).fetch,
      role: "seller",
      permissions: ["network.view", "network.fulfil"],
      features: ON,
    });
    await screen.findByRole("list", { name: "Buyurtma qatorlari" });
    expect(screen.queryByRole("button", { name: "Yetkazish: yuk xati yozish" })).toBeNull();
  });
});

describe("a new order", () => {
  const reads = { [NETWORK]: overviewBody({ links: [linkBody(), linkBody({ id: "a1a1a1a1-a1a1-4a1a-8a1a-a1a1a1a1a1a2", role: "supplier" })] }) };

  it("is saved as a draft with its lines, for the one supplier this shop buys from", async () => {
    const server = backend(reads, () => ok(draftBody(), 201));
    renderScreen(<ComposeScreen draftId={null} />, { fetch: server.fetch, role: "manager", features: ON });
    // The link in which this shop is the supplier is not one to order from.
    const supplier = (await screen.findByLabelText("Ta'minotchi")) as HTMLSelectElement;
    expect([...supplier.options].map((option) => option.textContent)).toEqual(["Ta'minotchini tanlang", "Baraka ulgurji"]);
    expect(supplier.value).toBe(NET_LINK_ID);
    fireEvent.click(screen.getByRole("button", { name: "Qoralamani saqlash" }));
    expect(await screen.findByText("Kamida bitta qator yozing.")).toBeTruthy();
    expect(server.writes()).toEqual([]);
    fireEvent.change(screen.getByLabelText("1-qator: tovar nomi"), { target: { value: "  Shakar " } });
    fireEvent.change(screen.getByLabelText("Miqdor"), { target: { value: "1,5" } });
    fireEvent.change(screen.getByLabelText("Birlik"), { target: { value: "qop" } });
    fireEvent.click(screen.getByRole("button", { name: "Qator qo'shish" }));
    fireEvent.change(screen.getByLabelText("2-qator: tovar nomi"), { target: { value: "Choy" } });
    fireEvent.click(screen.getByRole("button", { name: "Qoralamani saqlash" }));
    expect(await screen.findByText("Belgilangan qatorlarni tuzating.")).toBeTruthy();
    expect(server.writes()).toEqual([]);
    fireEvent.change(screen.getAllByLabelText("Miqdor")[1] as HTMLElement, { target: { value: "10" } });
    fireEvent.change(screen.getByLabelText("Kerakli sana"), { target: { value: "2026-10-09" } });
    fireEvent.click(screen.getByRole("button", { name: "Qoralamani saqlash" }));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]).toMatchObject({ method: "POST", path: `${NETWORK}/drafts` });
    expect(server.writes()[0]?.body).toEqual({
      link_id: NET_LINK_ID,
      wanted_date: "2026-10-09",
      lines: [
        { name: "Shakar", unit: "qop", qty: "1.5" },
        { name: "Choy", unit: "dona", qty: "10" },
      ],
    });
    await waitFor(() => expect(window.location.hash).toBe(`#/network/drafts/${ORDER_ID}`));
  });

  it("is sent in two steps, saved and then sent, and opens the order it became", async () => {
    const server = backend({ ...reads, [`${NETWORK}/drafts/${ORDER_ID}`]: draftBody() }, (sent) =>
      sent.path.endsWith("/send") ? ok(orderBody({ role: "buyer" }), 201) : ok(draftBody()),
    );
    renderScreen(<ComposeScreen draftId={ORDER_ID} />, { fetch: server.fetch, role: "manager", features: ON });
    expect(((await screen.findByLabelText("1-qator: tovar nomi")) as HTMLInputElement).value).toBe("Shakar");
    fireEvent.click(screen.getByRole("button", { name: "Ta'minotchiga yuborish" }));
    await waitFor(() => expect(server.writes()).toHaveLength(2));
    expect(server.writes().map((sent) => `${sent.method} ${sent.path}`)).toEqual([`PUT ${NETWORK}/drafts/${ORDER_ID}`, `POST ${NETWORK}/drafts/${ORDER_ID}/send`]);
    const keys = server.writes().map((sent) => sent.headers["Idempotency-Key"]);
    expect(keys[1]).toBe(`${keys[0]}-send`);
    await waitFor(() => expect(window.location.hash).toBe(`#/network/orders/${ORDER_ID}`));
  });

  it("is not there for a member who may not order, nor without a supplier to order from", async () => {
    renderScreen(<ComposeScreen draftId={null} />, { fetch: backend(reads).fetch, role: "seller", permissions: ["network.view"], features: ON });
    expect(await screen.findByText("Bunday sahifa yo'q yoki sizda unga ruxsat yo'q.")).toBeTruthy();
    cleanup();
    renderScreen(<ComposeScreen draftId={null} />, { fetch: backend({ [NETWORK]: overviewBody({ links: [] }) }).fetch, role: "manager", features: ON });
    expect(await screen.findByText("Buyurtma berish uchun siz xaridor bo'lgan faol hamkorlik kerak.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Ta'minotchiga yuborish" })).toBeNull();
  });
});

describe("a delivery note", () => {
  const CONFIRM = "Tasdiqlash: tovar omborga kirim qilinadi, ta'minotchi daftariga nasiya savdo yoziladi";

  it("is confirmed by a button that says what it does, onto the item chosen on the order", async () => {
    let confirmed = false;
    const server = fakeServer((sent) => {
      if (sent.method !== "GET") {
        confirmed = true;
        return ok(noteBody({ status: "received", stock_document_id: STOCK_DOCUMENT_ID }));
      }
      if (sent.path === `${SHOP_BASE}/stock/settings`) {
        return ok(stockSettingsBody());
      }
      return ok(confirmed ? noteBody({ status: "received", decided_at: "2026-10-06T07:00:00+00:00", stock_document_id: STOCK_DOCUMENT_ID }) : noteBody());
    });
    renderScreen(<NoteScreen noteId={NOTE_ID} office />, { fetch: server.fetch, role: "manager", features: ON });
    expect(await screen.findByText("Sizning tasdig'ingiz kutilmoqda. Tasdiqlamaguningizcha hech kimning daftariga hech narsa yozilmaydi.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Yukni qabul qilish" }));
    expect(screen.getByText("Buyurtmada tanlangan o'z tovaringizga tushadi.")).toBeTruthy();
    expect(server.writes()).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: CONFIRM }));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]).toMatchObject({ method: "POST", path: `${NETWORK}/notes/${NOTE_ID}/confirm` });
    // Nothing was paid on delivery: no method is said.
    expect(server.writes()[0]?.body).toEqual({ lines: [{ line_no: 1, item_id: OWN_ITEM_ID }] });
    expect((await screen.findByRole("link", { name: "Ombor hujjatini ochish" })).getAttribute("href")).toBe(`#/stock-documents/${STOCK_DOCUMENT_ID}`);
    expect(screen.queryByRole("button", { name: "Yukni qabul qilish" })).toBeNull();
  });

  it("needs every line to end on an item or be added with its selling price, and takes the item the server names", async () => {
    const lines = [{ line_no: 1, name: "Shakar", unit: "kg", qty: "50", unit_price: 12000, line_total: 600000, item_id: null, received_qty: null }];
    let attempt = 0;
    const server = backend({ [`${NETWORK}/notes/${NOTE_ID}`]: noteBody({ lines, paid: 100000, terms: "part" }), [`${SHOP_BASE}/stock/items`]: { items: [], next_cursor: null } }, () => {
      attempt += 1;
      return attempt === 1 ? refusal(409, "CATALOG_NAME_TAKEN", "Bunday nomli tovar bor.", { existing_id: OWN_ITEM_ID }) : ok(noteBody({ status: "received" }));
    });
    renderScreen(<NoteScreen noteId={NOTE_ID} office={false} />, { fetch: server.fetch, role: "manager", features: ON });
    fireEvent.click(await screen.findByRole("button", { name: "Yukni qabul qilish" }));
    fireEvent.click(screen.getByRole("button", { name: CONFIRM }));
    expect(await screen.findByText("Bu qator uchun tovar tanlang yoki uni yangi tovar sifatida qo'shing.")).toBeTruthy();
    expect(server.writes()).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "Yangi tovar sifatida qo'shish" }));
    fireEvent.click(screen.getByRole("button", { name: CONFIRM }));
    expect(await screen.findByText("Narxni to'g'ri yozing.")).toBeTruthy();
    expect(server.writes()).toEqual([]);
    fireEvent.change(screen.getByLabelText("Sotish narxi, so'm"), { target: { value: "15000" } });
    fireEvent.click(screen.getByRole("button", { name: CONFIRM }));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]?.body).toEqual({ lines: [{ line_no: 1, new_price: 15000 }] });
    // The catalogue has an item of that name: the server says which, and it is offered instead.
    expect(await screen.findByText("Bunday nomli tovar bor.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Katalogdagi shu nomli tovardan foydalanish" }));
    expect(screen.getByText("O'z katalogimizdagi tovar: Shakar")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: CONFIRM }));
    await waitFor(() => expect(server.writes()).toHaveLength(2));
    expect(server.writes()[1]?.body).toEqual({ lines: [{ line_no: 1, item_id: OWN_ITEM_ID }] });
  });

  it("asks how the money handed over was paid only in a shop that keeps a cash book", async () => {
    const server = fakeServer((sent) => {
      if (sent.method !== "GET") {
        return ok(noteBody({ status: "received" }));
      }
      return sent.path === `${SHOP_BASE}/stock/settings` ? ok(stockSettingsBody({ cash_book: true })) : ok(noteBody({ paid: 100000, terms: "part" }));
    });
    renderScreen(<NoteScreen noteId={NOTE_ID} office />, { fetch: server.fetch, role: "manager", features: ON });
    fireEvent.click(await screen.findByRole("button", { name: "Yukni qabul qilish" }));
    fireEvent.change(await screen.findByLabelText("To'lov usuli"), { target: { value: "card" } });
    fireEvent.click(screen.getByRole("button", { name: CONFIRM }));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]?.body).toEqual({ lines: [{ line_no: 1, item_id: OWN_ITEM_ID }], method: "card" });
  });

  it("hides confirming from a member who may not receive goods or, when something was paid, pay a supplier", async () => {
    const reads = { [`${NETWORK}/notes/${NOTE_ID}`]: noteBody({ paid: 100000, terms: "part" }) };
    const open = (permissions: string[]) => renderScreen(<NoteScreen noteId={NOTE_ID} office />, { fetch: backend(reads).fetch, role: "seller", permissions, features: ON });
    open(["network.view", "network.confirm", "stock.receive", "suppliers.pay"]);
    expect(await screen.findByRole("button", { name: "Yukni qabul qilish" })).toBeTruthy();
    for (const held of [
      ["network.view", "network.confirm", "suppliers.pay"],
      ["network.view", "network.confirm", "stock.receive"],
      ["network.view", "stock.receive", "suppliers.pay"],
    ]) {
      cleanup();
      open(held);
      await screen.findByRole("button", { name: "Chop etish" });
      expect(screen.queryByRole("button", { name: "Yukni qabul qilish" }), held.join()).toBeNull();
    }
    // Rejecting moves neither goods nor money: the permission to confirm is enough for it.
    expect(screen.queryByRole("button", { name: "Yuk xatini rad etish" })).toBeNull();
    cleanup();
    open(["network.view", "network.confirm"]);
    expect(await screen.findByRole("button", { name: "Yuk xatini rad etish" })).toBeTruthy();
  });

  it("is rejected with a reason and what did arrive, and corrected by the supplier with a new note", async () => {
    const buyer = backend({ [`${NETWORK}/notes/${NOTE_ID}`]: noteBody() }, () => ok(noteBody({ status: "rejected", reject_reason: "Kam keldi" })));
    renderScreen(<NoteScreen noteId={NOTE_ID} office />, { fetch: buyer.fetch, role: "manager", features: ON });
    fireEvent.click(await screen.findByRole("button", { name: "Yuk xatini rad etish" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Rad etish sababi" }), { target: { value: "Kam keldi" } });
    fireEvent.change(screen.getByLabelText("Shakar: yozilgani 50 kg, amalda olingani"), { target: { value: "45" } });
    fireEvent.click(screen.getByRole("button", { name: "Yuk xatini rad etish" }));
    await waitFor(() => expect(buyer.writes()).toHaveLength(1));
    expect(buyer.writes()[0]).toMatchObject({ path: `${NETWORK}/notes/${NOTE_ID}/reject`, body: { reason: "Kam keldi", lines: [{ line_no: 1, received_qty: "45" }] } });
    cleanup();
    const NEW_NOTE = "d4d4d4d4-d4d4-4d4d-8d4d-d4d4d4d4d4d5";
    const rejected = noteBody({ role: "supplier", partner: { name: "Ziyo market" }, status: "rejected", reject_reason: "Kam keldi" });
    const supplier = backend({ [`${NETWORK}/notes/${NOTE_ID}`]: rejected }, () => ok(noteBody({ id: NEW_NOTE, number: 6, role: "supplier", supersedes_id: NOTE_ID }), 201));
    renderScreen(<NoteScreen noteId={NOTE_ID} office />, { fetch: supplier.fetch, role: "manager", features: ON });
    // The supplier neither confirms nor rejects its own note.
    fireEvent.click(await screen.findByRole("button", { name: "Yuk xatini tuzatish" }));
    expect(screen.queryByRole("button", { name: "Yukni qabul qilish" })).toBeNull();
    fireEvent.change(screen.getByRole("textbox", { name: "Tuzatish sababi" }), { target: { value: "Miqdor tuzatildi" } });
    fireEvent.change(screen.getByLabelText("Shakar: miqdor, kg"), { target: { value: "45" } });
    fireEvent.click(screen.getByRole("button", { name: "Yangi yuk xatini yozish" }));
    await waitFor(() => expect(supplier.writes()).toHaveLength(1));
    expect(supplier.writes()[0]).toMatchObject({ path: `${NETWORK}/notes/${NOTE_ID}/correct` });
    expect(supplier.writes()[0]?.body).toEqual({ reason: "Miqdor tuzatildi", lines: [{ line_no: 1, qty: "45", unit_price: 12000 }] });
    expect((await screen.findByRole("link", { name: "Yuk xati № 6" })).getAttribute("href")).toBe(`#/network/notes/${NEW_NOTE}`);
  });

  it("is printed from the page, with its controls left off the paper", async () => {
    const print = vi.fn();
    Object.defineProperty(window, "print", { configurable: true, value: print });
    const { container } = renderScreen(<NoteScreen noteId={NOTE_ID} office />, { fetch: backend({ [`${NETWORK}/notes/${NOTE_ID}`]: noteBody() }).fetch, role: "manager", features: ON });
    fireEvent.click(await screen.findByRole("button", { name: "Chop etish" }));
    expect(print).toHaveBeenCalledTimes(1);
    const note = container.querySelector(".net-note");
    expect(note?.textContent).toContain("Yuk xati № 5");
    expect(plain(note?.textContent)).toContain("Nasiyaga qolgan600 000 so'm");
    // Every button of the page is in a part that is not printed; the note itself has none.
    expect(note?.querySelector("button")).toBeNull();
    for (const button of container.querySelectorAll("button")) {
      expect(button.closest(".no-print"), button.textContent ?? "").not.toBeNull();
    }
    expect(container.querySelector("nav")?.classList.contains("no-print")).toBe(true);
  });

  it("opens the list of the ones that await an answer on that status", async () => {
    const server = backend({ [`${NETWORK}/notes`]: { notes: [noteBody({ lines: undefined, events: undefined })], next_cursor: null } });
    renderScreen(<NotesScreen waiting />, { fetch: server.fetch, role: "manager", features: ON });
    const [row] = await waitFor(() => rows("Yuk xatlari"));
    expect(row?.textContent).toContain("Tasdiq kutilmoqda");
    expect(server.sent.find((sent) => sent.path === `${NETWORK}/notes`)?.query).toEqual({ status: "issued" });
  });
});

describe("the payments", () => {
  const own = paymentBody({ id: "e5e5e5e5-e5e5-4e5e-8e5e-e5e5e5e5e5e6", role: "buyer", partner: { name: "Baraka ulgurji" }, recorded_by: "own", in_own_books: true });
  const declined = paymentBody({
    id: "e5e5e5e5-e5e5-4e5e-8e5e-e5e5e5e5e5e7",
    role: "buyer",
    recorded_by: "own",
    status: "declined",
    decline_reason: "Pul kelmadi",
    in_own_books: true,
  });
  const reads = { [NETWORK]: overviewBody(), [`${NETWORK}/payments`]: { payments: [paymentBody(), own, declined], next_cursor: null } };

  it("say whose step each one awaits, and what a declined one left in this shop's books", async () => {
    renderScreen(<PaymentsScreen />, { fetch: backend(reads).fetch, role: "manager", features: ON });
    const [theirs, ours, refused] = await waitFor(() => rows("To'lovlar"));
    expect(theirs?.textContent).toContain("Bizga to'landi");
    expect(theirs?.textContent).toContain("Kim yozdihamkor");
    expect(theirs?.textContent).toContain("Bizning daftardaYozilmagan");
    expect(theirs?.textContent).toContain("Sizning javobingiz kutilmoqda");
    expect(within(theirs as HTMLElement).getByRole("button", { name: "To'lovni tasdiqlash" })).toBeTruthy();
    expect(ours?.textContent).toContain("Biz to'ladik");
    expect(ours?.textContent).toContain("Hamkorning tasdig'i kutilmoqda");
    // What this shop recorded it may take back; it cannot confirm it to itself.
    expect(within(ours as HTMLElement).getByRole("button", { name: "Qaytarib olish" })).toBeTruthy();
    expect(within(ours as HTMLElement).queryByRole("button", { name: "To'lovni tasdiqlash" })).toBeNull();
    expect(refused?.textContent).toContain(
      "Hamkor rad etdi, lekin yozuv daftaringizda turibdi: ta'minotchi hisobidagi o'z yozuvingizni bekor qiling. Sabab: Pul kelmadi",
    );
    expect(within(refused as HTMLElement).queryByRole("button")).toBeNull();
  });

  it("record a payment to a supplier, said to take effect on the other side only when it confirms", async () => {
    const server = backend(reads, () => ok(own, 201));
    renderScreen(<PaymentsScreen />, { fetch: server.fetch, role: "manager", features: ON });
    fireEvent.click(await screen.findByRole("button", { name: "To'lov yozish" }));
    expect(screen.getByText("“Baraka ulgurji” ga to'laganingizni yozasiz. To'lov darhol ta'minotchi hisobingizga yoziladi.")).toBeTruthy();
    expect(screen.getByText("Hamkor tomonda to'lov faqat u tasdiqlaganidan keyin kuchga kiradi.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "To'lovni yozish" }));
    expect(await screen.findByText("Summani to'g'ri yozing.")).toBeTruthy();
    expect(server.writes()).toEqual([]);
    fireEvent.change(screen.getByLabelText("To'lov summasi"), { target: { value: "250000" } });
    fireEvent.click(screen.getByRole("button", { name: "To'lovni yozish" }));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]).toMatchObject({ method: "POST", path: `${NETWORK}/payments` });
    expect(server.writes()[0]?.body).toEqual({ link_id: NET_LINK_ID, amount: 250000, currency: "UZS" });
  });

  it("decline a payment with a reason, and offer nothing to one who may not move that money", async () => {
    const server = backend(reads, () => ok(paymentBody({ status: "declined" })));
    renderScreen(<PaymentsScreen />, { fetch: server.fetch, role: "manager", features: ON });
    fireEvent.click(await screen.findByRole("button", { name: "To'lovni rad etish" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Rad etish sababi" }), { target: { value: "Pul kelmadi" } });
    fireEvent.click(screen.getByRole("button", { name: "To'lovni rad etish" }));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]).toMatchObject({ path: `${NETWORK}/payments/${PAYMENT_ID}/decline`, body: { reason: "Pul kelmadi" } });
    cleanup();
    // May confirm in the network, but not record a customer's payment nor pay a supplier.
    renderScreen(<PaymentsScreen />, { fetch: backend(reads).fetch, role: "seller", permissions: ["network.view", "network.confirm"], features: ON });
    await waitFor(() => rows("To'lovlar"));
    expect(screen.queryByRole("button", { name: /To'lov/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Qaytarib olish" })).toBeNull();
  });
});

// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { fakeServer, ok, refusal, type Sent } from "../../testing/fakeServer";
import { exact, renderScreen } from "../../testing/renderScreen";
import type { Role } from "../navigation";
import { StockItemScreen } from "./StockItemScreen";
import { StockScreen } from "./StockScreen";
import { costedItemBody, EAN, ITEM_ID, OTHER_ITEM, STOCK, stockItemBody, stockSettingsBody, UNKNOWN_EAN } from "./testing";

afterEach(cleanup);

const SEARCH = "Shtrix-kodni skanerlang yoki nom yozing";
const NOT_FOUND = refusal(404, "NOT_FOUND", "Topilmadi.");

function backend(options: { costs?: boolean; patch?: (sent: Sent) => ReturnType<typeof ok> | ReturnType<typeof refusal> } = {}) {
  const item = options.costs ? costedItemBody : stockItemBody;
  return fakeServer((sent) => {
    if (sent.method === "PATCH" && sent.path === `${STOCK}/items/${OTHER_ITEM}`) {
      return options.patch?.(sent) ?? ok(item({ id: OTHER_ITEM, name: "Choy", barcodes: [UNKNOWN_EAN] }));
    }
    if (sent.method === "PUT" && sent.path === `${STOCK}/settings`) {
      return ok(stockSettingsBody({ refuse_negative: true }));
    }
    if (sent.method !== "GET") {
      return NOT_FOUND;
    }
    switch (sent.path) {
      case `${STOCK}/settings`:
        return ok(stockSettingsBody());
      case `${STOCK}/items`:
        return ok({
          items: [
            item(),
            item({ id: OTHER_ITEM, name: "Choy", unit: "dona", on_hand: "2", low_stock: "5", low: true, barcodes: [] }),
            item({ id: "44444444-4444-4444-8444-444444444443", name: "Un", on_hand: "-3.5", barcodes: [] }),
          ],
          next_cursor: null,
        });
      case `${STOCK}/lookup`:
        return sent.query["code"] === EAN ? ok(item()) : NOT_FOUND;
      case `${STOCK}/items/${ITEM_ID}`:
        return ok(item());
      case `${STOCK}/items/${ITEM_ID}/movements`:
        return ok({
          movements: [
            {
              id: "aaaaaaaa-0000-4000-8000-000000000001",
              seq: 2,
              kind: "sale",
              qty: "-0.5",
              on_hand_after: "7.5",
              reason: null,
              document: null,
              ledger_entry_id: null,
              reverses_id: null,
              reversed: false,
              author_id: "33333333-3333-4333-8333-333333333333",
              created_at: "2026-10-06T06:00:00+00:00",
              sale_total: 7500,
              ...(options.costs ? { cost: { currency: "UZS", unit_cost: 12000, total: 6000, value_after: 90000 } } : {}),
            },
            {
              id: "aaaaaaaa-0000-4000-8000-000000000002",
              seq: 1,
              kind: "receipt",
              qty: "8",
              on_hand_after: "8",
              reason: null,
              document: { id: "88888888-8888-4888-8888-888888888881", kind: "receipt", number: 7 },
              ledger_entry_id: null,
              reverses_id: null,
              reversed: false,
              author_id: "33333333-3333-4333-8333-333333333333",
              created_at: "2026-10-05T06:00:00+00:00",
              ...(options.costs ? { cost: { currency: "UZS", unit_cost: 12000, total: 96000, value_after: 96000 } } : {}),
            },
          ],
          next_cursor: null,
        });
      default:
        return NOT_FOUND;
    }
  });
}

function open(role: Role, server = backend({ costs: role !== "seller" })) {
  renderScreen(<StockScreen host={{}} />, { fetch: server.fetch, role });
  return server;
}

/** Types a code into the search box slowly and presses Enter, as a person does. */
function enter(text: string) {
  const input = screen.getByLabelText(SEARCH);
  fireEvent.change(input, { target: { value: text } });
  fireEvent.submit(input.closest("form") as HTMLFormElement);
}

const rows = () => within(screen.getByRole("list", { name: "Ombordagi tovarlar" })).getAllByRole("listitem");

describe("the stock list", () => {
  it("shows what is on hand, with a low and a negative stock said in words", async () => {
    open("seller");
    await screen.findByText("Shakar");
    expect(rows().map((row) => row.textContent)).toEqual([
      "ShakarQoldiq7,5 kg Sotish narxi15 000 so'm",
      "ChoyQoldiq2 dona Kam qoldiSotish narxi15 000 so'm",
      "UnQoldiq-3,5 kg MinusdaSotish narxi15 000 so'm",
    ]);
  });

  it("shows a seller no cost, no value and no margin: the server sent none and no column exists", async () => {
    open("seller");
    await screen.findByText("Shakar");
    for (const word of ["O'rtacha tannarx", "Tannarx bo'yicha qiymati", "Bir birlikdan foyda"]) {
      expect(screen.queryByText(word)).toBeNull();
    }
    expect(screen.queryByRole("link", { name: "Ombor hisoboti" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Tez kirim" })).toBeNull();
  });

  it("shows a manager the cost the server sent, and offers the receipt and the report", async () => {
    open("manager");
    await screen.findByText("Shakar");
    expect(rows()[0]?.textContent).toBe(
      "ShakarQoldiq7,5 kg Sotish narxi15 000 so'mO'rtacha tannarx12 000 so'mTannarx bo'yicha qiymati90 000 so'mBir birlikdan foyda3 000 so'm",
    );
    expect(screen.getByRole("link", { name: "Tez kirim" }).getAttribute("href")).toBe("#/stock/receipt");
    expect(screen.getByRole("link", { name: "Ombor hisoboti" }).getAttribute("href")).toBe("#/stock/report");
  });

  it("asks for the counted items first, then for what runs low, then by name", async () => {
    const server = open("seller");
    await screen.findByText("Shakar");
    const asked = () => server.sent.filter((sent) => sent.path === `${STOCK}/items`).map((sent) => sent.query);
    expect(asked()).toEqual([{ filter: "tracked" }]);
    fireEvent.click(screen.getByRole("button", { name: "Kam qolganlar" }));
    await waitFor(() => expect(asked().at(-1)).toEqual({ filter: "low" }));
    enter("shak");
    await waitFor(() => expect(asked().at(-1)).toEqual({ q: "shak", filter: "low" }));
    // A name is never sent to the barcode lookup.
    expect(server.sent.filter((sent) => sent.path === `${STOCK}/lookup`)).toEqual([]);
  });
});

describe("a barcode in the search", () => {
  it("is looked up and shows its item with what is on hand", async () => {
    const server = open("seller");
    await screen.findByText("Shakar");
    enter(EAN);
    const link = await waitFor(() => within(screen.getByRole("status")).getByRole("link", { name: "Shakar" }));
    expect(link.getAttribute("href")).toBe(`#/stock/items/${ITEM_ID}`);
    expect(screen.getByRole("status").textContent).toContain("7,5 kg");
    expect(server.sent.find((sent) => sent.path === `${STOCK}/lookup`)?.query).toEqual({ code: EAN });
  });

  it("that no item has says so with the code, and offers a seller nothing more", async () => {
    open("seller");
    await screen.findByText("Shakar");
    enter(UNKNOWN_EAN);
    const notice = await screen.findByRole("alert");
    expect(notice.textContent).toBe("73513537 shtrix-kodli tovar topilmadi.");
    expect(within(notice).queryByRole("button")).toBeNull();
    expect(screen.queryByRole("button", { name: "Bu kodni tovarga biriktirish" })).toBeNull();
  });

  it("that no item has may be given to an item by one who may change goods", async () => {
    const server = open("manager");
    await screen.findByText("Shakar");
    enter(UNKNOWN_EAN);
    fireEvent.click(await screen.findByRole("button", { name: "Bu kodni tovarga biriktirish" }));
    expect(screen.getByText("73513537 kodi qaysi tovarniki? Ro'yxatdan tovarni tanlang.")).toBeTruthy();
    // Nothing is written until an item is chosen.
    expect(server.writes()).toEqual([]);
    fireEvent.click(within(rows()[1] as HTMLElement).getByRole("button", { name: "Shu tovarga biriktirish" }));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    const write = server.writes()[0];
    expect(write?.method).toBe("PATCH");
    expect(write?.path).toBe(`${STOCK}/items/${OTHER_ITEM}`);
    expect(write?.body).toEqual({ barcodes: [UNKNOWN_EAN] });
    expect(write?.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
    await waitFor(() => expect(screen.queryByText("73513537 kodi qaysi tovarniki? Ro'yxatdan tovarni tanlang.")).toBeNull());
  });

  it("keeps the codes the item already has when one more is attached", async () => {
    const server = backend({ costs: true });
    renderScreen(<StockScreen host={{}} />, { fetch: server.fetch, role: "manager" });
    await screen.findByText("Shakar");
    enter(UNKNOWN_EAN);
    fireEvent.click(await screen.findByRole("button", { name: "Bu kodni tovarga biriktirish" }));
    fireEvent.click(within(rows()[0] as HTMLElement).getByRole("button", { name: "Shu tovarga biriktirish" }));
    await waitFor(() => expect(server.sent.some((sent) => sent.method === "PATCH")).toBe(true));
    expect(server.sent.find((sent) => sent.method === "PATCH")?.body).toEqual({ barcodes: [EAN, UNKNOWN_EAN] });
  });

  it("shows the server's refusal when another item already has the code", async () => {
    const server = backend({ costs: true, patch: () => refusal(409, "BARCODE_TAKEN", "Bu shtrix-kod boshqa tovarga biriktirilgan.") });
    renderScreen(<StockScreen host={{}} />, { fetch: server.fetch, role: "manager" });
    await screen.findByText("Shakar");
    enter(UNKNOWN_EAN);
    fireEvent.click(await screen.findByRole("button", { name: "Bu kodni tovarga biriktirish" }));
    fireEvent.click(within(rows()[1] as HTMLElement).getByRole("button", { name: "Shu tovarga biriktirish" }));
    expect(await screen.findByText("Bu shtrix-kod boshqa tovarga biriktirilgan.")).toBeTruthy();
  });
});

describe("refusing sales beyond stock", () => {
  it("is a setting for those who may change the shop's rules, saved with one request", async () => {
    const server = open("manager");
    const box = (await screen.findByLabelText("Ombordagidan ko'p sotishni rad etish")) as HTMLInputElement;
    expect(box.checked).toBe(false);
    fireEvent.click(box);
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]?.method).toBe("PUT");
    expect(server.writes()[0]?.body).toEqual({ refuse_negative: true });
  });

  it("is not shown to a seller, and its settings are not asked for them", async () => {
    const server = open("seller");
    await screen.findByText("Shakar");
    expect(screen.queryByText("Ombor sozlamalari")).toBeNull();
    expect(server.sent.filter((sent) => sent.path === `${STOCK}/settings`)).toEqual([]);
  });
});

describe("one item", () => {
  it("shows a seller the quantity, the barcodes and the movements, and nothing of cost", async () => {
    const server = backend();
    renderScreen(<StockItemScreen itemId={ITEM_ID} host={{}} />, { fetch: server.fetch, role: "seller" });
    expect(await screen.findByRole("heading", { name: "Shakar" })).toBeTruthy();
    expect(screen.getByText(exact(EAN))).toBeTruthy();
    const moves = within(await screen.findByRole("list", { name: "Harakatlar" })).getAllByRole("listitem");
    expect(moves.map((row) => row.textContent)).toEqual([
      "2026-yil 6-oktabr, 11:00Nima bo'ldiSotuvMiqdor-0,5 kgKeyingi qoldiq7,5 kg",
      "2026-yil 5-oktabr, 11:00Nima bo'ldiKirim · Kirim № 7Miqdor8 kgKeyingi qoldiq8 kg",
    ]);
    expect(screen.queryByText("O'rtacha tannarx")).toBeNull();
    expect(screen.queryByText("Tannarx")).toBeNull();
    // Changing how an item is counted changes the catalog: not a seller's.
    expect(screen.queryByRole("button", { name: "Ombor sozlamalarini o'zgartirish" })).toBeNull();
  });

  it("shows cost where the server sent it", async () => {
    const server = backend({ costs: true });
    renderScreen(<StockItemScreen itemId={ITEM_ID} host={{}} />, { fetch: server.fetch, role: "manager" });
    await screen.findByRole("heading", { name: "Shakar" });
    expect(screen.getByText("O'rtacha tannarx").nextElementSibling?.textContent).toBe("12 000 so'm");
    const moves = within(await screen.findByRole("list", { name: "Harakatlar" })).getAllByRole("listitem");
    expect(moves[1]?.textContent).toContain("Tannarx96 000 so'm");
  });

  it("lets a manager set the low level and the barcodes, and sends only what the form holds", async () => {
    const server = fakeServer((sent) => {
      if (sent.method === "PATCH") {
        return ok(costedItemBody({ low_stock: "2.5" }));
      }
      if (sent.path === `${STOCK}/settings`) {
        return ok(stockSettingsBody());
      }
      if (sent.path === `${STOCK}/items/${ITEM_ID}`) {
        return ok(costedItemBody());
      }
      return ok({ movements: [], next_cursor: null });
    });
    renderScreen(<StockItemScreen itemId={ITEM_ID} host={{}} />, { fetch: server.fetch, role: "manager" });
    fireEvent.click(await screen.findByRole("button", { name: "Ombor sozlamalarini o'zgartirish" }));
    fireEvent.change(screen.getByLabelText("Kam qoldi chegarasi"), { target: { value: "abc" } });
    fireEvent.click(screen.getByRole("button", { name: "Saqlash" }));
    expect(await screen.findByText("Miqdorni raqam bilan yozing, masalan 12 yoki 1,5. Ko'pi bilan uchta kasr xona.")).toBeTruthy();
    expect(server.writes()).toEqual([]);

    fireEvent.change(screen.getByLabelText("Kam qoldi chegarasi"), { target: { value: "2,5" } });
    const add = screen.getByLabelText("Shtrix-kod qo'shish: skanerlang yoki yozing");
    fireEvent.change(add, { target: { value: UNKNOWN_EAN } });
    fireEvent.submit(add.closest("form") as HTMLFormElement);
    fireEvent.click(screen.getByRole("button", { name: `${EAN} kodini olib tashlash` }));
    fireEvent.click(screen.getByRole("button", { name: "Saqlash" }));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]?.body).toEqual({ tracked: true, low_stock: "2.5", barcodes: [UNKNOWN_EAN] });
    expect(await screen.findByText("Saqlandi.")).toBeTruthy();
  });
});

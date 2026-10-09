// @vitest-environment jsdom
import { cleanup, fireEvent, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { CUSTOMER_ID, customerBody, detailBody, entryBody, fakeServer, FIVE_ITEMS, lineBody, ok, refusal, type Reply, SHOP_BASE } from "../../testing/fakeServer";
import { renderScreen } from "../../testing/renderScreen";
import type { Language } from "../../i18n/types";
import { AddGoodsScreen } from "./AddGoodsScreen";
import { EntryScreen } from "./EntryScreen";

/**
 * What the stock tells a seller on the sale screens that existed before it (the expansion's module I):
 * a warning after a sale that took an item below zero or was in another unit, and the refusal of a shop
 * that does not sell beyond its stock. The server sends neither while the stock is switched off, and
 * then the screens are as they always were.
 */

afterEach(cleanup);

const ENTRY_ID = "77777777-7777-4777-8777-777777777777";
const NON = "44444444-4444-4444-8444-444444444441";

const recorded = (extra: Record<string, unknown> = {}) =>
  ok(
    {
      entry: { id: ENTRY_ID, kind: "credit", amount: 45000, promised_date: "2026-11-05", lines: [lineBody()] },
      customer: customerBody({ balance: 165000 }),
      ...extra,
    },
    201,
  );

function shop(onWrite: () => Reply) {
  return fakeServer((sent) => {
    if (sent.method !== "GET") {
      return onWrite();
    }
    if (sent.path === `${SHOP_BASE}/catalog`) {
      return ok({ items: FIVE_ITEMS, next_cursor: null });
    }
    return ok(detailBody());
  });
}

async function sell(server: ReturnType<typeof fakeServer>, language: Language = "uz") {
  renderScreen(<EntryScreen customerId={CUSTOMER_ID} kind="credit" />, { fetch: server.fetch, language });
  const amount = await screen.findByLabelText(language === "uz" ? "Summa, so'm" : "Сумма, сум");
  fireEvent.change(amount, { target: { value: "45000" } });
  fireEvent.click(screen.getByRole("button", { name: language === "uz" ? "Nasiyani yozish" : "Записать продажу в долг" }));
}

const WARNINGS = [
  { kind: "negative", item: NON, name: "Non", on_hand: "-2.5" },
  { kind: "unit", item: "44444444-4444-4444-8444-444444444444", name: "Guruch", unit: "kg" },
];

describe("a sale that the stock has something to say about", () => {
  it("is saved, and warns that an item went below zero or was sold in another unit", async () => {
    const server = shop(() => recorded({ stock_warnings: WARNINGS }));
    await sell(server);
    const done = await screen.findByRole("status");
    expect(within(done).getByText("«Non» omborda minusga tushdi: qoldiq -2,5. Kirimni yozib qo'ying.")).toBeTruthy();
    expect(
      within(done).getByText(
        "«Guruch» omborda «kg» birligida hisoblanadi. Bu sotuv boshqa birlikda yozildi, shuning uchun ombordan chiqarilmadi.",
      ),
    ).toBeTruthy();
    // A warning, not a block: the sale is recorded and the screen says so as always.
    expect(done.textContent).toContain("Ali Valiyev");
    expect(server.writes()).toHaveLength(1);
  });

  it("says the same in Russian", async () => {
    const server = shop(() => recorded({ stock_warnings: WARNINGS }));
    await sell(server, "ru");
    expect(await screen.findByText("«Non» ушёл в минус на складе: остаток -2,5. Запишите приход.")).toBeTruthy();
  });

  it("shows nothing of the stock when the answer carries no warning, as with the stock switched off", async () => {
    const server = shop(() => recorded());
    await sell(server);
    const done = await screen.findByRole("status");
    expect(done.textContent).not.toMatch(/ombor/i);
    expect(done.querySelectorAll(".row__warning")).toHaveLength(0);
  });

  it("leaves out a warning of a kind it does not know rather than showing something half-read", async () => {
    const server = shop(() => recorded({ stock_warnings: [{ kind: "expired", item: NON, name: "Non" }] }));
    await sell(server);
    const done = await screen.findByRole("status");
    expect(done.querySelectorAll(".row__warning")).toHaveLength(0);
  });
});

describe("a sale beyond the stock, in a shop that refuses it", () => {
  const refused = () =>
    refusal(409, "STOCK_INSUFFICIENT", "Omborda bu tovar yetarli emas. Avval kirim yozing yoki miqdorni kamaytiring.", {
      item: NON,
      name: "Non",
      on_hand: "1.5",
      wanted: "3",
    });

  it("is not saved: the server's sentence, and which item, how much there is and how much was wanted", async () => {
    const server = shop(refused);
    await sell(server);
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe(
      "Omborda bu tovar yetarli emas. Avval kirim yozing yoki miqdorni kamaytiring.«Non»: omborda 1,5, sotuvga 3 kerak.",
    );
    // The form is still there to be corrected.
    expect(screen.getByRole("button", { name: "Nasiyani yozish" })).toBeTruthy();
  });

  it("adds no figures to any other refusal", async () => {
    const server = shop(() => refusal(409, "LIMIT_REACHED", "Mijozning nasiya limiti tugagan.", { limit: "100000", balance: "120000" }));
    await sell(server);
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).not.toContain("omborda");
  });

  it("adds no figures when the refusal carries none that read as quantities", async () => {
    const server = shop(() => refusal(409, "STOCK_INSUFFICIENT", "Omborda bu tovar yetarli emas.", { name: "Non", on_hand: "lots", wanted: "3" }));
    await sell(server);
    expect((await screen.findByRole("alert")).textContent).toBe("Omborda bu tovar yetarli emas.");
  });
});

describe("goods added to a sale afterwards", () => {
  const amountOnly = detailBody({ entries: [entryBody({ id: ENTRY_ID, amount: 12000, lines: [], created_at: "2026-10-06T06:00:00+00:00" })] });

  it("warn in the same way", async () => {
    const server = fakeServer((sent) => {
      if (sent.method !== "GET") {
        return ok({
          entry: { id: ENTRY_ID, amount: 12000, lines: [lineBody({ qty: "3", unit_price: 4000, line_total: 12000 })] },
          stock_warnings: [WARNINGS[0]],
        });
      }
      if (sent.path === `${SHOP_BASE}/catalog`) {
        return ok({ items: FIVE_ITEMS, next_cursor: null });
      }
      return ok(amountOnly);
    });
    renderScreen(<AddGoodsScreen customerId={CUSTOMER_ID} entryId={ENTRY_ID} />, { fetch: server.fetch, role: "manager" });
    fireEvent.click(within(await screen.findByRole("list", { name: "Katalogdagi tovarlar" })).getByRole("button", { name: /^Non/ }));
    fireEvent.change(screen.getByLabelText("Non: miqdor"), { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: "Tovarlarni saqlash" }));
    expect(await screen.findByText("«Non» omborda minusga tushdi: qoldiq -2,5. Kirimni yozib qo'ying.")).toBeTruthy();
  });
});

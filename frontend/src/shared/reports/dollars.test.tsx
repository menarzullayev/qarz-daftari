// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { CUSTOMER_ID, fakeServer, ok, overdueReportBody, periodReportBody, SHOP_BASE } from "../../testing/fakeServer";
import { renderScreen } from "../../testing/renderScreen";
import ReportsScreen from "./ReportsScreen";

/**
 * The reports of a shop that works in dollars have the same money sections twice: from the so'm book
 * and, under `usd`, from the dollar book in cents. The screen shows one book at a time, chosen by a
 * so'm | $ switch, and no figure of it is a sum of the two.
 */

afterEach(cleanup);

const PERIOD_USD = {
  outstanding: { start: 10000, end: 12500 },
  credit: { amount: 5000, count: 2, customers: 1 },
  payments: { amount: 2500, count: 1, customers: 1 },
  opening: { amount: 0, count: 0 },
  net_change: 2500,
  reversals: { amount: 0, count: 0 },
  on_time: { due_amount: 2000, on_time_amount: 1500, percent: 75 },
  days: [
    { date: "2026-10-01", credit: 5000, payments: 0 },
    { date: "2026-10-03", credit: 0, payments: 2500 },
  ],
  top_debtors: [{ customer_id: CUSTOMER_ID, display_name: "Ali Valiyev", balance: 12500 }],
  staff: [],
};
const OVERDUE_USD = {
  total: { amount: 500, customers: 1 },
  bands: [
    { band: "1_7", from_days: 1, to_days: 7, amount: 500, customers: 1 },
    { band: "8_30", from_days: 8, to_days: 30, amount: 0, customers: 0 },
  ],
};

const shop = (dollars: boolean) =>
  fakeServer((sent) =>
    sent.path === `${SHOP_BASE}/reports/period`
      ? ok(periodReportBody(dollars ? { usd: PERIOD_USD } : {}))
      : ok(overdueReportBody(dollars ? { usd: OVERDUE_USD } : {})),
  );
const plain = (text: string | null | undefined) => (text ?? "").replace(/\u00a0/g, " ");
const page = () => plain(document.body.textContent);
const figure = (label: string) => plain(screen.getByText(label).closest(".figure")?.textContent);
const currencyChoice = () => screen.queryByRole("group", { name: "Valyuta" });
const choose = (name: "so'm" | "$") => fireEvent.click(within(currencyChoice() as HTMLElement).getByRole("button", { name }));
const open = async (server: ReturnType<typeof fakeServer>) => {
  renderScreen(<ReportsScreen />, { fetch: server.fetch, role: "owner" });
  await screen.findByText("Davr boshidagi qarz");
  await screen.findByText(/holatiga\.$/);
};

describe("the reports of a shop that works in dollars", () => {
  it("starts with the so'm book and offers the dollar one", async () => {
    const server = shop(true);
    await open(server);
    expect(within(currencyChoice() as HTMLElement).getAllByRole("button").map((button) => [button.textContent, button.getAttribute("aria-pressed")])).toEqual([
      ["so'm", "true"],
      ["$", "false"],
    ]);
    expect(figure("Davr boshidagi qarz")).toBe("Davr boshidagi qarz500 000 so'm");
    expect(figure("Davr oxiridagi qarz")).toBe("Davr oxiridagi qarz600 000 so'm");
    // The so'm book is shown whole and alone: not one dollar figure beside it (the switch names "$").
    expect(page()).not.toMatch(/\d \$/);
    expect(page()).not.toContain("125.00");
  });

  it("shows every money section from the dollar book when dollars are chosen, and asks the server nothing more", async () => {
    const server = shop(true);
    await open(server);
    const asked = server.sent.length;
    choose("$");
    expect(figure("Davr boshidagi qarz")).toBe("Davr boshidagi qarz100.00 $");
    expect(figure("Davr oxiridagi qarz")).toBe("Davr oxiridagi qarz125.00 $");
    expect(figure("Davrdagi o'zgarish")).toBe("Davrdagi o'zgarish+25.00 $");
    // The period's check holds in dollars by itself: 100.00 + 50.00 + 0.00 − 25.00 = 125.00.
    expect(plain(document.querySelector(".equation")?.textContent)).toContain("100.00 $");
    expect(plain(document.querySelector(".equation")?.textContent)).toContain("125.00 $");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(page()).toContain("Muddati shu davrda kelgan 20.00 $ dan 15.00 $ o'z vaqtida to'langan.");
    const debtors = within(screen.getByRole("region", { name: "Eng katta qarzdorlar (davr oxirida)" }));
    expect(plain(debtors.getByRole("listitem").textContent)).toBe("Ali Valiyev125.00 $");
    const late = screen.getByRole("region", { name: "Muddati o'tgan qarz, kechikish bo'yicha" });
    expect(plain(late.textContent)).toContain("5.00 $");
    expect(plain(late.textContent)).not.toContain("180 000");
    // No figure of the so'm book is left on the screen (the switch still names it), and none is a sum.
    expect(page()).not.toMatch(/\d so'm/);
    expect(page()).not.toContain("600 000");
    // The customers and the disputes of the period are counted once, whatever the currency.
    expect(figure("Yangi mijozlar")).toBe("Yangi mijozlar2 ta mijoz");
    expect(server.sent).toHaveLength(asked);

    choose("so'm");
    expect(figure("Davr oxiridagi qarz")).toBe("Davr oxiridagi qarz600 000 so'm");
  });

  it("says that the dollar equation does not hold when the server's dollar figures do not add up", async () => {
    const server = fakeServer((sent) =>
      sent.path === `${SHOP_BASE}/reports/period`
        ? ok(periodReportBody({ usd: { ...PERIOD_USD, outstanding: { start: 10000, end: 99999 } } }))
        : ok(overdueReportBody({ usd: OVERDUE_USD })),
    );
    await open(server);
    // The so'm book adds up, so nothing is wrong while it is shown.
    expect(screen.queryByRole("alert")).toBeNull();
    choose("$");
    await waitFor(() => expect(plain(screen.getByRole("alert").textContent)).toContain("125.00 $"));
    expect(plain(screen.getByRole("alert").textContent)).toContain("999.99 $");
  });
});

describe("the reports of a shop without dollars", () => {
  it("offers no choice of currency and shows nothing about dollars", async () => {
    await open(shop(false));
    expect(currencyChoice()).toBeNull();
    expect(figure("Davr oxiridagi qarz")).toBe("Davr oxiridagi qarz600 000 so'm");
    expect(page()).not.toContain("$");
    expect(page().toLowerCase()).not.toContain("dollar");
    expect(page()).not.toContain("USD");
  });
});

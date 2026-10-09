// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { I18nProvider } from "../../i18n/I18nProvider";
import {
  accountBody,
  accountEntryBody,
  fakeServer,
  LINK_ID,
  ME_BASE,
  NOON,
  noticeBody,
  ok,
  type Reply,
  type Sent,
} from "../../testing/fakeServer";
import { createApi } from "../api";
import { AccountScreen } from "./AccountScreen";
import CustomerArea from "./CustomerArea";

/**
 * US dollars beside so'm on the customer's own pages: the list of accounts, one account, and "I have
 * paid". A shop without dollars answers without `usd` and without `currency`, and its pages must show
 * nothing about dollars at all.
 */

afterEach(cleanup);
beforeEach(() => {
  window.location.hash = "#/my";
});

const plain = (text: string | null | undefined) => (text ?? "").replace(/\u00a0/g, " ");
const page = () => plain(document.body.textContent);
const type = (input: HTMLElement, value: string) => fireEvent.change(input, { target: { value } });
const currencyChoice = () => screen.queryByRole("group", { name: "Valyuta" });
const choose = (name: "so'm" | "$") => fireEvent.click(within(currencyChoice() as HTMLElement).getByRole("button", { name }));

function expectNoDollars() {
  expect(page()).not.toContain("$");
  expect(page().toLowerCase()).not.toContain("dollar");
  expect(page()).not.toContain("USD");
  expect(currencyChoice()).toBeNull();
  expect(screen.queryByRole("group", { name: "Dollarda" })).toBeNull();
  expect(document.querySelector(".money")).toBeNull();
}

const HISTORY_USD = { on_time_percent: 75, on_time_amount: 1500, due_amount: 2000, longest_delay_days: 3 };
const USD = { balance: 1250, overdue: { amount: 500, due_today: 250 }, payment_history: HISTORY_USD };
const USD_ENTRY = accountEntryBody({ id: "22222222-2222-4222-8222-22222222aaaa", amount: 1250, currency: "USD" });

/** The customer's account; `onWrite` answers anything that is not a read. */
function backend(account: Record<string, unknown>, onWrite: (sent: Sent) => Reply = () => ok(noticeBody(), 201)) {
  return fakeServer((sent) => (sent.method === "GET" ? ok(account) : onWrite(sent)));
}

async function open(server: ReturnType<typeof fakeServer>) {
  const api = createApi({ fetch: server.fetch, auth: { kind: "bearer", token: "customer-session" } }).account(LINK_ID);
  render(
    <I18nProvider initialLanguage="uz">
      <AccountScreen api={api} back={<a href="#/my">back</a>} now={() => NOON} />
    </I18nProvider>,
  );
  await screen.findByRole("heading", { level: 2, name: "Baraka savdo" });
}

const IN_DOLLARS = accountBody({ usd: USD, entries: [USD_ENTRY, accountEntryBody()], entries_total: 2 });

describe("a customer's own account", () => {
  it("shows what is owed in each currency, what of the dollars is late, and the dollar entry with its sign", async () => {
    await open(backend(IN_DOLLARS));
    expect(plain(document.querySelector(".balance")?.textContent)).toBe("Qarzingiz: 120 000 so'm 12.50 $");
    expect(page()).toContain("5.00 $ muddati o'tgan");
    expect(page()).toContain("Bugun to'lanishi kerak: 2.50 $");
    const entries = within(screen.getByRole("region", { name: "Yozuvlar" }));
    expect([...entries.getByRole("list").querySelectorAll(".row__amount")].map((amount) => plain(amount.textContent))).toEqual([
      "12.50 $",
      "45 000 so'm",
    ]);
    // Nothing is a sum of the two: 120 000 and 12.50 (1 250 cents) are never joined.
    expect(page()).not.toContain("121 250");
  });

  it("shows the customer their dollar payment history apart from the so'm one", async () => {
    await open(backend(IN_DOLLARS));
    const history = within(screen.getByRole("region", { name: "To'lov tarixingiz" })).getByRole("group", { name: "Dollarda" });
    expect(plain(history.textContent)).toContain("Muddati kelgan qarzlaringizning 75% qismini o'z vaqtida to'lagansiz.");
    expect(plain(history.textContent)).toContain("Muddati kelgan 20.00 $ dan 15.00 $ va'da qilingan kungacha to'langan.");
  });

  it("says that no dollar debt has fallen due yet, when that is so", async () => {
    await open(backend(accountBody({ usd: { ...USD, payment_history: null } })));
    const history = within(screen.getByRole("region", { name: "To'lov tarixingiz" })).getByRole("group", { name: "Dollarda" });
    expect(history.textContent).toContain("Hali to'lash muddati kelgan qarzingiz bo'lmagan.");
  });

  it("names both debts a removal waits for, each by itself", async () => {
    const server = backend(IN_DOLLARS, () => ok({ removed: false, waiting_for_balance: 120000, usd: { waiting_for_balance: 1250 } }));
    await open(server);
    fireEvent.click(screen.getByRole("button", { name: "Ma'lumotlarimni o'chirishni so'rash" }));
    fireEvent.click(screen.getByRole("button", { name: "Ha, o'chirilsin" }));
    await waitFor(() => expect(page()).toContain("Ma'lumotlaringiz 120 000 so'm · 12.50 $ qarz to'langanidan keyin o'chiriladi."));
  });

  it("shows an account in a shop without dollars as it always was", async () => {
    const server = backend(accountBody(), () => ok({ removed: false, waiting_for_balance: 120000 }));
    await open(server);
    expect(plain(document.querySelector(".balance")?.textContent)).toBe("Qarzingiz: 120 000 so'm");
    expectNoDollars();
    fireEvent.click(screen.getByRole("button", { name: "Ma'lumotlarimni o'chirishni so'rash" }));
    fireEvent.click(screen.getByRole("button", { name: "Ha, o'chirilsin" }));
    await waitFor(() => expect(page()).toContain("Ma'lumotlaringiz 120 000 so'm qarz to'langanidan keyin o'chiriladi."));
    expectNoDollars();
  });
});

describe("\"I have paid\" in dollars", () => {
  const ask = () => fireEvent.click(screen.getByRole("button", { name: "To'ladim" }));
  const send = () => fireEvent.click(screen.getByRole("button", { name: "Xabarni yuborish" }));
  const amount = () => document.getElementById("notice-amount") as HTMLInputElement;
  const label = () => document.querySelector('label[for="notice-amount"]')?.textContent;

  it("starts in so'm, offers dollars, and sends a notice of dollars in cents with its currency", async () => {
    const server = backend(IN_DOLLARS, () => ok(noticeBody({ amount: 500, currency: "USD" }), 201));
    await open(server);
    ask();
    expect(label()).toBe("To'langan summa, so'm");
    expect(plain(document.getElementById("notice-amount-hint")?.textContent)).toBe("Ko'pi bilan qarzingiz: 120 000 so'm.");

    choose("$");
    expect(label()).toBe("To'langan summa, $");
    expect(amount().getAttribute("inputmode")).toBe("decimal");
    expect(plain(document.getElementById("notice-amount-hint")?.textContent)).toBe("Ko'pi bilan qarzingiz: 12.50 $.");
    type(amount(), "5");
    send();
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]).toMatchObject({ method: "POST", path: `${ME_BASE}/payment-notices` });
    expect(server.writes()[0]?.body).toEqual({ amount: 500, currency: "USD" });
    await waitFor(() => expect(page()).toContain("Xabar do'konga yuborildi: 5.00 $."));
  });

  it("sends a notice of so'm from a shop with dollars exactly as before: the amount alone", async () => {
    const server = backend(IN_DOLLARS);
    await open(server);
    ask();
    type(amount(), "50000");
    send();
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]?.body).toEqual({ amount: 50000 });
  });

  it.each([
    ["12.51", "To'lov mijozning qarzidan katta bo'lishi mumkin emas."],
    ["5.005", "Dollarda nuqtadan keyin ko'pi bilan ikki raqam yoziladi, masalan 12.50."],
    ["5 ming", "Summani tushunib bo'lmadi. Dollarda yozing, masalan 12.50."],
    ["0", "Summa 0.01 $ dan 10 000.00 $ gacha bo'lishi kerak."],
    ["", "Summani kiriting."],
  ])("refuses the dollar amount %j and sends nothing", async (text, message) => {
    const server = backend(IN_DOLLARS);
    await open(server);
    ask();
    choose("$");
    type(amount(), text);
    send();
    expect(plain(document.getElementById("notice-amount-error")?.textContent)).toBe(message);
    expect(server.writes()).toHaveLength(0);
  });

  it("compares a notice with the debt of its own currency: 500 so'm is not 500 cents", async () => {
    // 50 000 so'm fits the so'm debt of 120 000; the same digits as dollars would be 50 000.00 $.
    const server = backend(IN_DOLLARS);
    await open(server);
    ask();
    type(amount(), "50000");
    choose("$");
    expect(amount().value).toBe("");
    type(amount(), "50000");
    send();
    expect(plain(document.getElementById("notice-amount-error")?.textContent)).toBe("Summa 0.01 $ dan 10 000.00 $ gacha bo'lishi kerak.");
    expect(server.writes()).toHaveLength(0);
  });

  it("is offered, and starts in dollars, to a customer who owes dollars only", async () => {
    const server = backend(accountBody({ balance: 0, usd: { ...USD, balance: 1250 } }), () => ok(noticeBody({ amount: 1250, currency: "USD" }), 201));
    await open(server);
    ask();
    expect(label()).toBe("To'langan summa, $");
    type(amount(), "12.50");
    send();
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]?.body).toEqual({ amount: 1250, currency: "USD" });
  });

  it("is not offered when nothing is owed in either currency", async () => {
    await open(backend(accountBody({ balance: 0, usd: { ...USD, balance: 0 } })));
    expect(screen.queryByRole("button", { name: "To'ladim" })).toBeNull();
  });

  it("shows a notice of dollars, and what the shop recorded for it, in dollars", async () => {
    const notices = [
      noticeBody({ amount: 500, currency: "USD" }),
      noticeBody({ id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaab", status: "accepted", amount: 500, recorded_amount: 450, currency: "USD" }),
      noticeBody({ id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaac" }),
    ];
    await open(backend(accountBody({ usd: USD, payment_notices: notices })));
    const section = within(screen.getByRole("region", { name: "To'lov xabarlarim" }));
    const rows = section.getAllByRole("listitem").map((row) => plain(row.textContent));
    expect(rows[0]).toContain("5.00 $");
    expect(rows[1]).toContain("Do'kon to'lovni boshqa summada yozdi: 4.50 $.");
    expect(rows[2]).toContain("50 000 so'm");
    expect(rows[2]).not.toContain("$");
  });

  it("offers a shop without dollars no currency, and sends the amount alone", async () => {
    const server = backend(accountBody());
    await open(server);
    ask();
    expect(label()).toBe("To'langan summa, so'm");
    expect(amount().getAttribute("inputmode")).toBe("numeric");
    expectNoDollars();
    // A decimal stays what it was for so'm: refused.
    type(amount(), "12.50");
    send();
    expect(document.getElementById("notice-amount-error")?.textContent).toBe("Summa butun so'mda bo'lishi kerak, tiyinsiz.");
    type(amount(), "50000");
    send();
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]?.body).toEqual({ amount: 50000 });
    await waitFor(() => expect(page()).toContain("Xabar do'konga yuborildi: 50 000 so'm."));
    expectNoDollars();
  });
});

describe("the customer's list of accounts", () => {
  const OTHER = "77777777-7777-4777-8777-777777777778";
  const account = (linkId: string, shop: string, balance: number, usd?: number) => ({
    link_id: linkId,
    shop_name: shop,
    display_name: "Ali Valiyev",
    balance,
    ...(usd === undefined ? {} : { usd: { balance: usd } }),
  });
  const show = (items: unknown[]) => {
    const server = fakeServer(() => ok({ items }));
    const api = createApi({ fetch: server.fetch, auth: { kind: "bearer", token: "customer-session" } });
    render(
      <I18nProvider initialLanguage="uz">
        <CustomerArea api={api} staffHome={false} now={() => NOON} />
      </I18nProvider>,
    );
  };
  const links = () => within(screen.getByRole("main")).getAllByRole("link").map((link) => plain(link.textContent));

  it("shows both debts where the shop works in dollars, and so'm alone where it does not", async () => {
    show([account(LINK_ID, "Baraka savdo", 120000, 1250), account(OTHER, "Oltin don", 30000)]);
    await screen.findByText("Oltin don");
    expect(links()).toEqual(["Baraka savdo120 000 so'm 12.50 $", "Oltin don30 000 so'm"]);
  });

  it("shows nothing about dollars when no shop has them", async () => {
    show([account(LINK_ID, "Baraka savdo", 120000), account(OTHER, "Oltin don", 30000)]);
    await screen.findByText("Oltin don");
    expect(links()).toEqual(["Baraka savdo120 000 so'm", "Oltin don30 000 so'm"]);
    expectNoDollars();
  });
});

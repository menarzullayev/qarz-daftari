// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import {
  creditSettingsBody,
  CUSTOMER_ID,
  customerBody,
  detailBody,
  entryBody,
  fakeServer,
  linkBody,
  NO_OVERDUE,
  ok,
  openDateRequestBody,
  openDisputeBody,
  openNoticeBody,
  refusal,
  remindersBody,
  type Reply,
  type Sent,
  settingsBody,
  SHOP_BASE,
} from "../../testing/fakeServer";
import { renderScreen } from "../../testing/renderScreen";
import type { EntryKind } from "../api";
import type { Role } from "../navigation";
import { CreditSettingsSection } from "./CreditSettingsSection";
import { CustomerScreen } from "./CustomerScreen";
import { CustomersScreen } from "./CustomersScreen";
import DateRequestsScreen from "./DateRequestsScreen";
import { DisputesScreen } from "./DisputesScreen";
import { EntryScreen } from "./EntryScreen";
import { OverviewScreen } from "./OverviewScreen";
import PaymentNoticesScreen from "./PaymentNoticesScreen";
import { RemindersScreen } from "./RemindersScreen";
import { ShopSettingsScreen } from "./ShopSettingsScreen";

/**
 * US dollars beside so'm on the staff's screens. Each screen is shown twice: for a shop that works in
 * dollars, whose answers carry `usd` figures and `currency: "USD"`, and for a shop whose answers carry
 * neither, where nothing about dollars may appear: no "$", no word, no choice of currency.
 */

afterEach(cleanup);

const plain = (text: string | null | undefined) => (text ?? "").replace(/\u00a0/g, " ");
const page = () => plain(document.body.textContent);
const type = (input: HTMLElement, value: string) => fireEvent.change(input, { target: { value } });
const currencyChoice = () => screen.queryByRole("group", { name: "Valyuta" });
const choose = (name: "so'm" | "$") => fireEvent.click(within(currencyChoice() as HTMLElement).getByRole("button", { name }));
const pressed = () =>
  within(currencyChoice() as HTMLElement)
    .getAllByRole("button")
    .map((button) => [button.textContent, button.getAttribute("aria-pressed")]);

/** Nothing on the page says that dollars exist. */
function expectNoDollars() {
  expect(page()).not.toContain("$");
  expect(page().toLowerCase()).not.toContain("dollar");
  expect(page()).not.toContain("USD");
  expect(currencyChoice()).toBeNull();
  expect(screen.queryByRole("group", { name: "Dollarda" })).toBeNull();
  expect(document.querySelector(".money")).toBeNull();
}

const OVERDUE_USD = { amount: 500, since: "2026-10-01", days: 5, due_today: 250 };
const HISTORY_USD = { on_time_percent: 75, on_time_amount: 1500, due_amount: 2000, longest_delay_days: 3 };
const CREDIT_USD = { default_credit_limit: 50000, limit_bounds: [100, 100000000] };

describe("the shop's dollar switch (shop settings)", () => {
  /** The shop's settings; a write answers with them after the change unless `onWrite` answers. */
  function shop(start: Record<string, unknown>, onWrite?: (sent: Sent) => Reply) {
    let current = start;
    return fakeServer((sent) => {
      if (sent.method === "GET") {
        return ok(current);
      }
      if (onWrite) {
        return onWrite(sent);
      }
      current = { ...current, ...(sent.body as Record<string, unknown>) };
      return ok(current);
    });
  }
  const open = async (server: ReturnType<typeof fakeServer>, role: Role = "owner", language: "uz" | "ru" = "uz") => {
    renderScreen(<ShopSettingsScreen />, { fetch: server.fetch, role, language });
    await screen.findByText(language === "uz" ? "Do'kon nomi" : "Название магазина");
  };
  const toggle = () => screen.getByRole<HTMLInputElement>("checkbox", { name: "Do'kon dollarda ham ishlaydi" });
  const save = () => fireEvent.click(screen.getByRole("button", { name: "Saqlash" }));

  it("offers the owner the switch while the platform offers dollars, and sends only it when turned on", async () => {
    const server = shop(settingsBody({ usd_on: false }));
    await open(server);
    expect(toggle().checked).toBe(false);
    expect(screen.getByText("So'mdagi va dollardagi qarzlar alohida yuritiladi va bir-biriga qo'shilmaydi.")).toBeTruthy();

    fireEvent.click(toggle());
    save();
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("Sozlamalar saqlandi."));
    expect(server.writes()).toHaveLength(1);
    expect(server.writes()[0]).toMatchObject({ method: "PATCH", path: SHOP_BASE });
    expect(server.writes()[0]?.body).toEqual({ usd_on: true });
    expect(toggle().checked).toBe(true);
    // Saved: saving again has nothing to send.
    save();
    expect(server.writes()).toHaveLength(1);
  });

  it("turns dollars off, and does not send the switch when something else changed", async () => {
    const server = shop(settingsBody({ usd_on: true }));
    await open(server);
    expect(toggle().checked).toBe(true);
    type(screen.getByLabelText("Do'kon nomi"), "Ziyo market");
    save();
    await screen.findByRole("status");
    expect(server.writes()[0]?.body).toEqual({ name: "Ziyo market" });

    fireEvent.click(toggle());
    save();
    await waitFor(() => expect(server.writes()).toHaveLength(2));
    expect(server.writes()[1]?.body).toEqual({ usd_on: false });
    await waitFor(() => expect(toggle().checked).toBe(false));
  });

  it("shows the server's refusal while a customer owes dollars, and leaves the switch on", async () => {
    const refused = "Dollarni o'chirib bo'lmaydi: mijozlarda dollarda qarz bor. Avval dollardagi barcha qarzlar yopilsin.";
    const server = shop(settingsBody({ usd_on: true }), () => refusal(409, "USD_BALANCE_OPEN", refused));
    await open(server);
    fireEvent.click(toggle());
    expect(toggle().checked).toBe(false);
    save();
    expect((await screen.findByRole("alert")).textContent).toBe(refused);
    expect(screen.queryByRole("status")).toBeNull();
    // What the server holds is what the screen shows again.
    expect(toggle().checked).toBe(true);
    expect(server.writes()).toHaveLength(1);
  });

  it.each([
    [
      "USD_SUPPLIER_BALANCE_OPEN",
      "Dollarni o'chirib bo'lmaydi: ta'minotchilar bilan dollarda hisob-kitob yopilmagan. Avval ta'minotchilar bilan dollardagi hisob nolga keltirilsin.",
    ],
    [
      "USD_STOCK_OPEN",
      "Dollarni o'chirib bo'lmaydi: omborda tannarxi dollarda yuritilgan tovar bor. Avval bu tovarlar sotilsin, qaytarilsin yoki hisobdan chiqarilsin.",
    ],
  ])("says which of the stock's dollars holds the switch (%s), and leaves it on", async (code, refused) => {
    const server = shop(settingsBody({ usd_on: true }), () => refusal(409, code, refused));
    await open(server);
    fireEvent.click(toggle());
    save();
    expect((await screen.findByRole("alert")).textContent).toBe(refused);
    expect(toggle().checked).toBe(true);
  });

  it("does not put the switch back on for a refusal that is not about dollars", async () => {
    const server = shop(settingsBody({ usd_on: true }), () => refusal(409, "SHOP_SUSPENDED", "Do'kon to'xtatilgan."));
    await open(server);
    fireEvent.click(toggle());
    save();
    await screen.findByRole("alert");
    expect(toggle().checked).toBe(false);
  });

  it("has the switch in Russian", async () => {
    await open(shop(settingsBody({ usd_on: false })), "owner", "ru");
    expect(screen.getByRole("checkbox", { name: "Магазин работает и в долларах" })).toBeTruthy();
    expect(screen.getByText("Долги в сумах и в долларах ведутся отдельно и не складываются.")).toBeTruthy();
  });

  it.each([
    [true, "Ha"],
    [false, "Yo'q"],
  ])("tells a manager, who only reads, whether the shop works in dollars (%s)", async (on, word) => {
    await open(shop(settingsBody({ usd_on: on })), "manager");
    const term = screen.getByText("Do'kon dollarda ham ishlaydi");
    expect(term.tagName).toBe("DT");
    expect(term.nextElementSibling?.textContent).toBe(word);
    expect(screen.queryByRole("checkbox")).toBeNull();
  });

  it.each(["owner", "manager"] as const)("shows a %s nothing about dollars when the platform does not offer them", async (role) => {
    const server = shop(settingsBody());
    await open(server, role);
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(screen.queryByText("Do'kon dollarda ham ishlaydi")).toBeNull();
    expectNoDollars();
    if (role === "owner") {
      type(screen.getByLabelText("Do'kon nomi"), "Ziyo market");
      save();
      await screen.findByRole("status");
      // The request is the one it always was: no dollar switch in it.
      expect(server.writes()[0]?.body).toEqual({ name: "Ziyo market" });
    }
  });
});

describe("the customer book", () => {
  const VALI = "11111111-1111-4111-8111-111111111112";
  const list = (items: unknown[]) => fakeServer(() => ok({ items, next_cursor: null }));
  const rows = () => screen.getAllByRole("listitem").map((row) => plain(row.textContent));

  it("shows both balances side by side, never added", async () => {
    const server = list([
      customerBody({ usd: { balance: 1250, credit_limit: null } }),
      customerBody({ id: VALI, display_name: "Vali", phone: null, balance: 0, usd: { balance: 125050, credit_limit: null } }),
    ]);
    renderScreen(<CustomersScreen />, { fetch: server.fetch });
    await screen.findByText("Vali");
    expect(rows()).toEqual(["Ali Valiyev120 000 so'm 12.50 $+998901234567", "Vali0 so'm 1 250.50 $"]);
    // Two figures, each its own element; no figure anywhere is their sum.
    expect([...document.querySelectorAll(".money")].map((money) => plain(money.textContent))).toEqual([
      "120 000 so'm",
      "12.50 $",
      "0 so'm",
      "1 250.50 $",
    ]);
    expect(page()).not.toContain("121 250");
  });

  it("offers a payment to a customer who owes dollars only", async () => {
    const server = list([customerBody({ balance: 0, usd: { balance: 1250, credit_limit: null } })]);
    renderScreen(<CustomersScreen pick />, { fetch: server.fetch });
    await screen.findByText("Ali Valiyev");
    expect(screen.getByRole("link", { name: "To'lov" }).getAttribute("href")).toBe(`#/customers/${CUSTOMER_ID}/payment`);
  });

  it("offers no payment when nothing is owed in either currency", async () => {
    const server = list([customerBody({ balance: 0, usd: { balance: 0, credit_limit: null } })]);
    renderScreen(<CustomersScreen pick />, { fetch: server.fetch });
    await screen.findByText("Ali Valiyev");
    expect(screen.queryByRole("link", { name: "To'lov" })).toBeNull();
  });

  it("shows a shop without dollars the so'm balance alone, as it always did", async () => {
    const server = list([customerBody(), customerBody({ id: VALI, display_name: "Vali", phone: null, balance: 0 })]);
    renderScreen(<CustomersScreen pick />, { fetch: server.fetch });
    await screen.findByText("Vali");
    expect(rows()).toEqual(["Ali Valiyev120 000 so'mNasiyaTo'lov", "Vali0 so'mNasiya"]);
    expectNoDollars();
  });
});

describe("a customer's page", () => {
  const USD_ENTRY = entryBody({ id: "22222222-2222-4222-8222-22222222aaaa", seq: 2, amount: 1250, currency: "USD" });

  function shop(detail: Record<string, unknown>, credit: Record<string, unknown>, onWrite: (sent: Sent) => Reply = () => ok({})) {
    return fakeServer((sent) => {
      if (sent.method !== "GET") {
        return onWrite(sent);
      }
      if (sent.path.endsWith("/credit-settings")) {
        return ok(credit);
      }
      return sent.path.endsWith("/link") ? ok(linkBody()) : ok(detail);
    });
  }
  const inDollars = () =>
    shop(
      detailBody({
        usd: { balance: 1250, credit_limit: null, overdue: OVERDUE_USD, payment_history: HISTORY_USD },
        entries: [USD_ENTRY, entryBody()],
        entries_total: 2,
      }),
      creditSettingsBody({ usd: CREDIT_USD }),
      (sent) =>
        sent.path.endsWith("/reminders/manual")
          ? ok({ sent: true, channel: "telegram", amount: 0, usd: { amount: 500 } })
          : ok(customerBody({ usd: { balance: 1250, credit_limit: 75050 } })),
    );
  const open = async (server: ReturnType<typeof fakeServer>, role: Role = "manager") => {
    renderScreen(<CustomerScreen customerId={CUSTOMER_ID} />, { fetch: server.fetch, role });
    await screen.findByRole("heading", { level: 2, name: "Ali Valiyev" });
    await screen.findAllByText(/^Limit/);
  };
  const dollarLimit = () => within(screen.getByRole("region", { name: "Nasiya limiti" })).getByRole("group", { name: "Dollarda" });

  it("shows what is owed in each currency, what of the dollars is late, and the dollar entry with its sign", async () => {
    await open(inDollars());
    const card = document.querySelector(".card") as HTMLElement;
    expect(plain(card.querySelector(".balance")?.textContent)).toBe("Qarz: 120 000 so'm 12.50 $");
    expect(plain(card.textContent)).toContain("5.00 $ muddati o'tgan");
    expect(plain(card.textContent)).toContain("Bugun to'lanishi kerak: 2.50 $");
    const amounts = [...document.querySelectorAll(".rows .row__amount")].map((amount) => plain(amount.textContent));
    expect(amounts).toEqual(["12.50 $", "45 000 so'm"]);
  });

  it("shows the dollar payment history apart from the so'm one", async () => {
    await open(inDollars());
    const history = within(screen.getByRole("region", { name: "To'lov tarixi" })).getByRole("group", { name: "Dollarda" });
    expect(plain(history.textContent)).toContain("O'z vaqtida to'langan: 75%.");
    expect(plain(history.textContent)).toContain("Muddati kelgan 20.00 $ dan 15.00 $ va'da qilingan kungacha to'langan.");
  });

  it("offers goods for the so'm sale only: a sale in dollars has none", async () => {
    await open(inDollars());
    const links = screen.getAllByRole("link", { name: "Tovarlarni qo'shish" }).map((link) => link.getAttribute("href"));
    expect(links).toEqual([`#/customers/${CUSTOMER_ID}/entries/22222222-2222-4222-8222-222222222222/goods`]);
  });

  it("shows the dollar limit apart from the so'm one, and sets it in cents in its own field", async () => {
    const server = inDollars();
    await open(server);
    expect(plain(dollarLimit().textContent)).toContain("Limit: 500.00 $ (do'konning umumiy limiti).");
    // The so'm limit says nothing of dollars: the shop has no so'm default, so there is none.
    expect(screen.getByText("Limit belgilanmagan.")).toBeTruthy();

    fireEvent.click(within(dollarLimit()).getByRole("button", { name: "Mijozga limit belgilash" }));
    const field = screen.getByLabelText<HTMLInputElement>("Mijoz limiti, $");
    expect(field.getAttribute("inputmode")).toBe("decimal");
    type(field, "750.5");
    expect(plain(document.getElementById("credit-limit-usd-hint")?.textContent)).toBe("750.50 $");
    fireEvent.click(within(dollarLimit()).getByRole("button", { name: "Limitni saqlash" }));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]).toMatchObject({ method: "PATCH", path: `${SHOP_BASE}/customers/${CUSTOMER_ID}` });
    expect(server.writes()[0]?.body).toEqual({ credit_limit_usd: 75050 });
  });

  it.each([
    ["750.555", "Limitni dollarda yozing, masalan 500 yoki 500.50."],
    ["besh yuz", "Limitni dollarda yozing, masalan 500 yoki 500.50."],
    ["0.99", "Limit 1.00 $ dan 1 000 000.00 $ gacha bo'lishi kerak."],
    ["1000000.01", "Limit 1.00 $ dan 1 000 000.00 $ gacha bo'lishi kerak."],
    ["", "Limitni kiriting."],
  ])("refuses the dollar limit %j and sends nothing", async (text, message) => {
    const server = inDollars();
    await open(server);
    fireEvent.click(within(dollarLimit()).getByRole("button", { name: "Mijozga limit belgilash" }));
    type(screen.getByLabelText("Mijoz limiti, $"), text);
    fireEvent.click(within(dollarLimit()).getByRole("button", { name: "Limitni saqlash" }));
    expect(plain(document.getElementById("credit-limit-usd-error")?.textContent)).toBe(message);
    expect(server.writes()).toHaveLength(0);
  });

  it("says what a reminder stated in dollars", async () => {
    await open(inDollars());
    fireEvent.click(screen.getByRole("button", { name: "Eslatma yuborish" }));
    await waitFor(() => expect(plain(screen.getByRole("status").textContent)).toBe("Eslatma Telegram orqali yuborildi: 5.00 $."));
  });

  it("says that an SMS stated so'm only, and what was due in dollars and not mentioned", async () => {
    const server = shop(detailBody({ usd: { balance: 1250, credit_limit: null } }), creditSettingsBody({ usd: CREDIT_USD }), () =>
      ok({ sent: true, channel: "sms", amount: 45000, usd: { amount: 0, unstated: 1250 } }),
    );
    await open(server);
    fireEvent.click(screen.getByRole("button", { name: "Eslatma yuborish" }));
    await waitFor(() => expect(plain(screen.getByRole("status").textContent)).toBe("Eslatma SMS orqali yuborildi: 45 000 so'm."));
    expect(plain(screen.getByText(/^SMS faqat so'mdagi qarzni aytdi\./).textContent)).toBe(
      "SMS faqat so'mdagi qarzni aytdi. Dollardagi 12.50 $ qarz eslatilmadi: u faqat Telegram orqali eslatiladi.",
    );
  });

  it("adds nothing to an SMS that left no dollars out", async () => {
    const server = shop(detailBody({ usd: { balance: 0, credit_limit: null } }), creditSettingsBody({ usd: CREDIT_USD }), () =>
      ok({ sent: true, channel: "sms", amount: 45000, usd: { amount: 0 } }),
    );
    await open(server);
    fireEvent.click(screen.getByRole("button", { name: "Eslatma yuborish" }));
    await waitFor(() => expect(plain(screen.getByRole("status").textContent)).toBe("Eslatma SMS orqali yuborildi: 45 000 so'm."));
    expect(screen.queryByText(/SMS faqat so'mdagi qarzni aytdi/)).toBeNull();
  });

  it("shows a seller the dollar limit and no way to change it", async () => {
    await open(inDollars(), "seller");
    expect(plain(dollarLimit().textContent)).toContain("Limit: 500.00 $");
    expect(within(dollarLimit()).queryByRole("button")).toBeNull();
  });

  it("shows a shop without dollars nothing about them", async () => {
    const server = shop(detailBody(), creditSettingsBody({ default_credit_limit: 500000 }), () =>
      ok({ sent: true, channel: "telegram", amount: 45000 }),
    );
    await open(server);
    expect(plain(document.querySelector(".card .balance")?.textContent)).toBe("Qarz: 120 000 so'm");
    expectNoDollars();
    fireEvent.click(screen.getByRole("button", { name: "Eslatma yuborish" }));
    await waitFor(() => expect(plain(screen.getByRole("status").textContent)).toBe("Eslatma Telegram orqali yuborildi: 45 000 so'm."));
    fireEvent.click(screen.getByRole("button", { name: "Mijozga limit belgilash" }));
    expect(screen.queryByLabelText("Mijoz limiti, $")).toBeNull();
    expectNoDollars();
  });

  it("does not show a dollar limit the shop's settings do not carry", async () => {
    // The customer's answer has dollars and the settings' answer has none: there is no limit to show or set.
    await open(shop(detailBody({ usd: { balance: 0, credit_limit: null } }), creditSettingsBody()));
    expect(within(screen.getByRole("region", { name: "Nasiya limiti" })).queryByRole("group", { name: "Dollarda" })).toBeNull();
  });
});

describe("recording an entry", () => {
  const DETAIL_USD = detailBody({ usd: { balance: 1250, credit_limit: null, overdue: NO_OVERDUE, payment_history: null } });
  const recorded = (kind: string, amount: number, currency?: string) =>
    ok(
      {
        entry: { id: "e-new", seq: 2, kind, amount, note: null, created_at: "2026-10-06T07:00:00+00:00", promised_date: null, lines: [], ...(currency ? { currency } : {}) },
        customer: customerBody(currency ? { usd: { balance: kind === "credit" ? 2500 : 0, credit_limit: null } } : {}),
      },
      201,
    );
  function shop(detail: Record<string, unknown>, onWrite: (sent: Sent) => Reply, credit: Record<string, unknown> = creditSettingsBody({ usd: CREDIT_USD })) {
    return fakeServer((sent) => {
      if (sent.method !== "GET") {
        return onWrite(sent);
      }
      return sent.path.endsWith("/credit-settings") ? ok(credit) : ok(detail);
    });
  }
  const open = async (server: ReturnType<typeof fakeServer>, kind: EntryKind = "credit", role: Role = "seller") => {
    renderScreen(<EntryScreen customerId={CUSTOMER_ID} kind={kind} />, { fetch: server.fetch, role });
    await screen.findByRole("heading", { level: 2, name: "Ali Valiyev" });
  };
  const amount = () => document.getElementById("entry-amount") as HTMLInputElement;
  const label = () => document.querySelector('label[for="entry-amount"]')?.textContent;
  const submit = (kind: EntryKind = "credit") =>
    fireEvent.click(screen.getByRole("button", { name: kind === "credit" ? "Nasiyani yozish" : "To'lovni yozish" }));

  it("starts in so'm and offers dollars; in dollars it takes decimals, sends cents, and has no goods", async () => {
    const server = shop(DETAIL_USD, () => recorded("credit", 1250, "USD"));
    await open(server);
    expect(pressed()).toEqual([
      ["so'm", "true"],
      ["$", "false"],
    ]);
    expect(label()).toBe("Summa, so'm");
    expect(screen.getByRole("button", { name: "Tovarlar bilan yozish" })).toBeTruthy();
    expect(plain(document.querySelector(".balance")?.textContent)).toBe("Qarz: 120 000 so'm 12.50 $");

    choose("$");
    expect(pressed()).toEqual([
      ["so'm", "false"],
      ["$", "true"],
    ]);
    expect(label()).toBe("Summa, $");
    expect(amount().getAttribute("inputmode")).toBe("decimal");
    expect(screen.queryByRole("button", { name: "Tovarlar bilan yozish" })).toBeNull();
    expect(screen.getByText("Dollardagi nasiyaga tovarlar ro'yxati qo'shilmaydi: tovar narxlari so'mda yuritiladi. Savdoni summasi bilan yozing.")).toBeTruthy();
    expect(document.getElementById("entry-amount-hint")?.textContent).toBe("Masalan: 12.50");

    type(amount(), "12,5");
    expect(plain(document.getElementById("entry-amount-hint")?.textContent)).toBe("12.50 $");
    submit();
    const done = await screen.findByRole("status");
    expect(server.writes()).toHaveLength(1);
    expect(server.writes()[0]).toMatchObject({ method: "POST", path: `${SHOP_BASE}/customers/${CUSTOMER_ID}/entries` });
    expect(server.writes()[0]?.body).toEqual({ kind: "credit", amount: 1250, currency: "USD" });
    expect(plain(done.textContent)).toContain("Ali Valiyev: 12.50 $ nasiya yozildi.");
    expect(plain(done.querySelector(".balance")?.textContent)).toBe("Yangi qarz: 120 000 so'm 25.00 $");
  });

  it("sends a so'm sale of a shop with dollars exactly as before: no currency", async () => {
    const server = shop(DETAIL_USD, () => recorded("credit", 45000));
    await open(server);
    type(amount(), "45 ming");
    submit();
    await screen.findByRole("status");
    expect(server.writes()[0]?.body).toEqual({ kind: "credit", amount: 45000 });
  });

  it("does not carry an amount typed for one currency over to the other", async () => {
    const server = shop(DETAIL_USD, () => recorded("credit", 1250, "USD"));
    await open(server);
    type(amount(), "45000");
    choose("$");
    expect(amount().value).toBe("");
    submit();
    expect(document.getElementById("entry-amount-error")?.textContent).toBe("Summani kiriting.");
    expect(server.writes()).toHaveLength(0);
  });

  it("drops goods that were being listed when the sale is switched to dollars", async () => {
    const server = shop(DETAIL_USD, () => recorded("credit", 1250, "USD"));
    await open(server);
    fireEvent.click(screen.getByRole("button", { name: "Tovarlar bilan yozish" }));
    choose("$");
    // The goods editor is gone and the amount field is back: the sale is by its amount.
    expect(amount()).toBeTruthy();
    type(amount(), "12.50");
    submit();
    await screen.findByRole("status");
    expect(server.writes()[0]?.body).toEqual({ kind: "credit", amount: 1250, currency: "USD" });
  });

  it.each([
    ["12.505", "Dollarda nuqtadan keyin ko'pi bilan ikki raqam yoziladi, masalan 12.50."],
    ["12 ming", "Summani tushunib bo'lmadi. Dollarda yozing, masalan 12.50."],
    ["$12", "Summani tushunib bo'lmadi. Dollarda yozing, masalan 12.50."],
    ["0", "Summa 0.01 $ dan 10 000.00 $ gacha bo'lishi kerak."],
    ["10000.01", "Summa 0.01 $ dan 10 000.00 $ gacha bo'lishi kerak."],
    ["", "Summani kiriting."],
  ])("refuses the dollar amount %j and sends nothing", async (text, message) => {
    const server = shop(DETAIL_USD, () => recorded("credit", 1250, "USD"));
    await open(server);
    choose("$");
    type(amount(), text);
    submit();
    expect(plain(document.getElementById("entry-amount-error")?.textContent)).toBe(message);
    expect(server.writes()).toHaveLength(0);
  });

  it("warns by the dollar limit and the dollar debt for a sale in dollars, and by the so'm ones for so'm", async () => {
    // The dollar default is 500.00 $ and 12.50 $ is owed; there is no so'm limit at all.
    const server = shop(DETAIL_USD, () => recorded("credit", 1250, "USD"));
    await open(server, "credit", "manager");
    await waitFor(() => expect(server.sent.some((sent) => sent.path.endsWith("/credit-settings"))).toBe(true));
    type(amount(), "99000000");
    expect(screen.queryByRole("note")).toBeNull();
    choose("$");
    type(amount(), "487.50");
    await waitFor(() => expect(screen.queryByRole("note")).toBeNull());
    type(amount(), "487.51");
    expect(plain((await screen.findByRole("note")).textContent)).toContain("Bu savdo bilan qarz 500.01 $ bo'ladi va 500.00 $ limitdan oshadi.");
  });

  it("shows a refused limit in the currency of the sale", async () => {
    const server = shop(DETAIL_USD, () => refusal(409, "LIMIT_REACHED", "Limitdan oshib ketadi.", { limit: "50000", balance: "50001" }));
    await open(server);
    choose("$");
    type(amount(), "487.51");
    submit();
    expect(plain((await screen.findByRole("alert")).textContent)).toContain("Limit: 500.00 $. Bu savdo bilan qarz: 500.01 $.");
  });

  it("starts a payment in dollars when dollars are all that is owed, and pays the whole dollar debt", async () => {
    const server = shop(detailBody({ balance: 0, usd: { balance: 1250, credit_limit: null } }), () => recorded("payment", 1250, "USD"));
    await open(server, "payment");
    expect(label()).toBe("Summa, $");
    fireEvent.click(screen.getByRole("button", { name: "Butun qarz: 12.50\u00a0$" }));
    expect(amount().value).toBe("12.50");
    submit("payment");
    const done = await screen.findByRole("status");
    expect(server.writes()[0]?.body).toEqual({ kind: "payment", amount: 1250, currency: "USD" });
    expect(plain(done.textContent)).toContain("Ali Valiyev: 12.50 $ to'lov qabul qilindi.");
  });

  it("starts a payment in so'm while so'm are owed, and pays the so'm debt", async () => {
    const server = shop(DETAIL_USD, () => recorded("payment", 120000));
    await open(server, "payment");
    expect(label()).toBe("Summa, so'm");
    fireEvent.click(screen.getByRole("button", { name: "Butun qarz: 120\u00a0000 so'm" }));
    submit("payment");
    await screen.findByRole("status");
    expect(server.writes()[0]?.body).toEqual({ kind: "payment", amount: 120000 });
  });

  it.each(["credit", "payment"] as const)("offers a shop without dollars no currency for a %s, and sends none", async (kind) => {
    const server = shop(detailBody(), () => recorded(kind, 45000), creditSettingsBody());
    await open(server, kind);
    expect(label()).toBe("Summa, so'm");
    expect(amount().getAttribute("inputmode")).toBe("numeric");
    expectNoDollars();
    // A decimal stays what it was for so'm: refused.
    type(amount(), "12.50");
    submit(kind);
    expect(document.getElementById("entry-amount-error")?.textContent).toBe("Summa butun so'mda bo'lishi kerak, tiyinsiz.");
    type(amount(), "45000");
    submit(kind);
    const done = await screen.findByRole("status");
    expect(server.writes()).toHaveLength(1);
    expect(server.writes()[0]?.body).toEqual({ kind, amount: 45000 });
    expect(plain(done.querySelector(".balance")?.textContent)).toBe("Yangi qarz: 120 000 so'm");
    expectNoDollars();
  });
});

describe("the overview", () => {
  const TOTALS = { outstanding: 1250000, debtors: 5, overdue: { amount: 320000, customers: 2 }, due_today: 45000 };
  const TOTALS_USD = { outstanding: 125050, debtors: 2, overdue: { amount: 5000, customers: 1 }, due_today: 1250 };
  const ALI = { ...customerBody(), overdue: { amount: 45000, since: "2026-09-24", days: 12, due_today: 0 } };
  const ALI_USD = { ...ALI, usd: { balance: 1250, credit_limit: null, overdue: OVERDUE_USD } };
  const shop = (totals: unknown, debtors: unknown[]) =>
    fakeServer((sent) => (sent.path.endsWith("/overview") ? ok(totals) : ok({ items: debtors, next_cursor: null })));
  const figures = () =>
    screen.getAllByRole("term").map((term) => [
      term.textContent,
      ...Array.from(term.parentElement?.querySelectorAll("dd") ?? [], (value) => plain(value.textContent)),
    ]);
  const debtorCalls = (server: ReturnType<typeof fakeServer>) =>
    server.sent.filter((sent) => sent.path.endsWith("/debtors")).map((sent) => sent.query);

  it("shows the dollar debts as figures of their own under the so'm ones", async () => {
    renderScreen(<OverviewScreen />, { fetch: shop({ ...TOTALS, usd: TOTALS_USD }, [ALI_USD]).fetch });
    await screen.findByText("Jami qarz, dollarda");
    expect(figures()).toEqual([
      ["Jami qarz", "1 250 000 so'm", "5 ta mijoz"],
      ["Muddati o'tgan", "320 000 so'm", "2 ta mijoz"],
      ["Bugun to'lanishi kerak", "45 000 so'm"],
      ["Jami qarz, dollarda", "1 250.50 $", "2 ta mijoz"],
      ["Muddati o'tgan, dollarda", "50.00 $", "1 ta mijoz"],
      ["Bugun to'lanishi kerak, dollarda", "12.50 $"],
    ]);
  });

  it("lists the debtors by the so'm debt, and by the dollar debt when dollars are chosen", async () => {
    const server = shop({ ...TOTALS, usd: TOTALS_USD }, [ALI_USD]);
    renderScreen(<OverviewScreen />, { fetch: server.fetch });
    await screen.findByText("Jami qarz, dollarda");
    await screen.findByText("Ali Valiyev");
    expect(pressed()).toEqual([
      ["so'm", "true"],
      ["$", "false"],
    ]);
    const row = plain(within(screen.getByRole("list")).getByRole("listitem").textContent);
    expect(row).toContain("120 000 so'm 12.50 $");
    expect(row).toContain("45 000 so'm muddati o'tgan");
    expect(row).toContain("5.00 $ muddati o'tgan");
    expect(row).toContain("Bugun to'lanishi kerak: 2.50 $");
    expect(debtorCalls(server)).toEqual([{ overdue: "false" }]);

    choose("$");
    await waitFor(() => expect(debtorCalls(server)).toEqual([{ overdue: "false" }, { overdue: "false", currency: "USD" }]));
    fireEvent.click(screen.getByRole("button", { name: "Muddati o'tganlar" }));
    await waitFor(() => expect(debtorCalls(server)[2]).toEqual({ overdue: "true", currency: "USD" }));
    choose("so'm");
    await waitFor(() => expect(debtorCalls(server)[3]).toEqual({ overdue: "true" }));
  });

  it("shows a shop without dollars the three so'm figures, no choice of currency, and never names one", async () => {
    const server = shop(TOTALS, [ALI]);
    renderScreen(<OverviewScreen />, { fetch: server.fetch });
    await screen.findByText("Ali Valiyev");
    await screen.findByText("Jami qarz");
    expect(figures()).toEqual([
      ["Jami qarz", "1 250 000 so'm", "5 ta mijoz"],
      ["Muddati o'tgan", "320 000 so'm", "2 ta mijoz"],
      ["Bugun to'lanishi kerak", "45 000 so'm"],
    ]);
    expectNoDollars();
    fireEvent.click(screen.getByRole("button", { name: "Muddati o'tganlar" }));
    await waitFor(() => expect(debtorCalls(server)).toEqual([{ overdue: "false" }, { overdue: "true" }]));
  });
});

describe("the shop's credit settings", () => {
  function shop(start: Record<string, unknown>) {
    let current = start;
    return fakeServer((sent) => {
      if (sent.method !== "GET") {
        const body = sent.body as Record<string, unknown>;
        if ("default_credit_limit_usd" in body) {
          current = { ...current, usd: { ...CREDIT_USD, default_credit_limit: body["default_credit_limit_usd"] } };
        }
      }
      return ok(current);
    });
  }
  const open = async (server: ReturnType<typeof fakeServer>, role: Role = "manager") => {
    renderScreen(<CreditSettingsSection />, { fetch: server.fetch, role });
    await screen.findByText(role === "seller" ? "Umumiy limit" : "Do'konning umumiy limiti, so'm");
  };
  const field = () => screen.getByLabelText<HTMLInputElement>("Do'konning umumiy limiti, $");
  const save = () => fireEvent.click(screen.getByRole("button", { name: "Nasiya sozlamalarini saqlash" }));

  it("has a default dollar limit beside the so'm one, changed and removed in its own field", async () => {
    const server = shop(creditSettingsBody({ usd: CREDIT_USD }));
    await open(server);
    expect(field().value).toBe("500.00");
    expect(field().getAttribute("inputmode")).toBe("decimal");
    type(field(), "1000");
    expect(plain(document.getElementById("credit-default-usd-hint")?.textContent)).toBe("1 000.00 $");
    save();
    await screen.findByRole("status");
    expect(server.writes()[0]).toMatchObject({ method: "PATCH", path: `${SHOP_BASE}/credit-settings` });
    expect(server.writes()[0]?.body).toEqual({ default_credit_limit_usd: 100000 });
    expect(field().value).toBe("1000.00");

    type(field(), "");
    save();
    await waitFor(() => expect(server.writes()).toHaveLength(2));
    expect(server.writes()[1]?.body).toEqual({ default_credit_limit_usd: null });
  });

  it.each([
    ["0.5", "Limit 1.00 $ dan 1 000 000.00 $ gacha bo'lishi kerak."],
    ["500.505", "Limitni dollarda yozing, masalan 500 yoki 500.50."],
    ["500 ming", "Limitni dollarda yozing, masalan 500 yoki 500.50."],
  ])("refuses the default dollar limit %j and sends nothing", async (text, message) => {
    const server = shop(creditSettingsBody({ usd: CREDIT_USD }));
    await open(server);
    type(field(), text);
    save();
    expect(plain(document.getElementById("credit-default-usd-error")?.textContent)).toBe(message);
    expect(server.writes()).toHaveLength(0);
  });

  it("tells a seller, who only reads, the default dollar limit", async () => {
    await open(shop(creditSettingsBody({ usd: CREDIT_USD })), "seller");
    expect(plain(screen.getByText("Umumiy limit, dollarda").nextElementSibling?.textContent)).toBe("500.00 $");
  });

  it.each(["manager", "seller"] as const)("shows a %s of a shop without dollars no dollar limit", async (role) => {
    const server = shop(creditSettingsBody({ default_credit_limit: 500000 }));
    await open(server, role);
    expect(screen.queryByLabelText("Do'konning umumiy limiti, $")).toBeNull();
    expectNoDollars();
    if (role === "manager") {
      type(screen.getByLabelText("Do'konning umumiy limiti, so'm"), "600000");
      save();
      await waitFor(() => expect(server.writes()).toHaveLength(1));
      expect(server.writes()[0]?.body).toEqual({ default_credit_limit: 600000 });
    }
  });
});

describe("payment notices", () => {
  const NOTICE_USD = openNoticeBody({ amount: 500, customer_balance: 1250, currency: "USD", has_receipt: false });
  const shop = (items: unknown[]) => fakeServer((sent) => (sent.method === "GET" ? ok({ items }) : ok({})));
  const open = async (server: ReturnType<typeof fakeServer>) => {
    renderScreen(<PaymentNoticesScreen />, { fetch: server.fetch });
    await screen.findByText("Ali Valiyev");
  };

  it("shows a notice of dollars in dollars, and accepts it as stated or corrected, in cents", async () => {
    const server = shop([NOTICE_USD]);
    await open(server);
    const row = plain(screen.getByRole("listitem").textContent);
    expect(row).toContain("Ali Valiyev5.00 $");
    expect(row).toContain("Xabar qilingan summa: 5.00 $. Mijozning qarzi: 12.50 $.");

    fireEvent.click(screen.getByRole("button", { name: "Qabul qilish" }));
    const field = screen.getByLabelText<HTMLInputElement>("Yoziladigan to'lov summasi, $");
    expect(field.value).toBe("5.00");
    expect(field.getAttribute("inputmode")).toBe("decimal");
    type(field, "4.5");
    fireEvent.click(screen.getByRole("button", { name: "Ha, to'lov yozilsin" }));
    await waitFor(() => expect(plain(screen.getByRole("status").textContent)).toBe("To'lov yozildi: 4.50 $."));
    expect(server.writes()[0]?.body).toEqual({ amount: 450 });
  });

  it("sends no amount when the stated dollars are accepted as they are", async () => {
    const server = shop([NOTICE_USD]);
    await open(server);
    fireEvent.click(screen.getByRole("button", { name: "Qabul qilish" }));
    fireEvent.click(screen.getByRole("button", { name: "Ha, to'lov yozilsin" }));
    await screen.findByRole("status");
    expect(server.writes()[0]?.body).toEqual({});
  });

  it.each([
    ["12.51", "Summa mijozning qarzidan (12.50 $) katta bo'lishi mumkin emas."],
    ["4.555", "Dollarda nuqtadan keyin ko'pi bilan ikki raqam yoziladi, masalan 12.50."],
    ["5 ming", "Summani tushunib bo'lmadi. Dollarda yozing, masalan 12.50."],
  ])("refuses the correction %j of a dollar notice and sends nothing", async (text, message) => {
    const server = shop([NOTICE_USD]);
    await open(server);
    fireEvent.click(screen.getByRole("button", { name: "Qabul qilish" }));
    type(screen.getByLabelText("Yoziladigan to'lov summasi, $"), text);
    fireEvent.click(screen.getByRole("button", { name: "Ha, to'lov yozilsin" }));
    expect(plain(screen.getByRole("alert").textContent)).toBe(message);
    expect(server.writes()).toHaveLength(0);
  });

  it("shows a notice of so'm as it always was", async () => {
    const server = shop([openNoticeBody({ has_receipt: false })]);
    await open(server);
    expect(plain(screen.getByRole("listitem").textContent)).toContain("Xabar qilingan summa: 50 000 so'm. Mijozning qarzi: 120 000 so'm.");
    fireEvent.click(screen.getByRole("button", { name: "Qabul qilish" }));
    expect(screen.getByLabelText<HTMLInputElement>("Yoziladigan to'lov summasi, so'm").value).toBe("50000");
    expectNoDollars();
  });
});

describe("disputes, date requests and reminders", () => {
  const amounts = () => [...document.querySelectorAll(".row__amount")].map((amount) => plain(amount.textContent));

  it("shows a dispute over a dollar entry with its sign, also where the reversal is confirmed", async () => {
    const server = fakeServer(() => ok({ items: [openDisputeBody({ amount: 1250, currency: "USD" }), openDisputeBody({ id: "88888888-8888-4888-8888-88888888888a" })] }));
    renderScreen(<DisputesScreen />, { fetch: server.fetch, role: "manager" });
    await screen.findAllByText("Ali Valiyev");
    expect(amounts()).toEqual(["12.50 $", "45 000 so'm"]);
    fireEvent.click(screen.getAllByRole("button", { name: "Yozuvni bekor qilish" })[0] as HTMLElement);
    expect(plain(screen.getByRole("group").textContent)).toContain("12.50 $");
  });

  it("shows disputes of a shop without dollars as they always were", async () => {
    const server = fakeServer(() => ok({ items: [openDisputeBody()] }));
    renderScreen(<DisputesScreen />, { fetch: server.fetch, role: "manager" });
    await screen.findByText("Ali Valiyev");
    expect(amounts()).toEqual(["45 000 so'm"]);
    expectNoDollars();
  });

  it("shows a request about a dollar entry with its sign, also where it is accepted", async () => {
    const server = fakeServer(() => ok({ items: [openDateRequestBody({ amount: 1250, currency: "USD" })] }));
    renderScreen(<DateRequestsScreen />, { fetch: server.fetch, role: "manager" });
    await screen.findByText("Ali Valiyev");
    expect(amounts()).toEqual(["12.50 $"]);
    fireEvent.click(screen.getByRole("button", { name: "Qabul qilish" }));
    expect(plain(screen.getByRole("group").textContent)).toContain("12.50 $");
  });

  it("shows date requests of a shop without dollars as they always were", async () => {
    const server = fakeServer(() => ok({ items: [openDateRequestBody()] }));
    renderScreen(<DateRequestsScreen />, { fetch: server.fetch, role: "manager" });
    await screen.findByText("Ali Valiyev");
    expect(amounts()).toEqual(["45 000 so'm"]);
    expectNoDollars();
  });

  const unreachable = (item: Record<string, unknown>, settings: Record<string, unknown> = {}) =>
    fakeServer((sent) =>
      sent.path.endsWith("/unreachable")
        ? ok({ items: [{ customer_id: CUSTOMER_ID, display_name: "Ali Valiyev", phone: null, amount: 45000, ...item }] })
        : ok(remindersBody(settings)),
    );
  const SMS_NOTE = /^SMS faqat so'mdagi qarzni aytadi\. Dollardagi qarz haqidagi eslatma faqat Telegram orqali boradi\./;

  it("tells a shop that works in dollars, under the SMS switch, that an SMS states so'm only, and no other shop", async () => {
    renderScreen(<RemindersScreen />, { fetch: unreachable({ usd: { amount: 0 } }, { usd: { sms: false } }).fetch, role: "manager" });
    expect(await screen.findByText(SMS_NOTE)).toBeTruthy();
    cleanup();
    renderScreen(<RemindersScreen />, { fetch: unreachable({}).fetch, role: "manager" });
    await screen.findByText("Ali Valiyev");
    await screen.findByRole("button", { name: "Saqlash" });
    expect(screen.queryByText(SMS_NOTE)).toBeNull();
    expectNoDollars();
  });

  it("shows what is due in each currency of a customer nobody can reach", async () => {
    renderScreen(<RemindersScreen />, { fetch: unreachable({ usd: { amount: 1250 } }).fetch, role: "manager" });
    await screen.findByText("Ali Valiyev");
    const list = within(screen.getByRole("region", { name: "Yetib bo'lmaydigan mijozlar" }));
    expect(plain(list.getByRole("link").textContent)).toBe("Ali Valiyev45 000 so'm 12.50 $");
  });

  it("shows a shop without dollars the so'm that are due, alone", async () => {
    renderScreen(<RemindersScreen />, { fetch: unreachable({}).fetch, role: "manager" });
    await screen.findByText("Ali Valiyev");
    const list = within(screen.getByRole("region", { name: "Yetib bo'lmaydigan mijozlar" }));
    expect(plain(list.getByRole("link").textContent)).toBe("Ali Valiyev45 000 so'm");
    expectNoDollars();
  });
});

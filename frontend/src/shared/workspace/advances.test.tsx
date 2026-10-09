// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it } from "vitest";

import { loadLanguage } from "../../i18n/catalog";
import { I18nProvider } from "../../i18n/I18nProvider";
import type { Language } from "../../i18n/types";
import {
  creditSettingsBody,
  CUSTOMER_ID,
  customerBody,
  deferred,
  detailBody,
  fakeServer,
  linkBody,
  NO_OVERDUE,
  ok,
  refusal,
  type Reply,
  type Sent,
  SHOP_BASE,
} from "../../testing/fakeServer";
import { exact, renderScreen } from "../../testing/renderScreen";
import { advanceAsked, ApiError } from "../api";
import type { Role } from "../navigation";
import { CreditSettingsSection } from "./CreditSettingsSection";
import { CustomerScreen } from "./CustomerScreen";
import { CustomersScreen } from "./CustomersScreen";
import { EntryScreen } from "./EntryScreen";
import { OverviewScreen } from "./OverviewScreen";
import { BalanceLine, inCredit, isMixed, Money } from "./parts";

/**
 * The founder's decision: a shop may accept advances. A customer who paid more than they owe has a
 * balance below zero, which is their advance. Every test here has its other half: what a shop that does
 * not accept advances sees and sends is what it always was.
 */

afterEach(cleanup);
// Uzbek Cyrillic, Tajik, Karakalpak and English are fetched in the application; here they are at hand before a test asks.
beforeAll(async () => {
  await Promise.all((["uz-Cyrl", "tg", "kaa", "en"] as const).map(loadLanguage));
});

const NBSP = "\u00a0";
const ASKED = "To'lov mijozning qarzidan katta. Ortig'i oldindan to'lov (avans) bo'lib qolishini tasdiqlang.";
const QUESTION =
  `To'lov qarzdan 30${NBSP}000 so'm ko'p (qarz: 120${NBSP}000 so'm). Ortig'i mijozning oldindan to'lovi (avans) bo'lib ` +
  "qoladi va keyingi nasiyalarga hisoblanadi.";
/** Markup with its non-breaking spaces written as they are in the text, not as an entity. */
const markup = (node: Element | null | undefined) => (node?.innerHTML ?? "").replace(/&nbsp;/g, NBSP);
const type = (input: HTMLElement, value: string) => fireEvent.change(input, { target: { value } });
const button = (name: string) => screen.getByRole<HTMLButtonElement>("button", { name });

describe("a payment larger than the debt, where the shop accepts advances", () => {
  const recorded = (balance: number) =>
    ok(
      {
        entry: { id: "e-new", seq: 2, kind: "payment", amount: 150000, note: null, created_at: "2026-10-06T07:00:00+00:00", promised_date: null },
        customer: customerBody({ balance }),
      },
      201,
    );
  const ask = (fields = { debt: "120000", over: "30000", advance: "30000" }) => refusal(409, "ADVANCE_NOT_CONFIRMED", ASKED, fields);

  /** A customer who owes 120 000; `onWrite` answers each POST of an entry. */
  function shop(onWrite: (sent: Sent, attempt: number) => Reply) {
    let attempt = 0;
    return fakeServer((sent) => (sent.method === "GET" ? ok(detailBody()) : onWrite(sent, attempt++)));
  }

  async function pay(server: ReturnType<typeof fakeServer>, amount: string, language: Language = "uz") {
    renderScreen(<EntryScreen customerId={CUSTOMER_ID} kind="payment" />, { fetch: server.fetch, language });
    type(await screen.findByLabelText<HTMLInputElement>(/^(Summa, so'm|Amount, soum)$/), amount);
    fireEvent.click(button(language === "uz" ? "To'lovni yozish" : "Record payment"));
  }

  it("asks before keeping the excess, then sends the same payment again with the advance said and a new key", async () => {
    const server = shop((_sent, attempt) => (attempt === 0 ? ask() : recorded(-30000)));
    await pay(server, "150000");

    const question = await screen.findByText(exact(QUESTION));
    // Nothing was saved, and the form with its submit button is not there to be pressed again.
    expect(server.writes()).toHaveLength(1);
    expect(server.writes()[0]?.body).toEqual({ kind: "payment", amount: 150000 });
    expect(screen.queryByRole("button", { name: "To'lovni yozish" })).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    // The advance after it is the excess itself, so it is not said twice.
    expect(question.parentElement?.textContent).not.toContain("Shunda mijozning avansi");

    fireEvent.click(button("Ha, avans bo'lib qolsin"));
    const done = await screen.findByRole("status");
    expect(server.writes()).toHaveLength(2);
    expect(server.writes()[1]?.body).toEqual({ kind: "payment", amount: 150000, advance: true });
    const [first, second] = server.writes().map((sent) => sent.headers["Idempotency-Key"]);
    expect(second).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
    expect(second).not.toBe(first);
    // The new balance is an advance: said in words with the amount itself, with no "debt" and no minus.
    expect(done.textContent).toContain(`Haqdor: 30${NBSP}000 so'm`);
    expect(done.textContent).not.toContain("Yangi qarz");
    expect(done.textContent).not.toMatch(/[-−]\s?30/);
  });

  it("sends nothing more when the person says no, and gives the form back as it was", async () => {
    const server = shop(() => ask());
    await pay(server, "150000");
    await screen.findByText(exact(QUESTION));

    fireEvent.click(button("Bekor qilish"));
    expect(screen.queryByText(exact(QUESTION))).toBeNull();
    expect(screen.getByLabelText<HTMLInputElement>("Summa, so'm").value).toBe("150000");
    expect(button("To'lovni yozish").disabled).toBe(false);
    expect(screen.queryByRole("alert")).toBeNull();
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(server.writes()).toHaveLength(1);
    expect(server.writes().every((sent) => !("advance" in (sent.body as object)))).toBe(true);
  });

  it("says what the customer's advance will be when they were already in credit", async () => {
    const server = shop(() => ask({ debt: "0", over: "1000", advance: "16000" }));
    await pay(server, "1000");
    expect(await screen.findByText(exact(`Shunda mijozning avansi 16${NBSP}000 so'm bo'ladi.`))).toBeTruthy();
  });

  it("sends one confirmed payment for a double tap, and keeps the question while it is on its way", async () => {
    const answer = deferred<{ status: number; body: unknown }>();
    const server = shop((_sent, attempt) => (attempt === 0 ? ask() : answer.promise));
    await pay(server, "150000");
    await screen.findByText(exact(QUESTION));
    const yes = button("Ha, avans bo'lib qolsin");
    fireEvent.click(yes);
    fireEvent.click(yes);
    await waitFor(() => expect(button("Saqlanmoqda…").disabled).toBe(true));
    expect(button("Bekor qilish").disabled).toBe(true);
    expect(server.writes()).toHaveLength(2);
    answer.resolve(recorded(-30000));
    await screen.findByRole("status");
  });

  it("shows the server's words when the confirmed payment is refused, and repeats it under the same key", async () => {
    const TOO_LARGE = "Oldindan to'lov juda katta: bitta mijozning avansi bitta yozuv chegarasidan oshmaydi.";
    const server = shop((_sent, attempt) => (attempt === 0 ? ask() : refusal(409, "ADVANCE_TOO_LARGE", TOO_LARGE)));
    await pay(server, "150000");
    await screen.findByText(exact(QUESTION));
    fireEvent.click(button("Ha, avans bo'lib qolsin"));
    expect((await screen.findByRole("alert")).textContent).toBe(TOO_LARGE);
    expect(screen.queryByRole("status")).toBeNull();

    fireEvent.click(button("Ha, avans bo'lib qolsin"));
    await waitFor(() => expect(server.writes()).toHaveLength(3));
    const keys = server.writes().map((sent) => sent.headers["Idempotency-Key"]);
    expect(keys[2]).toBe(keys[1]);
  });

  it("asks in the reader's language, with the amounts in the payment's own currency", async () => {
    const server = shop(() => ask());
    await pay(server, "150000", "en");
    expect(
      await screen.findByText(
        exact(
          `This payment is 30${NBSP}000 soum more than the debt (debt: 120${NBSP}000 soum). The rest will be kept as the ` +
            "customer's advance and used for the next credit sales.",
        ),
      ),
    ).toBeTruthy();
    expect(button("Yes, keep it as an advance")).toBeTruthy();
  });

  it("does not offer to confirm a refusal whose figures are not amounts: the server's words are shown", async () => {
    const server = shop(() => ask({ debt: "120000", over: "-5", advance: "x" }));
    await pay(server, "150000");
    expect((await screen.findAllByRole("alert"))[0]?.textContent).toContain(ASKED);
    expect(screen.queryByRole("button", { name: "Ha, avans bo'lib qolsin" })).toBeNull();
    expect(button("To'lovni yozish")).toBeTruthy();
  });

  it("is refused exactly as before where the shop does not accept advances: no question, nothing resent", async () => {
    const REFUSED = "To'lov mijozning qarzidan katta bo'lishi mumkin emas.";
    const server = shop(() => refusal(409, "EXCEEDS_BALANCE", REFUSED));
    await pay(server, "150000");
    await waitFor(() => expect(screen.getAllByRole("alert").length).toBeGreaterThan(0));
    expect(screen.getAllByRole("alert")[0]?.textContent).toBe(REFUSED);
    expect(screen.getByLabelText("Summa, so'm").getAttribute("aria-invalid")).toBe("true");
    expect(screen.queryByRole("button", { name: "Ha, avans bo'lib qolsin" })).toBeNull();
    expect(button("To'lovni yozish").disabled).toBe(false);
    expect(server.writes()).toHaveLength(1);
    expect(server.writes()[0]?.body).toEqual({ kind: "payment", amount: 150000 });
  });

  it("never sends the advance with a payment nobody was asked about, nor with a credit sale", async () => {
    const server = shop(() => recorded(70000));
    await pay(server, "50000");
    await screen.findByRole("status");
    expect(server.writes()[0]?.body).toEqual({ kind: "payment", amount: 50000 });
  });
});

describe("reading an ADVANCE_NOT_CONFIRMED refusal", () => {
  const error = (code: string, fields: Record<string, string>) => new ApiError(409, code, "…", fields);

  it("takes the three amounts in minor units", () => {
    expect(advanceAsked(error("ADVANCE_NOT_CONFIRMED", { debt: "12000", over: "500", advance: "500" }))).toEqual({
      debt: 12000,
      over: 500,
      advance: 500,
    });
  });

  it.each([
    ["another code", "EXCEEDS_BALANCE", { debt: "1", over: "1", advance: "1" }],
    ["a missing amount", "ADVANCE_NOT_CONFIRMED", { debt: "1", over: "1" }],
    ["a negative amount", "ADVANCE_NOT_CONFIRMED", { debt: "1", over: "-1", advance: "1" }],
    ["a fraction", "ADVANCE_NOT_CONFIRMED", { debt: "1.5", over: "1", advance: "1" }],
    ["an amount too long to be a safe number", "ADVANCE_NOT_CONFIRMED", { debt: "1", over: "1", advance: "9".repeat(16) }],
  ])("is nothing for %s", (_what, code, fields) => {
    expect(advanceAsked(error(code, fields))).toBeNull();
  });

  it("is nothing without an error", () => {
    expect(advanceAsked(null)).toBeNull();
  });
});

describe("the shop's setting: accept advances", () => {
  const LABEL = "Oldindan to'lovni (avans) qabul qilish";
  const STATE = "Oldindan to'lov (avans) qabul qilinadi. Buni faqat do'kon egasi o'zgartiradi.";
  const save = () => button("Nasiya sozlamalarini saqlash");

  function shop(settings: Record<string, unknown> = {}, onWrite?: (sent: Sent) => Reply) {
    let current: Record<string, unknown> = creditSettingsBody({ accept_advances: false, ...settings });
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

  async function open(server: ReturnType<typeof fakeServer>, role: Role) {
    renderScreen(<CreditSettingsSection />, { fetch: server.fetch, role });
    await screen.findByText(role === "seller" ? "Umumiy limit" : "Do'konning umumiy limiti, so'm");
  }

  it("gives the owner a switch that is off, with a line that says what it does", async () => {
    await open(shop(), "owner");
    const box = screen.getByRole<HTMLInputElement>("checkbox", { name: LABEL });
    expect(box.checked).toBe(false);
    expect(document.getElementById(box.getAttribute("aria-describedby") ?? "")?.textContent).toBe(
      "Mijoz qarzidan ko'p to'lasa, ortig'i uning avansi bo'lib qoladi va keyingi nasiyalarga hisoblanadi.",
    );
  });

  it.each([
    ["on", false, true],
    ["off", true, false],
  ])("turns it %s and sends only that", async (_what, before, after) => {
    const server = shop({ accept_advances: before });
    await open(server, "owner");
    const box = screen.getByRole<HTMLInputElement>("checkbox", { name: LABEL });
    expect(box.checked).toBe(before);
    fireEvent.click(box);
    fireEvent.click(save());
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("Nasiya sozlamalari saqlandi."));
    expect(server.writes()).toHaveLength(1);
    expect(server.writes()[0]).toMatchObject({ method: "PATCH", path: `${SHOP_BASE}/credit-settings` });
    expect(server.writes()[0]?.body).toEqual({ accept_advances: after });
    expect(screen.getByRole<HTMLInputElement>("checkbox", { name: LABEL }).checked).toBe(after);
  });

  it("shows the server's words when it cannot be turned off while an advance stands", async () => {
    const STAND = "Oldindan to'lovni o'chirib bo'lmaydi: ayrim mijozlarning avansi turibdi.";
    const server = shop({ accept_advances: true }, () => refusal(409, "ADVANCES_STAND", STAND));
    await open(server, "owner");
    fireEvent.click(screen.getByRole("checkbox", { name: LABEL }));
    fireEvent.click(save());
    expect((await screen.findByRole("alert")).textContent).toBe(STAND);
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("does not send the setting when the owner changed something else", async () => {
    const server = shop({ accept_advances: true });
    await open(server, "owner");
    type(screen.getByLabelText("Do'konning umumiy limiti, so'm"), "500000");
    fireEvent.click(save());
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]?.body).toEqual({ default_credit_limit: 500000 });
  });

  it.each(["manager", "seller"] as const)("gives a %s no switch, and tells them of it only while it is on", async (role) => {
    await open(shop({ accept_advances: true }), role);
    expect(screen.queryByRole("checkbox", { name: LABEL })).toBeNull();
    expect(screen.getByText(STATE)).toBeTruthy();
  });

  it.each(["manager", "seller"] as const)("shows a %s of a shop that does not accept advances nothing of them", async (role) => {
    await open(shop(), role);
    expect(screen.queryByRole("checkbox", { name: LABEL })).toBeNull();
    expect(screen.queryByText(STATE)).toBeNull();
    expect(document.body.textContent).not.toMatch(/avans/i);
  });

  it("never sends the setting from a manager's form, whatever else is saved", async () => {
    const server = shop({ accept_advances: true });
    await open(server, "manager");
    fireEvent.click(screen.getByRole("checkbox", { name: "Sotuvchilar limitdan oshirib nasiya yoza oladi (ogohlantirish bilan)" }));
    fireEvent.click(save());
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]?.body).toEqual({ sellers_may_exceed: true });
  });

  it("reads a shop whose answer does not name the setting as one that does not accept advances", async () => {
    const server = fakeServer(() => ok(creditSettingsBody()));
    await open(server, "owner");
    expect(screen.getByRole<HTMLInputElement>("checkbox", { name: LABEL }).checked).toBe(false);
  });
});

describe("a balance below zero is said in words", () => {
  const within$ = (uzs: number, usd: number | undefined, language: Language = "uz", mine = false) =>
    render(
      <I18nProvider initialLanguage={language}>
        <BalanceLine label="Qarz:" uzs={uzs} usd={usd} mine={mine} />
      </I18nProvider>,
    ).container;

  it.each([
    ["uz", `Haqdor: 15${NBSP}000 so'm`],
    ["uz-Cyrl", `Ҳақдор: 15${NBSP}000 сўм`],
    ["ru", `Предоплата: 15${NBSP}000 сум`],
    ["tg", `Пешпардохт: 15${NBSP}000 сӯм`],
    ["kaa", `Aldınnan tólem: 15${NBSP}000 swm`],
    ["en", `In credit: 15${NBSP}000 soum`],
  ] as const)("in %s, for the shop", (language, text) => {
    const line = within$(-15000, undefined, language);
    expect(line.textContent).toBe(text);
    expect(line.textContent).not.toMatch(/[-−]/);
  });

  it.each([
    ["uz", `Siz haqdorsiz: 15${NBSP}000 so'm`],
    ["uz-Cyrl", `Сиз ҳақдорсиз: 15${NBSP}000 сўм`],
    ["ru", `У вас предоплата: 15${NBSP}000 сум`],
    ["tg", `Шумо пешпардохт доред: 15${NBSP}000 сӯм`],
    ["kaa", `Sizde aldınnan tólem bar: 15${NBSP}000 swm`],
    ["en", `You are in credit by 15${NBSP}000 soum`],
  ] as const)("in %s, for the customer", (language, text) => {
    const line = within$(-15000, undefined, language, true);
    expect(line.textContent).toBe(text);
    expect(line.textContent).not.toMatch(/[-−]/);
  });

  it("is not told by a color or a class: the markup of the line is the one a debt has", () => {
    const debt = markup(within$(15000, undefined));
    const advance = markup(within$(-15000, undefined));
    expect(debt).toBe(`<p class="balance"><span>Qarz:</span> <strong>15${NBSP}000 so'm</strong></p>`);
    expect(advance).toBe(`<p class="balance"><strong>Haqdor: 15${NBSP}000 so'm</strong></p>`);
  });

  it.each([
    ["a so'm debt and a dollar advance", 15000, -2500, `Qarz: 15${NBSP}000 so'm · Haqdor: 25.00${NBSP}$`],
    ["a so'm advance and a dollar debt", -15000, 2500, `Qarz: 25.00${NBSP}$ · Haqdor: 15${NBSP}000 so'm`],
  ])("says %s as two parts, the debt first and each with its own label", (_what, uzs, usd, text) => {
    const line = within$(uzs, usd);
    expect(line.textContent).toBe(text);
    expect(line.textContent).not.toMatch(/Qarz: Haqdor|[-−]/);
    // The same two parts where there is no line around them: a row of a list.
    const row = render(
      <I18nProvider initialLanguage="uz">
        <Money uzs={uzs} usd={usd} />
      </I18nProvider>,
    ).container;
    expect(row.textContent).toBe(text);
  });

  it.each([
    [15000, -2500, `Qarzingiz: 15${NBSP}000 so'm · Siz haqdorsiz: 25.00${NBSP}$`],
    [-15000, 2500, `Qarzingiz: 25.00${NBSP}$ · Siz haqdorsiz: 15${NBSP}000 so'm`],
  ])("says the same mix (%d, %d) to the customer in their own words", (uzs, usd, text) => {
    const row = render(
      <I18nProvider initialLanguage="uz">
        <Money uzs={uzs} usd={usd} mine />
      </I18nProvider>,
    ).container;
    expect(row.textContent).toBe(text);
  });

  it("names the debt of a mix by the line's own label", () => {
    const line = render(
      <I18nProvider initialLanguage="uz">
        <BalanceLine label="Yangi qarz:" uzs={-15000} usd={2500} />
      </I18nProvider>,
    ).container;
    expect(line.textContent).toBe(`Yangi qarz: 25.00${NBSP}$ · Haqdor: 15${NBSP}000 so'm`);
  });

  it("keeps the two currencies apart where nothing is owed in one: no label of a debt, no sum", () => {
    expect(within$(0, -2500).textContent).toBe(`0 so'm Haqdor: 25.00${NBSP}$`);
    expect(within$(-15000, -2500).textContent).toBe(`Haqdor: 15${NBSP}000 so'm Haqdor: 25.00${NBSP}$`);
  });

  it.each([
    [1, -1, true],
    [-1, 1, true],
    [1, 1, false],
    [-1, -1, false],
    [0, -1, false],
    [-1, 0, false],
    [1, undefined, false],
    [-1, undefined, false],
  ])("isMixed(%d, %s) is %s", (uzs, usd, expected) => {
    expect(isMixed(uzs, usd)).toBe(expected);
  });

  it("writes a debt and a balance of nothing exactly as before", () => {
    const text = (uzs: number, usd?: number) =>
      markup(
        render(
          <I18nProvider initialLanguage="uz">
            <Money uzs={uzs} usd={usd} />
          </I18nProvider>,
        ).container,
      );
    expect(text(120000)).toBe(`120${NBSP}000 so'm`);
    expect(text(0)).toBe("0 so'm");
    expect(text(120000, 0)).toBe(`<span class="money">120${NBSP}000 so'm</span> <span class="money">0.00${NBSP}$</span>`);
  });

  it.each([
    [-1, undefined, true],
    [0, -1, true],
    [-1, -1, true],
    [0, undefined, false],
    [0, 0, false],
    [1, undefined, false],
    // Something is still owed in the other currency: "Debt:" stays in front.
    [-1, 1, false],
    [1, -1, false],
  ])("inCredit(%d, %s) is %s", (uzs, usd, expected) => {
    expect(inCredit(uzs, usd)).toBe(expected);
  });
});

describe("a customer in credit on the shop's screens", () => {
  function customerPage(detail: Record<string, unknown>) {
    const server = fakeServer((sent) => {
      if (sent.path.endsWith("/credit-settings")) {
        return ok(creditSettingsBody());
      }
      return sent.path.endsWith("/link") ? ok(linkBody()) : ok(detailBody(detail));
    });
    renderScreen(<CustomerScreen customerId={CUSTOMER_ID} />, { fetch: server.fetch, role: "manager" });
    return screen.findByRole("heading", { level: 2, name: "Ali Valiyev" });
  }

  it("is said on the customer's page, with no overdue line and no minus", async () => {
    await customerPage({ balance: -30000 });
    const line = document.querySelector(".balance--large");
    expect(line?.textContent).toBe(`Haqdor: 30${NBSP}000 so'm`);
    expect(document.querySelector(".row__warning")).toBeNull();
    expect(document.body.textContent).not.toMatch(/[-−]\s?30\s?000/);
  });

  it("leaves the page of a customer who owes as it was", async () => {
    await customerPage({});
    expect(markup(document.querySelector(".balance--large"))).toBe(`<span>Qarz:</span> <strong>120${NBSP}000 so'm</strong>`);
  });

  it("is said in the customer book", async () => {
    const VALI = customerBody({ id: "44444444-4444-4444-8444-444444444444", display_name: "Vali", phone: null, balance: -30000 });
    const server = fakeServer(() => ok({ items: [customerBody(), VALI], next_cursor: null }));
    renderScreen(<CustomersScreen />, { fetch: server.fetch });
    await screen.findByText("Vali");
    const [first, second] = within(screen.getByRole("list")).getAllByRole("listitem");
    expect(first?.textContent).toBe(`Ali Valiyev120${NBSP}000 so'm+998901234567`);
    expect(second?.textContent).toBe(`ValiHaqdor: 30${NBSP}000 so'm`);
  });

  it("offers a payment from the list only to someone who owes: there is nothing to pay off an advance", async () => {
    const VALI = customerBody({ id: "44444444-4444-4444-8444-444444444444", display_name: "Vali", balance: -30000 });
    const server = fakeServer(() => ok({ items: [VALI], next_cursor: null }));
    renderScreen(<CustomersScreen pick />, { fetch: server.fetch });
    await screen.findByText("Vali");
    expect(screen.queryByRole("link", { name: "To'lov" })).toBeNull();
  });
});

describe("the overview of a shop that holds advances", () => {
  const TOTALS = { outstanding: 1250000, debtors: 5, overdue: { amount: 320000, customers: 2 }, due_today: 45000 };
  const ALI = { ...customerBody(), overdue: NO_OVERDUE };
  const VALI = {
    ...customerBody({ id: "44444444-4444-4444-8444-444444444444", display_name: "Vali", balance: -30000 }),
    overdue: NO_OVERDUE,
  };
  const figures = () =>
    screen.getAllByRole("term").map((term) => [
      term.textContent,
      ...Array.from(term.parentElement?.querySelectorAll("dd") ?? [], (value) => value.textContent),
    ]);

  function shop(totals: Record<string, unknown>) {
    return fakeServer((sent) => {
      if (sent.path.endsWith("/overview")) {
        return ok(totals);
      }
      return ok({ items: sent.query["in_credit"] === "true" ? [VALI] : [ALI], next_cursor: null });
    });
  }

  it("shows what it holds beside what it is owed, and takes nothing from the debt", async () => {
    const server = shop({ ...TOTALS, advances: { amount: 30000, customers: 1 } });
    renderScreen(<OverviewScreen />, { fetch: server.fetch });
    await screen.findByText("Mijozlarning avanslari");
    expect(figures()).toEqual([
      ["Jami qarz", `1${NBSP}250${NBSP}000 so'm`, "5 ta mijoz"],
      ["Muddati o'tgan", `320${NBSP}000 so'm`, "2 ta mijoz"],
      ["Bugun to'lanishi kerak", `45${NBSP}000 so'm`],
      ["Mijozlarning avanslari", `30${NBSP}000 so'm`, "1 ta mijoz"],
    ]);
  });

  it("lists the customers in credit when asked, with their advance in words", async () => {
    const server = shop({ ...TOTALS, advances: { amount: 30000, customers: 1 } });
    renderScreen(<OverviewScreen />, { fetch: server.fetch });
    const tab = await screen.findByRole("button", { name: "Haqdorlar" });
    expect(tab.getAttribute("aria-pressed")).toBe("false");
    await screen.findByText("Ali Valiyev");

    fireEvent.click(tab);
    await screen.findByText("Vali");
    expect(screen.queryByText("Ali Valiyev")).toBeNull();
    expect(tab.getAttribute("aria-pressed")).toBe("true");
    expect(button("Hammasi").getAttribute("aria-pressed")).toBe("false");
    const asked = server.sent.filter((sent) => sent.path.endsWith("/overview/debtors")).map((sent) => sent.query);
    expect(asked).toEqual([{ overdue: "false" }, { overdue: "false", in_credit: "true" }]);
    const [row] = within(screen.getByRole("list")).getAllByRole("listitem");
    expect(row?.textContent).toBe(`ValiHaqdor: 30${NBSP}000 so'm`);

    fireEvent.click(button("Hammasi"));
    await screen.findByText("Ali Valiyev");
    expect(server.sent.at(-1)?.query).toEqual({ overdue: "false" });
  });

  it("shows the dollar advances in the dollar book, and offers the list for them too", async () => {
    const usd = { outstanding: 12000, debtors: 1, overdue: { amount: 0, customers: 0 }, due_today: 0, advances: { amount: 500, customers: 1 } };
    const server = shop({ ...TOTALS, usd });
    renderScreen(<OverviewScreen />, { fetch: server.fetch });
    await screen.findByText("Mijozlarning avanslari, dollarda");
    expect(figures().at(-1)).toEqual(["Mijozlarning avanslari, dollarda", `5.00${NBSP}$`, "1 ta mijoz"]);
    expect(screen.queryByText("Mijozlarning avanslari")).toBeNull();
    fireEvent.click(await screen.findByRole("button", { name: "Haqdorlar" }));
    fireEvent.click(button("$"));
    await waitFor(() => expect(server.sent.at(-1)?.query).toEqual({ overdue: "false", currency: "USD", in_credit: "true" }));
  });

  it("is the screen it always was where no advance is held: no figure, no tab, no such request", async () => {
    const server = shop(TOTALS);
    renderScreen(<OverviewScreen />, { fetch: server.fetch });
    await screen.findByText("Ali Valiyev");
    expect(figures()).toHaveLength(3);
    expect(screen.queryByRole("button", { name: "Haqdorlar" })).toBeNull();
    expect(screen.getByRole("group", { name: "Qarzdorlar ro'yxati filtri" }).innerHTML).toBe(
      '<button type="button" class="toggle__option" aria-pressed="true">Hammasi</button>' +
        '<button type="button" class="toggle__option" aria-pressed="false">Muddati o\'tganlar</button>',
    );
    expect(document.body.textContent).not.toMatch(/avans|Haqdor/i);
    expect(server.sent.every((sent) => !("in_credit" in sent.query))).toBe(true);
  });

  it("refuses totals whose advance is not a whole amount, rather than show them", async () => {
    const server = shop({ ...TOTALS, advances: { amount: 30000.5, customers: 1 } });
    renderScreen(<OverviewScreen />, { fetch: server.fetch });
    await screen.findByText("Ali Valiyev");
    expect(screen.queryByText("Mijozlarning avanslari")).toBeNull();
    expect(screen.queryByText("Jami qarz")).toBeNull();
    expect(screen.getAllByRole("alert").length).toBeGreaterThan(0);
  });
});

// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import {
  creditSettingsBody,
  CUSTOMER_ID,
  customerBody,
  deferred,
  detailBody,
  fakeServer,
  linkBody,
  ok,
  refusal,
  type Reply,
  type Sent,
  SHOP_BASE,
} from "../../testing/fakeServer";
import { renderScreen } from "../../testing/renderScreen";
import type { Role } from "../navigation";
import { CustomerScreen } from "./CustomerScreen";

afterEach(cleanup);

type Options = {
  detail?: Record<string, unknown>;
  credit?: () => Reply;
  onWrite?: (sent: Sent, attempt: number) => Reply;
};

/** One customer who owes 120 000; a PATCH of the customer is kept, so the next read shows it. */
function shop(options: Options = {}) {
  let detail: Record<string, unknown> = detailBody(options.detail);
  let attempt = 0;
  return fakeServer((sent) => {
    if (sent.method !== "GET") {
      if (options.onWrite) {
        return options.onWrite(sent, attempt++);
      }
      detail = { ...detail, ...(sent.body as Record<string, unknown>) };
      return ok(customerBody(detail));
    }
    if (sent.path.endsWith("/credit-settings")) {
      return options.credit ? options.credit() : ok(creditSettingsBody());
    }
    return sent.path.endsWith("/link") ? ok(linkBody()) : ok(detail);
  });
}

async function open(server: ReturnType<typeof fakeServer>, role: Role = "manager") {
  renderScreen(<CustomerScreen customerId={CUSTOMER_ID} />, { fetch: server.fetch, role });
  await screen.findByRole("heading", { level: 2, name: "Ali Valiyev" });
  await waitFor(() => expect(screen.queryByText("Yuklanmoqda…")).toBeNull());
}

const type = (input: HTMLElement, value: string) => fireEvent.change(input, { target: { value } });
const reminders = (server: ReturnType<typeof fakeServer>) => server.writes().filter((sent) => sent.path.endsWith("/reminders/manual"));
const remind = () => screen.getByRole<HTMLButtonElement>("button", { name: "Eslatma yuborish" });
const creditSection = () => screen.getByRole("region", { name: "Nasiya limiti" });
const historySection = () => screen.getByRole("region", { name: "To'lov tarixi" });

describe("sending a reminder by hand (REQ-025)", () => {
  it.each([
    ["telegram", "Eslatma Telegram orqali yuborildi: 45\u00a0000 so'm."],
    ["sms", "Eslatma SMS orqali yuborildi: 45\u00a0000 so'm."],
    ["pochta", "Eslatma yuborildi: 45\u00a0000 so'm."],
  ])("posts one keyed reminder and says it went out through %s", async (channel, message) => {
    const server = shop({ onWrite: () => ok({ sent: true, channel, amount: 45000 }, 201) });
    await open(server);
    fireEvent.click(remind());
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe(message));
    expect(reminders(server)).toHaveLength(1);
    expect(reminders(server)[0]).toMatchObject({ method: "POST", path: `${SHOP_BASE}/reminders/manual` });
    expect(reminders(server)[0]?.body).toEqual({ customer_id: CUSTOMER_ID });
    expect(reminders(server)[0]?.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
    // One a day: once it went out there is nothing left to press.
    expect(screen.queryByRole("button", { name: "Eslatma yuborish" })).toBeNull();
  });

  it.each([
    [409, "REMINDER_LIMIT_REACHED", "Bu mijozga bugun eslatma allaqachon yuborilgan. Kuniga bitta mumkin."],
    [409, "REMINDER_NOT_DUE", "Bu mijozda muddati o'tgan yoki bugun to'lanadigan qarz yo'q."],
    [409, "REMINDERS_OFF", "Eslatmalar do'kon yoki shu mijoz uchun o'chirilgan."],
    [409, "CUSTOMER_UNREACHABLE", "Bu mijozga yetib bo'lmaydi: Telegram ulanmagan, SMS esa o'chiq yoki raqam yo'q."],
    [403, "SHOP_SUSPENDED", "Do'kon to'xtatilgan. Faqat do'kon egasi ma'lumotlarni ko'ra oladi va eksport qila oladi."],
  ])("shows the server's refusal %i %s as it came", async (status, code, message) => {
    const server = shop({ onWrite: () => refusal(status, code, message) });
    await open(server);
    fireEvent.click(remind());
    expect((await screen.findByRole("alert")).textContent).toBe(message);
    expect(screen.queryByText(/yuborildi/)).toBeNull();
    expect(remind().disabled).toBe(false);
  });

  it("sends one reminder for a double tap, and the same key when it is retried", async () => {
    const answer = deferred<{ status: number; body: unknown }>();
    const server = shop({ onWrite: (_sent, attempt) => (attempt === 0 ? "offline" : answer.promise) });
    await open(server);
    fireEvent.click(remind());
    expect((await screen.findByRole("alert")).textContent).toContain("Serverga ulanib bo'lmadi.");

    const button = remind();
    fireEvent.click(button);
    fireEvent.click(button);
    fireEvent.click(button);
    expect(screen.getByRole<HTMLButtonElement>("button", { name: "Yuborilmoqda…" }).disabled).toBe(true);
    expect(reminders(server)).toHaveLength(2);
    answer.resolve(ok({ sent: true, channel: "telegram", amount: 45000 }, 201));
    await screen.findByRole("status");
    expect(reminders(server)).toHaveLength(2);
    expect(reminders(server)[1]?.headers["Idempotency-Key"]).toBe(reminders(server)[0]?.headers["Idempotency-Key"]);
  });

  it("is offered to a manager and an owner", async () => {
    for (const role of ["manager", "owner"] as const) {
      await open(shop(), role);
      expect(remind()).toBeTruthy();
      cleanup();
    }
  });

  it("is not offered for an archived customer, nor while the customer is being edited", async () => {
    await open(shop({ detail: { status: "archived", balance: 0 } }));
    expect(screen.queryByRole("button", { name: "Eslatma yuborish" })).toBeNull();
    cleanup();

    await open(shop());
    fireEvent.click(screen.getByRole("button", { name: "Tahrirlash" }));
    expect(screen.queryByRole("button", { name: "Eslatma yuborish" })).toBeNull();
  });
});

describe("what a seller is offered on the customer page (REQ-033)", () => {
  it("no reminder, no reminders switch and no way to set a limit", async () => {
    const server = shop({ detail: { credit_limit: 300000, reminders_off: true } });
    await open(server, "seller");
    expect(screen.queryByRole("button", { name: "Eslatma yuborish" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Tahrirlash" })).toBeNull();
    expect(screen.queryByLabelText("Bu mijozga eslatma yuborilmasin")).toBeNull();
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(within(creditSection()).queryByRole("button")).toBeNull();
    expect(screen.queryByLabelText("Mijoz limiti, so'm")).toBeNull();
    // What a seller may know is still shown: reminders are off, and which limit applies.
    expect(screen.getByText("Eslatmalar o'chirilgan")).toBeTruthy();
    expect(creditSection().textContent).toContain("300\u00a0000 so'm");
    expect(server.writes()).toHaveLength(0);
  });

  it("gives a manager the reminders switch, which sends only that", async () => {
    const server = shop();
    await open(server);
    fireEvent.click(screen.getByRole("button", { name: "Tahrirlash" }));
    fireEvent.click(screen.getByLabelText("Bu mijozga eslatma yuborilmasin"));
    fireEvent.click(screen.getByRole("button", { name: "Saqlash" }));
    expect(await screen.findByText("Eslatmalar o'chirilgan")).toBeTruthy();
    expect(server.writes()[0]).toMatchObject({ method: "PATCH", path: `${SHOP_BASE}/customers/${CUSTOMER_ID}` });
    expect(server.writes()[0]?.body).toEqual({ reminders_off: true });
  });
});

describe("the credit limit that applies (REQ-044)", () => {
  const limitText = () => creditSection().querySelector("p")?.textContent;

  it.each([
    ["the customer's own", 300000, 500000, "Limit: 300\u00a0000 so'm (shu mijoz uchun belgilangan)."],
    ["the customer's own when the shop has none", 300000, null, "Limit: 300\u00a0000 so'm (shu mijoz uchun belgilangan)."],
    ["the shop's default when the customer has none", null, 500000, "Limit: 500\u00a0000 so'm (do'konning umumiy limiti)."],
    ["none", null, null, "Limit belgilanmagan."],
  ])("shows %s to every role", async (_what, own, shopDefault, text) => {
    for (const role of ["seller", "manager", "owner"] as const) {
      const server = shop({ detail: { credit_limit: own }, credit: () => ok(creditSettingsBody({ default_credit_limit: shopDefault })) });
      await open(server, role);
      expect(limitText()).toBe(text);
      expect(server.sent.some((sent) => sent.method === "GET" && sent.path === `${SHOP_BASE}/credit-settings`)).toBe(true);
      cleanup();
    }
  });

  const STOPPED = "Limitdan oshadigan nasiyani faqat menejer yoki do'kon egasi yoza oladi.";

  it("tells a seller they are stopped at the limit only when the shop stops sellers and there is a limit", async () => {
    await open(shop({ detail: { credit_limit: 300000 } }), "seller");
    expect(within(creditSection()).getByText(STOPPED)).toBeTruthy();
    cleanup();

    await open(shop({ detail: { credit_limit: 300000 }, credit: () => ok(creditSettingsBody({ sellers_may_exceed: true })) }), "seller");
    expect(screen.queryByText(STOPPED)).toBeNull();
    cleanup();

    await open(shop(), "seller");
    expect(screen.queryByText(STOPPED)).toBeNull();
    cleanup();

    await open(shop({ detail: { credit_limit: 300000 } }), "manager");
    expect(screen.queryByText(STOPPED)).toBeNull();
  });

  it.each(["manager", "owner"] as const)("lets a %s set the customer's own limit", async (role) => {
    const server = shop({ credit: () => ok(creditSettingsBody({ default_credit_limit: 500000 })) });
    await open(server, role);
    fireEvent.click(screen.getByRole("button", { name: "Mijozga limit belgilash" }));
    type(screen.getByLabelText("Mijoz limiti, so'm"), "250 000");
    fireEvent.click(screen.getByRole("button", { name: "Limitni saqlash" }));
    await waitFor(() => expect(limitText()).toBe("Limit: 250\u00a0000 so'm (shu mijoz uchun belgilangan)."));
    expect(server.writes()).toHaveLength(1);
    expect(server.writes()[0]).toMatchObject({ method: "PATCH", path: `${SHOP_BASE}/customers/${CUSTOMER_ID}` });
    expect(server.writes()[0]?.body).toEqual({ credit_limit: 250000 });
    expect(server.writes()[0]?.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
  });

  it("changes a limit, and removes it with null so the shop's default applies again", async () => {
    const server = shop({ detail: { credit_limit: 300000 }, credit: () => ok(creditSettingsBody({ default_credit_limit: 500000 })) });
    await open(server);
    fireEvent.click(screen.getByRole("button", { name: "Mijoz limitini o'zgartirish" }));
    const field = screen.getByLabelText<HTMLInputElement>("Mijoz limiti, so'm");
    expect(field.value).toBe("300\u00a0000");
    type(field, "400000");
    fireEvent.click(screen.getByRole("button", { name: "Limitni saqlash" }));
    await waitFor(() => expect(limitText()).toBe("Limit: 400\u00a0000 so'm (shu mijoz uchun belgilangan)."));
    expect(server.writes()[0]?.body).toEqual({ credit_limit: 400000 });

    fireEvent.click(screen.getByRole("button", { name: "Mijoz limitini o'zgartirish" }));
    fireEvent.click(screen.getByRole("button", { name: "Mijoz limitini olib tashlash" }));
    await waitFor(() => expect(limitText()).toBe("Limit: 500\u00a0000 so'm (do'konning umumiy limiti)."));
    expect(server.writes()[1]?.body).toEqual({ credit_limit: null });
    expect(server.writes()[1]?.headers["Idempotency-Key"]).not.toBe(server.writes()[0]?.headers["Idempotency-Key"]);
  });

  it("offers no removal when the customer has no limit of their own, and sends nothing for an unchanged limit", async () => {
    const server = shop({ credit: () => ok(creditSettingsBody({ default_credit_limit: 500000 })) });
    await open(server);
    fireEvent.click(screen.getByRole("button", { name: "Mijozga limit belgilash" }));
    expect(screen.queryByRole("button", { name: "Mijoz limitini olib tashlash" })).toBeNull();
    cleanup();

    const same = shop({ detail: { credit_limit: 300000 } });
    await open(same);
    fireEvent.click(screen.getByRole("button", { name: "Mijoz limitini o'zgartirish" }));
    fireEvent.click(screen.getByRole("button", { name: "Limitni saqlash" }));
    expect(screen.queryByLabelText("Mijoz limiti, so'm")).toBeNull();
    expect(same.writes()).toHaveLength(0);
  });

  it.each([
    ["", "Limitni kiriting."],
    ["999", "Limit 1\u00a0000 so'mdan 10\u00a0000\u00a0000\u00a0000 so'mgacha bo'lishi kerak."],
    ["10000000001", "Limit 1\u00a0000 so'mdan 10\u00a0000\u00a0000\u00a0000 so'mgacha bo'lishi kerak."],
    ["250000.5", "Limit butun so'mda bo'lishi kerak, masalan 500000."],
    ["ko'p", "Limit butun so'mda bo'lishi kerak, masalan 500000."],
  ])("refuses the limit %j without calling the server", async (typed, message) => {
    const server = shop();
    await open(server);
    fireEvent.click(screen.getByRole("button", { name: "Mijozga limit belgilash" }));
    type(screen.getByLabelText("Mijoz limiti, so'm"), typed);
    fireEvent.click(screen.getByRole("button", { name: "Limitni saqlash" }));
    expect(within(creditSection()).getByRole("alert").textContent).toBe(message);
    expect(server.writes()).toHaveLength(0);
  });

  it.each([
    ["1000", 1000],
    ["10000000000", 10000000000],
  ])("accepts the bound %s", async (typed, amount) => {
    const server = shop();
    await open(server);
    fireEvent.click(screen.getByRole("button", { name: "Mijozga limit belgilash" }));
    type(screen.getByLabelText("Mijoz limiti, so'm"), typed);
    fireEvent.click(screen.getByRole("button", { name: "Limitni saqlash" }));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]?.body).toEqual({ credit_limit: amount });
  });

  it("shows the server's refusal, and a refused limit next to the field", async () => {
    const server = shop({
      onWrite: (_sent, attempt) =>
        attempt === 0
          ? refusal(403, "FORBIDDEN_ROLE", "Bu amal uchun sizning rolingiz yetarli emas.")
          : refusal(422, "VALIDATION", "Ma'lumotlar noto'g'ri kiritilgan.", { credit_limit: "x" }),
    });
    await open(server);
    fireEvent.click(screen.getByRole("button", { name: "Mijozga limit belgilash" }));
    type(screen.getByLabelText("Mijoz limiti, so'm"), "250000");
    fireEvent.click(screen.getByRole("button", { name: "Limitni saqlash" }));
    expect((await within(creditSection()).findByRole("alert")).textContent).toBe("Bu amal uchun sizning rolingiz yetarli emas.");

    type(screen.getByLabelText("Mijoz limiti, so'm"), "260000");
    fireEvent.click(screen.getByRole("button", { name: "Limitni saqlash" }));
    await waitFor(() => expect(within(creditSection()).getAllByRole("alert")).toHaveLength(2));
    expect(within(creditSection()).getAllByRole("alert").map((alert) => alert.textContent)).toEqual([
      "Ma'lumotlar noto'g'ri kiritilgan.",
      "Limit 1\u00a0000 so'mdan 10\u00a0000\u00a0000\u00a0000 so'mgacha bo'lishi kerak.",
    ]);
  });

  it("sends one request for a double tap, and the same key when it is retried", async () => {
    const answer = deferred<{ status: number; body: unknown }>();
    const server = shop({ onWrite: (_sent, attempt) => (attempt === 0 ? "offline" : answer.promise) });
    await open(server);
    fireEvent.click(screen.getByRole("button", { name: "Mijozga limit belgilash" }));
    type(screen.getByLabelText("Mijoz limiti, so'm"), "250000");
    fireEvent.click(screen.getByRole("button", { name: "Limitni saqlash" }));
    expect((await within(creditSection()).findByRole("alert")).textContent).toContain("Serverga ulanib bo'lmadi.");

    const button = screen.getByRole("button", { name: "Limitni saqlash" });
    fireEvent.click(button);
    fireEvent.click(button);
    fireEvent.submit(button.closest("form") as HTMLFormElement);
    expect(screen.getByRole<HTMLButtonElement>("button", { name: "Saqlanmoqda…" }).disabled).toBe(true);
    expect(server.writes()).toHaveLength(2);
    expect(server.writes()[1]?.headers["Idempotency-Key"]).toBe(server.writes()[0]?.headers["Idempotency-Key"]);
    expect(server.writes()[1]?.body).toEqual({ credit_limit: 250000 });
    answer.resolve(ok(customerBody({ credit_limit: 250000 })));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Saqlanmoqda…" })).toBeNull());
    expect(server.writes()).toHaveLength(2);
  });

  it("shows the failure of the shop's rules in its own section and keeps the rest of the page", async () => {
    let attempt = 0;
    const server = shop({
      detail: { credit_limit: 300000 },
      credit: () => (attempt++ === 0 ? refusal(403, "SHOP_SUSPENDED", "Do'kon to'xtatilgan.") : ok(creditSettingsBody())),
    });
    await open(server);
    expect(within(creditSection()).getByRole("alert").querySelector("p")?.textContent).toBe("Do'kon to'xtatilgan.");
    expect(screen.getByRole("region", { name: "Yozuvlar" })).toBeTruthy();
    fireEvent.click(within(creditSection()).getByRole("button", { name: "Qayta urinish" }));
    await waitFor(() => expect(creditSection().textContent).toContain("300\u00a0000 so'm"));
  });
});

describe("payment history on the staff customer page (REQ-045)", () => {
  const HISTORY = { on_time_percent: 67, on_time_amount: 200000, due_amount: 300000, longest_delay_days: 9 };

  it.each(["seller", "manager", "owner"] as const)("shows a %s the share repaid on time and the longest delay", async (role) => {
    await open(shop({ detail: { payment_history: HISTORY } }), role);
    expect(Array.from(historySection().querySelectorAll("p"), (node) => node.textContent)).toEqual([
      "O'z vaqtida to'langan: 67%. Eng uzoq kechikish: 9 kun.",
      "Muddati kelgan 300\u00a0000 so'm dan 200\u00a0000 so'm va'da qilingan kungacha to'langan.",
      "Faqat shu do'kondagi yozuvlar bo'yicha hisoblangan.",
    ]);
  });

  it("says there was no delay when there was none", async () => {
    await open(shop({ detail: { payment_history: { ...HISTORY, on_time_percent: 100, on_time_amount: 300000, longest_delay_days: 0 } } }));
    expect(historySection().textContent).toContain("O'z vaqtida to'langan: 100%. Kechikish bo'lmagan.");
    expect(historySection().textContent).not.toContain("Eng uzoq kechikish");
  });

  it("says that nothing has fallen due yet when there is no history", async () => {
    await open(shop({ detail: { payment_history: null } }));
    expect(Array.from(historySection().querySelectorAll("p"), (node) => node.textContent)).toEqual([
      "Hali to'lash muddati kelgan qarz bo'lmagan.",
    ]);
    expect(screen.queryByText(/O'z vaqtida to'langan/)).toBeNull();
  });

  it("is in Russian with Russian plurals when that is the language", async () => {
    const server = shop({ detail: { payment_history: { ...HISTORY, longest_delay_days: 2 } } });
    renderScreen(<CustomerScreen customerId={CUSTOMER_ID} />, { fetch: server.fetch, role: "seller", language: "ru" });
    const section = await screen.findByRole("region", { name: "История оплат" });
    expect(section.textContent).toContain("Оплачено вовремя: 67%. Самая долгая просрочка: 2 дня.");
    expect(section.textContent).toContain("Рассчитано только по записям этого магазина.");
  });
});

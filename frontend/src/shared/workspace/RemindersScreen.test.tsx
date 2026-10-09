// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import {
  CUSTOMER_ID,
  deferred,
  fakeServer,
  ok,
  refusal,
  remindersBody,
  type Reply,
  type Sent,
  SHOP_BASE,
} from "../../testing/fakeServer";
import { renderScreen } from "../../testing/renderScreen";
import type { Language } from "../../i18n/types";
import type { Role } from "../navigation";
import { fillWording, hourChoices, RemindersScreen } from "./RemindersScreen";

afterEach(cleanup);

const VALI = "44444444-4444-4444-8444-444444444444";
const UNREACHABLE = [
  { customer_id: CUSTOMER_ID, display_name: "Ali Valiyev", phone: "+998901234567", amount: 45000 },
  { customer_id: VALI, display_name: "Vali", phone: null, amount: 120000 },
];

type Options = {
  settings?: Record<string, unknown>;
  unreachable?: () => Reply;
  onWrite?: (sent: Sent, attempt: number) => Reply;
  read?: () => Reply | null;
};

/** A shop whose reminders are off, at ten, with the first wording; a write answers the settings after it. */
function shop(options: Options = {}) {
  let current: Record<string, unknown> = remindersBody(options.settings);
  let attempt = 0;
  return fakeServer((sent) => {
    if (sent.path.endsWith("/unreachable")) {
      return options.unreachable ? options.unreachable() : ok({ items: [] });
    }
    if (sent.method === "GET") {
      return options.read?.() ?? ok(current);
    }
    if (options.onWrite) {
      return options.onWrite(sent, attempt++);
    }
    current = { ...current, ...(sent.body as Record<string, unknown>) };
    return ok(current);
  });
}

async function open(server: ReturnType<typeof fakeServer>, role: Role = "manager", language: Language = "uz", shopName = "Ziyo market") {
  renderScreen(<RemindersScreen />, { fetch: server.fetch, role, language, shopName });
  await screen.findByRole("button", { name: language === "uz" ? "Saqlash" : "Сохранить" });
  await waitFor(() => expect(screen.queryByText(language === "uz" ? "Yuklanmoqda…" : "Загрузка…")).toBeNull());
}

const on = () => screen.getByLabelText<HTMLInputElement>("Avtomatik eslatmalar yoqilgan");
const hour = () => screen.getByLabelText<HTMLSelectElement>("Yuborish vaqti (Toshkent vaqti)");
const wording = (number: number) => screen.getByLabelText<HTMLInputElement>(`${number}-matn`);
const sms = () => screen.getByLabelText<HTMLInputElement>("SMS orqali ham yuborish");
const save = () => screen.getByRole<HTMLButtonElement>("button", { name: "Saqlash" });
const wordings = () => Array.from(document.querySelectorAll(".wording"), (node) => node.textContent ?? "");

describe("reminder settings (REQ-042)", () => {
  it.each(["manager", "owner"] as const)("loads the settings and the unreachable list for a %s", async (role) => {
    const server = shop();
    renderScreen(<RemindersScreen />, { fetch: server.fetch, role });
    expect(screen.getAllByRole("status").map((status) => status.textContent)).toEqual(["Yuklanmoqda…", "Yuklanmoqda…"]);
    await screen.findByRole("button", { name: "Saqlash" });
    expect(server.sent.map((sent) => `${sent.method} ${sent.path}`).sort()).toEqual([
      `GET ${SHOP_BASE}/reminders`,
      `GET ${SHOP_BASE}/reminders/unreachable`,
    ]);
    expect(on().checked).toBe(false);
    expect(hour().value).toBe("10");
    expect(sms().checked).toBe(false);
    expect([1, 2, 3].map((number) => wording(number).checked)).toEqual([true, false, false]);
  });

  it("offers exactly the hours from 8 to 20 that the server allows", async () => {
    await open(shop());
    expect(Array.from(hour().options, (option) => [option.value, option.textContent])).toEqual(
      [8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20].map((value) => [String(value), `${String(value).padStart(2, "0")}:00`]),
    );
  });

  it("follows the server when its range of hours is another one", async () => {
    await open(shop({ settings: { hours: [9, 11], hour: 9 } }));
    expect(Array.from(hour().options, (option) => option.value)).toEqual(["9", "10", "11"]);
  });

  it("keeps an hour the server holds outside its range visible instead of moving it silently", async () => {
    const server = shop({ settings: { hour: 7 } });
    await open(server);
    expect(hour().value).toBe("7");
    fireEvent.click(on());
    fireEvent.click(save());
    await screen.findByRole("status");
    expect(server.writes()[0]?.body).toEqual({ on: true });
  });

  it("shows each wording in the reader's language exactly as the server sent it, with an example filled in", async () => {
    await open(shop());
    expect(wordings()).toEqual(
      [1, 2, 3].flatMap((id) => [
        `${id}: «Ziyo market»: Ali Valiyev, bugun 45\u00a0000 so'm to'lash kuni.`,
        `${id}: «Ziyo market»: Ali Valiyev, 45\u00a0000 so'm qarzning to'lash muddati o'tgan.`,
      ]),
    );
    expect(wordings().some((text) => /[{}]/.test(text))).toBe(false);
    // One wording for each moment, in the reader's language: the server's other languages are not listed.
    expect(Array.from(document.querySelectorAll(".wording"), (node) => node.getAttribute("lang"))).toEqual(
      Array.from({ length: 6 }, () => "uz"),
    );
    expect(wordings().some((text) => /\p{Script=Cyrillic}/u.test(text))).toBe(false);
    expect(screen.getAllByText("To'lash kunida")).toHaveLength(3);
    expect(screen.getAllByText("Muddati o'tganda")).toHaveLength(3);
  });

  it("names an example shop when the shop's own name is not known", async () => {
    const server = shop();
    renderScreen(<RemindersScreen />, { fetch: server.fetch, role: "owner" });
    await screen.findByRole("button", { name: "Saqlash" });
    expect(wordings()[0]).toBe("1: «Baraka savdo»: Ali Valiyev, bugun 45\u00a0000 so'm to'lash kuni.");
  });

  it("is in Russian when that is the language, with the Russian wordings", async () => {
    await open(shop(), "owner", "ru");
    expect(screen.getByRole("heading", { level: 2, name: "Настройки напоминаний" })).toBeTruthy();
    expect(wordings().slice(0, 2)).toEqual([
      "1: «Ziyo market»: Али Валиев, сегодня срок оплаты 45\u00a0000 сум.",
      "1: «Ziyo market»: Али Валиев, срок оплаты долга 45\u00a0000 сум прошёл.",
    ]);
    expect(Array.from(document.querySelectorAll(".wording"), (node) => node.getAttribute("lang"))).toEqual(
      Array.from({ length: 6 }, () => "ru"),
    );
    expect(screen.getByLabelText("Отправлять также по SMS")).toBeTruthy();
  });

  it("says plainly that SMS also depends on the platform and may be unavailable", async () => {
    await open(shop());
    expect(
      screen.getByText(
        "SMS faqat platformada ham yoqilgan bo'lsa ishlaydi va mavjud bo'lmasligi mumkin. Telegramga ulangan mijozga eslatma Telegram orqali boradi.",
      ),
    ).toBeTruthy();
  });

  it.each([
    ["switching them on", () => fireEvent.click(on()), { on: true }],
    ["the first hour", () => fireEvent.change(hour(), { target: { value: "8" } }), { hour: 8 }],
    ["the last hour", () => fireEvent.change(hour(), { target: { value: "20" } }), { hour: 20 }],
    ["another wording", () => fireEvent.click(wording(3)), { template: 3 }],
    ["the SMS switch", () => fireEvent.click(sms()), { sms_on: true }],
  ])("sends only %s when only that changed", async (_what, change, body) => {
    const server = shop();
    await open(server);
    change();
    fireEvent.click(save());
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("Eslatma sozlamalari saqlandi."));
    expect(server.writes()).toHaveLength(1);
    expect(server.writes()[0]).toMatchObject({ method: "PATCH", path: `${SHOP_BASE}/reminders` });
    expect(server.writes()[0]?.body).toEqual(body);
    expect(server.writes()[0]?.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
  });

  it("switches reminders and SMS off again, and sends everything that changed in one request", async () => {
    const server = shop({ settings: { on: true, sms_on: true, hour: 9, template: 2 } });
    await open(server);
    fireEvent.click(on());
    fireEvent.click(sms());
    fireEvent.change(hour(), { target: { value: "18" } });
    fireEvent.click(wording(1));
    fireEvent.click(save());
    await screen.findByRole("status");
    expect(server.writes()[0]?.body).toEqual({ on: false, hour: 18, template: 1, sms_on: false });
    // The form now holds what the server answered, so saving again has nothing to send.
    fireEvent.click(save());
    expect(server.writes()).toHaveLength(1);
  });

  it("sends nothing when nothing was changed, or when a change was taken back", async () => {
    const server = shop();
    await open(server);
    fireEvent.click(save());
    fireEvent.click(on());
    fireEvent.click(on());
    fireEvent.click(save());
    expect(server.writes()).toHaveLength(0);
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("takes the saved notice away once something is changed again", async () => {
    const server = shop();
    await open(server);
    fireEvent.click(on());
    fireEvent.click(save());
    await screen.findByRole("status");
    fireEvent.click(sms());
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("sends one request for a double tap and the same key on a retry", async () => {
    const answer = deferred<{ status: number; body: unknown }>();
    const server = shop({ onWrite: (_sent, attempt) => (attempt === 0 ? "offline" : answer.promise) });
    await open(server);
    fireEvent.click(on());
    fireEvent.click(save());
    expect((await screen.findByRole("alert")).textContent).toContain("Serverga ulanib bo'lmadi.");

    const button = save();
    fireEvent.click(button);
    fireEvent.click(button);
    fireEvent.submit(button.closest("form") as HTMLFormElement);
    expect(screen.getByRole<HTMLButtonElement>("button", { name: "Saqlanmoqda…" }).disabled).toBe(true);
    // While the change is on its way the switches cannot be moved under it.
    expect(on().disabled).toBe(true);
    expect(server.writes()).toHaveLength(2);
    answer.resolve(ok(remindersBody({ on: true })));
    await screen.findByRole("status");
    expect(server.writes()).toHaveLength(2);
    expect(server.writes()[1]?.headers["Idempotency-Key"]).toBe(server.writes()[0]?.headers["Idempotency-Key"]);
    expect(server.writes()[1]?.body).toEqual({ on: true });
    expect(on().checked).toBe(true);
  });

  it("uses a new key for a different change", async () => {
    const server = shop({ onWrite: (_sent, attempt) => (attempt === 0 ? "offline" : ok(remindersBody({ on: true, hour: 12 }))) });
    await open(server);
    fireEvent.click(on());
    fireEvent.click(save());
    await screen.findByRole("alert");
    fireEvent.change(hour(), { target: { value: "12" } });
    fireEvent.click(save());
    await screen.findByRole("status");
    expect(server.writes()[1]?.body).toEqual({ on: true, hour: 12 });
    expect(server.writes()[1]?.headers["Idempotency-Key"]).not.toBe(server.writes()[0]?.headers["Idempotency-Key"]);
  });

  it.each([
    [403, "SHOP_SUSPENDED", "Do'kon to'xtatilgan. Faqat do'kon egasi ma'lumotlarni ko'ra oladi va eksport qila oladi."],
    [403, "FORBIDDEN_ROLE", "Bu amal uchun sizning rolingiz yetarli emas."],
  ])("shows the server's refusal %i %s and keeps what was chosen", async (status, code, message) => {
    const server = shop({ onWrite: () => refusal(status, code, message) });
    await open(server);
    fireEvent.click(on());
    fireEvent.click(save());
    expect((await screen.findByRole("alert")).textContent).toBe(message);
    expect(screen.queryByRole("status")).toBeNull();
    expect(on().checked).toBe(true);
  });

  it("puts a refused hour and a refused wording next to their fields", async () => {
    const server = shop({
      onWrite: () => refusal(422, "VALIDATION", "Ma'lumotlar noto'g'ri kiritilgan.", { hour: "x", template: "y" }),
    });
    await open(server);
    fireEvent.change(hour(), { target: { value: "20" } });
    fireEvent.click(save());
    await waitFor(() => expect(screen.getAllByRole("alert")).toHaveLength(3));
    expect(screen.getAllByRole("alert").map((alert) => alert.textContent)).toEqual([
      "Ma'lumotlar noto'g'ri kiritilgan.",
      "Vaqt 8:00 dan 20:00 gacha bo'lishi kerak.",
      "Ro'yxatdagi matnlardan birini tanlang.",
    ]);
    expect(hour().getAttribute("aria-invalid")).toBe("true");
  });

  it("offers a retry when the settings cannot be loaded", async () => {
    let attempt = 0;
    const server = shop({ read: () => (attempt++ === 0 ? "offline" : null) });
    renderScreen(<RemindersScreen />, { fetch: server.fetch, role: "owner" });
    fireEvent.click(await screen.findByRole("button", { name: "Qayta urinish" }));
    expect(await screen.findByLabelText("Avtomatik eslatmalar yoqilgan")).toBeTruthy();
  });

  it("shows the server's message when the settings are refused", async () => {
    const server = shop({ read: () => refusal(403, "SHOP_SUSPENDED", "Do'kon to'xtatilgan.") });
    renderScreen(<RemindersScreen />, { fetch: server.fetch, role: "manager" });
    expect((await screen.findByRole("alert")).querySelector("p")?.textContent).toBe("Do'kon to'xtatilgan.");
    expect(screen.queryByRole("button", { name: "Saqlash" })).toBeNull();
  });
});

describe("customers who cannot be reached (REQ-043)", () => {
  const rows = () => within(screen.getByRole("region", { name: "Yetib bo'lmaydigan mijozlar" })).getAllByRole("listitem");

  it("lists the name, the phone and the amount, with a link to the customer's page", async () => {
    await open(shop({ unreachable: () => ok({ items: UNREACHABLE }) }));
    const [ali, vali] = rows();
    expect(ali?.textContent).toBe("Ali Valiyev45\u00a0000 so'm+998901234567");
    expect(within(ali as HTMLElement).getByRole("link").getAttribute("href")).toBe(`#/customers/${CUSTOMER_ID}`);
    expect(vali?.textContent).toBe("Vali120\u00a0000 so'mTelefon kiritilmagan");
    expect(within(vali as HTMLElement).getByRole("link").getAttribute("href")).toBe(`#/customers/${VALI}`);
    expect(screen.getByText(/Mijoz sahifasida shaxsiy havola yaratib, uni Telegramga ulang\./)).toBeTruthy();
  });

  it("says so when everybody can be reached", async () => {
    await open(shop());
    expect(screen.getByText("Eslatma yetib bormaydigan mijoz yo'q.")).toBeTruthy();
    expect(screen.queryByRole("list")).toBeNull();
  });

  it("shows the server's refusal for the list alone and loads it again on retry", async () => {
    let attempt = 0;
    const server = shop({
      unreachable: () => (attempt++ === 0 ? refusal(403, "SHOP_SUSPENDED", "Do'kon to'xtatilgan.") : ok({ items: UNREACHABLE })),
    });
    renderScreen(<RemindersScreen />, { fetch: server.fetch, role: "manager" });
    expect((await screen.findByRole("alert")).querySelector("p")?.textContent).toBe("Do'kon to'xtatilgan.");
    // The settings above are their own request and are still there.
    expect(await screen.findByRole("button", { name: "Saqlash" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Qayta urinish" }));
    expect(await screen.findByText("Vali")).toBeTruthy();
  });
});

describe("reminders by role", () => {
  it("shows a seller nothing and asks the server nothing", async () => {
    const server = shop({ unreachable: () => ok({ items: UNREACHABLE }) });
    renderScreen(<RemindersScreen />, { fetch: server.fetch, role: "seller" });
    expect(screen.getByText("Bunday sahifa yo'q yoki sizda unga ruxsat yo'q.")).toBeTruthy();
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(server.sent).toHaveLength(0);
    expect(screen.queryByText("Ali Valiyev")).toBeNull();
    expect(screen.queryByRole("checkbox")).toBeNull();
  });
});

describe("reminder rules", () => {
  it("lists whole hours between the bounds, and none for bounds that make no sense", () => {
    expect(hourChoices({ first: 8, last: 20 })).toEqual([8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20]);
    expect(hourChoices({ first: 8, last: 8 })).toEqual([8]);
    expect(hourChoices({ first: 20, last: 8 })).toEqual([]);
    expect(hourChoices({ first: -3, last: 30 })).toHaveLength(24);
  });

  it("fills the three placeholders and touches nothing else", () => {
    const example = { shop: "Baraka", name: "Ali", amount: "45 000 so'm" };
    expect(fillWording("«{shop}»: {name}, bugun {amount}. {name}!", example)).toBe("«Baraka»: Ali, bugun 45 000 so'm. Ali!");
    expect(fillWording("{days} kun, {Shop}, { name }", example)).toBe("{days} kun, {Shop}, { name }");
    expect(fillWording("Rahmat!", example)).toBe("Rahmat!");
  });
});

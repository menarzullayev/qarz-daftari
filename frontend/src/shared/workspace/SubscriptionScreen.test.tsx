// @vitest-environment jsdom
import { act, cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  customerBody,
  fakeServer,
  NO_OVERDUE,
  ok,
  refusal,
  type Reply,
  SHOP_BASE,
  subscriptionBody,
} from "../../testing/fakeServer";
import { renderScreen } from "../../testing/renderScreen";
import type { Language } from "../../i18n/types";
import type { Role } from "../navigation";
import { OverviewScreen } from "./OverviewScreen";
import type { ShopMode } from "./shopMode";
import { SubscriptionScreen } from "./SubscriptionScreen";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const LIMITED = { state: "limited", ends_on: null, days_left: null };
const SUSPENDED = { state: "suspended", ends_on: null, days_left: null };

const facts = () => Array.from(document.querySelectorAll("dl.facts > *"), (node) => node.textContent);

function subscriptionOnly(answer: () => Reply) {
  return fakeServer(() => answer());
}

async function open(overrides: Record<string, unknown> = {}, language: Language = "uz") {
  const server = subscriptionOnly(() => ok(subscriptionBody(overrides)));
  renderScreen(<SubscriptionScreen />, { fetch: server.fetch, role: "owner", language });
  await screen.findAllByRole("term");
  return server;
}

describe("the owner's subscription screen (REQ-053, REQ-054)", () => {
  it("shows a trial: the state, the last day in Tashkent form, the days left and the monthly price", async () => {
    const server = subscriptionOnly(() => ok(subscriptionBody()));
    renderScreen(<SubscriptionScreen />, { fetch: server.fetch, role: "owner" });
    expect(screen.getByRole("status").textContent).toBe("Yuklanmoqda…");
    await screen.findAllByRole("term");
    expect(server.sent).toHaveLength(1);
    expect(server.sent[0]).toMatchObject({ method: "GET", path: `${SHOP_BASE}/subscription` });
    expect(facts()).toEqual([
      "Holat",
      "Sinov muddati",
      "Muddat tugaydi",
      "2026-yil 26-oktabr20 kun qoldi",
      "Narxi",
      "oyiga 100\u00a0000 so'm",
    ]);
  });

  it("shows a paid period and its end", async () => {
    await open({ state: "active", trial_ends: null, paid_through: "2026-11-06", ends_on: "2026-11-06", days_left: 31, price_uzs: 150000 });
    expect(facts()).toEqual([
      "Holat",
      "Faol",
      "Muddat tugaydi",
      "2026-yil 6-noyabr31 kun qoldi",
      "Narxi",
      "oyiga 150\u00a0000 so'm",
    ]);
  });

  it("says that today is the last day when no days are left", async () => {
    await open({ ends_on: "2026-10-06", days_left: 0 });
    expect(facts()[3]).toBe("2026-yil 6-oktabrBugun oxirgi kun");
  });

  it.each([
    [LIMITED, "Cheklangan"],
    [SUSPENDED, "To'xtatilgan"],
  ])("shows %j with no end date and no days", async (overrides, label) => {
    await open(overrides);
    expect(facts()).toEqual(["Holat", label, "Narxi", "oyiga 100\u00a0000 so'm"]);
    expect(screen.queryByText(/kun qoldi/)).toBeNull();
  });

  it("tells a suspended shop's owner what it means, and nobody else's", async () => {
    const TEXT = "Do'kon to'xtatilgan: ma'lumotlarni faqat do'kon egasi ko'ra oladi. Qo'llab-quvvatlashga murojaat qiling.";
    await open(SUSPENDED);
    expect(screen.getByText(TEXT)).toBeTruthy();
    cleanup();
    await open(LIMITED);
    expect(screen.queryByText(TEXT)).toBeNull();
  });

  it("shows a state it does not know as the server named it", async () => {
    await open({ state: "frozen", ends_on: null, days_left: null });
    expect(facts().slice(0, 2)).toEqual(["Holat", "frozen"]);
  });

  it("always says what limited mode still allows", async () => {
    for (const overrides of [{}, LIMITED]) {
      await open(overrides);
      expect(screen.getByRole("heading", { level: 2, name: "Cheklangan rejim" })).toBeTruthy();
      expect(
        screen.getByText(
          "Muddat tugagach do'kon cheklangan rejimga o'tadi: yangi nasiya yozilmaydi. To'lov qabul qilish, qarzlarni ko'rish va eslatmalar ishlayveradi.",
        ),
      ).toBeTruthy();
      cleanup();
    }
  });

  it("shows the card to pay to and copies its number", async () => {
    const writeText = vi.fn(async () => undefined);
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
    await open();
    expect(screen.getByText("8600 1234 5678 9012")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Nusxalash" }));
    expect(await screen.findByRole("button", { name: "Nusxalandi" })).toBeTruthy();
    expect(writeText).toHaveBeenCalledTimes(1);
    expect(writeText).toHaveBeenCalledWith("8600 1234 5678 9012");
  });

  it("says so when the card could not be copied, and leaves the number on the screen", async () => {
    vi.stubGlobal("navigator", { ...navigator, clipboard: undefined });
    await open();
    fireEvent.click(screen.getByRole("button", { name: "Nusxalash" }));
    expect(await screen.findByRole("button", { name: "Nusxalab bo'lmadi: matnni qo'lda belgilang" })).toBeTruthy();
    expect(screen.getByText("8600 1234 5678 9012")).toBeTruthy();
  });

  it("says that payment details are not set yet when the server has no card, and offers nothing to copy", async () => {
    await open({ card_number: null });
    expect(screen.getByText("To'lov rekvizitlari hali kiritilmagan.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Nusxalash" })).toBeNull();
  });

  it("sends the receipt to the Telegram chat with /obuna and has no upload", async () => {
    const server = await open();
    expect(screen.getByText("To'lovdan keyin chekni Telegramdagi do'kon botiga /obuna buyrug'i orqali yuboring.")).toBeTruthy();
    expect(document.querySelector("input")).toBeNull();
    expect(document.querySelector("form")).toBeNull();
    expect(server.writes()).toHaveLength(0);
  });

  it("is in Russian with Russian plurals when that is the language", async () => {
    await open({ ends_on: "2026-10-08", days_left: 2 }, "ru");
    expect(facts()).toEqual([
      "Состояние",
      "Пробный период",
      "Срок заканчивается",
      "8 октября 2026 г.Осталось 2 дня",
      "Цена",
      "100\u00a0000 сум в месяц",
    ]);
    expect(screen.getByText("После оплаты отправьте чек боту магазина в Telegram командой /obuna.")).toBeTruthy();
  });

  it("shows the server's refusal and loads again on retry", async () => {
    let attempt = 0;
    const server = subscriptionOnly(() =>
      attempt++ === 0 ? refusal(403, "FORBIDDEN_ROLE", "Bu amal uchun sizning rolingiz yetarli emas.") : ok(subscriptionBody()),
    );
    renderScreen(<SubscriptionScreen />, { fetch: server.fetch, role: "owner" });
    expect((await screen.findByRole("alert")).querySelector("p")?.textContent).toBe("Bu amal uchun sizning rolingiz yetarli emas.");
    fireEvent.click(screen.getByRole("button", { name: "Qayta urinish" }));
    expect(await screen.findByText("Sinov muddati")).toBeTruthy();
  });

  it.each(["seller", "manager"] as const)("shows a %s nothing and asks the server nothing", async (role) => {
    const server = subscriptionOnly(() => ok(subscriptionBody()));
    renderScreen(<SubscriptionScreen />, { fetch: server.fetch, role });
    expect(screen.getByText("Bunday sahifa yo'q yoki sizda unga ruxsat yo'q.")).toBeTruthy();
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(server.sent).toHaveLength(0);
    expect(screen.queryByText("8600 1234 5678 9012")).toBeNull();
    expect(screen.queryByRole("term")).toBeNull();
  });
});

describe("the subscription banner on the overview (REQ-057)", () => {
  const TOTALS = { outstanding: 120000, debtors: 1, overdue: { amount: 0, customers: 0 }, due_today: 0 };
  const LIMITED_TEXT = "Obuna tugagan: yangi nasiya yozilmaydi. To'lov qabul qilish ishlayveradi.";
  const SUSPENDED_TEXT = "Do'kon to'xtatilgan.";

  function overview(subscription: () => Reply) {
    return fakeServer((sent) => {
      if (sent.path.endsWith("/subscription")) {
        return subscription();
      }
      if (sent.path.endsWith("/overview")) {
        return ok(TOTALS);
      }
      return ok({ items: [{ ...customerBody(), overdue: NO_OVERDUE }], next_cursor: null });
    });
  }

  async function show(role: Role, subscription: () => Reply, shopMode: ShopMode | null = null) {
    const server = overview(subscription);
    renderScreen(<OverviewScreen />, { fetch: server.fetch, role, shopMode });
    await screen.findByText("Ali Valiyev");
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 5));
    });
    return server;
  }

  const banner = () => screen.queryByRole("note");
  const subscriptionCalls = (server: ReturnType<typeof fakeServer>) => server.sent.filter((sent) => sent.path.endsWith("/subscription"));

  it.each([
    [7, "Obuna muddati 7 kundan keyin tugaydi."],
    [1, "Obuna muddati 1 kundan keyin tugaydi."],
    [0, "Obuna muddati bugun tugaydi."],
  ])("tells the owner when %i days are left, with the way to the subscription", async (days, text) => {
    const server = await show("owner", () => ok(subscriptionBody({ days_left: days })));
    expect(banner()?.querySelector("p")?.textContent).toBe(text);
    expect(screen.getByRole("link", { name: "Obuna sahifasi" }).getAttribute("href")).toBe("#/subscription");
    expect(subscriptionCalls(server)).toHaveLength(1);
  });

  it.each([8, 20, 300])("tells the owner nothing when %i days are left", async (days) => {
    await show("owner", () => ok(subscriptionBody({ days_left: days })));
    expect(banner()).toBeNull();
    expect(screen.queryByRole("link", { name: "Obuna sahifasi" })).toBeNull();
  });

  it.each([
    [LIMITED, LIMITED_TEXT],
    [SUSPENDED, SUSPENDED_TEXT],
  ])("tells the owner that the shop is %j", async (overrides, text) => {
    await show("owner", () => ok(subscriptionBody(overrides)));
    expect(banner()?.querySelector("p")?.textContent).toBe(text);
    expect(screen.getByRole("link", { name: "Obuna sahifasi" })).toBeTruthy();
  });

  it("shows the owner nothing when the subscription cannot be read, unless a refusal has already named the mode", async () => {
    await show("owner", () => "offline");
    expect(banner()).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    cleanup();

    await show("owner", () => "offline", "limited");
    expect(banner()?.querySelector("p")?.textContent).toBe(LIMITED_TEXT);
  });

  it.each(["seller", "manager"] as const)("never asks the server for the subscription on behalf of a %s", async (role) => {
    const server = await show(role, () => ok(subscriptionBody({ days_left: 1 })));
    expect(subscriptionCalls(server)).toHaveLength(0);
    // The end of the period is the owner's business: other staff are not told it is near.
    expect(banner()).toBeNull();
  });

  it.each([
    ["seller", "limited", LIMITED_TEXT],
    ["manager", "limited", LIMITED_TEXT],
    ["seller", "suspended", SUSPENDED_TEXT],
    ["manager", "suspended", SUSPENDED_TEXT],
  ] as const)("tells a %s shortly that the shop is %s, without the owner's link", async (role, mode, text) => {
    const server = await show(role, () => ok(subscriptionBody()), mode);
    expect(banner()?.textContent).toBe(text);
    expect(screen.queryByRole("link", { name: "Obuna sahifasi" })).toBeNull();
    expect(subscriptionCalls(server)).toHaveLength(0);
  });

  it("is in Russian with Russian plurals when that is the language", async () => {
    const server = overview(() => ok(subscriptionBody({ days_left: 2 })));
    renderScreen(<OverviewScreen />, { fetch: server.fetch, role: "owner", language: "ru" });
    await waitFor(() => expect(banner()?.querySelector("p")?.textContent).toBe("Подписка закончится через 2 дня."));
    expect(screen.getByRole("link", { name: "Страница подписки" })).toBeTruthy();
  });
});

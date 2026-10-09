// @vitest-environment jsdom
import { act, cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  customerBody,
  fakeServer,
  NO_OVERDUE,
  ok,
  ownReceiptBody,
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

  describe("while the free plan is on (BR-33 to BR-35)", () => {
    const SMS_OFF = { offered: false, included: false, quota: 0, left: 0 };
    const plan = (overrides: Record<string, unknown> = {}) => ({ free_customers: 30, customers: 12, after_period: null, sms: SMS_OFF, ...overrides });
    const LIMITED_TITLE = { level: 2, name: "Cheklangan rejim" } as const;
    const THEN_FREE = "To'lov qilinmasa, do'kon bepul tarifda to'liq ishlayveradi.";
    const THEN_LIMITED = "To'lov qilinmasa, yangi nasiya yozilmaydi: mijozlar bepul tarifdagidan ko'p.";
    const ADDS = "To'lov qilsangiz, mijozlar soni cheklanmaydi.";

    it("names the free plan, the customers used of those it holds and what paying adds, and does not speak of limited mode", async () => {
      await open({ state: "free", ends_on: null, days_left: null, plan: plan() });
      expect(facts()).toEqual(["Holat", "Bepul tarif", "Mijozlar", "12 ta; bepul tarifda 30 tagacha", "Narxi", "oyiga 100\u00a0000 so'm"]);
      expect(screen.getByText(ADDS)).toBeTruthy();
      expect(screen.queryByRole("heading", LIMITED_TITLE)).toBeNull();
      expect(screen.queryByText(THEN_FREE)).toBeNull();
    });

    it("says of a trial that the shop stays free afterwards when the plan holds it, and limited when it does not", async () => {
      await open({ plan: plan({ after_period: "free" }) });
      expect(screen.getByText(THEN_FREE)).toBeTruthy();
      expect(screen.queryByText(THEN_LIMITED)).toBeNull();
      expect(screen.queryByRole("heading", LIMITED_TITLE)).toBeNull();
      cleanup();
      await open({ plan: plan({ customers: 45, after_period: "limited" }) });
      expect(screen.getByText(THEN_LIMITED)).toBeTruthy();
      expect(screen.queryByText(THEN_FREE)).toBeNull();
      expect(screen.getByRole("heading", LIMITED_TITLE)).toBeTruthy();
      expect(facts().slice(4, 6)).toEqual(["Mijozlar", "45 ta; bepul tarifda 30 tagacha"]);
    });

    it("offers SMS as what paying adds to a shop that does not pay, and counts what is left for one that does", async () => {
      const offered = { offered: true, included: false, quota: 50, left: 0 };
      await open({ plan: plan({ after_period: "free", sms: offered }) });
      expect(screen.getByText("To'lov qilsangiz, mijozlar soni cheklanmaydi va oyiga 50 tagacha SMS eslatma yuboriladi.")).toBeTruthy();
      expect(screen.queryByText("SMS eslatmalar")).toBeNull();
      cleanup();
      const paid = { state: "active", trial_ends: null, paid_through: "2026-11-06", ends_on: "2026-11-06", days_left: 31 };
      await open({ ...paid, plan: plan({ after_period: "free", sms: { ...offered, included: true, left: 37 } }) });
      expect(facts().slice(6, 8)).toEqual(["SMS eslatmalar", "shu oyda 37 ta qoldi (oyiga 50 ta)"]);
      expect(screen.queryByText(/To'lov qilsangiz/)).toBeNull();
    });

    it("tells a suspended shop about its suspension alone", async () => {
      await open({ ...SUSPENDED, plan: plan() });
      expect(facts()).toEqual(["Holat", "To'xtatilgan", "Narxi", "oyiga 100\u00a0000 so'm"]);
      expect(screen.queryByText(ADDS)).toBeNull();
    });

    it("says the same in Russian", async () => {
      await open({ state: "free", ends_on: null, days_left: null, plan: plan() }, "ru");
      expect(facts().slice(0, 4)).toEqual(["Состояние", "Бесплатный тариф", "Клиенты", "12; на бесплатном тарифе до 30"]);
      expect(screen.getByText("С оплатой число клиентов не ограничено.")).toBeTruthy();
    });

    it("shows none of it while the server sends no plan", async () => {
      await open();
      expect(screen.queryByText("Mijozlar")).toBeNull();
      expect(screen.queryByText(/To'lov qilsangiz/)).toBeNull();
      expect(screen.queryByText(/To'lov qilinmasa/)).toBeNull();
      expect(screen.getByRole("heading", LIMITED_TITLE)).toBeTruthy();
    });
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

  it("shows the card to pay to with its name, in groups of four, and copies its digits", async () => {
    const writeText = vi.fn(async () => undefined);
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
    await open();
    expect(screen.getByText("Humo · Anorbank")).toBeTruthy();
    expect(screen.getByText("8600 1234 5678 9012")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Nusxalash: Humo · Anorbank" }));
    expect(await screen.findByRole("button", { name: "Nusxalandi: Humo · Anorbank" })).toBeTruthy();
    expect(writeText).toHaveBeenCalledTimes(1);
    expect(writeText).toHaveBeenCalledWith("8600123456789012");
    // One card: there are no others to offer, and nothing calls it the primary one.
    expect(screen.queryByText(/Boshqa karta/)).toBeNull();
    expect(screen.queryByText("Asosiy karta")).toBeNull();
  });

  it("says so when the card could not be copied, and leaves the number on the screen", async () => {
    vi.stubGlobal("navigator", { ...navigator, clipboard: undefined });
    await open();
    fireEvent.click(screen.getByRole("button", { name: "Nusxalash: Humo · Anorbank" }));
    expect(await screen.findByRole("button", { name: "Nusxalab bo'lmadi: matnni qo'lda belgilang: Humo · Anorbank" })).toBeTruthy();
    expect(screen.getByText("8600 1234 5678 9012")).toBeTruthy();
  });

  it("says that payment details are not set yet when the server has no card, and offers nothing to copy", async () => {
    await open({ card_number: null, cards: [] });
    expect(screen.getByText("To'lov rekvizitlari hali kiritilmagan.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^Nusxalash/ })).toBeNull();
    expect(screen.queryByText(/Boshqa karta/)).toBeNull();
  });

  describe("with several cards", () => {
    const HUMO = { number: "8600123456789012", label: "Humo · Anorbank" };
    const UZCARD = { number: "5614681234567890", label: "Uzcard · Kapitalbank" };
    const VISA = { number: "4278310012345678", label: "Visa · Ipak Yo'li" };
    const several = { card_number: HUMO.number, cards: [HUMO, UZCARD, VISA] };
    const shown = () => document.querySelector("#subscription-card-title ~ .startcode__text")?.textContent;
    const others = () =>
      within(screen.getByRole("list", { name: "Boshqa karta (2)" }))
        .getAllByRole("listitem")
        .map((row) => row.querySelector(".row__link")?.textContent);

    it("shows the primary card first and the others behind one line that says how many there are", async () => {
      await open(several);
      expect(shown()).toBe("8600 1234 5678 9012");
      expect(screen.getByText("Asosiy karta")).toBeTruthy();
      const disclosure = screen.getByText("Boshqa karta (2)").closest("details");
      expect(disclosure?.open).toBe(false);
      // In the server's order, without the one already shown above.
      expect(others()).toEqual(["Uzcard · Kapitalbank5614 6812 3456 7890", "Visa · Ipak Yo'li4278 3100 1234 5678"]);
    });

    it("copies the number of any card, each button named by its card", async () => {
      const writeText = vi.fn(async () => undefined);
      vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
      await open(several);
      fireEvent.click(screen.getByRole("button", { name: "Nusxalash: Visa · Ipak Yo'li" }));
      expect(await screen.findByRole("button", { name: "Nusxalandi: Visa · Ipak Yo'li" })).toBeTruthy();
      expect(writeText).toHaveBeenCalledWith("4278310012345678");
      // Said of that card only.
      expect(screen.getByRole("button", { name: "Nusxalash: Humo · Anorbank" })).toBeTruthy();
      expect(screen.getByRole("button", { name: "Nusxalash: Uzcard · Kapitalbank" })).toBeTruthy();
    });

    it("makes a chosen card the one being paid to, and sends it with the receipt", async () => {
      const server = fakeServer((sent) =>
        sent.path.endsWith("/subscription") ? ok(subscriptionBody(several)) : sent.method === "GET" ? ok({ items: [] }) : ok(ownReceiptBody(), 201),
      );
      renderScreen(<SubscriptionScreen />, { fetch: server.fetch, role: "owner" });
      const form = await screen.findByRole("form", { name: "Chek yuborish" });
      // Until another is chosen the receipt is for the primary card.
      expect(within(form).getByText("Karta: Humo · Anorbank ··9012")).toBeTruthy();

      fireEvent.click(screen.getByRole("button", { name: "Shu kartaga to'layman: Uzcard · Kapitalbank" }));
      await waitFor(() => expect(shown()).toBe("5614 6812 3456 7890"));
      expect(screen.queryByText("Asosiy karta")).toBeNull();
      expect(others()).toEqual(["Humo · Anorbank8600 1234 5678 9012", "Visa · Ipak Yo'li4278 3100 1234 5678"]);
      expect(within(form).getByText("Karta: Uzcard · Kapitalbank ··7890")).toBeTruthy();

      fireEvent.change(screen.getByLabelText("Chek"), { target: { files: [new File(["jpeg"], "chek.jpg", { type: "image/jpeg" })] } });
      fireEvent.submit(form);
      await waitFor(() => expect(server.writes()).toHaveLength(1));
      expect(server.writes()[0]?.form?.["card"]).toBe("5614681234567890");
    });

    it("says the same in Russian", async () => {
      await open(several, "ru");
      expect(screen.getByText("Основная карта")).toBeTruthy();
      expect(screen.getByText("Другая карта (2)")).toBeTruthy();
      expect(screen.getByRole("button", { name: "Плачу на эту карту: Uzcard · Kapitalbank" })).toBeTruthy();
    });
  });

  it("says the receipt may go to the Telegram chat with /obuna, and offers to send one from here: nothing is sent by opening", async () => {
    const server = await open();
    expect(screen.getByText("To'lovdan keyin chekni Telegramdagi do'kon botiga /obuna buyrug'i orqali yuboring.")).toBeTruthy();
    expect(await screen.findByRole("form", { name: "Chek yuborish" })).toBeTruthy();
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

  it.each([
    ["free", "Obuna muddati 7 kundan keyin tugaydi. To'lov qilinmasa, do'kon bepul tarifda to'liq ishlayveradi."],
    ["limited", "Obuna muddati 7 kundan keyin tugaydi. To'lov qilinmasa, yangi nasiya yozilmaydi: mijozlar bepul tarifdagidan ko'p."],
    [null, "Obuna muddati 7 kundan keyin tugaydi."],
  ])("says what follows the period while the free plan is on: %s", async (after, text) => {
    const plan = { free_customers: 30, customers: 12, after_period: after, sms: { offered: false, included: false, quota: 0, left: 0 } };
    await show("owner", () => ok(subscriptionBody({ days_left: 7, plan })));
    expect(banner()?.querySelector("p")?.textContent).toBe(text);
  });

  it("shows a free shop's owner no banner: nothing is ending and nothing is refused", async () => {
    const plan = { free_customers: 30, customers: 12, after_period: null, sms: { offered: false, included: false, quota: 0, left: 0 } };
    await show("owner", () => ok(subscriptionBody({ state: "free", ends_on: null, days_left: null, plan })));
    expect(banner()).toBeNull();
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

// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { Reply, Sent } from "../testing/fakeServer";
import { loweredPlan, planSwitchedOff } from "./rules";
import { SettingsScreen } from "./SettingsScreen";
import { ShopScreen } from "./ShopScreen";
import { ShopsScreen } from "./ShopsScreen";
import { ADMIN, adminApi, cells, NOBODY, NOW, ok, OTHER_SHOP, platformBody, refusal, renderAdmin, SHOP_ID, shopBody, shopDetailBody } from "./testing";

/**
 * The free plan in the administrator's panel (module A, behind the platform switch `free_plan_on`). The
 * server says the plan is on by sending each shop's use of it; with the switch off it sends none, and
 * the panel is then what it was.
 */

beforeEach(() => {
  window.location.hash = "";
});
afterEach(cleanup);

const now = () => NOW;
const NO_PERIOD = { stored_state: "limited", trial_ends: null };
const freeShop = () => shopBody({ customer_count: 14, plan: { free_customers: 30, customers: 12 } }, { ...NO_PERIOD, state: "free" });
const fullShop = () =>
  shopBody({ id: OTHER_SHOP, name: "Ziyo market", customer_count: 45, plan: { free_customers: 30, customers: 41 } }, { ...NO_PERIOD, state: "limited" });

describe("the list of shops with the free plan", () => {
  const list = (handler: (sent: Sent) => Reply) => {
    const made = adminApi(handler);
    renderAdmin(<ShopsScreen api={made.api} />);
    return made.server;
  };
  const states = () => [...(screen.getByLabelText("Obuna holati") as HTMLSelectElement).options].map((option) => [option.value, option.textContent]);

  it("names a free shop free and says how many of the plan's customers each shop has", async () => {
    list(() => ok({ items: [freeShop(), fullShop()], next_cursor: null }));
    expect(cells(await screen.findByRole("table", { name: "Do'konlar" }))).toEqual([
      ["Do'kon", "Obuna holati", "Sinov tugaydi", "To'langan muddat", "Xodimlar soni", "Mijozlar soni", "Mijozlar (arxivsiz) / bepul tarif", "Ochilgan"],
      ["Baraka savdo", "Bepul tarif", "—", "—", "3", "14", "12 / 30", "2026-yil 1-sentabr, 10:00"],
      ["Ziyo market", "Cheklangan", "—", "—", "3", "45", "41 / 30", "2026-yil 1-sentabr, 10:00"],
    ]);
  });

  it("offers the free state as a filter, and keeps offering it when a filtered page is empty", async () => {
    const server = list((sent) => ok({ items: sent.query["state"] === undefined ? [freeShop(), fullShop()] : [], next_cursor: null }));
    await screen.findByRole("table");
    expect(states()).toEqual([
      ["", "Barcha holatlar"],
      ["trial", "Sinov muddati"],
      ["active", "Faol"],
      ["free", "Bepul tarif"],
      ["limited", "Cheklangan"],
      ["suspended", "To'xtatilgan"],
    ]);
    fireEvent.change(screen.getByLabelText("Obuna holati"), { target: { value: "free" } });
    await waitFor(() => expect(server.sent.at(-1)?.query).toEqual({ state: "free" }));
    expect(await screen.findByText("Bu qidiruv bo'yicha do'kon topilmadi.")).toBeTruthy();
    expect(states().map(([value]) => value)).toContain("free");
    expect((screen.getByLabelText("Obuna holati") as HTMLSelectElement).value).toBe("free");
  });

  it("with the switch off has no such column and no such filter", async () => {
    list(() => ok({ items: [shopBody({}, { ...NO_PERIOD, state: "limited" })], next_cursor: null }));
    expect(cells(await screen.findByRole("table", { name: "Do'konlar" }))).toEqual([
      ["Do'kon", "Obuna holati", "Sinov tugaydi", "To'langan muddat", "Xodimlar soni", "Mijozlar soni", "Ochilgan"],
      ["Baraka savdo", "Cheklangan", "—", "—", "3", "42", "2026-yil 1-sentabr, 10:00"],
    ]);
    expect(states().map(([value]) => value)).toEqual(["", "trial", "active", "limited", "suspended"]);
    expect(document.body.textContent).not.toContain("bepul tarif");
  });
});

describe("one shop with the free plan", () => {
  const open = (detail: unknown) => {
    const made = adminApi((sent) => (sent.path === `${ADMIN}/support-access` ? ok({ items: [], next_cursor: null }) : ok(detail)));
    renderAdmin(<ShopScreen api={made.api} shopId={SHOP_ID} now={now} who={NOBODY} />);
  };
  const facts = () => [...document.querySelectorAll("dl.facts")].map((list) => [...list.children].map((part) => part.textContent));

  it("shows the free state and the plan's customers used", async () => {
    open({ ...shopDetailBody(), ...freeShop() });
    expect(await screen.findByRole("heading", { level: 2, name: "Baraka savdo" })).toBeTruthy();
    expect(facts()[0]?.slice(-4)).toEqual(["Mijozlar soni", "14", "Mijozlar (arxivsiz) / bepul tarif", "12 / 30"]);
    // The stored row says "limited" for as long as the shop is free: that is not a state waiting for the review.
    expect(facts()[1]?.slice(0, 2)).toEqual(["Obuna holati", "Bepul tarif"]);
    expect(document.body.textContent).not.toContain("kunlik tekshiruvgacha");
  });

  it("still says when a finished period waits for the daily review", async () => {
    open({ ...shopDetailBody(), ...fullShop(), subscription: { ...fullShop().subscription, stored_state: "trial" } });
    expect(await screen.findByRole("heading", { level: 2, name: "Ziyo market" })).toBeTruthy();
    expect(facts()[1]?.slice(0, 2)).toEqual(["Obuna holati", "CheklanganYozuvda: Sinov muddati (kunlik tekshiruvgacha)"]);
  });

  it("with the switch off says nothing of the plan", async () => {
    open(shopDetailBody({}, { ...NO_PERIOD, state: "limited" }));
    expect(await screen.findByRole("heading", { level: 2, name: "Baraka savdo" })).toBeTruthy();
    expect(facts()[0]?.slice(-2)).toEqual(["Mijozlar soni", "42"]);
    expect(facts()[1]?.slice(0, 2)).toEqual(["Obuna holati", "Cheklangan"]);
    expect(document.body.textContent).not.toContain("bepul tarif");
  });
});

describe("lowering the free plan in the settings", () => {
  const NUMBER = "Bepul tarifdagi mijozlar soni (do'kon boshiga)";
  const SWITCH = "Bepul tarif yoqilgan (SMS faqat to'lagan do'konlarga)";
  const CODE = "Autentifikator kodi";
  type Server = ReturnType<typeof adminApi>["server"];

  /** The settings with the plan on at 30 customers; `preview` is the server's answer to the question. */
  const open = (preview: (sent: Sent) => Reply, settings: Record<string, unknown> = { free_plan_on: true }, lowered: unknown = undefined) => {
    const made = adminApi((sent) => {
      if (sent.method === "GET") {
        return sent.query["free_plan_customers"] === undefined && sent.query["free_plan_on"] === undefined ? ok(platformBody({}, settings)) : preview(sent);
      }
      const changes = (sent.body as { changes: Record<string, unknown> }).changes;
      const word = changes["free_plan_on"] === false ? "free_plan_off" : "free_plan_lowered";
      return ok(platformBody(lowered === undefined ? {} : { [word]: lowered }, { ...settings, ...changes }));
    });
    renderAdmin(<SettingsScreen api={made.api} />);
    return made.server;
  };
  const wouldLimit = (shops: number) => (sent: Sent) =>
    ok(platformBody({ free_plan_preview: { customers: Number(sent.query["free_plan_customers"]), shops_limited: shops } }, { free_plan_on: true }));
  const wouldLimitOff = (shops: number) => () => ok(platformBody({ free_plan_off_preview: { shops_limited: shops } }, { free_plan_on: true }));
  const questions = (server: Server) => server.sent.filter((sent) => sent.method === "GET" && Object.keys(sent.query).length > 0);
  const switchOff = async () => {
    fireEvent.click(await screen.findByLabelText(SWITCH));
    fireEvent.change(screen.getByLabelText(CODE), { target: { value: "123456" } });
  };
  const type = async (value: string) => fireEvent.change(await screen.findByLabelText(NUMBER), { target: { value } });
  const save = () => fireEvent.click(screen.getByRole("button", { name: "Saqlash" }));
  /** The question before a lowering is sent; the cards of the form are a group too, so it is found by its place. */
  const question = () => document.querySelector<HTMLElement>("form > div.notice[role=group]");
  const asked = () => waitFor(() => question() ?? Promise.reject(new Error("no question is asked")));

  it("asks the server how many shops a lower number would limit, and sends nothing until that was seen and agreed to", async () => {
    const server = open(wouldLimit(3), { free_plan_on: true }, { customers: 20, shops_limited: 3 });
    await type("20");
    expect(questions(server)).toHaveLength(0);
    save();
    const shown = await asked();
    expect(questions(server).map((sent) => sent.query)).toEqual([{ free_plan_customers: "20" }]);
    expect([...shown.querySelectorAll("p:not(.actions)")].map((line) => line.textContent)).toEqual([
      "Bepul tarif 30 tadan 20 tagacha mijozga kamaytiriladi.",
      "Cheklangan rejimga o'tadigan do'konlar: 3 ta. Ularda yangi nasiya yozilmaydi; yozilgan ma'lumotlar saqlanadi, ko'rish va to'lov qabul qilish ishlayveradi.",
      "Bu do'konlarning egalariga botda xabar yuboriladi: nima bo'lgani va /obuna buyrug'i.",
    ]);
    expect(screen.queryByRole("button", { name: "Saqlash" })).toBeNull();
    expect(server.writes()).toHaveLength(0);

    // Going back sends nothing and gives the form its button again.
    fireEvent.click(within(shown).getByRole("button", { name: "Orqaga" }));
    expect(question()).toBeNull();
    expect(server.writes()).toHaveLength(0);

    save();
    fireEvent.click(within(await asked()).getByRole("button", { name: "Ha, saqlansin" }));
    const done = await screen.findByRole("status");
    expect(server.writes().map((sent) => [sent.method, sent.path, sent.body])).toEqual([["PATCH", `${ADMIN}/settings`, { changes: { free_plan_customers: 20 } }]]);
    expect(within(done).getByText("Bepul tarifdagi mijozlar soni (do'kon boshiga): 30 → 20")).toBeTruthy();
    expect(within(done).getByText("Cheklangan rejimga o'tgan do'konlar: 3 ta. Egalariga botda xabar yuborildi.")).toBeTruthy();
    expect(question()).toBeNull();
  });

  it("saves at once when the lower number limits no shop", async () => {
    const server = open(wouldLimit(0), { free_plan_on: true }, { customers: 20, shops_limited: 0 });
    await type("20");
    save();
    const done = await screen.findByRole("status");
    expect(questions(server)).toHaveLength(1);
    expect(server.writes()).toHaveLength(1);
    expect(question()).toBeNull();
    expect(done.textContent).not.toContain("Cheklangan rejimga");
  });

  it("asks the server how many shops switching the plan off would limit, and sends nothing until that was seen and agreed to", async () => {
    const server = open(wouldLimitOff(7), { free_plan_on: true }, { shops_limited: 7 });
    await switchOff();
    expect(questions(server)).toHaveLength(0);
    save();
    const shown = await asked();
    expect(questions(server).map((sent) => sent.query)).toEqual([{ free_plan_on: "false" }]);
    expect([...shown.querySelectorAll("p:not(.actions)")].map((line) => line.textContent)).toEqual([
      "Bepul tarif o'chiriladi.",
      "Cheklangan rejimga o'tadigan do'konlar: 7 ta. Ularda yangi nasiya yozilmaydi; yozilgan ma'lumotlar saqlanadi, ko'rish va to'lov qabul qilish ishlayveradi.",
      "Bu do'konlarning egalariga botda xabar yuboriladi: nima bo'lgani va /obuna buyrug'i.",
    ]);
    expect(screen.queryByRole("button", { name: "Saqlash" })).toBeNull();
    expect(server.writes()).toHaveLength(0);

    fireEvent.click(within(shown).getByRole("button", { name: "Orqaga" }));
    expect(question()).toBeNull();
    expect(server.writes()).toHaveLength(0);

    save();
    fireEvent.click(within(await asked()).getByRole("button", { name: "Ha, saqlansin" }));
    const done = await screen.findByRole("status");
    expect(server.writes().map((sent) => [sent.method, sent.path, sent.body])).toEqual([["PATCH", `${ADMIN}/settings`, { changes: { free_plan_on: false }, code: "123456" }]]);
    expect(within(done).getByText("Cheklangan rejimga o'tgan do'konlar: 7 ta. Egalariga botda xabar yuborildi.")).toBeTruthy();
    expect(question()).toBeNull();
  });

  it("asks the one question of the switch when the plan is switched off and its number changed in one save", async () => {
    const server = open(wouldLimitOff(7), { free_plan_on: true }, { shops_limited: 7 });
    await type("20");
    await switchOff();
    save();
    const shown = await asked();
    // The number is not asked about: a plan that is off holds nobody, whatever it says.
    expect(questions(server).map((sent) => sent.query)).toEqual([{ free_plan_on: "false" }]);
    expect(shown.textContent).toContain("Bepul tarif o'chiriladi.");
    expect(shown.textContent).not.toContain("kamaytiriladi");
    fireEvent.click(within(shown).getByRole("button", { name: "Ha, saqlansin" }));
    const done = await screen.findByRole("status");
    expect(server.writes().map((sent) => sent.body)).toEqual([{ changes: { free_plan_on: false, free_plan_customers: 20 }, code: "123456" }]);
    expect(within(done).getByText("Cheklangan rejimga o'tgan do'konlar: 7 ta. Egalariga botda xabar yuborildi.")).toBeTruthy();
  });

  it("saves at once when the plan holds no shop to switch it off for", async () => {
    const server = open(wouldLimitOff(0), { free_plan_on: true }, { shops_limited: 0 });
    await switchOff();
    save();
    const done = await screen.findByRole("status");
    expect(questions(server)).toHaveLength(1);
    expect(server.writes()).toHaveLength(1);
    expect(question()).toBeNull();
    expect(done.textContent).not.toContain("Cheklangan rejimga");
  });

  it("sends nothing when the question of the switch cannot be answered, and says so", async () => {
    const server = open(() => "offline");
    await switchOff();
    save();
    expect((await screen.findByRole("alert")).textContent).toContain("Serverga ulanib bo'lmadi");
    expect(server.writes()).toHaveLength(0);
    expect(question()).toBeNull();
  });

  it("withdraws the question of the switch when the plan is left on after all", async () => {
    const server = open(wouldLimitOff(7));
    await switchOff();
    save();
    await asked();
    fireEvent.click(screen.getByLabelText(SWITCH));
    expect(question()).toBeNull();
    expect(server.writes()).toHaveLength(0);
    expect((screen.getByLabelText(SWITCH) as HTMLInputElement).checked).toBe(true);
  });

  it.each([
    ["a higher number", { free_plan_on: true }, "45"],
    ["the plan switched off", { free_plan_on: false }, "20"],
  ])("asks nothing with %s: the change is sent as every other", async (_, settings, value) => {
    const server = open(() => refusal(500, "INTERNAL", "no question is expected"), settings);
    await type(value);
    save();
    await screen.findByRole("status");
    expect(questions(server)).toHaveLength(0);
    expect(server.writes()).toHaveLength(1);
    expect(question()).toBeNull();
  });

  it("asks nothing when the plan is being switched on with a lower number: no shop was free before", async () => {
    const server = open(() => refusal(500, "INTERNAL", "no question is expected"), { free_plan_on: false });
    await type("20");
    fireEvent.click(screen.getByLabelText(SWITCH));
    fireEvent.change(screen.getByLabelText(CODE), { target: { value: "123456" } });
    save();
    await screen.findByRole("status");
    expect(questions(server)).toHaveLength(0);
    expect(server.writes()[0]?.body).toEqual({ changes: { free_plan_on: true, free_plan_customers: 20 }, code: "123456" });
  });

  it("sends nothing when the question cannot be answered, and says so", async () => {
    const server = open(() => "offline");
    await type("20");
    save();
    expect((await screen.findByRole("alert")).textContent).toContain("Serverga ulanib bo'lmadi");
    expect(server.writes()).toHaveLength(0);
    expect(question()).toBeNull();
    expect((screen.getByRole("button", { name: "Saqlash" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("withdraws the question when a field is changed, and asks again for the new number", async () => {
    const server = open(wouldLimit(3));
    await type("20");
    save();
    await asked();
    await type("10");
    expect(question()).toBeNull();
    save();
    await asked();
    expect(questions(server).map((sent) => sent.query["free_plan_customers"])).toEqual(["20", "10"]);
    expect(server.writes()).toHaveLength(0);
  });

  it("saves without a question when the server says nothing of the plan, as it does with the switch off", async () => {
    const server = open(() => ok(platformBody({}, { free_plan_on: true })));
    await type("20");
    save();
    await screen.findByRole("status");
    expect(server.writes()).toHaveLength(1);
    expect(question()).toBeNull();
  });
});

describe("the rule of a free plan switched off", () => {
  const on = { free_plan_on: true, free_plan_customers: 30 };

  it("is the switch turned off while the plan is on, whatever else changes", () => {
    expect(planSwitchedOff(on, { free_plan_on: false })).toBe(true);
    expect(planSwitchedOff(on, { free_plan_on: false, free_plan_customers: 5 })).toBe(true);
    expect(planSwitchedOff(on, { free_plan_on: false, trial_days: 7 })).toBe(true);
  });

  it.each([
    ["the plan left on", on, { free_plan_customers: 5 }],
    ["the plan said to be on again", on, { free_plan_on: true }],
    ["another setting", on, { trial_days: 7 }],
    ["the plan off and left off", { ...on, free_plan_on: false }, { free_plan_on: false }],
    ["the plan switched on", { ...on, free_plan_on: false }, { free_plan_on: true }],
    ["no switch held", { free_plan_customers: 30 }, { free_plan_on: false }],
  ])("is not %s", (_, values, changes) => {
    expect(planSwitchedOff(values, changes)).toBe(false);
  });
});

describe("the rule of a lowered free plan", () => {
  const on = { free_plan_on: true, free_plan_customers: 30 };

  it("is a lower number while the plan is on and stays on", () => {
    expect(loweredPlan(on, { free_plan_customers: 29 })).toEqual({ from: 30, to: 29 });
    expect(loweredPlan(on, { free_plan_customers: 1, trial_days: 7 })).toEqual({ from: 30, to: 1 });
    expect(loweredPlan(on, { free_plan_on: true, free_plan_customers: 5 })).toEqual({ from: 30, to: 5 });
  });

  it.each([
    ["the same number", on, { free_plan_customers: 30 }],
    ["a higher number", on, { free_plan_customers: 31 }],
    ["another setting", on, { trial_days: 7 }],
    ["the plan off", { ...on, free_plan_on: false }, { free_plan_customers: 5 }],
    ["the plan switched on by the change", { ...on, free_plan_on: false }, { free_plan_on: true, free_plan_customers: 5 }],
    ["the plan switched off by the change", on, { free_plan_on: false, free_plan_customers: 5 }],
    ["no number held", { free_plan_on: true }, { free_plan_customers: 5 }],
  ])("is not %s", (_, values, changes) => {
    expect(loweredPlan(values, changes)).toBeNull();
  });
});

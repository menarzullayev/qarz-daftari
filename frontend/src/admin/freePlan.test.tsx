// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { Reply, Sent } from "../testing/fakeServer";
import { ShopScreen } from "./ShopScreen";
import { ShopsScreen } from "./ShopsScreen";
import { ADMIN, adminApi, cells, NOBODY, NOW, ok, OTHER_SHOP, renderAdmin, SHOP_ID, shopBody, shopDetailBody } from "./testing";

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

// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  creditSettingsBody,
  CUSTOMER_ID,
  customerBody,
  detailBody,
  fakeServer,
  linkBody,
  NO_OVERDUE,
  NOON,
  ok,
  refusal,
  remindersBody,
  type Reply,
  type Sent,
  settingsBody,
  SHOP_BASE,
  SHOP_ID,
  subscriptionBody,
} from "../testing/fakeServer";
import { go } from "../testing/renderScreen";
import type { ApiAuth } from "./api";
import type { Role } from "./navigation";
import { StaffRoot } from "./StaffRoot";

const OTHER_SHOP = "5a0c6d3e-0000-4000-8000-00000000bbbb";
const LIMITED_MESSAGE = "Obuna tugagan: yangi nasiya yozilmaydi. To'lov qabul qilish va ko'rish ishlayveradi.";
const SUSPENDED_MESSAGE = "Do'kon to'xtatilgan. Faqat do'kon egasi ma'lumotlarni ko'ra oladi va eksport qila oladi.";
const LIMITED_BANNER = "Obuna tugagan: yangi nasiya yozilmaydi. To'lov qabul qilish ishlayveradi.";
const SUSPENDED_BANNER = "Do'kon to'xtatilgan.";

beforeEach(() => {
  window.location.hash = "";
  window.localStorage.clear();
});
afterEach(cleanup);

const bearer = async (): Promise<ApiAuth> => ({ kind: "bearer", token: "session-token" });
const heading = () => screen.getByRole("heading", { level: 1 }).textContent;
const banner = () => screen.queryByRole("note");
const membership = (role: Role, shopId = SHOP_ID, name = "Baraka savdo") => ({ shop_id: shopId, name, role });
const one = (role: Role) => ({ items: [membership(role)], active_shop: SHOP_ID });

/** A whole shop as the server answers it; `extra` answers first and may leave a request to the rest. */
function backend(shops: unknown, extra: (sent: Sent) => Reply | null = () => null) {
  return fakeServer((sent) => {
    const special = extra(sent);
    if (special !== null) {
      return special;
    }
    const path = sent.path.replace(/^\/api\/v1\/shops\/[^/]+/, "");
    if (sent.path === "/api/v1/me/shops") {
      return ok(shops);
    }
    if (sent.path === "/api/v1/me/active-shop") {
      return ok({ active_shop: (sent.body as { shop_id: string }).shop_id });
    }
    switch (path) {
      case "/overview":
        return ok({ outstanding: 120000, debtors: 1, overdue: { amount: 0, customers: 0 }, due_today: 0 });
      case "/overview/debtors":
        return ok({ items: [{ ...customerBody(), overdue: NO_OVERDUE }], next_cursor: null });
      case `/customers/${CUSTOMER_ID}`:
        return ok(detailBody());
      case `/customers/${CUSTOMER_ID}/link`:
        return ok(linkBody());
      case "/credit-settings":
        return ok(creditSettingsBody(sent.method === "GET" ? {} : (sent.body as Record<string, unknown>)));
      case "/reminders":
        return ok(remindersBody(sent.method === "GET" ? {} : (sent.body as Record<string, unknown>)));
      case "/reminders/unreachable":
        return ok({ items: [] });
      case "/subscription":
        return ok(subscriptionBody());
      case "":
        return sent.method === "GET" ? ok(settingsBody()) : ok(settingsBody(sent.body as Record<string, unknown>));
    }
    return refusal(404, "NOT_FOUND", "Topilmadi.");
  });
}

function start(server: ReturnType<typeof fakeServer>) {
  return render(<StaffRoot entryKey="entry.app" initialLanguage="uz" connect={bearer} fetch={server.fetch} now={() => NOON} />);
}

const asked = (server: ReturnType<typeof fakeServer>, suffix: string) => server.sent.filter((sent) => sent.path.endsWith(suffix));
const settle = () => new Promise((resolve) => setTimeout(resolve, 20));

describe("the reminders section by role", () => {
  it.each(["manager", "owner"] as const)("opens the settings and the unreachable list for a %s", async (role) => {
    const server = backend(one(role));
    start(server);
    await screen.findByText("Ali Valiyev");
    go("#/reminders");
    expect(heading()).toBe("Eslatmalar");
    expect(await screen.findByLabelText("Avtomatik eslatmalar yoqilgan")).toBeTruthy();
    expect(await screen.findByText("Eslatma yetib bormaydigan mijoz yo'q.")).toBeTruthy();
    // The wordings are shown with this shop's own name.
    expect(document.querySelector(".wording")?.textContent).toContain("«Baraka savdo»");
  });

  it("keeps a seller out without asking the server", async () => {
    const server = backend(one("seller"));
    start(server);
    await screen.findByText("Ali Valiyev");
    go("#/reminders");
    expect(heading()).toBe("Sahifa topilmadi");
    await settle();
    expect(asked(server, "/reminders")).toHaveLength(0);
    expect(asked(server, "/reminders/unreachable")).toHaveLength(0);
  });
});

describe("the subscription section by role", () => {
  it("opens for the owner", async () => {
    const server = backend(one("owner"));
    start(server);
    await screen.findByText("Ali Valiyev");
    go("#/subscription");
    expect(heading()).toBe("Obuna");
    expect(await screen.findByText("Sinov muddati")).toBeTruthy();
    expect(screen.getByText("8600 1234 5678 9012")).toBeTruthy();
  });

  it.each(["seller", "manager"] as const)("is not a page for a %s, and the server is not asked", async (role) => {
    const server = backend(one(role));
    start(server);
    await screen.findByText("Ali Valiyev");
    go("#/subscription");
    expect(heading()).toBe("Sahifa topilmadi");
    await settle();
    expect(asked(server, "/subscription")).toHaveLength(0);
  });
});

describe("credit settings inside the shop settings", () => {
  it("lets a manager change the credit rules while the shop's own settings stay read-only", async () => {
    const server = backend(one("manager"));
    start(server);
    await screen.findByText("Ali Valiyev");
    go("#/shop-settings");
    expect(await screen.findByText("Sozlamalarni faqat do'kon egasi o'zgartira oladi.")).toBeTruthy();
    fireEvent.change(await screen.findByLabelText("Do'konning umumiy limiti, so'm"), { target: { value: "500000" } });
    fireEvent.click(screen.getByRole("button", { name: "Nasiya sozlamalarini saqlash" }));
    expect(await screen.findByText("Nasiya sozlamalari saqlandi.")).toBeTruthy();
    expect(server.writes()).toHaveLength(1);
    expect(server.writes()[0]).toMatchObject({
      method: "PATCH",
      path: `${SHOP_BASE}/credit-settings`,
      body: { default_credit_limit: 500000 },
    });
    // Nothing on the page saves the shop's own settings for a manager.
    expect(screen.queryByRole("button", { name: "Saqlash" })).toBeNull();
  });

  it("gives the owner two forms that save to their own places", async () => {
    const server = backend(one("owner"));
    start(server);
    await screen.findByText("Ali Valiyev");
    go("#/shop-settings");
    fireEvent.change(await screen.findByLabelText("Odatdagi to'lash muddati, kun"), { target: { value: "14" } });
    fireEvent.click(screen.getByRole("button", { name: "Saqlash" }));
    expect(await screen.findByText("Sozlamalar saqlandi.")).toBeTruthy();
    fireEvent.click(await screen.findByLabelText("Sotuvchilar limitdan oshirib nasiya yoza oladi (ogohlantirish bilan)"));
    fireEvent.click(screen.getByRole("button", { name: "Nasiya sozlamalarini saqlash" }));
    expect(await screen.findByText("Nasiya sozlamalari saqlandi.")).toBeTruthy();
    expect(server.writes().map((sent) => [sent.path, sent.body])).toEqual([
      [SHOP_BASE, { default_promise_days: 14 }],
      [`${SHOP_BASE}/credit-settings`, { sellers_may_exceed: true }],
    ]);
  });

  it("asks nothing about credit settings for a seller, who has no shop settings", async () => {
    const server = backend(one("seller"));
    start(server);
    await screen.findByText("Ali Valiyev");
    go("#/shop-settings");
    expect(heading()).toBe("Sahifa topilmadi");
    await settle();
    expect(asked(server, "/credit-settings")).toHaveLength(0);
  });
});

describe("a limited or suspended shop as staff other than the owner learn of it (REQ-057)", () => {
  const limited = (sent: Sent) =>
    sent.method === "POST" && sent.path.endsWith("/entries") ? refusal(402, "SUBSCRIPTION_LIMITED", LIMITED_MESSAGE) : null;

  it.each(["seller", "manager"] as const)("shows a %s the refusal where it came back, then a short banner on the overview", async (role) => {
    const server = backend(one(role), limited);
    start(server);
    await screen.findByText("Ali Valiyev");
    expect(banner()).toBeNull();
    expect(asked(server, "/subscription")).toHaveLength(0);

    go(`#/customers/${CUSTOMER_ID}/credit`);
    fireEvent.change(await screen.findByLabelText("Summa, so'm"), { target: { value: "45000" } });
    fireEvent.click(screen.getByRole("button", { name: "Nasiyani yozish" }));
    expect((await screen.findByRole("alert")).textContent).toBe(LIMITED_MESSAGE);

    go("#/");
    await waitFor(() => expect(banner()?.textContent).toBe(LIMITED_BANNER));
    expect(screen.queryByRole("link", { name: "Obuna sahifasi" })).toBeNull();
    expect(asked(server, "/subscription")).toHaveLength(0);
  });

  it("shows a seller of a suspended shop the server's message on what it refused, and the banner", async () => {
    const suspended = (sent: Sent) =>
      sent.path.startsWith(SHOP_BASE) ? refusal(403, "SHOP_SUSPENDED", SUSPENDED_MESSAGE) : null;
    const server = backend(one("seller"), suspended);
    start(server);
    await waitFor(() => expect(screen.getAllByRole("alert")).toHaveLength(2));
    expect(screen.getAllByRole("alert").map((alert) => alert.querySelector("p")?.textContent)).toEqual([
      SUSPENDED_MESSAGE,
      SUSPENDED_MESSAGE,
    ]);
    await waitFor(() => expect(banner()?.textContent).toBe(SUSPENDED_BANNER));
  });

  it("learns nothing from a refusal that is about something else", async () => {
    const server = backend(one("seller"), (sent) =>
      sent.method === "POST" ? refusal(409, "LIMIT_REACHED", "Limitdan oshadi.", { limit: "150000", balance: "165000" }) : null,
    );
    start(server);
    await screen.findByText("Ali Valiyev");
    go(`#/customers/${CUSTOMER_ID}/credit`);
    fireEvent.change(await screen.findByLabelText("Summa, so'm"), { target: { value: "45000" } });
    fireEvent.click(screen.getByRole("button", { name: "Nasiyani yozish" }));
    await screen.findByRole("alert");
    go("#/");
    await screen.findByText("Ali Valiyev");
    await settle();
    expect(banner()).toBeNull();
  });

  it("forgets what one shop's refusals said when another shop is chosen", async () => {
    const server = backend(
      { items: [membership("seller"), membership("seller", OTHER_SHOP, "Ziyo market")], active_shop: SHOP_ID },
      (sent) => (sent.path.startsWith(SHOP_BASE) ? limited(sent) : null),
    );
    start(server);
    await screen.findByText("Ali Valiyev");
    go(`#/customers/${CUSTOMER_ID}/credit`);
    fireEvent.change(await screen.findByLabelText("Summa, so'm"), { target: { value: "45000" } });
    fireEvent.click(screen.getByRole("button", { name: "Nasiyani yozish" }));
    await screen.findByRole("alert");
    go("#/");
    await waitFor(() => expect(banner()?.textContent).toBe(LIMITED_BANNER));

    fireEvent.click(screen.getByRole("button", { name: "Boshqa do'konga o'tish" }));
    fireEvent.click(screen.getByRole("button", { name: /Ziyo market/ }));
    await waitFor(() => expect(screen.getByRole("banner").querySelector("strong")?.textContent).toBe("Ziyo market"));
    await screen.findByText("Ali Valiyev");
    expect(banner()).toBeNull();
  });
});

describe("the owner's banner on the overview", () => {
  it("comes from the subscription and leads to its screen", async () => {
    const server = backend(one("owner"), (sent) =>
      sent.path.endsWith("/subscription") ? ok(subscriptionBody({ ends_on: "2026-10-09", days_left: 3 })) : null,
    );
    start(server);
    await waitFor(() => expect(banner()?.querySelector("p")?.textContent).toBe("Obuna muddati 3 kundan keyin tugaydi."));
    fireEvent.click(screen.getByRole("link", { name: "Obuna sahifasi" }));
    await waitFor(() => expect(heading()).toBe("Obuna"));
    expect(await screen.findByText("2026-yil 9-oktabr", { exact: false })).toBeTruthy();
  });

  it("is absent while the period is far from its end", async () => {
    const server = backend(one("owner"));
    start(server);
    await screen.findByText("Ali Valiyev");
    await waitFor(() => expect(asked(server, "/subscription")).toHaveLength(1));
    await settle();
    expect(banner()).toBeNull();
  });
});

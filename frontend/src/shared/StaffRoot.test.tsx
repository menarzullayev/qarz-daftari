// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  CUSTOMER_ID,
  customerBody,
  detailBody,
  fakeServer,
  NO_OVERDUE,
  NOON,
  ok,
  refusal,
  type Reply,
  type Sent,
  SHOP_BASE,
  SHOP_ID,
} from "../testing/fakeServer";
import { go } from "../testing/renderScreen";
import type { ApiAuth } from "./api";
import type { Role } from "./navigation";
import { StaffRoot } from "./StaffRoot";

const OTHER_SHOP = "5a0c6d3e-0000-4000-8000-00000000bbbb";

beforeEach(() => {
  window.location.hash = "";
  window.localStorage.clear();
});
afterEach(cleanup);

const bearer = async (): Promise<ApiAuth> => ({ kind: "bearer", token: "session-token" });
const heading = () => screen.getByRole("heading", { level: 1 }).textContent;
const shopName = () => screen.getByRole("banner").querySelector("strong")?.textContent;

const membership = (role: Role, shopId = SHOP_ID, name = "Baraka savdo") => ({ shop_id: shopId, name, role });

/** A server for one staff member: their shops, and a shop with one debtor. */
function backend(shops: unknown, extra: (sent: Sent) => Reply | null = () => null) {
  return fakeServer((sent) => {
    const special = extra(sent);
    if (special !== null) {
      return special;
    }
    if (sent.path === "/api/v1/me/shops") {
      return ok(shops);
    }
    if (sent.path === "/api/v1/me/active-shop") {
      return ok({ active_shop: (sent.body as { shop_id: string }).shop_id });
    }
    if (sent.path.endsWith("/overview")) {
      return ok({ outstanding: 120000, debtors: 1, overdue: { amount: 0, customers: 0 }, due_today: 0 });
    }
    if (sent.path.endsWith("/overview/debtors")) {
      return ok({ items: [{ ...customerBody(), overdue: NO_OVERDUE }], next_cursor: null });
    }
    if (sent.path.endsWith(`/customers/${CUSTOMER_ID}`)) {
      return ok(detailBody());
    }
    return ok({ items: [customerBody()], next_cursor: null });
  });
}

function start(server: ReturnType<typeof fakeServer>, connect: () => Promise<ApiAuth | null> = bearer) {
  return render(
    <StaffRoot entryKey="entry.app" initialLanguage="uz" connect={connect} fetch={server.fetch} now={() => NOON} />,
  );
}

describe("opening the workspace", () => {
  it("signs in, finds the active shop, and shows its overview", async () => {
    const server = backend({ items: [membership("seller")], active_shop: SHOP_ID });
    start(server);
    expect(screen.getByRole("status").textContent).toBe("Yuklanmoqda…");
    expect(screen.queryByRole("navigation")).toBeNull();

    expect(await screen.findByText("Ali Valiyev")).toBeTruthy();
    expect(heading()).toBe("Umumiy ko'rinish");
    expect(shopName()).toBe("Baraka savdo");
    expect(server.sent[0]).toMatchObject({ method: "GET", path: "/api/v1/me/shops" });
    expect(server.sent.every((sent) => sent.headers["Authorization"] === "Bearer session-token")).toBe(true);
    expect(server.sent.some((sent) => sent.path === `${SHOP_BASE}/overview`)).toBe(true);
  });

  it("uses the only shop without asking, and does not change the active shop on the server", async () => {
    const server = backend({ items: [membership("owner")], active_shop: null });
    start(server);
    expect(await screen.findByText("Ali Valiyev")).toBeTruthy();
    expect(server.writes()).toHaveLength(0);
    expect(screen.queryByRole("button", { name: "Boshqa do'konga o'tish" })).toBeNull();
  });

  it("asks which shop when there are several and none is active, then remembers the choice", async () => {
    const server = backend({
      items: [membership("seller", SHOP_ID, "Baraka savdo"), membership("owner", OTHER_SHOP, "Ziyo market")],
      active_shop: null,
    });
    start(server);
    expect(await screen.findByRole("heading", { level: 1, name: "Do'konni tanlang" })).toBeTruthy();
    expect(screen.queryByRole("navigation")).toBeNull();
    expect(server.sent.filter((sent) => sent.path.startsWith("/api/v1/shops/"))).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: /Ziyo market/ }));
    await waitFor(() => expect(shopName()).toBe("Ziyo market"));
    expect(server.writes()).toHaveLength(1);
    expect(server.writes()[0]).toMatchObject({ method: "PUT", path: "/api/v1/me/active-shop", body: { shop_id: OTHER_SHOP } });
    // The role of the chosen shop decides the navigation: an owner there, a seller in the other.
    expect(within(screen.getByRole("navigation")).getByRole("link", { name: "Xodimlar" })).toBeTruthy();
    await waitFor(() =>
      expect(server.sent.some((sent) => sent.path === `/api/v1/shops/${OTHER_SHOP}/overview`)).toBe(true),
    );
  });

  it("lets someone with several shops switch from the overview", async () => {
    const server = backend({
      items: [membership("seller", SHOP_ID, "Baraka savdo"), membership("owner", OTHER_SHOP, "Ziyo market")],
      active_shop: SHOP_ID,
    });
    start(server);
    fireEvent.click(await screen.findByRole("button", { name: "Boshqa do'konga o'tish" }));
    fireEvent.click(screen.getByRole("button", { name: /Ziyo market/ }));
    await waitFor(() => expect(shopName()).toBe("Ziyo market"));
    expect(window.location.hash).toBe("#/");
  });

  it("shows the refusal when the chosen shop cannot be made active", async () => {
    const server = backend(
      { items: [membership("seller"), membership("owner", OTHER_SHOP, "Ziyo market")], active_shop: null },
      (sent) => (sent.method === "PUT" ? refusal(404, "NOT_FOUND", "Topilmadi.") : null),
    );
    start(server);
    fireEvent.click(await screen.findByRole("button", { name: /Ziyo market/ }));
    expect((await screen.findByRole("alert")).textContent).toContain("Topilmadi.");
    expect(heading()).toBe("Do'konni tanlang");
  });

  it("says so when the person is in no shop", async () => {
    const server = backend({ items: [], active_shop: null });
    start(server);
    expect(await screen.findByRole("heading", { level: 1, name: "Do'kon yo'q" })).toBeTruthy();
    expect(screen.queryByRole("navigation")).toBeNull();
  });
});

describe("without a valid session", () => {
  it("asks to sign in when there is nothing to sign in with, and calls nothing", async () => {
    const server = backend({ items: [membership("owner")], active_shop: SHOP_ID });
    start(server, async () => null);
    expect(await screen.findByRole("heading", { level: 1, name: "Kirish talab qilinadi" })).toBeTruthy();
    expect(server.sent).toHaveLength(0);
    expect(screen.queryByRole("navigation")).toBeNull();
  });

  it("asks to sign in when the server does not accept the session", async () => {
    const server = fakeServer(() => refusal(401, "UNAUTHENTICATED", "Avval tizimga kiring."));
    start(server);
    expect(await screen.findByRole("heading", { level: 1, name: "Kirish talab qilinadi" })).toBeTruthy();
  });

  it("returns to the sign-in notice when the session ends while working", async () => {
    let expired = false;
    const server = backend({ items: [membership("seller")], active_shop: SHOP_ID }, () =>
      expired ? refusal(401, "UNAUTHENTICATED", "Avval tizimga kiring.") : null,
    );
    start(server);
    await screen.findByText("Ali Valiyev");
    expired = true;
    go("#/customers");
    expect(await screen.findByRole("heading", { level: 1, name: "Kirish talab qilinadi" })).toBeTruthy();
    expect(screen.queryByRole("navigation")).toBeNull();
    expect(screen.queryByText("Ali Valiyev")).toBeNull();
  });

  it("offers a retry when signing in fails for another reason", async () => {
    const server = backend({ items: [membership("seller")], active_shop: SHOP_ID });
    let attempt = 0;
    start(server, async () => {
      if (attempt++ === 0) {
        throw new TypeError("Failed to fetch");
      }
      return bearer();
    });
    fireEvent.click(await screen.findByRole("button", { name: "Qayta urinish" }));
    expect(await screen.findByText("Ali Valiyev")).toBeTruthy();
  });
});

describe("routes of the workspace", () => {
  it("opens a customer inside the Customers section and keeps that tab marked", async () => {
    window.location.hash = `#/customers/${CUSTOMER_ID}`;
    const server = backend({ items: [membership("seller")], active_shop: SHOP_ID });
    start(server);
    expect(await screen.findByRole("heading", { level: 2, name: "Ali Valiyev" })).toBeTruthy();
    expect(heading()).toBe("Mijoz");
    const current = within(screen.getByRole("navigation"))
      .getAllByRole("link")
      .filter((link) => link.getAttribute("aria-current") === "page");
    expect(current.map((link) => link.textContent)).toEqual(["Mijozlar"]);

    go(`#/customers/${CUSTOMER_ID}/payment`);
    expect(heading()).toBe("To'lov qabul qilish");
    expect(await screen.findByLabelText("Summa, so'm")).toBeTruthy();

    go("#/new");
    expect(heading()).toBe("Yangi yozuv");
    expect(await screen.findByRole("link", { name: "Nasiya" })).toBeTruthy();

    go("#/customers/new");
    expect(heading()).toBe("Yangi mijoz");
    expect(screen.getByLabelText("Ism")).toBeTruthy();
  });

  it("treats anything that is not a customer identifier as an unknown page and asks the server nothing", async () => {
    const server = backend({ items: [membership("owner")], active_shop: SHOP_ID });
    start(server);
    await screen.findByText("Ali Valiyev");
    const before = server.sent.length;
    for (const hash of ["#/customers/42", "#/customers/../overview", `#/customers/${CUSTOMER_ID}/refund`, "#/customers/new/credit"]) {
      go(hash);
      expect(heading()).toBe("Sahifa topilmadi");
    }
    expect(server.sent).toHaveLength(before);
  });

  it("still keeps a seller out of the manager sections", async () => {
    const server = backend({ items: [membership("seller")], active_shop: SHOP_ID });
    start(server);
    await screen.findByText("Ali Valiyev");
    go("#/reports");
    expect(heading()).toBe("Sahifa topilmadi");
    go("#/staff");
    expect(heading()).toBe("Sahifa topilmadi");
  });
});

// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { fakeServer, ok, refusal, type Reply, type Sent, SHOP_BASE } from "../testing/fakeServer";
import { OfficeBanner } from "./OfficeBanner";
import { OwnerTotals } from "./OwnerTotals";
import { CSRF, MANAGER_ID, OTHER_SHOP, OWNER_ID, PENDING_DELETION, renderOffice, transferBody } from "./testing";

afterEach(cleanup);

const TRANSFER = `${SHOP_BASE}/ownership-transfer`;

function backend(pending: unknown, extra: (sent: Sent) => Reply | null = () => null) {
  const held = { pending };
  const server = fakeServer((sent) => {
    const special = extra(sent);
    if (special !== null) {
      return special;
    }
    if (sent.path === TRANSFER) {
      return ok({ pending: held.pending });
    }
    if (sent.path === `${TRANSFER}/accept` || sent.path === `${TRANSFER}/decline`) {
      held.pending = null;
      return ok(transferBody({ status: sent.path.endsWith("accept") ? "accepted" : "declined" }));
    }
    if (sent.path === `${SHOP_BASE}/deletion`) {
      return ok(PENDING_DELETION);
    }
    return refusal(404, "NOT_FOUND", "Topilmadi.");
  });
  return { ...server, held };
}

const asManager = (server: ReturnType<typeof backend>, reloadSession?: () => void) =>
  renderOffice(<OfficeBanner />, {
    fetch: server.fetch,
    role: "manager",
    membershipId: MANAGER_ID,
    ...(reloadSession ? { reloadSession } : {}),
  });

describe("an ownership offer, as the manager it is addressed to sees it", () => {
  it("shows the offer with its expiry and the two answers", async () => {
    const server = backend(transferBody());
    asManager(server);
    expect(
      await screen.findByText("Do'kon egasi sizga do'kon egaligini taklif qilmoqda. Taklif 2026-yil 8-oktabr, 12:00 gacha amal qiladi."),
    ).toBeTruthy();
    expect(screen.getByRole("region", { name: "Egalikni o'tkazish" })).toBeTruthy();
    expect(screen.getAllByRole("button").map((button) => button.textContent)).toEqual(["Qabul qilish", "Rad etish"]);
    // A manager may not read the deletion state, and is not asked to.
    expect(server.sent.some((sent) => sent.path.endsWith("/deletion"))).toBe(false);
  });

  it("accepts only after a yes, then reads the person's shops and roles again", async () => {
    const server = backend(transferBody());
    let reloaded = 0;
    asManager(server, () => {
      reloaded += 1;
    });
    fireEvent.click(await screen.findByRole("button", { name: "Qabul qilish" }));
    expect(screen.getByText(/Do'kon egaligini qabul qilasizmi\? Siz do'kon egasi bo'lasiz/)).toBeTruthy();
    expect(server.writes()).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Yo'q" }));
    expect(server.writes()).toHaveLength(0);
    expect(reloaded).toBe(0);

    fireEvent.click(screen.getByRole("button", { name: "Qabul qilish" }));
    fireEvent.click(screen.getByRole("button", { name: "Ha, qabul qilaman" }));
    await waitFor(() => expect(reloaded).toBe(1));
    expect(server.writes()).toHaveLength(1);
    expect(server.writes()[0]).toMatchObject({ method: "POST", path: `${TRANSFER}/accept` });
    expect(server.writes()[0]?.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
    expect(server.writes()[0]?.headers["X-CSRF-Token"]).toBe(CSRF);
  });

  it("declines only after a yes, and the offer leaves the screen without a new sign-in", async () => {
    const server = backend(transferBody());
    let reloaded = 0;
    asManager(server, () => {
      reloaded += 1;
    });
    fireEvent.click(await screen.findByRole("button", { name: "Rad etish" }));
    expect(screen.getByText("Egalik taklifi rad etilsinmi?")).toBeTruthy();
    expect(server.writes()).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Ha, rad etaman" }));
    await waitFor(() => expect(screen.queryByRole("region")).toBeNull());
    expect(server.writes()[0]).toMatchObject({ method: "POST", path: `${TRANSFER}/decline` });
    expect(reloaded).toBe(0);
  });

  it.each([
    ["NOT_TRANSFER_TARGET", "Bu taklifga faqat taklif qilingan menejer javob bera oladi."],
    ["TRANSFER_TARGET_INVALID", "Egalikni faqat shu do'konning faol menejeriga o'tkazish mumkin."],
    ["NOT_FOUND", "Topilmadi."],
  ])("shows the server's %s refusal and keeps the question open", async (code, message) => {
    const server = backend(transferBody(), (sent) => (sent.method === "POST" ? refusal(409, code, message) : null));
    asManager(server);
    fireEvent.click(await screen.findByRole("button", { name: "Qabul qilish" }));
    fireEvent.click(screen.getByRole("button", { name: "Ha, qabul qilaman" }));
    expect((await screen.findByRole("alert")).textContent).toContain(message);
    expect(screen.getByRole("button", { name: "Ha, qabul qilaman" })).toBeTruthy();
  });

  it("shows nothing to a manager the offer is not addressed to", async () => {
    const server = backend(transferBody({ to_membership: "33333333-3333-4333-8333-333333777777" }));
    const view = asManager(server);
    await waitFor(() => expect(server.sent.some((sent) => sent.path === TRANSFER)).toBe(true));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(view.container.textContent).toBe("");
  });

  it("shows nothing when no offer waits", async () => {
    const server = backend(null);
    const view = asManager(server);
    await waitFor(() => expect(server.sent).toHaveLength(1));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(view.container.textContent).toBe("");
  });

  it("shows the owner no answer buttons for their own offer", async () => {
    const server = backend(transferBody(), (sent) => (sent.path.endsWith("/deletion") ? ok({ status: "active", deletion_due: null }) : null));
    const view = renderOffice(<OfficeBanner />, { fetch: server.fetch, role: "owner", membershipId: OWNER_ID });
    // The owner's three reads: the deletion request, the ownership offer, and the support accesses.
    await waitFor(() => expect(server.sent).toHaveLength(3));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(view.container.textContent).toBe("");
  });

  it("shows a seller nothing and asks the server nothing", async () => {
    const server = backend(transferBody({ to_membership: MANAGER_ID }));
    const view = renderOffice(<OfficeBanner />, { fetch: server.fetch, role: "seller", membershipId: MANAGER_ID });
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(view.container.textContent).toBe("");
    expect(server.sent).toHaveLength(0);
  });
});

describe("an owner's combined totals", () => {
  const shop = (shopId: string, name: string, role: "owner" | "manager" | "seller") => ({ shopId, name, role, membershipId: null });
  const TOTALS = {
    items: [
      { shop_id: "a", name: "Baraka savdo", outstanding: 5808000, debtors: 9, overdue: 450000, due_today: 78000 },
      { shop_id: OTHER_SHOP, name: "Ziyo market", outstanding: 1200000, debtors: 3, overdue: 0, due_today: 0 },
    ],
    total: { outstanding: 7008000, debtors: 12, overdue: 450000, due_today: 78000 },
  };
  const totalsServer = (reply: Reply = ok(TOTALS)) =>
    fakeServer((sent) => {
      if (sent.path === "/api/v1/me/owner-totals") {
        return reply;
      }
      return sent.path === TRANSFER ? ok({ pending: null }) : ok({ status: "active", deletion_due: null });
    });
  const asked = (server: ReturnType<typeof fakeServer>) => server.sent.filter((sent) => sent.path === "/api/v1/me/owner-totals");

  it("shows each owned shop and the sum, in whole UZS, as a table with a total row", async () => {
    const server = totalsServer();
    renderOffice(<OwnerTotals />, {
      fetch: server.fetch,
      shops: [shop("a", "Baraka savdo", "owner"), shop(OTHER_SHOP, "Ziyo market", "owner")],
    });
    const table = await screen.findByRole("table", { name: "Barcha do'konlarim" });
    const rows = [...table.querySelectorAll("tr")].map((row) =>
      // Thousands are separated by a no-break space; the expectation below is written with plain ones.
      [...row.children].map((cell) => cell.textContent?.replace(/\u00a0/g, " ")),
    );
    expect(rows).toEqual([
      ["Do'kon", "Jami qarz", "Qarzdorlar", "Muddati o'tgan", "Bugun to'lanishi kerak"],
      ["Baraka savdo", "5 808 000 so'm", "9 ta mijoz", "450 000 so'm", "78 000 so'm"],
      ["Ziyo market", "1 200 000 so'm", "3 ta mijoz", "0 so'm", "0 so'm"],
      ["Jami", "7 008 000 so'm", "12 ta mijoz", "450 000 so'm", "78 000 so'm"],
    ]);
    expect(table.querySelector("tfoot th")?.getAttribute("scope")).toBe("row");
    expect(screen.getByText(/Mijozlar do'konlar o'rtasida birlashtirilmaydi/)).toBeTruthy();
  });

  it.each([
    ["one shop", [shop("a", "Baraka savdo", "owner")]],
    ["one owned shop among several", [shop("a", "Baraka savdo", "owner"), shop("b", "Ziyo market", "manager")]],
    ["no owned shop", [shop("a", "Baraka savdo", "seller"), shop("b", "Ziyo market", "manager")]],
  ])("is not shown, and not asked for, with %s", async (_name, shops) => {
    const server = totalsServer();
    renderOffice(<OwnerTotals />, { fetch: server.fetch, shops });
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(screen.queryByText("Barcha do'konlarim")).toBeNull();
    expect(asked(server)).toHaveLength(0);
  });

  it("refuses totals that are not whole numbers instead of showing them", async () => {
    const server = totalsServer(ok({ ...TOTALS, total: { ...TOTALS.total, outstanding: 7008000.5 } }));
    renderOffice(<OwnerTotals />, {
      fetch: server.fetch,
      shops: [shop("a", "Baraka savdo", "owner"), shop(OTHER_SHOP, "Ziyo market", "owner")],
    });
    expect((await screen.findByRole("alert")).textContent).toContain("Xatolik yuz berdi");
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("shows the server's refusal with a retry", async () => {
    const server = totalsServer(refusal(401, "UNAUTHENTICATED", "Avval tizimga kiring."));
    renderOffice(<OwnerTotals />, {
      fetch: server.fetch,
      shops: [shop("a", "Baraka savdo", "owner"), shop(OTHER_SHOP, "Ziyo market", "owner")],
    });
    expect((await screen.findByRole("alert")).textContent).toContain("Avval tizimga kiring.");
    expect(screen.getByRole("button", { name: "Qayta urinish" })).toBeTruthy();
  });
});

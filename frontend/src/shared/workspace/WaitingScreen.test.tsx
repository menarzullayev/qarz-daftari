// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import {
  CUSTOMER_ID,
  customerBody,
  deferred,
  fakeServer,
  ok,
  refusal,
  type Reply,
  type Sent,
  SHOP_BASE,
} from "../../testing/fakeServer";
import { renderScreen } from "../../testing/renderScreen";
import type { Role } from "../navigation";
import { WaitingScreen } from "./WaitingScreen";

afterEach(cleanup);

const VALI = { id: "99999999-9999-4999-8999-999999999991", name: "Vali T.", since: "2026-10-06T05:10:00+00:00" };
const SALIM = { id: "99999999-9999-4999-8999-999999999992", name: "Salim", since: "2026-10-05T19:30:00+00:00" };
const OTHER_CUSTOMER = "11111111-1111-4111-8111-111111111112";
const BOOK = [customerBody(), customerBody({ id: OTHER_CUSTOMER, display_name: "Vali Toshev", balance: 0 })];

/** A shop where `waiting()` people wait; `onWrite` answers an attach or a dismiss. */
function shop(waiting: () => unknown[], onWrite: (sent: Sent, attempt: number) => Reply = () => ok({})) {
  let attempt = 0;
  return fakeServer((sent) => {
    if (sent.method !== "GET") {
      return onWrite(sent, attempt++);
    }
    if (sent.path === `${SHOP_BASE}/waiting`) {
      return ok({ items: waiting() });
    }
    const q = (sent.query["q"] ?? "").toLowerCase();
    return ok({ items: BOOK.filter((customer) => customer.display_name.toLowerCase().includes(q)), next_cursor: null });
  });
}

async function open(server: ReturnType<typeof fakeServer>, role: Role = "seller") {
  renderScreen(<WaitingScreen />, { fetch: server.fetch, role });
  await waitFor(() => expect(screen.queryByText("Yuklanmoqda…")).toBeNull());
}

const rows = () => screen.getAllByRole("listitem").filter((item) => item.classList.contains("row"));
const row = (name: string) => rows().find((item) => item.textContent?.includes(name)) as HTMLElement;

describe("waiting list", () => {
  it.each(["seller", "manager", "owner"] as Role[])("shows a %s who waits and since when, in Tashkent time", async (role) => {
    const server = shop(() => [VALI, SALIM]);
    await open(server, role);
    expect(server.sent[0]).toMatchObject({ method: "GET", path: `${SHOP_BASE}/waiting` });
    expect(rows()).toHaveLength(2);
    expect(row("Vali T.").textContent).toContain("Kutmoqda: 2026-yil 6-oktabr, 10:10 dan");
    expect(row("Salim").textContent).toContain("Kutmoqda: 2026-yil 6-oktabr, 00:30 dan");
    expect(within(row("Salim")).getByRole("button", { name: "Mijozga biriktirish" })).toBeTruthy();
    expect(within(row("Salim")).getByRole("button", { name: "So'rovni rad etish" })).toBeTruthy();
  });

  it("says when nobody waits", async () => {
    await open(shop(() => []));
    expect(screen.getByText("Hozir hech kim kutmayapti.")).toBeTruthy();
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });

  it("shows the server's message when the list cannot be read, and retries", async () => {
    let fail = true;
    const server = fakeServer(() => (fail ? refusal(403, "SHOP_SUSPENDED", "Do'kon to'xtatilgan.") : ok({ items: [VALI] })));
    await open(server);
    expect(screen.getByRole("alert").textContent).toContain("Do'kon to'xtatilgan.");
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Qayta urinish" }));
    expect(await screen.findByText("Vali T.")).toBeTruthy();
  });
});

describe("attaching a waiting person to a customer", () => {
  async function pick(server: ReturnType<typeof fakeServer>, search: string, customer: string) {
    await open(server);
    fireEvent.click(within(row("Vali T.")).getByRole("button", { name: "Mijozga biriktirish" }));
    const field = screen.getByRole("searchbox", { name: "Mijozni ism yoki telefon bo'yicha qidiring" });
    fireEvent.change(field, { target: { value: search } });
    const list = await screen.findByRole("list", { name: "Biriktirish mumkin bo'lgan mijozlar" });
    await waitFor(() => expect(within(list).getAllByRole("button")).toHaveLength(1));
    fireEvent.click(within(list).getByRole("button", { name: new RegExp(customer) }));
  }

  it("searches active customers, asks, then posts one keyed attach and reads the list again", async () => {
    let attached = false;
    const server = shop(
      () => (attached ? [SALIM] : [VALI, SALIM]),
      () => {
        attached = true;
        return ok({ customer_id: OTHER_CUSTOMER, linked: true });
      },
    );
    await pick(server, "tosh", "Vali Toshev");

    const search = server.sent.filter((sent) => sent.path === `${SHOP_BASE}/customers`).at(-1);
    expect(search?.query).toMatchObject({ q: "tosh", status: "active" });
    // Nothing is sent until the choice is confirmed.
    expect(server.writes()).toHaveLength(0);
    expect(document.body.textContent).toContain("«Vali T.» «Vali Toshev» mijoziga biriktirilsinmi?");

    fireEvent.click(screen.getByRole("button", { name: "Ha, biriktirilsin" }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("«Vali T.» «Vali Toshev» mijoziga biriktirildi."));
    expect(server.writes()).toHaveLength(1);
    expect(server.writes()[0]).toMatchObject({
      method: "POST",
      path: `${SHOP_BASE}/waiting/${VALI.id}/attach`,
      body: { customer_id: OTHER_CUSTOMER },
    });
    expect(server.writes()[0]?.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
    await waitFor(() => expect(rows()).toHaveLength(1));
    expect(screen.queryByText("Vali T.")).toBeNull();
  });

  it("goes back to the search when the answer is no, and sends nothing", async () => {
    const server = shop(() => [VALI]);
    await pick(server, "ali v", "Ali Valiyev");
    fireEvent.click(screen.getByRole("button", { name: "Orqaga" }));
    expect(screen.getByRole("searchbox")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Bekor qilish" }));
    expect(screen.queryByRole("searchbox")).toBeNull();
    expect(server.writes()).toHaveLength(0);
  });

  it.each([
    [409, "CUSTOMER_ALREADY_LINKED", "Bu mijoz allaqachon Telegram hisobiga ulangan."],
    [409, "CUSTOMER_ARCHIVED", "Bu mijoz arxivda. Avval arxivdan chiqaring."],
    [404, "NOT_FOUND", "Topilmadi."],
  ])("shows the server's refusal %i %s and keeps the person in the list", async (status, code, message) => {
    const server = shop(() => [VALI], () => refusal(status, code, message));
    await pick(server, "ali v", "Ali Valiyev");
    fireEvent.click(screen.getByRole("button", { name: "Ha, biriktirilsin" }));
    expect((await screen.findByRole("alert")).textContent).toBe(message);
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.getByText("Vali T.")).toBeTruthy();
  });

  it("retries with the same key, and sends one request for a double tap", async () => {
    const held = deferred<Reply>();
    const server = shop(() => [VALI], (_sent, attempt) => (attempt === 0 ? "offline" : (held.promise as Reply)));
    await pick(server, "ali v", "Ali Valiyev");
    fireEvent.click(screen.getByRole("button", { name: "Ha, biriktirilsin" }));
    await screen.findByRole("alert");

    fireEvent.click(screen.getByRole("button", { name: "Ha, biriktirilsin" }));
    const pending = screen.getByRole("button", { name: "Saqlanmoqda…" }) as HTMLButtonElement;
    expect(pending.disabled).toBe(true);
    fireEvent.click(pending);
    held.resolve(ok({ customer_id: CUSTOMER_ID, linked: true }));
    await screen.findByRole("status");

    const keys = server.writes().map((sent) => sent.headers["Idempotency-Key"]);
    expect(keys).toHaveLength(2);
    expect(keys[0]).toBe(keys[1]);
  });
});

describe("dismissing a waiting person", () => {
  it("posts one keyed dismiss for that person and reads the list again", async () => {
    let dismissed = false;
    const server = shop(
      () => (dismissed ? [VALI] : [VALI, SALIM]),
      () => {
        dismissed = true;
        return ok({ dismissed: true });
      },
    );
    await open(server);
    fireEvent.click(within(row("Salim")).getByRole("button", { name: "So'rovni rad etish" }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("«Salim» so'rovi rad etildi."));
    expect(server.writes()).toHaveLength(1);
    expect(server.writes()[0]).toMatchObject({ method: "POST", path: `${SHOP_BASE}/waiting/${SALIM.id}/dismiss` });
    expect(server.writes()[0]?.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
    await waitFor(() => expect(rows()).toHaveLength(1));
  });

  it("disables every control while a request is in flight", async () => {
    const held = deferred<Reply>();
    const server = shop(() => [VALI, SALIM], () => held.promise as Reply);
    await open(server);
    fireEvent.click(within(row("Salim")).getByRole("button", { name: "So'rovni rad etish" }));
    for (const control of screen.getAllByRole("button")) {
      expect((control as HTMLButtonElement).disabled).toBe(true);
    }
    fireEvent.click(within(row("Vali T.")).getByRole("button", { name: "So'rovni rad etish" }));
    held.resolve(ok({ dismissed: true }));
    await screen.findByRole("status");
    expect(server.writes()).toHaveLength(1);
  });

  it("shows the server's refusal", async () => {
    const server = shop(() => [VALI], () => refusal(404, "NOT_FOUND", "Topilmadi."));
    await open(server);
    fireEvent.click(screen.getByRole("button", { name: "So'rovni rad etish" }));
    expect((await screen.findByRole("alert")).textContent).toBe("Topilmadi.");
    expect(screen.getByText("Vali T.")).toBeTruthy();
  });
});

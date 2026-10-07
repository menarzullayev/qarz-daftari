// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { CUSTOMER_ID, customerBody, fakeServer, ok, refusal, type Reply, type Sent, SHOP_BASE, SHOP_ID } from "../testing/fakeServer";
import { ACTION_GROUPS, ActivityScreen } from "./ActivityScreen";
import { MANAGER_ID, OWNER_ID, renderOffice, STAFF } from "./testing";

beforeEach(() => {
  window.location.hash = "";
});
afterEach(cleanup);

const GONE_ID = "33333333-3333-4333-8333-333333999999";

const activityBody = (id: number, overrides: Record<string, unknown> = {}) => ({
  id: `99999999-9999-4999-8999-99999999990${id}`,
  at: "2026-10-06T05:10:00+00:00",
  actor_kind: "staff",
  actor_id: MANAGER_ID,
  action: "customer.created",
  subject_type: "customer",
  subject_id: CUSTOMER_ID,
  ...overrides,
});

const ROWS = [
  activityBody(1),
  activityBody(2, { actor_id: OWNER_ID, action: "staff.updated", subject_type: "membership", subject_id: MANAGER_ID }),
  activityBody(3, { actor_kind: "customer", actor_id: null, action: "customer.linked" }),
  activityBody(4, { actor_kind: "system", actor_id: null, action: "subscription.limited", subject_type: "shop", subject_id: SHOP_ID }),
  activityBody(5, { actor_id: GONE_ID, action: "ledger.future_thing", subject_type: "gadget", subject_id: null }),
];

function backend(extra: (sent: Sent) => Reply | null = () => null) {
  return fakeServer((sent) => {
    const special = extra(sent);
    if (special !== null) {
      return special;
    }
    switch (sent.path) {
      case `${SHOP_BASE}/activity`:
        return ok({ items: ROWS, next_cursor: null });
      case `${SHOP_BASE}/staff`:
        return ok({ items: STAFF });
      case `${SHOP_BASE}/customers`:
        return ok({ items: [customerBody()], next_cursor: null });
      case `${SHOP_BASE}/ownership-transfer`:
        return ok({ pending: null });
      case `${SHOP_BASE}/deletion`:
        return ok({ status: "active", deletion_due: null });
    }
    return refusal(404, "NOT_FOUND", "Topilmadi.");
  });
}

const logCalls = (server: ReturnType<typeof fakeServer>) => server.sent.filter((sent) => sent.path === `${SHOP_BASE}/activity`);
const cells = (row: HTMLElement) => [...row.querySelectorAll("th, td")].map((cell) => cell.textContent);

describe("who may read the activity log", () => {
  it.each(["manager", "seller"] as const)("shows a %s nothing and asks the server nothing", async (role) => {
    const server = backend();
    renderOffice(<ActivityScreen />, { fetch: server.fetch, role });
    expect(screen.getByText("Bunday sahifa yo'q yoki sizda unga ruxsat yo'q.")).toBeTruthy();
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(server.sent.filter((sent) => /activity|staff/.test(sent.path))).toHaveLength(0);
    expect(screen.queryByLabelText("Amal turi")).toBeNull();
  });
});

describe("the log", () => {
  it("is a real table that says when, who by role and membership, what, and about what", async () => {
    const server = backend();
    renderOffice(<ActivityScreen />, { fetch: server.fetch });
    const table = await screen.findByRole("table", { name: "Amallar jurnali" });
    await within(table).findAllByText("Menejer · 3a1b2c");
    expect(within(table).getAllByRole("columnheader").map((cell) => cell.textContent)).toEqual(["Vaqt", "Kim", "Nima", "Nima haqida"]);
    const rows = within(table).getAllByRole("row").slice(1);
    expect(cells(rows[0] as HTMLElement).slice(0, 3)).toEqual(["2026-yil 6-oktabr, 10:10", "Menejer · 3a1b2c", "Mijoz qo'shildi"]);
    expect(cells(rows[1] as HTMLElement)).toEqual([
      "2026-yil 6-oktabr, 10:10",
      "Do'kon egasi · 333333 (siz)",
      "Xodim o'zgartirildi",
      "Menejer · 3a1b2c",
    ]);
    expect(cells(rows[2] as HTMLElement).slice(1, 3)).toEqual(["Mijoz", "Mijoz Telegramga ulandi"]);
    expect(cells(rows[3] as HTMLElement).slice(1)).toEqual(["Tizim", "Obuna tugadi: do'kon cheklangan rejimga o'tdi", "Do'kon"]);
    // Someone no longer on the staff keeps their code; an action or subject the catalog does not know
    // is shown under the server's own name rather than hidden.
    expect(cells(rows[4] as HTMLElement).slice(1)).toEqual(["Xodim · 999999", "ledger.future_thing", "gadget"]);
  });

  it("links a customer the action was about to that customer's page", async () => {
    const server = backend();
    renderOffice(<ActivityScreen />, { fetch: server.fetch });
    const links = await screen.findAllByRole("link", { name: "Mijoz sahifasi" });
    expect(links).toHaveLength(2);
    expect(links[0]?.getAttribute("href")).toBe(`#/customers/${CUSTOMER_ID}`);
    // Only a customer is linked: a membership, a shop and an unknown subject are not.
    expect(within(await screen.findByRole("table")).getAllByRole("link")).toHaveLength(2);
  });

  it("reads the next page with the server's cursor and keeps the filters", async () => {
    const server = backend((sent) => {
      if (sent.path !== `${SHOP_BASE}/activity`) {
        return null;
      }
      return sent.query["cursor"] === "c2"
        ? ok({ items: [activityBody(7, { action: "customer.archived" })], next_cursor: null })
        : ok({ items: [activityBody(6)], next_cursor: "c2" });
    });
    renderOffice(<ActivityScreen />, { fetch: server.fetch });
    fireEvent.change(await screen.findByLabelText("Amal turi"), { target: { value: "customer" } });
    fireEvent.click(await screen.findByRole("button", { name: "Yana ko'rsatish" }));
    expect(await screen.findByText("Mijoz arxivlandi")).toBeTruthy();
    expect(screen.getByText("Mijoz qo'shildi")).toBeTruthy();
    expect(logCalls(server).at(-1)?.query).toEqual({ action: "customer", cursor: "c2" });
    expect(screen.queryByRole("button", { name: "Yana ko'rsatish" })).toBeNull();
  });

  it("says so when nothing matches, and shows a failure with a retry", async () => {
    let mode: "empty" | "fail" | "ok" = "empty";
    const server = backend((sent) => {
      if (sent.path !== `${SHOP_BASE}/activity` || mode === "ok") {
        return null;
      }
      return mode === "empty" ? ok({ items: [], next_cursor: null }) : refusal(403, "SHOP_SUSPENDED", "Do'kon to'xtatilgan.");
    });
    renderOffice(<ActivityScreen />, { fetch: server.fetch });
    expect(await screen.findByText("Bu filtr bo'yicha amal yo'q.")).toBeTruthy();
    mode = "fail";
    fireEvent.change(screen.getByLabelText("Amal turi"), { target: { value: "staff" } });
    expect((await screen.findByRole("alert")).textContent).toContain("Do'kon to'xtatilgan.");
    mode = "ok";
    fireEvent.click(screen.getByRole("button", { name: "Qayta urinish" }));
    expect(await screen.findByRole("table")).toBeTruthy();
  });
});

describe("filters", () => {
  it("sends no filter at first, and has a label tied to each control", async () => {
    const server = backend();
    renderOffice(<ActivityScreen />, { fetch: server.fetch });
    await screen.findByRole("table");
    expect(logCalls(server)[0]?.query).toEqual({});
    for (const label of ["Amal turi", "Xodim", "Mijoz bo'yicha"]) {
      const control = screen.getByLabelText(label);
      expect(control.id).not.toBe("");
      expect(document.querySelector(`label[for="${control.id}"]`)?.textContent).toBe(label);
    }
  });

  it("offers every group of actions and filters by the start of the action's name", async () => {
    const server = backend();
    renderOffice(<ActivityScreen />, { fetch: server.fetch });
    const select = (await screen.findByLabelText("Amal turi")) as HTMLSelectElement;
    expect([...select.options].map((option) => option.value)).toEqual(["", ...ACTION_GROUPS]);
    expect([...select.options].every((option) => option.textContent !== "")).toBe(true);
    fireEvent.change(select, { target: { value: "ledger" } });
    await waitFor(() => expect(logCalls(server).at(-1)?.query).toEqual({ action: "ledger" }));
    fireEvent.change(select, { target: { value: "" } });
    await waitFor(() => expect(logCalls(server).at(-1)?.query).toEqual({}));
  });

  it("filters by a member of staff chosen from the shop's staff", async () => {
    const server = backend();
    renderOffice(<ActivityScreen />, { fetch: server.fetch });
    const select = (await screen.findByLabelText("Xodim")) as HTMLSelectElement;
    await waitFor(() => expect(select.options).toHaveLength(4));
    expect([...select.options].map((option) => option.textContent)).toEqual([
      "Barcha xodimlar",
      "Do'kon egasi · 333333 (siz)",
      "Menejer · 3a1b2c",
      "Sotuvchi · 3d4e5f",
    ]);
    fireEvent.change(select, { target: { value: MANAGER_ID } });
    await waitFor(() => expect(logCalls(server).at(-1)?.query).toEqual({ actor: MANAGER_ID }));
  });

  it("filters by a customer found by name, names the filter, and removes it", async () => {
    const server = backend();
    renderOffice(<ActivityScreen />, { fetch: server.fetch });
    await screen.findByRole("table");
    expect(server.sent.some((sent) => sent.path === `${SHOP_BASE}/customers`)).toBe(false);
    fireEvent.change(screen.getByLabelText("Mijoz bo'yicha"), { target: { value: " Ali " } });
    fireEvent.submit(screen.getByRole("search"));
    const found = await screen.findByRole("list", { name: "Topilgan mijozlar" });
    expect(server.sent.find((sent) => sent.path === `${SHOP_BASE}/customers`)?.query).toEqual({ q: "Ali", limit: "5" });
    fireEvent.click(within(found).getByRole("button", { name: /Ali Valiyev/ }));
    await waitFor(() => expect(logCalls(server).at(-1)?.query).toEqual({ subject: CUSTOMER_ID }));
    expect(screen.getByText("Faqat shu mijoz: Ali Valiyev")).toBeTruthy();
    // The filtered customer is now named in the rows too.
    expect((await screen.findAllByRole("link", { name: "Ali Valiyev" }))[0]?.getAttribute("href")).toBe(`#/customers/${CUSTOMER_ID}`);

    fireEvent.click(screen.getByRole("button", { name: "Mijoz filtrini olib tashlash" }));
    await waitFor(() => expect(logCalls(server).at(-1)?.query).toEqual({}));
    expect(screen.getByLabelText("Mijoz bo'yicha")).toBeTruthy();
  });

  it("filters by the customer of a row, and combines the three filters", async () => {
    const server = backend();
    renderOffice(<ActivityScreen />, { fetch: server.fetch });
    fireEvent.click((await screen.findAllByRole("button", { name: "Faqat shu mijoz" }))[0] as HTMLElement);
    await waitFor(() => expect(logCalls(server).at(-1)?.query).toEqual({ subject: CUSTOMER_ID }));
    expect(screen.getByText("Faqat shu mijoz: tanlangan mijoz")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Amal turi"), { target: { value: "customer" } });
    fireEvent.change(screen.getByLabelText("Xodim"), { target: { value: MANAGER_ID } });
    await waitFor(() =>
      expect(logCalls(server).at(-1)?.query).toEqual({ subject: CUSTOMER_ID, action: "customer", actor: MANAGER_ID }),
    );
  });

  it("says so when no customer matches the search", async () => {
    const server = backend((sent) => (sent.path === `${SHOP_BASE}/customers` ? ok({ items: [], next_cursor: null }) : null));
    renderOffice(<ActivityScreen />, { fetch: server.fetch });
    fireEvent.change(await screen.findByLabelText("Mijoz bo'yicha"), { target: { value: "zzz" } });
    fireEvent.submit(screen.getByRole("search"));
    expect(await screen.findByText("Hech narsa topilmadi.")).toBeTruthy();
  });

  it("never writes: the log is read-only", async () => {
    const server = backend();
    renderOffice(<ActivityScreen />, { fetch: server.fetch });
    await screen.findByRole("table");
    fireEvent.change(screen.getByLabelText("Amal turi"), { target: { value: "shop" } });
    await waitFor(() => expect(logCalls(server)).toHaveLength(2));
    expect(server.writes()).toHaveLength(0);
  });
});

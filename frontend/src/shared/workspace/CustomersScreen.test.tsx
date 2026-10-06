// @vitest-environment jsdom
import { act, cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { customerBody, fakeServer, ok, refusal, SHOP_BASE } from "../../testing/fakeServer";
import { renderScreen } from "../../testing/renderScreen";
import { CustomersScreen, SEARCH_DELAY_MS } from "./CustomersScreen";

afterEach(cleanup);

const ALI = customerBody();
const VALI = customerBody({ id: "44444444-4444-4444-8444-444444444444", display_name: "Vali", phone: null, balance: 0 });

const page = (items: unknown[], nextCursor: string | null = null) => ok({ items, next_cursor: nextCursor });
const rows = () => within(screen.getByRole("list")).getAllByRole("listitem");
const search = () => screen.getByRole<HTMLInputElement>("searchbox", { name: "Ism yoki telefon bo'yicha qidirish" });

describe("customer book", () => {
  it("lists active customers with their balance and a link to each", async () => {
    const server = fakeServer(() => page([ALI, VALI]));
    renderScreen(<CustomersScreen />, { fetch: server.fetch });
    expect(screen.getByRole("status").textContent).toBe("Yuklanmoqda…");
    await screen.findByText("Vali");

    expect(server.sent[0]).toMatchObject({ method: "GET", path: `${SHOP_BASE}/customers`, query: { status: "active" } });
    const [first, second] = rows();
    expect(first?.textContent).toBe("Ali Valiyev120\u00a0000 so'm+998901234567");
    expect(second?.textContent).toBe("Vali0 so'm");
    expect(within(first as HTMLElement).getByRole("link").getAttribute("href")).toBe(`#/customers/${ALI.id}`);
    expect(screen.getByRole("link", { name: "Yangi mijoz" }).getAttribute("href")).toBe("#/customers/new");
  });

  it("searches by what was typed once typing pauses", async () => {
    const server = fakeServer((sent) => page(sent.query["q"] === "val" ? [VALI] : [ALI, VALI]));
    renderScreen(<CustomersScreen />, { fetch: server.fetch });
    await screen.findByText("Ali Valiyev");

    // Keystrokes a moment apart, each well inside the pause the search waits for.
    for (const typed of ["v", "va", " val "]) {
      fireEvent.change(search(), { target: { value: typed } });
      await act(() => new Promise((resolve) => setTimeout(resolve, SEARCH_DELAY_MS / 6)));
    }
    await waitFor(() => expect(screen.queryByText("Ali Valiyev")).toBeNull());
    expect(rows()).toHaveLength(1);
    // One search for the final text, not one per keystroke.
    expect(server.sent.map((sent) => sent.query["q"])).toEqual([undefined, "val"]);
  });

  it("searches at once when the form is submitted", async () => {
    const server = fakeServer(() => page([ALI]));
    renderScreen(<CustomersScreen />, { fetch: server.fetch });
    await screen.findByText("Ali Valiyev");
    fireEvent.change(search(), { target: { value: "90 123" } });
    fireEvent.submit(screen.getByRole("search"));
    await waitFor(() => expect(server.sent).toHaveLength(2));
    expect(server.sent[1]?.query).toEqual({ q: "90 123", status: "active" });
  });

  it("shows archived customers when asked", async () => {
    const server = fakeServer((sent) => page(sent.query["status"] === "archived" ? [VALI] : [ALI]));
    renderScreen(<CustomersScreen />, { fetch: server.fetch });
    await screen.findByText("Ali Valiyev");
    fireEvent.click(screen.getByRole("button", { name: "Arxiv" }));
    await screen.findByText("Vali");
    expect(screen.queryByText("Ali Valiyev")).toBeNull();
    expect(screen.getByRole("button", { name: "Arxiv" }).getAttribute("aria-pressed")).toBe("true");
    expect(server.sent[1]?.query).toEqual({ status: "archived" });
  });

  it("loads more customers with the cursor and keeps the ones already shown", async () => {
    const server = fakeServer((sent) => (sent.query["cursor"] === "after-ali" ? page([VALI]) : page([ALI], "after-ali")));
    renderScreen(<CustomersScreen />, { fetch: server.fetch });
    fireEvent.click(await screen.findByRole("button", { name: "Yana ko'rsatish" }));
    await screen.findByText("Vali");
    expect(rows()).toHaveLength(2);
    expect(server.sent[1]?.query).toEqual({ status: "active", cursor: "after-ali" });
    expect(screen.queryByRole("button", { name: "Yana ko'rsatish" })).toBeNull();
  });

  it("tells an empty book from a search with no match and from an empty archive", async () => {
    const server = fakeServer(() => page([]));
    renderScreen(<CustomersScreen />, { fetch: server.fetch });
    expect(await screen.findByText("Hali mijoz yo'q. Birinchi mijozni qo'shing.")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Arxiv" }));
    expect(await screen.findByText("Arxivda mijoz yo'q.")).toBeTruthy();

    fireEvent.change(search(), { target: { value: "zzz" } });
    fireEvent.submit(screen.getByRole("search"));
    expect(await screen.findByText("Hech narsa topilmadi.")).toBeTruthy();
    expect(screen.queryByRole("list")).toBeNull();
  });

  it("shows the server's message when the list cannot be read, and retries", async () => {
    let fail = true;
    const server = fakeServer(() => (fail ? refusal(403, "SHOP_SUSPENDED", "Do'kon to'xtatilgan.") : page([ALI])));
    renderScreen(<CustomersScreen />, { fetch: server.fetch });
    expect((await screen.findByRole("alert")).textContent).toContain("Do'kon to'xtatilgan.");
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Qayta urinish" }));
    expect(await screen.findByText("Ali Valiyev")).toBeTruthy();
  });

  it("falls back to its own words when there is no connection", async () => {
    const server = fakeServer(() => "offline");
    renderScreen(<CustomersScreen />, { fetch: server.fetch, language: "ru" });
    expect((await screen.findByRole("alert")).textContent).toContain("Не удалось связаться с сервером.");
  });
});

describe("picking a customer for a new entry", () => {
  it("offers a credit sale for everyone and a payment only to someone who owes", async () => {
    const server = fakeServer(() => page([ALI, VALI]));
    renderScreen(<CustomersScreen pick />, { fetch: server.fetch });
    await screen.findByText("Vali");
    expect(screen.getByText("Mijozni tanlang yoki yangisini qo'shing.")).toBeTruthy();

    const [owes, settled] = rows();
    const links = (row: HTMLElement | undefined) =>
      within(row as HTMLElement)
        .getAllByRole("link")
        .map((link) => [link.textContent, link.getAttribute("href")]);
    expect(links(owes)).toEqual([
      ["Nasiya", `#/customers/${ALI.id}/credit`],
      ["To'lov", `#/customers/${ALI.id}/payment`],
    ]);
    // Vali owes nothing, and a payment cannot exceed the debt.
    expect(links(settled)).toEqual([["Nasiya", `#/customers/${VALI.id}/credit`]]);
  });

  it("has no archive switch: an entry is for an active customer", async () => {
    const server = fakeServer(() => page([ALI]));
    renderScreen(<CustomersScreen pick />, { fetch: server.fetch });
    await screen.findByText("Ali Valiyev");
    expect(screen.queryByRole("button", { name: "Arxiv" })).toBeNull();
    expect(server.sent[0]?.query).toEqual({ status: "active" });
  });
});

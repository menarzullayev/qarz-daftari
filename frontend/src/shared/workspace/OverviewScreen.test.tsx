// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { customerBody, fakeServer, NO_OVERDUE, ok, refusal, type Reply, type Sent, SHOP_BASE } from "../../testing/fakeServer";
import { exact, renderScreen } from "../../testing/renderScreen";
import { OverviewScreen } from "./OverviewScreen";

afterEach(cleanup);

const TOTALS = { outstanding: 1250000, debtors: 5, overdue: { amount: 320000, customers: 2 }, due_today: 45000 };

const ALI = { ...customerBody(), overdue: { amount: 45000, since: "2026-09-24", days: 12, due_today: 20000 } };
const VALI = {
  ...customerBody({ id: "44444444-4444-4444-8444-444444444444", display_name: "Vali", balance: 80000 }),
  overdue: NO_OVERDUE,
};

function shop(debtors: (sent: Sent) => Reply, totals: (sent: Sent) => Reply = () => ok(TOTALS)) {
  return fakeServer((sent) => (sent.path.endsWith("/overview") ? totals(sent) : debtors(sent)));
}

const debtorRows = () => within(screen.getByRole("list")).getAllByRole("listitem");

describe("overview", () => {
  it("shows what the shop is owed, what is late, and what falls due today", async () => {
    const server = shop(() => ok({ items: [ALI, VALI], next_cursor: null }));
    renderScreen(<OverviewScreen />, { fetch: server.fetch });
    expect(screen.getAllByRole("status").map((status) => status.textContent)).toEqual(["Yuklanmoqda…", "Yuklanmoqda…"]);

    expect(await screen.findByText(exact("1\u00a0250\u00a0000 so'm"))).toBeTruthy();
    const figures = screen.getAllByRole("term").map((term) => [
      term.textContent,
      ...Array.from(term.parentElement?.querySelectorAll("dd") ?? [], (value) => value.textContent),
    ]);
    expect(figures).toEqual([
      ["Jami qarz", "1\u00a0250\u00a0000 so'm", "5 ta mijoz"],
      ["Muddati o'tgan", "320\u00a0000 so'm", "2 ta mijoz"],
      ["Bugun to'lanishi kerak", "45\u00a0000 so'm"],
    ]);
    expect(server.sent.map((sent) => sent.path).sort()).toEqual([`${SHOP_BASE}/overview`, `${SHOP_BASE}/overview/debtors`]);
  });

  it("lists debtors in the server's order with a link to each and their overdue status", async () => {
    const server = shop(() => ok({ items: [ALI, VALI], next_cursor: null }));
    renderScreen(<OverviewScreen />, { fetch: server.fetch });
    await screen.findByText("Vali");

    const [first, second] = debtorRows();
    expect(first?.textContent).toContain("Ali Valiyev");
    expect(first?.textContent).toContain("120\u00a0000 so'm");
    expect(first?.textContent).toContain("45\u00a0000 so'm muddati o'tgan");
    expect(first?.textContent).toContain("12 kun kechikkan");
    expect(first?.textContent).toContain("Bugun to'lanishi kerak: 20\u00a0000 so'm");
    expect(within(first as HTMLElement).getByRole("link").getAttribute("href")).toBe(`#/customers/${ALI.id}`);
    // A debtor who is not late carries no overdue line.
    expect(second?.textContent).toBe("Vali80\u00a0000 so'm");
  });

  it("asks the server for overdue debtors only when the filter is switched", async () => {
    const server = shop((sent) => ok({ items: sent.query["overdue"] === "true" ? [ALI] : [ALI, VALI], next_cursor: null }));
    renderScreen(<OverviewScreen />, { fetch: server.fetch });
    await screen.findByText("Vali");
    const all = screen.getByRole("button", { name: "Hammasi" });
    const late = screen.getByRole("button", { name: "Muddati o'tganlar" });
    expect(all.getAttribute("aria-pressed")).toBe("true");

    fireEvent.click(late);
    await waitFor(() => expect(screen.queryByText("Vali")).toBeNull());
    await screen.findByText("Ali Valiyev");
    expect(late.getAttribute("aria-pressed")).toBe("true");
    const debtorCalls = server.sent.filter((sent) => sent.path.endsWith("/debtors")).map((sent) => sent.query);
    expect(debtorCalls).toEqual([{ overdue: "false" }, { overdue: "true" }]);
  });

  it("loads the next page with the server's cursor", async () => {
    const server = shop((sent) =>
      sent.query["cursor"] === "after-ali" ? ok({ items: [VALI], next_cursor: null }) : ok({ items: [ALI], next_cursor: "after-ali" }),
    );
    renderScreen(<OverviewScreen />, { fetch: server.fetch });
    fireEvent.click(await screen.findByRole("button", { name: "Yana ko'rsatish" }));
    await screen.findByText("Vali");
    expect(debtorRows()).toHaveLength(2);
    expect(screen.queryByRole("button", { name: "Yana ko'rsatish" })).toBeNull();
  });

  it.each([
    [false, "Hozircha hech kim qarzdor emas."],
    [true, "Muddati o'tgan qarz yo'q."],
  ])("says so when there is nobody to list (overdue only: %s)", async (onlyOverdue, message) => {
    const server = shop(() => ok({ items: [], next_cursor: null }));
    renderScreen(<OverviewScreen />, { fetch: server.fetch });
    await screen.findByText("Hozircha hech kim qarzdor emas.");
    if (onlyOverdue) {
      fireEvent.click(screen.getByRole("button", { name: "Muddati o'tganlar" }));
    }
    expect(await screen.findByText(message)).toBeTruthy();
    expect(screen.queryByRole("list")).toBeNull();
  });

  it("shows the server's refusal and loads again on retry", async () => {
    let suspended = true;
    const answer = () => (suspended ? refusal(403, "SHOP_SUSPENDED", "Do'kon to'xtatilgan.") : ok({ items: [ALI], next_cursor: null }));
    const server = shop(answer, () => (suspended ? refusal(403, "SHOP_SUSPENDED", "Do'kon to'xtatilgan.") : ok(TOTALS)));
    renderScreen(<OverviewScreen />, { fetch: server.fetch });
    await waitFor(() => expect(screen.getAllByRole("alert")).toHaveLength(2));
    expect(screen.getAllByRole("alert").map((alert) => alert.querySelector("p")?.textContent)).toEqual([
      "Do'kon to'xtatilgan.",
      "Do'kon to'xtatilgan.",
    ]);

    suspended = false;
    for (const retry of screen.getAllByRole("button", { name: "Qayta urinish" })) {
      fireEvent.click(retry);
    }
    expect(await screen.findByText("Ali Valiyev")).toBeTruthy();
    expect(await screen.findByText(exact("1\u00a0250\u00a0000 so'm"))).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("is in Russian with Russian plurals when that is the language", async () => {
    const server = shop(() => ok({ items: [ALI], next_cursor: null }));
    renderScreen(<OverviewScreen />, { fetch: server.fetch, language: "ru" });
    await screen.findByText("Ali Valiyev");
    expect(screen.getByRole("heading", { level: 2 }).textContent).toBe("Должники");
    expect(screen.getByText("5 клиентов")).toBeTruthy();
    expect(screen.getByText("2 клиента")).toBeTruthy();
    expect(screen.getByText("Просрочено на 12 дней")).toBeTruthy();
  });
});

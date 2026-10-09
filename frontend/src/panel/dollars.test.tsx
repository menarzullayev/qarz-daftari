// @vitest-environment jsdom
import { cleanup, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { ShopMembership } from "../shared/api";
import { CustomersScreen } from "../shared/workspace/CustomersScreen";
import { OverviewScreen } from "../shared/workspace/OverviewScreen";
import { CUSTOMER_ID, customerBody, fakeServer, NO_OVERDUE, ok, SHOP_BASE, SHOP_ID } from "../testing/fakeServer";
import { renderScreen } from "../testing/renderScreen";
import { OwnerTotals } from "./OwnerTotals";
import { DesktopLayout } from "./tables";
import { OTHER_SHOP, renderOffice, setWidth } from "./testing";

/**
 * US dollars beside so'm in the web panel's own renderings: the tables of a wide screen and the totals
 * of every shop a person owns. A dollar figure stands under its so'm figure in the same cell; the two
 * are never added, and a shop without dollars has a cell with its so'm figure alone, as before.
 */

afterEach(cleanup);

const plain = (text: string | null | undefined) => (text ?? "").replace(/\u00a0/g, " ");
const cells = (table: HTMLElement) => [...table.querySelectorAll("tr")].map((row) => [...row.children].map((cell) => plain(cell.textContent)));

function expectNoDollars() {
  const page = plain(document.body.textContent);
  expect(page).not.toContain("$");
  expect(page.toLowerCase()).not.toContain("dollar");
  expect(page).not.toContain("USD");
  expect(screen.queryByRole("group", { name: "Valyuta" })).toBeNull();
  expect(document.querySelector(".money")).toBeNull();
}

const VALI = "11111111-1111-4111-8111-111111111112";

describe("the customers' table", () => {
  const show = (items: unknown[], pick = false) => {
    setWidth(1280);
    const server = fakeServer(() => ok({ items, next_cursor: null }));
    renderScreen(
      <DesktopLayout>
        <CustomersScreen pick={pick} />
      </DesktopLayout>,
      { fetch: server.fetch, role: "owner" },
    );
    return screen.findByRole("table", { name: "Mijozlar ro'yxati" });
  };

  it("shows the dollar debt under the so'm debt, in the same column", async () => {
    const table = await show([
      customerBody({ usd: { balance: 1250, credit_limit: null } }),
      customerBody({ id: VALI, display_name: "Vali", phone: null, balance: 0, usd: { balance: 125050, credit_limit: null } }),
    ]);
    expect(cells(table)).toEqual([
      ["Mijoz", "Telefon", "Qarz"],
      ["Ali Valiyev", "+998901234567", "120 000 so'm 12.50 $"],
      ["Vali", "—", "0 so'm 1 250.50 $"],
    ]);
    expect(table.querySelectorAll("td.table__num .money")).toHaveLength(4);
  });

  it("offers a payment to a customer who owes dollars only, and none to one who owes nothing", async () => {
    const table = await show(
      [
        customerBody({ balance: 0, usd: { balance: 1250, credit_limit: null } }),
        customerBody({ id: VALI, display_name: "Vali", phone: null, balance: 0, usd: { balance: 0, credit_limit: null } }),
      ],
      true,
    );
    expect(cells(table).map((row) => row[3])).toEqual(["Amallar", "NasiyaTo'lov", "Nasiya"]);
  });

  it("shows a shop without dollars the so'm debt alone", async () => {
    const table = await show([customerBody()]);
    expect(cells(table)).toEqual([
      ["Mijoz", "Telefon", "Qarz"],
      ["Ali Valiyev", "+998901234567", "120 000 so'm"],
    ]);
    expectNoDollars();
  });
});

describe("the debtors' table", () => {
  const TOTALS = { outstanding: 120000, debtors: 1, overdue: { amount: 45000, customers: 1 }, due_today: 0 };
  const USD_TOTALS = { outstanding: 1250, debtors: 1, overdue: { amount: 500, customers: 1 }, due_today: 250 };
  const LATE = { amount: 45000, since: "2026-10-01", days: 5, due_today: 0 };
  const show = (dollars: boolean) => {
    setWidth(1280);
    const debtor = {
      ...customerBody(dollars ? { usd: { balance: 1250, credit_limit: null, overdue: { amount: 500, since: "2026-10-01", days: 5, due_today: 250 } } } : {}),
      overdue: LATE,
    };
    const server = fakeServer((sent) =>
      sent.path === `${SHOP_BASE}/overview` ? ok(dollars ? { ...TOTALS, usd: USD_TOTALS } : TOTALS) : ok({ items: [debtor], next_cursor: null }),
    );
    renderScreen(
      <DesktopLayout>
        <OverviewScreen />
      </DesktopLayout>,
      { fetch: server.fetch, role: "owner" },
    );
    return screen.findByRole("table", { name: "Qarzdorlar" });
  };

  it("shows each dollar figure under its so'm figure", async () => {
    const table = await show(true);
    expect(cells(table)).toEqual([
      ["Mijoz", "Qarz", "Muddati o'tgan", "Kechikish", "Bugun to'lanadi"],
      ["Ali Valiyev", "120 000 so'm 12.50 $", "45 000 so'm 5.00 $", "5 kun kechikkan", "0 so'm 2.50 $"],
    ]);
  });

  it("shows a shop without dollars the so'm figures alone, with a dash for nothing", async () => {
    const table = await show(false);
    expect(cells(table)).toEqual([
      ["Mijoz", "Qarz", "Muddati o'tgan", "Kechikish", "Bugun to'lanadi"],
      ["Ali Valiyev", "120 000 so'm", "45 000 so'm", "5 kun kechikkan", "—"],
    ]);
    expectNoDollars();
  });
});

describe("the totals of every shop a person owns", () => {
  const owned = (shopId: string, name: string): ShopMembership => ({ shopId, name, role: "owner", membershipId: null });
  const SHOPS = [owned(SHOP_ID, "Baraka savdo"), owned(OTHER_SHOP, "Ziyo market")];
  const BARAKA = { shop_id: SHOP_ID, name: "Baraka savdo", outstanding: 5808000, debtors: 9, overdue: 450000, due_today: 78000 };
  const ZIYO = { shop_id: OTHER_SHOP, name: "Ziyo market", outstanding: 1200000, debtors: 3, overdue: 0, due_today: 0 };
  const TOTAL = { outstanding: 7008000, debtors: 12, overdue: 450000, due_today: 78000 };
  const USD = { outstanding: 125050, debtors: 2, overdue: 5000, due_today: 1250 };
  const show = (body: unknown) => {
    const server = fakeServer((sent) =>
      sent.path === "/api/v1/me/owner-totals" ? ok(body) : sent.path.endsWith("/transfer") ? ok({ pending: null }) : ok({ status: "active", deletion_due: null }),
    );
    renderOffice(<OwnerTotals />, { fetch: server.fetch, shops: SHOPS });
    return screen.findByRole("table", { name: "Barcha do'konlarim" });
  };

  it("shows the dollars of a shop that works in them, and of the sum, beside the so'm and never added", async () => {
    const table = await show({ items: [{ ...BARAKA, usd: USD }, ZIYO], total: { ...TOTAL, usd: USD } });
    expect(cells(table)).toEqual([
      ["Do'kon", "Jami qarz", "Qarzdorlar", "Muddati o'tgan", "Bugun to'lanishi kerak"],
      ["Baraka savdo", "5 808 000 so'm 1 250.50 $", "9 ta mijoz $ 2 ta mijoz", "450 000 so'm 50.00 $", "78 000 so'm 12.50 $"],
      // A shop without dollars among them keeps its so'm figures alone.
      ["Ziyo market", "1 200 000 so'm", "3 ta mijoz", "0 so'm", "0 so'm"],
      ["Jami", "7 008 000 so'm 1 250.50 $", "12 ta mijoz $ 2 ta mijoz", "450 000 so'm 50.00 $", "78 000 so'm 12.50 $"],
    ]);
  });

  it("refuses dollar totals that are not whole cents instead of showing them", async () => {
    const server = fakeServer((sent) =>
      sent.path === "/api/v1/me/owner-totals"
        ? ok({ items: [{ ...BARAKA, usd: { ...USD, outstanding: 1250.5 } }, ZIYO], total: TOTAL })
        : ok({ pending: null, status: "active", deletion_due: null }),
    );
    renderOffice(<OwnerTotals />, { fetch: server.fetch, shops: SHOPS });
    expect((await screen.findByRole("alert")).textContent).toContain("Xatolik yuz berdi");
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("shows owners of shops without dollars the so'm totals alone", async () => {
    const table = await show({ items: [BARAKA, ZIYO], total: TOTAL });
    expect(cells(table)).toEqual([
      ["Do'kon", "Jami qarz", "Qarzdorlar", "Muddati o'tgan", "Bugun to'lanishi kerak"],
      ["Baraka savdo", "5 808 000 so'm", "9 ta mijoz", "450 000 so'm", "78 000 so'm"],
      ["Ziyo market", "1 200 000 so'm", "3 ta mijoz", "0 so'm", "0 so'm"],
      ["Jami", "7 008 000 so'm", "12 ta mijoz", "450 000 so'm", "78 000 so'm"],
    ]);
    expectNoDollars();
  });

  it("names the customer of the tables by the same id the rows link to", () => {
    // A guard for the fixtures above: the debtor and the customer rows are the customer of the fake server.
    expect(customerBody().id).toBe(CUSTOMER_ID);
    expect(NO_OVERDUE.amount).toBe(0);
  });
});

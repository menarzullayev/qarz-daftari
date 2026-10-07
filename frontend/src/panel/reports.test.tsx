// @vitest-environment jsdom
import { cleanup, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import ReportsScreen from "../shared/reports/ReportsScreen";
import { CUSTOMER_ID, fakeServer, ok, overdueReportBody, periodReportBody, SHOP_BASE } from "../testing/fakeServer";
import { renderScreen } from "../testing/renderScreen";
import { DesktopLayout } from "./tables";
import { setWidth } from "./testing";

afterEach(cleanup);

const server = () => fakeServer((sent) => ok(sent.path === `${SHOP_BASE}/reports/period` ? periodReportBody() : overdueReportBody()));
const show = (width: number) => {
  setWidth(width);
  return renderScreen(
    <DesktopLayout>
      <ReportsScreen />
    </DesktopLayout>,
    { fetch: server().fetch, role: "owner" },
  );
};
const cells = (table: HTMLElement) =>
  [...table.querySelectorAll("tr")].map((row) => [...row.children].map((cell) => cell.textContent?.replace(/\u00a0/g, " ")));

describe("the reports on a wide screen of the web panel", () => {
  it("draws the days as a real table with a header cell per row and amounts in their own columns", async () => {
    show(1280);
    const table = await screen.findByRole("table", { name: "Kunlar bo'yicha" });
    expect(table.tagName).toBe("TABLE");
    expect(cells(table)).toEqual([
      ["Kun", "Nasiya", "To'lovlar"],
      ["2026-yil 1-oktabr", "100 000 so'm", "0 so'm"],
      ["2026-yil 3-oktabr", "0 so'm", "250 000 so'm"],
      ["2026-yil 6-oktabr", "200 000 so'm", "0 so'm"],
    ]);
    expect(within(table).getAllByRole("columnheader").every((cell) => cell.getAttribute("scope") === "col")).toBe(true);
    expect(within(table).getAllByRole("rowheader").every((cell) => cell.getAttribute("scope") === "row")).toBe(true);
    expect(table.querySelectorAll("td.table__num")).toHaveLength(6);
  });

  it("draws the largest debtors, the staff and the overdue bands as tables too", async () => {
    show(1280);
    const debtors = await screen.findByRole("table", { name: "Eng katta qarzdorlar (davr oxirida)" });
    expect(cells(debtors)).toEqual([
      ["Mijoz", "Qarz"],
      ["Ali Valiyev", "400 000 so'm"],
      ["Vali Aliyev", "200 000 so'm"],
    ]);
    expect(within(debtors).getAllByRole("link")[0]?.getAttribute("href")).toBe(`#/customers/${CUSTOMER_ID}`);

    expect(cells(screen.getByRole("table", { name: "Xodimlar bo'yicha" }))).toEqual([
      ["Xodim", "Nasiya", "Nasiya yozuvlari", "To'lovlar", "To'lov yozuvlari"],
      ["Do'kon egasi · 333333 (siz)", "100 000 so'm", "1", "250 000 so'm", "2"],
      ["Sotuvchi · 3d4e5f", "200 000 so'm", "3", "0 so'm", "0"],
    ]);

    const overdue = await screen.findByRole("table", { name: "Muddati o'tgan qarz, kechikish bo'yicha" });
    expect(cells(overdue)).toEqual([
      ["Kechikish", "Summa", "Mijozlar"],
      ["1–7 kun", "45 000 so'm", "1 ta mijoz"],
      ["8–30 kun", "100 000 so'm", "2 ta mijoz"],
      ["31–90 kun", "0 so'm", "0 ta mijoz"],
      ["91 kun va undan ko'p", "35 000 so'm", "1 ta mijoz"],
      ["Jami", "180 000 so'm", "3 ta mijoz"],
    ]);
    expect(overdue.querySelector("tfoot")).not.toBeNull();
    // The rows a phone shows are not drawn beside the tables.
    expect(document.querySelector("ul.rows")).toBeNull();
  });

  it("falls back to the phone's rows under the panel's breakpoint", async () => {
    show(1000);
    expect(await screen.findByRole("list", { name: "Kunlar bo'yicha" })).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
  });
});

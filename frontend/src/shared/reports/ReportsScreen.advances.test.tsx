// @vitest-environment jsdom
import { cleanup, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { fakeServer, ok, overdueReportBody, periodReportBody, refusal, SHOP_BASE } from "../../testing/fakeServer";
import { renderScreen } from "../../testing/renderScreen";
import ReportsScreen from "./ReportsScreen";

/**
 * The period report of a shop that held advances: what it held at both ends stands beside what it was
 * owed, and the check line holds of the two together. A shop that held none reads the report it always
 * read.
 */

afterEach(cleanup);

const plain = (text: string | null | undefined) => (text ?? "").replace(/\u00a0/g, " ");
const figure = (label: string) => plain(screen.getByText(label).closest(".figure")?.textContent);
const equation = () => plain(document.querySelector(".equation")?.textContent);

function show(period: Record<string, unknown>) {
  const server = fakeServer((sent) =>
    sent.path === `${SHOP_BASE}/reports/period`
      ? ok(periodReportBody(period))
      : sent.path === `${SHOP_BASE}/reports/overdue`
        ? ok(overdueReportBody())
        : refusal(404, "NOT_FOUND", "Topilmadi."),
  );
  renderScreen(<ReportsScreen />, { fetch: server.fetch, role: "owner" });
  return screen.findByText("Tekshirish");
}

// Of the 290 000 paid, 40 000 was beyond a debt and is held: 500 000 + 300 000 + 50 000 − 290 000 = 600 000 − 40 000.
const HELD = {
  advances: { start: 0, end: 40000 },
  payments: { amount: 290000, count: 3, customers: 2 },
  net_change: 60000,
};

describe("the period report of a shop that held advances", () => {
  it("shows what it held at both ends beside the debt, which is not reduced by it", async () => {
    await show(HELD);
    expect(figure("Davr boshidagi qarz")).toBe("Davr boshidagi qarz500 000 so'm");
    expect(figure("Davr oxiridagi qarz")).toBe("Davr oxiridagi qarz600 000 so'm");
    expect(figure("Davr boshidagi avanslar")).toBe("Davr boshidagi avanslar0 so'm");
    expect(figure("Davr oxiridagi avanslar")).toBe("Davr oxiridagi avanslar40 000 so'm");
  });

  it("checks the figures on the debt less the advances, and finds them right", async () => {
    await show(HELD);
    expect(equation()).toBe("(500 000 so'm − 0 so'm) + 300 000 so'm + 50 000 so'm − 290 000 so'm = (600 000 so'm − 40 000 so'm)");
    expect(screen.getByText("Mijozlarning avanslari qarzdan ayirib hisoblanadi: (qarz − avanslar).")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("says so when the figures do not add up once the advances are counted", async () => {
    await show({ ...HELD, advances: { start: 0, end: 30000 } });
    expect(plain(screen.getByRole("alert").textContent)).toContain("chap tomon 560 000 so'm");
  });

  it("is the report it always was for a shop that held none", async () => {
    await show({});
    expect(screen.queryByText("Davr boshidagi avanslar")).toBeNull();
    expect(screen.queryByText("Davr oxiridagi avanslar")).toBeNull();
    expect(equation()).toBe("500 000 so'm + 300 000 so'm + 50 000 so'm − 250 000 so'm = 600 000 so'm");
    expect(document.body.textContent).not.toMatch(/avans/i);
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

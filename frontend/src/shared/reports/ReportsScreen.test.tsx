// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import {
  CUSTOMER_ID,
  fakeServer,
  ok,
  overdueReportBody,
  periodReportBody,
  refusal,
  type Reply,
  type Sent,
  SHOP_BASE,
} from "../../testing/fakeServer";
import { renderScreen } from "../../testing/renderScreen";
import type { Role } from "../navigation";
import ReportsScreen from "./ReportsScreen";

afterEach(cleanup);

const PERIOD = `${SHOP_BASE}/reports/period`;
const OVERDUE = `${SHOP_BASE}/reports/overdue`;

/** The two reports; `period` answers the period report for the query it was asked with. */
function shop(period: (sent: Sent) => Reply = () => ok(periodReportBody()), overdue: () => Reply = () => ok(overdueReportBody())) {
  return fakeServer((sent) => (sent.path === PERIOD ? period(sent) : sent.path === OVERDUE ? overdue() : refusal(404, "NOT_FOUND", "Topilmadi.")));
}

// NOON is Tuesday 6 October 2026 in Tashkent.
const show = (server: ReturnType<typeof fakeServer>, role: Role = "manager", language: "uz" | "ru" = "uz") =>
  renderScreen(<ReportsScreen />, { fetch: server.fetch, role, language });
const asked = (server: ReturnType<typeof fakeServer>) => server.sent.filter((sent) => sent.path === PERIOD).map((sent) => sent.query);
const plain = (text: string | null | undefined) => (text ?? "").replace(/\u00a0/g, " ");
const section = (name: string) => screen.getByRole("region", { name });
const figure = (label: string) => plain(screen.getByText(label).closest(".figure")?.textContent);
const lines = (list: HTMLElement) => within(list).getAllByRole("listitem").map((item) => [...item.children].map((part) => plain(part.textContent)));

describe("who has the reports", () => {
  it("shows a seller the not-found screen and asks the server nothing", async () => {
    const server = shop();
    show(server, "seller");
    expect(screen.getByText("Bunday sahifa yo'q yoki sizda unga ruxsat yo'q.")).toBeTruthy();
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(server.sent).toHaveLength(0);
    expect(screen.queryByLabelText("Boshlanish sanasi")).toBeNull();
  });

  it.each(["manager", "owner"] as const)("shows a %s this month up to today, and overdue debt by age", async (role) => {
    const server = shop();
    show(server, role);
    await screen.findByText("Tekshirish");
    expect(asked(server)).toEqual([{ from: "2026-10-01", to: "2026-10-06" }]);
    expect(server.sent.filter((sent) => sent.path === OVERDUE)).toHaveLength(1);
    expect(server.writes()).toHaveLength(0);
  });
});

describe("the period", () => {
  it("has a labelled date field for each end, neither of which can be set past today", async () => {
    show(shop());
    const from = (await screen.findByLabelText("Boshlanish sanasi")) as HTMLInputElement;
    const to = screen.getByLabelText("Tugash sanasi") as HTMLInputElement;
    expect([from.type, from.value, from.max]).toEqual(["date", "2026-10-01", "2026-10-06"]);
    expect([to.type, to.value, to.max]).toEqual(["date", "2026-10-06", "2026-10-06"]);
    expect(screen.getByText("Davr ko'pi bilan 366 kun bo'ladi va bugundan keyinga o'tmaydi.")).toBeTruthy();
  });

  it.each([
    ["Bugun", { from: "2026-10-06", to: "2026-10-06" }],
    ["Shu hafta", { from: "2026-10-05", to: "2026-10-06" }],
    ["Shu oy", { from: "2026-10-01", to: "2026-10-06" }],
    ["O'tgan oy", { from: "2026-09-01", to: "2026-09-30" }],
  ])("preset %s asks for its days and fills the two fields", async (name, period) => {
    const server = shop();
    show(server);
    await screen.findByText("Tekshirish");
    const presets = within(screen.getByRole("group", { name: "Davr" }));
    fireEvent.click(presets.getByRole("button", { name: "Bugun" }));
    fireEvent.click(presets.getByRole("button", { name }));
    await waitFor(() => expect(asked(server).at(-1)).toEqual(period));
    expect((screen.getByLabelText("Boshlanish sanasi") as HTMLInputElement).value).toBe(period.from);
    expect((screen.getByLabelText("Tugash sanasi") as HTMLInputElement).value).toBe(period.to);
    expect(presets.getAllByRole("button").filter((button) => button.getAttribute("aria-pressed") === "true").map((button) => button.textContent)).toEqual([name]);
  });

  it("asks for a typed period, and then no preset is marked", async () => {
    const server = shop();
    show(server);
    fireEvent.change(await screen.findByLabelText("Boshlanish sanasi"), { target: { value: "2026-09-15" } });
    fireEvent.change(screen.getByLabelText("Tugash sanasi"), { target: { value: "2026-10-02" } });
    // Nothing is asked while the dates are being typed.
    expect(asked(server)).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Ko'rsatish" }));
    await waitFor(() => expect(asked(server).at(-1)).toEqual({ from: "2026-09-15", to: "2026-10-02" }));
    expect(within(screen.getByRole("group", { name: "Davr" })).getAllByRole("button").every((button) => button.getAttribute("aria-pressed") === "false")).toBe(true);
  });

  it.each([
    ["", "2026-10-06", "Boshlanish sanasi", "Sanani tanlang."],
    ["2026-10-01", "", "Tugash sanasi", "Sanani tanlang."],
    ["2026-10-06", "2026-10-05", "Boshlanish sanasi", "Boshlanish sanasi tugash sanasidan keyin bo'lmasin."],
    ["2026-10-01", "2026-10-07", "Tugash sanasi", "Davr bugundan keyin tugashi mumkin emas."],
    ["2025-10-05", "2026-10-06", "Tugash sanasi", "Davr 366 kundan uzun bo'lmasin."],
  ])("asks nothing for %j to %j and says why next to the field", async (from, to, label, message) => {
    const server = shop();
    show(server);
    fireEvent.change(await screen.findByLabelText("Boshlanish sanasi"), { target: { value: from } });
    fireEvent.change(screen.getByLabelText("Tugash sanasi"), { target: { value: to } });
    fireEvent.click(screen.getByRole("button", { name: "Ko'rsatish" }));
    const field = screen.getByLabelText(label);
    expect(field.getAttribute("aria-invalid")).toBe("true");
    expect(field.getAttribute("aria-describedby")).toContain(screen.getByText(message).id);
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(asked(server)).toHaveLength(1);
  });

  it("accepts a period of exactly 366 days", async () => {
    const server = shop();
    show(server);
    fireEvent.change(await screen.findByLabelText("Boshlanish sanasi"), { target: { value: "2025-10-06" } });
    fireEvent.click(screen.getByRole("button", { name: "Ko'rsatish" }));
    await waitFor(() => expect(asked(server).at(-1)).toEqual({ from: "2025-10-06", to: "2026-10-06" }));
  });

  it.each([
    [{ to: "IN_FUTURE" }, "Tugash sanasi", "Davr bugundan keyin tugashi mumkin emas."],
    [{ to: "PERIOD_TOO_LONG" }, "Tugash sanasi", "Davr 366 kundan uzun bo'lmasin."],
    [{ from: "FROM_AFTER_TO" }, "Boshlanish sanasi", "Boshlanish sanasi tugash sanasidan keyin bo'lmasin."],
    [{ from: "DATE_INVALID" }, "Boshlanish sanasi", "Sanani tanlang."],
  ])("shows the server's refusal %j next to the field it is about", async (fields, label, message) => {
    // The server's clock, not this one, decides what "today" is: its refusal must be shown too.
    const server = shop(() => refusal(422, "VALIDATION", "Ma'lumotlar noto'g'ri kiritilgan.", fields));
    show(server);
    // The general message is shown as for any failure; what is wrong is said next to the field.
    expect(await screen.findByText("Ma'lumotlar noto'g'ri kiritilgan.")).toBeTruthy();
    const field = await screen.findByLabelText(label);
    await waitFor(() => expect(field.getAttribute("aria-invalid")).toBe("true"));
    expect(field.getAttribute("aria-describedby")).toContain(screen.getByText(message).id);
    expect(screen.queryByText("Tekshirish")).toBeNull();
  });

  it("shows any other failure with the server's message and a retry", async () => {
    let fail = true;
    const server = shop(() => (fail ? refusal(403, "SHOP_SUSPENDED", "Do'kon to'xtatilgan.") : ok(periodReportBody())));
    show(server);
    expect(await screen.findByText("Do'kon to'xtatilgan.")).toBeTruthy();
    fail = false;
    fireEvent.click(screen.getAllByRole("button", { name: "Qayta urinish" })[0] as HTMLElement);
    expect(await screen.findByText("Tekshirish")).toBeTruthy();
  });
});

describe("the period report", () => {
  it("names the period, in words, as the server answered it", async () => {
    show(shop());
    expect(await screen.findByRole("heading", { level: 2, name: "2026-yil 1-oktabr — 2026-yil 6-oktabr" })).toBeTruthy();
    cleanup();
    show(shop(() => ok(periodReportBody({ from: "2026-10-06", to: "2026-10-06" }))));
    expect(await screen.findByRole("heading", { level: 2, name: "2026-yil 6-oktabr" })).toBeTruthy();
  });

  it("shows every total with the money formatting, and counts of entries and customers", async () => {
    show(shop());
    await screen.findByText("Tekshirish");
    expect(figure("Davr boshidagi qarz")).toBe("Davr boshidagi qarz500 000 so'm");
    expect(figure("Davr oxiridagi qarz")).toBe("Davr oxiridagi qarz600 000 so'm");
    expect(figure("Davrdagi o'zgarish")).toBe("Davrdagi o'zgarish+100 000 so'm");
    expect(figure("Berilgan nasiya")).toBe("Berilgan nasiya300 000 so'm4 ta yozuv, 3 ta mijoz");
    expect(figure("Qaytarilgan (to'lovlar)")).toBe("Qaytarilgan (to'lovlar)250 000 so'm2 ta yozuv, 2 ta mijoz");
    expect(figure("Kiritilgan boshlang'ich qarz")).toBe("Kiritilgan boshlang'ich qarz50 000 so'm1 ta yozuv");
    expect(figure("Bekor qilingan yozuvlar")).toBe("Bekor qilingan yozuvlar20 000 so'm1 ta yozuv");
    expect(figure("Yangi mijozlar")).toBe("Yangi mijozlar2 ta mijoz");
    expect(figure("Ochilgan e'tirozlar")).toBe("Ochilgan e'tirozlar1");
  });

  it("shows a fall of the debt with its minus sign and no plus", async () => {
    show(shop(() => ok(periodReportBody({ outstanding: { start: 600000, end: 350000 }, credit: { amount: 0, count: 0, customers: 0 }, opening: { amount: 0, count: 0 }, net_change: -250000 }))));
    await screen.findByText("Tekshirish");
    expect(figure("Davrdagi o'zgarish")).toBe("Davrdagi o'zgarish-250 000 so'm");
  });

  it("writes the equation out in figures so a person can check it", async () => {
    show(shop());
    const check = await waitFor(() => section("Tekshirish"));
    expect(within(check).getByText("Davr boshidagi qarz + berilgan nasiya + boshlang'ich qarz − to'lovlar = davr oxiridagi qarz")).toBeTruthy();
    expect(plain(check.querySelector(".equation")?.textContent)).toBe("500 000 so'm + 300 000 so'm + 50 000 so'm − 250 000 so'm = 600 000 so'm");
    expect(within(check).queryByRole("alert")).toBeNull();
  });

  it("says so, instead of passing it off as a report, when the figures do not add up", async () => {
    show(shop(() => ok(periodReportBody({ outstanding: { start: 500000, end: 610000 } }))));
    const check = await waitFor(() => section("Tekshirish"));
    // The line shows the end the server reported, not the one that would make it add up.
    expect(plain(check.querySelector(".equation")?.textContent)).toBe("500 000 so'm + 300 000 so'm + 50 000 so'm − 250 000 so'm = 610 000 so'm");
    expect(plain(within(check).getByRole("alert").textContent)).toBe(
      "Hisob mos kelmadi: chap tomon 600 000 so'm, davr oxiridagi qarz esa 610 000 so'm. Qo'llab-quvvatlashga xabar bering.",
    );
  });

  it("shows the share repaid by the promised date, with the amounts behind it", async () => {
    show(shop());
    const onTime = await waitFor(() => section("Va'da qilingan kungacha qaytarilgan"));
    expect(within(onTime).getByText("75%")).toBeTruthy();
    expect(plain(onTime.textContent)).toContain("Muddati shu davrda kelgan 200 000 so'm dan 150 000 so'm o'z vaqtida to'langan.");
  });

  it("says nothing fell due, rather than 0%, when the share is null", async () => {
    show(shop(() => ok(periodReportBody({ on_time: { due_amount: 0, on_time_amount: 0, percent: null } }))));
    const onTime = await waitFor(() => section("Va'da qilingan kungacha qaytarilgan"));
    expect(within(onTime).getByText("Bu davrda to'lash muddati kelgan qarz bo'lmagan.")).toBeTruthy();
    expect(onTime.textContent).not.toContain("%");
  });

  it("shows a real 0% as 0%", async () => {
    show(shop(() => ok(periodReportBody({ on_time: { due_amount: 200000, on_time_amount: 0, percent: 0 } }))));
    const onTime = await waitFor(() => section("Va'da qilingan kungacha qaytarilgan"));
    expect(within(onTime).getByText("0%")).toBeTruthy();
  });

  it("lists the days with a sale or a payment as readable rows, and says how many days are left out", async () => {
    show(shop());
    const days = await waitFor(() => section("Kunlar bo'yicha"));
    expect(lines(within(days).getByRole("list", { name: "Kunlar bo'yicha" }))).toEqual([
      ["2026-yil 1-oktabr", "Nasiya: 100 000 so'm · To'lov: 0 so'm"],
      ["2026-yil 3-oktabr", "Nasiya: 0 so'm · To'lov: 250 000 so'm"],
      ["2026-yil 6-oktabr", "Nasiya: 200 000 so'm · To'lov: 0 so'm"],
    ]);
    expect(within(days).getByText("Nasiya ham, to'lov ham yozilmagan 3 kun ko'rsatilmagan.")).toBeTruthy();
    // On a phone there is no table: a table is the web panel's.
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("says so when no day saw a sale or a payment, and leaves no note when every day did", async () => {
    show(shop(() => ok(periodReportBody({ days: [{ date: "2026-10-06", credit: 0, payments: 0 }] }))));
    expect(await screen.findByText("Bu davrda nasiya ham, to'lov ham yozilmagan.")).toBeTruthy();
    cleanup();
    show(shop(() => ok(periodReportBody({ days: [{ date: "2026-10-06", credit: 5000, payments: 0 }] }))));
    await screen.findByText("Tekshirish");
    expect(screen.queryByText(/ko'rsatilmagan/)).toBeNull();
  });

  it("lists the largest debtors, each a link to that customer", async () => {
    show(shop());
    const debtors = await waitFor(() => section("Eng katta qarzdorlar (davr oxirida)"));
    expect(lines(within(debtors).getByRole("list"))).toEqual([
      ["Ali Valiyev400 000 so'm"],
      ["Vali Aliyev200 000 so'm"],
    ]);
    expect(within(debtors).getAllByRole("link").map((link) => link.getAttribute("href"))).toEqual([
      `#/customers/${CUSTOMER_ID}`,
      "#/customers/11111111-1111-4111-8111-111111111112",
    ]);
  });

  it("lists entries per member of staff by role and membership code, marking the reader's own", async () => {
    show(shop());
    const staff = await waitFor(() => section("Xodimlar bo'yicha"));
    expect(lines(within(staff).getByRole("list"))).toEqual([
      ["Do'kon egasi · 333333 (siz)", "Nasiya: 100 000 so'm (1) · To'lov: 250 000 so'm (2)"],
      ["Sotuvchi · 3d4e5f", "Nasiya: 200 000 so'm (3) · To'lov: 0 so'm (0)"],
    ]);
  });

  it("says so when there is no debtor and no entry by staff", async () => {
    show(shop(() => ok(periodReportBody({ top_debtors: [], staff: [] }))));
    expect(await screen.findByText("Davr oxirida qarzdor bo'lmagan.")).toBeTruthy();
    expect(screen.getByText("Bu davrda xodimlar nasiya yoki to'lov yozmagan.")).toBeTruthy();
  });

  it("refuses a report with a fraction of a so'm instead of showing it", async () => {
    show(shop(() => ok(periodReportBody({ net_change: 100000.5 }))));
    expect(await screen.findByText("Xatolik yuz berdi")).toBeTruthy();
    expect(screen.queryByText("Tekshirish")).toBeNull();
  });

  it("reads in Russian with the Russian plural forms", async () => {
    show(shop(), "owner", "ru");
    await screen.findByText("Проверка");
    expect(figure("Продано в долг")).toBe("Продано в долг300 000 сум4 записи, 3 клиента");
    expect(figure("Внесён начальный долг")).toBe("Внесён начальный долг50 000 сум1 запись");
    expect(screen.getByText("Не показаны 3 дня без продаж в долг и оплат.")).toBeTruthy();
  });
});

describe("overdue debt by age", () => {
  it("shows the four bands with their days, amounts and customers, the total, and the day it is as of", async () => {
    show(shop());
    const overdue = await waitFor(() => section("Muddati o'tgan qarz, kechikish bo'yicha"));
    await within(overdue).findByText("2026-yil 6-oktabr holatiga.");
    expect(lines(within(overdue).getByRole("list"))).toEqual([
      ["1–7 kun45 000 so'm", "1 ta mijoz"],
      ["8–30 kun100 000 so'm", "2 ta mijoz"],
      ["31–90 kun0 so'm", "0 ta mijoz"],
      ["91 kun va undan ko'p35 000 so'm", "1 ta mijoz"],
      ["Jami180 000 so'm", "3 ta mijoz"],
    ]);
    expect(within(overdue).getByText(/jamida u bir marta sanaladi/)).toBeTruthy();
  });

  it("says so when nothing is overdue", async () => {
    show(shop(undefined, () => ok(overdueReportBody({ total: { amount: 0, customers: 0 }, bands: [] }))));
    expect(await screen.findByText("Muddati o'tgan qarz yo'q.")).toBeTruthy();
  });

  it("fails on its own, with a retry, and leaves the period report standing", async () => {
    let fail = true;
    const server = shop(undefined, () => (fail ? "offline" : ok(overdueReportBody())));
    show(server);
    await screen.findByText("Tekshirish");
    const overdue = section("Muddati o'tgan qarz, kechikish bo'yicha");
    expect((await within(overdue).findByRole("alert")).textContent).toContain("Serverga ulanib bo'lmadi");
    fail = false;
    fireEvent.click(within(overdue).getByRole("button", { name: "Qayta urinish" }));
    expect(await within(overdue).findByText("8–30 kun")).toBeTruthy();
  });

  it("is not asked again when the period changes: it is as of today whatever the period", async () => {
    const server = shop();
    show(server);
    await screen.findByText("Tekshirish");
    fireEvent.click(screen.getByRole("button", { name: "Bugun" }));
    await waitFor(() => expect(asked(server)).toHaveLength(2));
    expect(server.sent.filter((sent) => sent.path === OVERDUE)).toHaveLength(1);
  });
});

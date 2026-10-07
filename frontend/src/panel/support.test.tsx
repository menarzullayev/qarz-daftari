// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import ExportsScreen from "../shared/exports/ExportsScreen";
import { ACCESS_ID, exportBody, fakeServer, ok, refusal, type Reply, type Sent, SHOP_BASE, SUPPORT_ADMIN_ID, supportAccessBody } from "../testing/fakeServer";
import { renderScreen } from "../testing/renderScreen";
import { ActivityScreen } from "./ActivityScreen";
import { DeletionSection } from "./DeletionSection";
import { OfficeBanner } from "./OfficeBanner";
import { PANEL_EXTENSION } from "./PanelRoot";
import { DesktopLayout } from "./tables";
import { NO_DELETION, PENDING_DELETION, renderOffice, setWidth, STAFF } from "./testing";

beforeEach(() => {
  window.location.hash = "";
  setWidth(375);
});
afterEach(cleanup);

const SUPPORT = `${SHOP_BASE}/support-access`;
const BANNER = "Administrator hozir do'kon ma'lumotlarini ko'ra oladi. Kirish 2026-yil 6-oktabr, 13:00 da tugaydi.";
const ended = supportAccessBody({ state: "closed", closed_at: "2026-10-06T07:00:00+00:00", closed_by: "owner" });

function backend(accesses: unknown[] = [supportAccessBody()], deletion: unknown = NO_DELETION, extra: (sent: Sent) => Reply | null = () => null) {
  const held = { accesses };
  const server = fakeServer((sent) => {
    const special = extra(sent);
    if (special !== null) {
      return special;
    }
    if (sent.path === SUPPORT) {
      return ok({ items: held.accesses, next_cursor: null });
    }
    if (sent.path === `${SUPPORT}/${ACCESS_ID}/end`) {
      held.accesses = [ended];
      return ok(ended);
    }
    if (sent.path === `${SHOP_BASE}/ownership-transfer`) {
      return ok({ pending: null });
    }
    if (sent.path === `${SHOP_BASE}/deletion`) {
      return ok(deletion);
    }
    return refusal(404, "NOT_FOUND", "Topilmadi.");
  });
  return { ...server, held };
}
const cells = (table: HTMLElement) => [...table.querySelectorAll("tr")].map((row) => [...row.children].map((cell) => cell.textContent));
const supportCalls = (server: ReturnType<typeof backend>) => server.sent.filter((sent) => sent.path.includes("/support-access"));

describe("the notice above every screen while an administrator can read the shop", () => {
  it("tells the owner that it is so, until when and why, and leads to the settings", async () => {
    const server = backend();
    renderOffice(<OfficeBanner />, { fetch: server.fetch });
    const notice = await screen.findByRole("status");
    expect(notice.textContent).toBe(`${BANNER} Sabab: Egasi yordam so'radi Ko'rish va tugatish`);
    expect(within(notice).getByRole("link", { name: "Ko'rish va tugatish" }).getAttribute("href")).toBe("#/shop-settings");
    expect(supportCalls(server).map((sent) => [sent.method, sent.path, sent.query])).toEqual([["GET", SUPPORT, {}]]);
  });

  it("says it in Russian", async () => {
    renderOffice(<OfficeBanner />, { fetch: backend().fetch, language: "ru" });
    expect((await screen.findByRole("status")).textContent).toContain("Администратор сейчас может видеть данные магазина. Доступ закончится 6 октября 2026 г., 13:00, не позже.");
  });

  it("stands beside the notice of a shop waiting to be deleted: neither hides the other", async () => {
    renderOffice(<OfficeBanner />, { fetch: backend([supportAccessBody()], PENDING_DELETION).fetch });
    await waitFor(() => expect(screen.getAllByRole("status")).toHaveLength(2));
    const notices = screen.getAllByRole("status").map((notice) => notice.textContent);
    expect(notices[0]).toContain(BANNER);
    expect(notices[1]).toContain("Do'konni o'chirish so'ralgan.");
  });

  it.each([
    ["no access was ever opened", []],
    ["the last one was ended", [ended]],
    ["the last one ran out", [supportAccessBody({ state: "expired" })]],
    ["the server calls it active but its time is over", [supportAccessBody({ ends_at: "2026-10-06T06:59:00+00:00" })]],
  ])("says nothing when %s", async (_, accesses) => {
    const server = backend(accesses);
    const view = renderOffice(<OfficeBanner />, { fetch: server.fetch });
    await waitFor(() => expect(supportCalls(server)).toHaveLength(1));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(view.container.textContent).toBe("");
  });

  it("says one notice for each administrator who can read the shop now", async () => {
    const second = supportAccessBody({ id: "cccccccc-cccc-4ccc-8ccc-cccccccccccd", reason: "Hisobot xatosi", ends_at: "2026-10-06T09:00:00+00:00" });
    renderOffice(<OfficeBanner />, { fetch: backend([second, supportAccessBody()]).fetch });
    await waitFor(() => expect(screen.getAllByRole("status")).toHaveLength(2));
    expect(screen.getAllByRole("status")[0]?.textContent).toContain("Sabab: Hisobot xatosi");
  });

  it.each(["manager", "seller"] as const)("shows a %s nothing and asks nothing about support access", async (role) => {
    const server = backend();
    const view = renderOffice(<OfficeBanner />, { fetch: server.fetch, role });
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(view.container.textContent).toBe("");
    expect(supportCalls(server)).toEqual([]);
  });

  it("stays silent when the accesses cannot be read", async () => {
    const server = backend([], NO_DELETION, (sent) => (sent.path === SUPPORT ? "offline" : null));
    const view = renderOffice(<OfficeBanner />, { fetch: server.fetch });
    await waitFor(() => expect(supportCalls(server)).toHaveLength(1));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(view.container.textContent).toBe("");
  });
});

describe("support access under the shop's settings of the web panel", () => {
  const settings = (
    <>
      <OfficeBanner />
      <PANEL_EXTENSION.SettingsExtra />
    </>
  );

  it("shows the owner the section above the deletion of the shop", async () => {
    renderOffice(settings, { fetch: backend().fetch });
    await screen.findByRole("button", { name: "Kirishni tugatish" });
    expect(screen.getAllByRole("heading", { level: 2 }).map((heading) => heading.textContent)).toEqual(["Yordam uchun kirish", "Do'konni o'chirish"]);
  });

  it("takes the notice off every screen once the access is ended here", async () => {
    const server = backend();
    renderOffice(settings, { fetch: server.fetch });
    expect((await screen.findByRole("status")).textContent).toContain(BANNER);
    fireEvent.click(await screen.findByRole("button", { name: "Kirishni tugatish" }));
    fireEvent.click(screen.getByRole("button", { name: "Ha, tugatilsin" }));
    expect(await screen.findByText("Kirish tugatildi.")).toBeTruthy();
    await waitFor(() => expect(screen.queryByText(BANNER)).toBeNull());
    expect(server.writes().map((sent) => sent.path)).toEqual([`${SUPPORT}/${ACCESS_ID}/end`]);
    expect(server.writes()[0]?.headers["X-CSRF-Token"]).toBeTruthy();
  });

  it.each(["manager", "seller"] as const)("shows a %s neither section", async (role) => {
    const server = backend();
    const view = renderOffice(<PANEL_EXTENSION.SettingsExtra />, { fetch: server.fetch, role });
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(view.container.textContent).toBe("");
    expect(supportCalls(server)).toEqual([]);
  });

  it("draws the history as a real table on a wide screen", async () => {
    setWidth(1280);
    renderOffice(
      <DesktopLayout>
        <PANEL_EXTENSION.SettingsExtra />
      </DesktopLayout>,
      { fetch: backend([supportAccessBody(), ended]).fetch },
    );
    const table = await screen.findByRole("table", { name: "Kirishlar tarixi" });
    expect(cells(table)).toEqual([
      ["Administrator", "Sabab", "Boshlangan", "Tugash vaqti", "Qanday tugagan"],
      ["a1b2c3", "Egasi yordam so'radi", "2026-yil 6-oktabr, 11:00", "2026-yil 6-oktabr, 13:00", "Hozir ochiq"],
      ["a1b2c3", "Egasi yordam so'radi", "2026-yil 6-oktabr, 11:00", "2026-yil 6-oktabr, 13:00", "Egasi tugatgan · 2026-yil 6-oktabr, 12:00"],
    ]);
    expect(within(table).getAllByRole("rowheader")).toHaveLength(2);
  });
});

describe("the exports on a wide screen of the web panel", () => {
  it("draws the list as a real table, with the download in its own column", async () => {
    setWidth(1280);
    const server = fakeServer((sent) =>
      sent.path.endsWith("/download")
        ? ok({ url: "/files/abc.def", expires_at: "2026-10-06T07:05:00+00:00" })
        : ok({ items: [exportBody(), exportBody({ id: "j2", status: "failed", error: "timeout", rows: null, available: false, available_until: null })] }),
    );
    renderScreen(
      <DesktopLayout>
        <ExportsScreen />
      </DesktopLayout>,
      { fetch: server.fetch, role: "owner" },
    );
    const table = await screen.findByRole("table", { name: "Oxirgi eksportlar" });
    expect(cells(table)).toEqual([
      ["So'ralgan", "Kim so'ragan", "Holat", "Fayl"],
      ["2026-yil 6-oktabr, 10:58", "Siz", "Tayyor1234 qator · Fayl 2026-yil 13-oktabr, 11:00 gacha saqlanadi, so'ng o'chiriladi.", "Yuklab olish havolasini olish"],
      ["2026-yil 6-oktabr, 10:58", "Siz", "TayyorlanmadiTayyorlash juda uzoq davom etdi va to'xtatildi. Qayta so'rang.", ""],
    ]);
    fireEvent.click(within(table).getByRole("button", { name: "Yuklab olish havolasini olish" }));
    expect((await within(table).findByRole("link", { name: "Faylni yuklab olish" })).getAttribute("href")).toBe("/files/abc.def");
  });
});

describe("an export before the shop is deleted (REQ-048)", () => {
  it.each([
    ["before the request", NO_DELETION],
    ["while the request waits", PENDING_DELETION],
  ])("is offered %s, as a link to the export screen", async (_, deletion) => {
    renderOffice(<DeletionSection />, { fetch: backend([], deletion).fetch });
    const link = await screen.findByRole("link", { name: "Eksportga o'tish" });
    expect(link.getAttribute("href")).toBe("#/import-export");
    expect(screen.getByText("O'chirilgan ma'lumotlar qaytarilmaydi. O'chirishni so'rashdan oldin daftarni Excel fayliga eksport qilib oling.")).toBeTruthy();
  });

  it.each(["manager", "seller"] as const)("is not offered to a %s, who cannot delete the shop", async (role) => {
    const view = renderOffice(<DeletionSection />, { fetch: backend([], NO_DELETION).fetch, role });
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(view.container.textContent).toBe("");
  });
});

describe("what an administrator did under a support access, in the owner's activity log", () => {
  const row = (id: number, overrides: Record<string, unknown>) => ({
    id: `99999999-9999-4999-8999-99999999990${id}`,
    at: "2026-10-06T05:10:00+00:00",
    actor_kind: "admin",
    actor_id: SUPPORT_ADMIN_ID,
    subject_type: "support_access",
    subject_id: ACCESS_ID,
    ...overrides,
  });
  const ROWS = [
    row(1, { action: "support_access.opened" }),
    row(2, { action: "support_access.customers_listed" }),
    row(3, { action: "support_access.customer_viewed", subject_type: "shop" }),
    row(4, { action: "support_access.closed" }),
    row(5, { action: "support_access.ended", actor_kind: "staff", actor_id: STAFF[0]?.id }),
    row(6, { action: "export.requested", actor_kind: "staff", actor_id: STAFF[0]?.id, subject_type: "shop" }),
    row(7, { action: "support_access.opened", actor_id: null }),
  ];
  const log = () =>
    fakeServer((sent) =>
      sent.path === `${SHOP_BASE}/activity`
        ? ok({ items: ROWS, next_cursor: null })
        : sent.path === `${SHOP_BASE}/staff`
          ? ok({ items: STAFF })
          : sent.path === `${SHOP_BASE}/ownership-transfer`
            ? ok({ pending: null })
            : sent.path === `${SHOP_BASE}/deletion`
              ? ok(NO_DELETION)
              : ok({ items: [], next_cursor: null }),
    );

  it("names the administrator by a code, never as the system, and words each action", async () => {
    renderOffice(<ActivityScreen />, { fetch: log().fetch });
    const table = await screen.findByRole("table", { name: "Amallar jurnali" });
    expect(cells(table).slice(1).map((line) => line.slice(1))).toEqual([
      ["Administrator · a1b2c3", "Administrator yordam uchun kirish ochdi", "Yordam uchun kirish"],
      ["Administrator · a1b2c3", "Administrator mijozlar ro'yxatini ko'rdi", "Yordam uchun kirish"],
      ["Administrator · a1b2c3", "Administrator mijoz sahifasini ko'rdi", "Do'kon"],
      ["Administrator · a1b2c3", "Administrator kirishni yopdi", "Yordam uchun kirish"],
      ["Do'kon egasi · 333333 (siz)", "Egasi administrator kirishini tugatdi", "Yordam uchun kirish"],
      ["Do'kon egasi · 333333 (siz)", "Eksport so'raldi", "Do'kon"],
      ["Administrator", "Administrator yordam uchun kirish ochdi", "Yordam uchun kirish"],
    ]);
  });

  it("can be narrowed to support access and to exports", async () => {
    const server = log();
    renderOffice(<ActivityScreen />, { fetch: server.fetch });
    const select = (await screen.findByLabelText("Amal turi")) as HTMLSelectElement;
    expect([...select.options].filter((option) => ["export", "support_access"].includes(option.value)).map((option) => option.textContent)).toEqual([
      "Eksport",
      "Administratorning kirishi",
    ]);
    fireEvent.change(select, { target: { value: "support_access" } });
    await waitFor(() => expect(server.sent.filter((sent) => sent.path === `${SHOP_BASE}/activity`).at(-1)?.query).toEqual({ action: "support_access" }));
  });
});

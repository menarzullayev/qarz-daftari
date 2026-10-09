// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { catalogs, compareCatalogs, placeholdersOf, translate } from "../i18n/catalog";
import { ruReports } from "../i18n/reports/ru";
import { uzReports } from "../i18n/reports/uz";
import type { MessageKey } from "../i18n/types";
import { uz } from "../i18n/uz";
import {
  accountBody,
  accountEntryBody,
  customerBody,
  dateRequestBody,
  fakeServer,
  LINK_ID,
  ME_BASE,
  NO_OVERDUE,
  NOON,
  ok,
  openDateRequestBody,
  overdueReportBody,
  periodReportBody,
  refusal,
  SHOP_BASE,
  SHOP_ID,
} from "../testing/fakeServer";
import { go } from "../testing/renderScreen";
import type { ApiAuth } from "./api";
import type { Role } from "./navigation";
import { StaffRoot } from "./StaffRoot";

beforeEach(() => {
  window.location.hash = "";
  window.localStorage.clear();
});
afterEach(cleanup);

const bearer = async (): Promise<ApiAuth> => ({ kind: "bearer", token: "session-token" });
const heading = () => screen.getByRole("heading", { level: 1 }).textContent;

function backend(role: Role | null, accounts: unknown[] = []) {
  return fakeServer((sent) => {
    if (sent.method !== "GET") {
      return ok(dateRequestBody(), 201);
    }
    switch (sent.path) {
      case "/api/v1/me/shops":
        return ok({ items: role ? [{ shop_id: SHOP_ID, name: "Baraka savdo", role, membership_id: null }] : [], active_shop: role ? SHOP_ID : null });
      case "/api/v1/me/accounts":
        return ok({ items: accounts });
      case ME_BASE:
        return ok(accountBody({ entries: [accountEntryBody({ date_request: dateRequestBody({ status: "declined", decline_reason: null, closed_at: "2026-10-04T07:00:00+00:00" }) })] }));
      case `${SHOP_BASE}/overview`:
        return ok({ outstanding: 120000, debtors: 1, overdue: { amount: 0, customers: 0 }, due_today: 0 });
      case `${SHOP_BASE}/overview/debtors`:
        return ok({ items: [{ ...customerBody(), overdue: NO_OVERDUE }], next_cursor: null });
      case `${SHOP_BASE}/disputes`:
        return ok({ items: [] });
      case `${SHOP_BASE}/date-requests`:
        return ok({ items: [openDateRequestBody()] });
      case `${SHOP_BASE}/exports`:
      case `${SHOP_BASE}/imports`:
        return ok({ items: [] });
      case `${SHOP_BASE}/reports/period`:
        return ok(periodReportBody());
      case `${SHOP_BASE}/reports/overdue`:
        return ok(overdueReportBody());
    }
    return refusal(404, "NOT_FOUND", "Topilmadi.");
  });
}

async function start(server: ReturnType<typeof fakeServer>, hash = "") {
  window.location.hash = hash;
  render(<StaffRoot entryKey="entry.app" initialLanguage="uz" connect={bearer} fetch={server.fetch} now={() => NOON} customerPage />);
  await screen.findAllByRole("navigation");
}

describe("reports and date requests in the workspace", () => {
  it.each(["#/reports", "#/date-requests"])("gives a seller the same not-found screen at %s as for any section that is not theirs", async (hash) => {
    const server = backend("seller");
    await start(server, hash);
    expect(heading()).toBe("Sahifa topilmadi");
    expect(screen.getByText("Bunday sahifa yo'q yoki sizda unga ruxsat yo'q.")).toBeTruthy();
    const here = document.querySelector("main")?.innerHTML;
    go("#/staff");
    expect(document.querySelector("main")?.innerHTML).toBe(here);
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(server.sent.filter((sent) => /reports|date-requests/.test(sent.path))).toEqual([]);
    const links = within(screen.getByRole("navigation")).getAllByRole("link").map((link) => link.textContent);
    expect(links).not.toContain("Hisobotlar");
    expect(links).not.toContain("E'tirozlar va so'rovlar");
  });

  it.each(["manager", "owner"] as const)("opens the reports for a %s in place of the placeholder", async (role) => {
    const server = backend(role);
    await start(server, "#/reports");
    expect(heading()).toBe("Hisobotlar");
    expect(await screen.findByText("Tekshirish")).toBeTruthy();
    expect(screen.queryByText("Bu bo'lim tez orada tayyor bo'ladi.")).toBeNull();
    expect(within(screen.getByRole("navigation")).getByRole("link", { name: "Hisobotlar" }).getAttribute("aria-current")).toBe("page");
  });

  it.each(["manager", "owner"] as const)("opens the export and the import for a %s at import and export", async (role) => {
    const server = backend(role);
    await start(server, "#/import-export");
    expect(heading()).toBe("Import va eksport");
    expect(await screen.findByRole("button", { name: "Eksport so'rash" })).toBeTruthy();
    expect(await screen.findByText("Hali eksport so'ralmagan.")).toBeTruthy();
    expect(server.sent.filter((sent) => sent.path.includes("/exports")).map((sent) => `${sent.method} ${sent.path}`)).toEqual([`GET ${SHOP_BASE}/exports`]);
    // Import is the other half of the section, under its own heading, loaded apart.
    const half = await screen.findByRole("region", { name: "Import" });
    expect(within(half).getByRole("button", { name: "Faylni yuklash" })).toBeTruthy();
    expect(screen.queryByText("Bu bo'lim tez orada tayyor bo'ladi.")).toBeNull();
    await waitFor(() => expect(server.sent.filter((sent) => sent.path.includes("/imports")).map((sent) => `${sent.method} ${sent.path}`)).toEqual([`GET ${SHOP_BASE}/imports`]));
    expect(within(screen.getByRole("navigation")).getByRole("link", { name: "Import va eksport" }).getAttribute("aria-current")).toBe("page");
  });

  it("gives a seller the not-found screen at import and export, and asks nothing", async () => {
    const server = backend("seller");
    await start(server, "#/import-export");
    expect(heading()).toBe("Sahifa topilmadi");
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(server.sent.filter((sent) => sent.path.includes("/exports") || sent.path.includes("/imports"))).toEqual([]);
  });

  it("reaches the date requests from the disputes, in the same section, and back", async () => {
    const server = backend("manager");
    await start(server, "#/disputes");
    const link = await screen.findByRole("link", { name: "Muddat so'rovlari" });
    expect(link.getAttribute("href")).toBe("#/date-requests");
    go("#/date-requests");
    expect(heading()).toBe("Muddat so'rovlari");
    expect(await screen.findByText("Ali Valiyev")).toBeTruthy();
    const current = within(screen.getAllByRole("navigation")[0] as HTMLElement)
      .getAllByRole("link")
      .filter((candidate) => candidate.getAttribute("aria-current") === "page");
    expect(current.map((candidate) => candidate.textContent)).toEqual(["E'tirozlar va so'rovlar"]);
    expect(screen.getByRole("link", { name: "E'tirozlar" }).getAttribute("href")).toBe("#/disputes");
  });

  it("gives the customer's own page the clock, so a recent decline is not offered again", async () => {
    const server = backend(null, [{ link_id: LINK_ID, shop_name: "Baraka savdo", display_name: "Ali Valiyev", balance: 120000 }]);
    window.location.hash = "";
    render(<StaffRoot entryKey="entry.app" initialLanguage="uz" connect={bearer} fetch={server.fetch} now={() => NOON} customerPage />);
    expect(await screen.findByText("Bu yozuv bo'yicha qayta so'rash 2026-yil 11-oktabr, 12:00 dan keyin mumkin.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Muddatni kechroq so'rash" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "E'tiroz bildirish" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "E'tirozni yuborish" })).toBeTruthy());
  });
});

const SRC = resolve(import.meta.dirname, "..");
const sources = () =>
  readdirSync(SRC, { recursive: true, encoding: "utf8" })
    .map((name) => name.replaceAll("\\", "/"))
    .filter((name) => /\.(ts|tsx)$/.test(name) && !/\.test\.tsx?$/.test(name))
    .map((name) => ({ name, text: readFileSync(resolve(SRC, name), "utf8") }));

describe("the reports stay out of the first load (NFR-010)", () => {
  // The cash screen is loaded on demand like the reports, and shares their period, tables and words:
  // what it imports from them travels with it, never with the first load.
  const REPORTS = /^(shared\/reports\/|i18n\/reports\/|shared\/cash\/)/;

  it("are loaded on demand: nothing imports the reports or the date-request list except through import()", () => {
    const offenders: string[] = [];
    for (const file of sources().filter((source) => !REPORTS.test(source.name))) {
      for (const match of file.text.matchAll(/^\s*import\s+(?!type\b)[^"']*?["']([^"']+)["']/gm)) {
        if (/reports\/|DateRequestsScreen/.test(match[1] ?? "")) {
          offenders.push(`${file.name}: ${match[1]}`);
        }
      }
    }
    expect(offenders).toEqual([]);
    const app = sources().find((source) => source.name === "shared/StaffApp.tsx")?.text ?? "";
    expect(app).toContain('lazy(() => import("./reports/ReportsScreen"))');
    expect(app).toContain('lazy(() => import("./cash/CashScreen"))');
    expect(app).not.toMatch(/^import .*["']\.\/cash\//m);
    expect(app).toContain('lazy(() => import("./workspace/DateRequestsScreen"))');
  });

  it("keep their text in their own catalog, named by nothing outside the reports", () => {
    const keys = Object.keys(uzReports);
    const offenders = sources()
      .filter((source) => !REPORTS.test(source.name))
      .flatMap((file) => keys.filter((key) => file.text.includes(`"${key}"`)).map((key) => `${file.name}: ${key}`));
    expect(offenders).toEqual([]);
    expect(keys.filter((key) => key in uz)).toEqual([]);
  });

  it("have the same keys, kinds and placeholders in both languages, and every message resolves", () => {
    expect(compareCatalogs(uzReports, ruReports)).toEqual({ missingInSecond: [], missingInFirst: [], kindMismatch: [], placeholderMismatch: [] });
    expect(compareCatalogs(uzReports, { ...ruReports, "reports.show": { one: "a", few: "b", many: "c" } }).kindMismatch).toEqual(["reports.show"]);
    for (const lang of ["uz", "ru"] as const) {
      for (const key of Object.keys(uzReports) as MessageKey[]) {
        // Added to the loaded messages by the reports module, which the tests above imported on demand.
        const message = catalogs[lang][key];
        const template = typeof message === "string" ? message : (Object.values(message)[0] ?? "");
        const params = Object.fromEntries(placeholdersOf(template).map((name) => [name, 3]));
        expect(translate(lang, key, typeof message === "string" ? params : { ...params, count: 3 })).not.toMatch(/[{}]/);
      }
    }
  });

  it("use only the plain apostrophe and no Cyrillic in Uzbek", () => {
    const entries = Object.entries(uzReports);
    expect(entries.filter(([, message]) => /[`‘’ʻʼ´]/.test(JSON.stringify(message))).map(([key]) => key)).toEqual([]);
    expect(entries.filter(([, message]) => /\p{Script=Cyrillic}/u.test(JSON.stringify(message))).map(([key]) => key)).toEqual([]);
  });
});

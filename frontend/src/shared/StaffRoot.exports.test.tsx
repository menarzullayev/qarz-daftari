// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { uzExports } from "../i18n/exports/uz";
import { uzImports } from "../i18n/imports/uz";
import { uzReceipts } from "../i18n/receipts/uz";
import { uzSupport } from "../i18n/support/uz";
import { creditSettingsBody, fakeServer, NOON, ok, refusal, settingsBody, SHOP_BASE, SHOP_ID, supportAccessBody } from "../testing/fakeServer";
import type { ApiAuth } from "./api";
import type { Role } from "./navigation";
import { StaffRoot } from "./StaffRoot";

beforeEach(() => {
  window.location.hash = "";
  window.localStorage.clear();
});
afterEach(cleanup);

const bearer = async (): Promise<ApiAuth> => ({ kind: "bearer", token: "session-token" });
const SUPPORT = `${SHOP_BASE}/support-access`;

function backend(role: Role) {
  return fakeServer((sent) => {
    switch (sent.path) {
      case "/api/v1/me/shops":
        return ok({ items: [{ shop_id: SHOP_ID, name: "Baraka savdo", role, membership_id: null }], active_shop: SHOP_ID });
      case "/api/v1/me/accounts":
        return ok({ items: [] });
      case `${SHOP_BASE}/overview`:
        return ok({ outstanding: 0, debtors: 0, overdue: { amount: 0, customers: 0 }, due_today: 0 });
      case `${SHOP_BASE}/overview/debtors`:
        return ok({ items: [], next_cursor: null });
      case SHOP_BASE:
        return ok(settingsBody());
      case `${SHOP_BASE}/credit-settings`:
        return ok(creditSettingsBody());
      case SUPPORT:
        return ok({ items: [supportAccessBody()], next_cursor: null });
    }
    return refusal(404, "NOT_FOUND", "Topilmadi.");
  });
}

async function miniApp(server: ReturnType<typeof fakeServer>, hash: string) {
  window.location.hash = hash;
  render(<StaffRoot entryKey="entry.app" initialLanguage="uz" connect={bearer} fetch={server.fetch} now={() => NOON} customerPage />);
  await screen.findAllByRole("navigation");
}
const supportCalls = (server: ReturnType<typeof fakeServer>) => server.sent.filter((sent) => sent.path.includes("/support-access"));

describe("support access in the Mini App", () => {
  it("shows the owner the section under the shop's settings, with the access that is open now", async () => {
    const server = backend("owner");
    await miniApp(server, "#/shop-settings");
    const section = await screen.findByRole("region", { name: "Yordam uchun kirish" });
    expect(await within(section).findByRole("button", { name: "Kirishni tugatish" })).toBeTruthy();
    expect(within(section).getByText("Ko'rsatilgan sabab: Egasi yordam so'radi")).toBeTruthy();
    expect(supportCalls(server).map((sent) => `${sent.method} ${sent.path}`)).toEqual([`GET ${SUPPORT}`]);
    // The Mini App has no room for a table: the history is a list of rows.
    expect(document.querySelector("table")).toBeNull();
  });

  it("shows a manager the settings without it, and asks nothing about support access", async () => {
    const server = backend("manager");
    await miniApp(server, "#/shop-settings");
    await waitFor(() => expect(server.sent.some((sent) => sent.path === `${SHOP_BASE}/credit-settings`)).toBe(true));
    await new Promise((resolve) => setTimeout(resolve, 40));
    expect(screen.queryByRole("region", { name: "Yordam uchun kirish" })).toBeNull();
    expect(supportCalls(server)).toEqual([]);
  });

  it("asks nothing about it on the way to recording a sale: the overview makes no such call", async () => {
    const server = backend("owner");
    await miniApp(server, "#/");
    await waitFor(() => expect(server.sent.some((sent) => sent.path === `${SHOP_BASE}/overview`)).toBe(true));
    await new Promise((resolve) => setTimeout(resolve, 40));
    expect(supportCalls(server)).toEqual([]);
  });
});

const SRC = resolve(import.meta.dirname, "..");
const sources = () =>
  readdirSync(SRC, { recursive: true, encoding: "utf8" })
    .map((name) => name.replaceAll("\\", "/"))
    .filter((name) => /\.(ts|tsx)$/.test(name) && !/\.test\.tsx?$/.test(name))
    .map((name) => ({ name, text: readFileSync(resolve(SRC, name), "utf8") }));
const staticImports = (text: string) => [...text.matchAll(/^\s*import\s+(?!type\b)[^"']*?["']([^"']+)["']/gm)].map((match) => match[1] ?? "");

describe("exports and support access stay out of the Mini App's first load (NFR-010)", () => {
  const EXPORTS = /^(shared\/exports\/|i18n\/exports\/)/;
  const SUPPORT_FILES = /^(shared\/support\/|i18n\/support\/)/;

  it("loads the export screen on demand: nothing imports it except through import()", () => {
    const offenders = sources()
      .filter((source) => !EXPORTS.test(source.name))
      .flatMap((file) => staticImports(file.text).filter((specifier) => /(^|\/)exports\//.test(specifier)).map((specifier) => `${file.name}: ${specifier}`));
    expect(offenders).toEqual([]);
    const app = sources().find((source) => source.name === "shared/StaffApp.tsx")?.text ?? "";
    expect(app).toContain('lazy(() => withMessages(import("./exports/ExportsScreen")))');
  });

  it("loads the import screen and the owner's subscription receipts on demand too: nothing imports them except through import()", () => {
    for (const [folder, importer, line] of [
      ["imports", "shared/StaffApp.tsx", 'lazy(() => withMessages(import("./imports/ImportScreen")))'],
      ["receipts", "shared/workspace/SubscriptionScreen.tsx", 'lazy(() => withMessages(import("../receipts/ReceiptSection")))'],
    ] as const) {
      const own = new RegExp(`^(shared/${folder}/|i18n/${folder}/)`);
      const offenders = sources()
        .filter((source) => !own.test(source.name))
        .flatMap((file) => staticImports(file.text).filter((specifier) => new RegExp(`(^|/)${folder}/`).test(specifier)).map((specifier) => `${file.name}: ${specifier}`));
      expect(offenders).toEqual([]);
      expect(sources().find((source) => source.name === importer)?.text ?? "").toContain(line);
    }
    expect(staticImports('import ImportScreen from "./imports/ImportScreen";').some((specifier) => /(^|\/)imports\//.test(specifier))).toBe(true);
  });

  it("loads the owner's support access on demand everywhere but in the web panel, whose notice is on every screen", () => {
    const importers = sources()
      .filter((source) => !SUPPORT_FILES.test(source.name))
      .filter((file) => staticImports(file.text).some((specifier) => /(^|\/)support\/(messages|supportApi|SupportAccessSection)$/.test(specifier)))
      .map((file) => file.name)
      .sort();
    expect(importers).toEqual(["panel/OfficeBanner.tsx", "panel/PanelRoot.tsx", "panel/office.tsx"]);
    const app = sources().find((source) => source.name === "shared/StaffApp.tsx")?.text ?? "";
    expect(app).toContain('lazy(() => withMessages(import("./support/SupportAccessSection")))');
  });

  it("would notice a static import, and lets a type-only one and an import() pass", () => {
    expect(staticImports('import ExportsScreen from "./exports/ExportsScreen";')).toEqual(["./exports/ExportsScreen"]);
    expect(staticImports('import "../shared/support/messages";')).toEqual(["../shared/support/messages"]);
    expect(staticImports('import type { ExportJob } from "./exports/exportsApi";')).toEqual([]);
    expect(staticImports('const Screen = lazy(() => import("./exports/ExportsScreen"));')).toEqual([]);
  });

  it("keeps their text in their own catalogs: no key of theirs is named by a file loaded first", () => {
    const named = (keys: string[], allowed: RegExp) =>
      sources()
        .filter((source) => !allowed.test(source.name))
        .flatMap((file) => keys.filter((key) => file.text.includes(`"${key}"`)).map((key) => `${file.name}: ${key}`));
    expect(named(Object.keys(uzExports), EXPORTS)).toEqual([]);
    expect(named(Object.keys(uzImports), /^(shared\/imports\/|i18n\/imports\/)/)).toEqual([]);
    expect(named(Object.keys(uzReceipts), /^(shared\/receipts\/|i18n\/receipts\/)/)).toEqual([]);
    // The panel's notice speaks from the support catalog, which the panel loads when it starts.
    expect(named(Object.keys(uzSupport), /^(shared\/support\/|i18n\/support\/|panel\/OfficeBanner\.tsx$)/)).toEqual([]);
  });

  it("gives the administrator's entry neither: it has its own text and its own calls for support access", () => {
    const admin = sources().filter((source) => source.name.startsWith("admin/") && source.name !== "admin/testing.tsx");
    expect(admin.length).toBeGreaterThan(12);
    const offenders = admin.flatMap((file) =>
      [...file.text.matchAll(/["']([^"']*(?:shared\/support|shared\/exports|shared\/imports|shared\/receipts|i18n\/support|i18n\/exports|i18n\/imports|i18n\/receipts)[^"']*)["']/g)].map((match) => `${file.name}: ${match[1]}`),
    );
    expect(offenders).toEqual([]);
  });
});

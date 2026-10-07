import { readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { catalogs, compareCatalogs, isSameCatalogShape, placeholdersOf, translate } from "../i18n/catalog";
import { ruPanel } from "../i18n/panel/ru";
import { uzPanel } from "../i18n/panel/uz";
import type { MessageKey } from "../i18n/types";
import { uz } from "../i18n/uz";
import { ACTION_GROUPS } from "./ActivityScreen";
import "./messages";

/**
 * The panel's code and text must stay out of the Telegram Mini App's first load (NFR-010): the Mini App
 * has a 300 KB budget on a slow phone, and nothing here is of use to it. These checks hold the line in
 * the source, where a slip is cheap to see; `npm run size` measures the result.
 */

const SRC = resolve(import.meta.dirname, "..");

function sources(folder: string): { name: string; text: string }[] {
  return readdirSync(resolve(SRC, folder), { recursive: true, encoding: "utf8" })
    .map((name) => name.replaceAll("\\", "/"))
    .filter((name) => /\.(ts|tsx|css)$/.test(name) && !/\.test\.tsx?$/.test(name))
    .map((name) => ({ name: `${folder}/${name}`, text: readFileSync(resolve(SRC, folder, name), "utf8") }));
}

/** Everything the Mini App's entry can reach: its own folder, the shared code, and the main catalog. */
const OUTSIDE_PANEL = [...sources("app"), ...sources("shared"), ...sources("admin"), ...sources("testing")].concat(
  sources("i18n").filter((file) => !file.name.startsWith("i18n/panel/")),
);

/** Module specifiers a file imports, statically or on demand. `import type` is erased and does not count. */
function imports(text: string): string[] {
  const found = [...text.matchAll(/^\s*import\s+(?!type\b)[^"']*?["']([^"']+)["']/gm), ...text.matchAll(/import\(\s*["']([^"']+)["']\s*\)/g)];
  return found.map((match) => match[1] ?? "");
}

describe("the panel's code stays in the panel's entry", () => {
  it("looks at the files the Mini App is built from", () => {
    const names = OUTSIDE_PANEL.map((file) => file.name);
    expect(names).toContain("app/main.tsx");
    expect(names).toContain("shared/StaffRoot.tsx");
    expect(names).toContain("shared/layout.tsx");
    expect(names).toContain("i18n/catalog.ts");
    expect(names.some((name) => name.startsWith("panel/") || name.startsWith("i18n/panel/"))).toBe(false);
  });

  it("is imported by nothing outside it", () => {
    const offenders = OUTSIDE_PANEL.filter((file) =>
      imports(file.text).some((specifier) => /(^|\/)panel(\/|$)/.test(specifier)),
    ).map((file) => file.name);
    expect(offenders).toEqual([]);
  });

  it("would notice such an import, static or on demand, and lets a type-only one pass", () => {
    expect(imports('import { PanelRoot } from "../panel/PanelRoot";')).toEqual(["../panel/PanelRoot"]);
    expect(imports('import "../panel/panel.css";')).toEqual(["../panel/panel.css"]);
    expect(imports('const m = await import("../i18n/panel/uz");')).toEqual(["../i18n/panel/uz"]);
    expect(imports('import {\n  a,\n  b,\n} from "../panel/x";')).toEqual(["../panel/x"]);
    expect(imports('import type { uzPanel } from "./panel/uz";')).toEqual([]);
  });

  it("keeps the panel's text out of the shared screens: none of its keys is named outside it", () => {
    const keys = Object.keys(uzPanel);
    const offenders: string[] = [];
    for (const file of OUTSIDE_PANEL) {
      for (const key of keys) {
        if (file.text.includes(`"${key}"`)) {
          offenders.push(`${file.name}: ${key}`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });

  it("names Telegram's sign-in script in the widget alone", () => {
    const mentions = [...OUTSIDE_PANEL, ...sources("panel")]
      .filter((file) => file.text.includes("telegram-widget"))
      .map((file) => file.name);
    expect(mentions).toEqual(["panel/TelegramLogin.tsx"]);
  });

  it("never writes a session, a token or anything else to browser storage", () => {
    const offenders = sources("panel")
      .filter((file) => /localStorage|sessionStorage|indexedDB|document\.cookie/.test(file.text))
      .map((file) => file.name);
    // The helper for tests clears nothing and stores nothing either.
    expect(offenders).toEqual([]);
  });
});

describe("the panel's catalog", () => {
  it("has the same keys, kinds and placeholders in both languages", () => {
    const difference = compareCatalogs(uzPanel, ruPanel);
    expect(difference).toEqual({ missingInSecond: [], missingInFirst: [], kindMismatch: [], placeholderMismatch: [] });
    expect(isSameCatalogShape(difference)).toBe(true);
    expect(Object.keys(uzPanel).length).toBeGreaterThan(100);
  });

  it("would notice a key missing from Russian or a placeholder that differs", () => {
    const withoutKey = Object.fromEntries(Object.entries(ruPanel).filter(([key]) => key !== "panel.signOut"));
    expect(compareCatalogs(uzPanel, withoutKey).missingInSecond).toEqual(["panel.signOut"]);
    expect(compareCatalogs(uzPanel, { ...ruPanel, "deletion.pending": "Удаление запрошено." }).placeholderMismatch).toEqual([
      "deletion.pending",
    ]);
  });

  it("shares no key with the main catalog, so neither can replace the other's text", () => {
    expect(Object.keys(uzPanel).filter((key) => key in uz)).toEqual([]);
  });

  it("is loaded by the panel's modules, and every message resolves in both languages", () => {
    for (const lang of ["uz", "ru"] as const) {
      for (const key of Object.keys(uzPanel) as MessageKey[]) {
        const message = catalogs[lang][key];
        const template = typeof message === "string" ? message : (Object.values(message)[0] ?? "");
        const params = Object.fromEntries(placeholdersOf(template).map((name) => [name, 3]));
        const text = translate(lang, key, typeof message === "string" ? params : { ...params, count: 3 });
        expect(text).not.toMatch(/[{}]/);
        expect(text.length).toBeGreaterThan(0);
      }
    }
  });

  it("uses only the plain apostrophe in Uzbek and no Cyrillic letter there", () => {
    const lookAlikes = /[`‘’ʻʼ´]/;
    const entries = Object.entries(uzPanel);
    expect(entries.filter(([, message]) => lookAlikes.test(JSON.stringify(message))).map(([key]) => key)).toEqual([]);
    expect(entries.filter(([, message]) => /\p{Script=Cyrillic}/u.test(JSON.stringify(message))).map(([key]) => key)).toEqual([]);
    expect(Object.entries(ruPanel).filter(([, message]) => !/\p{Script=Cyrillic}/u.test(JSON.stringify(message))).map(([key]) => key)).toEqual([
      "panel.shop.option",
      "staff.member.label",
    ]);
  });

  it("has a name for every group of actions the log filters by", () => {
    for (const group of ACTION_GROUPS) {
      expect(translate("uz", `activity.group.${group}`).length).toBeGreaterThan(0);
      expect(translate("ru", `activity.group.${group}`).length).toBeGreaterThan(0);
    }
  });
});

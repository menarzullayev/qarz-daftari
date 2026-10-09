import { readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { catalogs, compareCatalogs, placeholdersOf, translate } from "../../i18n/catalog";
import { ru } from "../../i18n/ru";
import { ruStock } from "../../i18n/stock/ru";
import { uzStock } from "../../i18n/stock/uz";
import type { MessageKey } from "../../i18n/types";
import { uz } from "../../i18n/uz";
import { definedTokens, readStyleSheet, styleProblems, usedTokens } from "../../testing/styleGuard";
import "./messages";

/**
 * What holds the stock's module to the rules every module follows (docs/09-development-plan/EXPANSION.md):
 * its text in two languages and in its own catalog, its style sheet made of the design tokens, its code
 * outside the first load, and the proxy's permission for the camera limited to the two staff pages.
 */

const SRC = resolve(import.meta.dirname, "..", "..");
const REPO = resolve(SRC, "..", "..");

function sources(directory: string): { name: string; text: string }[] {
  return readdirSync(directory, { withFileTypes: true, recursive: true })
    .filter((entry) => entry.isFile() && /\.tsx?$/.test(entry.name) && !entry.name.includes(".test."))
    .map((entry) => {
      const path = resolve(entry.parentPath, entry.name);
      return { name: path.slice(SRC.length + 1).replaceAll("\\", "/"), text: readFileSync(path, "utf8") };
    });
}

describe("the stock's text", () => {
  it("has the same keys, kinds and placeholders in Uzbek and in Russian", () => {
    expect(compareCatalogs(uzStock, ruStock)).toEqual({ missingInSecond: [], missingInFirst: [], kindMismatch: [], placeholderMismatch: [] });
    // The check sees a difference when there is one.
    const short = Object.fromEntries(Object.entries(ruStock).filter(([key]) => key !== "stock.low"));
    expect(compareCatalogs(uzStock, short).missingInSecond).toEqual(["stock.low"]);
    expect(compareCatalogs(uzStock, { ...ruStock, "stock.low": { one: "a", few: "b", many: "c" } }).kindMismatch).toEqual(["stock.low"]);
  });

  it("resolves in both languages with its parameters, and no message is left untranslated", () => {
    for (const lang of ["uz", "ru"] as const) {
      for (const key of Object.keys(uzStock) as MessageKey[]) {
        const message = catalogs[lang][key];
        const template = typeof message === "string" ? message : (Object.values(message)[0] ?? "");
        const params = Object.fromEntries(placeholdersOf(template).map((name) => [name, 3]));
        expect(translate(lang, key, typeof message === "string" ? params : { ...params, count: 3 })).not.toMatch(/[{}]/);
      }
    }
    // Russian is written in Cyrillic and Uzbek in Latin: a message copied across would show here.
    const cyrillic = /\p{Script=Cyrillic}/u;
    for (const [key, message] of Object.entries(uzStock)) {
      expect(cyrillic.test(JSON.stringify(message)), key).toBe(false);
    }
    const latinOnly = Object.entries(ruStock).filter(([, message]) => !cyrillic.test(JSON.stringify(message)));
    // The one message that is the same in every language: a kind and a number.
    expect(latinOnly.map(([key]) => key)).toEqual(["stock.doc.ref"]);
  });

  it("is in its own catalog: no key of it is in the first load's, except the few the first load shows", () => {
    const shared = Object.keys(uzStock).filter((key) => key in uz || key in ru);
    expect(shared).toEqual([]);
    // What the shell and the sale screens need before the module is fetched: the three section names
    // and the three sentences about a sale. They are in the main catalog, in both languages.
    for (const key of ["nav.stock", "nav.stockDocuments", "nav.suppliers", "stock.warn.negative", "stock.warn.unit", "stock.refused.figures"]) {
      expect(key in uz && key in ru, key).toBe(true);
      expect(key in uzStock, key).toBe(false);
    }
  });

  it("is named by nothing outside the stock's module", () => {
    const keys = Object.keys(uzStock);
    const offenders = sources(SRC)
      .filter((source) => !/^(shared\/stock|i18n\/stock)\//.test(source.name))
      .flatMap((file) => keys.filter((key) => file.text.includes(`"${key}"`)).map((key) => `${file.name}: ${key}`));
    expect(offenders).toEqual([]);
  });
});

describe("the stock's code", () => {
  it("is fetched on demand: nothing outside it imports it, but for the one lazy import and the route names", () => {
    const importers = sources(SRC)
      // The network between shops is fetched on demand as well, and builds on the stock's parts.
      .filter((source) => !source.name.startsWith("shared/stock/") && !source.name.startsWith("shared/network/"))
      .flatMap((file) =>
        [...file.text.matchAll(/^import (?!type\b)[^;]*?from\s+["'][^"']*\/stock\/[^"']*["'];/gm)].map((match) => `${file.name}: ${match[0]}`),
      );
    expect(importers).toEqual([]);
    const app = sources(SRC).find((source) => source.name === "shared/StaffApp.tsx")?.text ?? "";
    expect(app).toContain('lazy(() => import("./stock/StockScreens"))');
    // Would a plain import be noticed? This is what the check looks for.
    expect(/^import (?!type\b)[^;]*?from\s+["'][^"']*\/stock\/[^"']*["'];/m.test('import { StockScreen } from "./stock/StockScreen";')).toBe(true);
  });

  it("has no inline style anywhere: the page's policy refuses them", () => {
    for (const file of sources(resolve(SRC, "shared", "stock"))) {
      expect(file.text, file.name).not.toMatch(/\bstyle=\{/);
      expect(file.text, file.name).not.toMatch(/\.style\./);
    }
    expect(/\bstyle=\{/.test("<p style={{ color: 1 }} />")).toBe(true);
  });
});

describe("the stock's style sheet", () => {
  const css = readStyleSheet("shared", "stock", "stock.css");
  const tokens = definedTokens();

  it("writes out no color, uses no unknown variable and leaves the focus ring alone", () => {
    expect(styleProblems(css, tokens)).toEqual([]);
    const used = usedTokens(css);
    expect(used.length).toBeGreaterThan(0);
    expect(used.filter((name) => !tokens.has(name))).toEqual([]);
  });

  it("moves nothing: no animation and no transition", () => {
    expect(css).not.toMatch(/animation|transition|@keyframes/);
  });

  it("would be refused for a color, an unknown variable or a removed outline", () => {
    expect(styleProblems(css + ".scan { color: #c00; }", tokens)).toEqual(["color written out: #c00"]);
    expect(styleProblems(css + ".scan { color: var(--qd-scan); }", tokens)).toEqual(["unknown variable: --qd-scan"]);
    expect(styleProblems(css + ".scan__video { outline: none; }", tokens)).toEqual(["outline changed"]);
  });
});

describe("the proxy lets the staff pages, and no other page, use the camera", () => {
  const snippet = (name: string) =>
    readFileSync(resolve(REPO, "deploy", "production", "nginx", "snippets", name), "utf8").replace(/#.*/g, "");
  const headers = (text: string, name: string) =>
    [...text.matchAll(new RegExp(`^add_header ${name} "?([^";]*)"? always;`, "gm"))].map((match) => match[1] ?? "");
  const policy = (text: string) => Object.fromEntries(headers(text, "Permissions-Policy").flatMap((value) => value.split(",").map((part) => part.trim().split("="))));
  const names = (text: string) => [...text.matchAll(/^add_header (\S+)/gm)].map((match) => match[1]);

  it("allows the camera to the page itself in the staff headers, and nothing else more than before", () => {
    const common = policy(snippet("headers-common.conf"));
    const staff = policy(snippet("headers-staff.conf"));
    expect(common["camera"]).toBe("()");
    expect(staff["camera"]).toBe("(self)");
    expect({ ...staff, camera: "()" }).toEqual(common);
    // Never every origin, and never the microphone: a barcode needs a picture only.
    expect(staff["camera"]).not.toContain("*");
    expect(staff["microphone"]).toBe("()");
  });

  it("sends every other header of the common file unchanged, each once", () => {
    const common = snippet("headers-common.conf");
    const staff = snippet("headers-staff.conf");
    expect(names(staff)).toEqual(names(common));
    expect(new Set(names(staff)).size).toBe(names(staff).length);
    for (const name of names(common).filter((header) => header !== "Permissions-Policy")) {
      expect(headers(staff, name ?? ""), name).toEqual(headers(common, name ?? ""));
    }
  });

  it("is included by the Mini App's and the panel's headers in place of the common file, so no header is sent twice", () => {
    for (const name of ["headers-app.conf", "headers-panel.conf"]) {
      const text = snippet(name);
      expect(text, name).toContain("include /etc/nginx/snippets/headers-staff.conf;");
      expect(text, name).not.toContain("headers-common.conf");
      expect(headers(text, "Permissions-Policy"), name).toEqual([]);
    }
  });

  it("is not included by any other page: the administrators', the customer's, the API, the files, the worker", () => {
    const others = readdirSync(resolve(REPO, "deploy", "production", "nginx", "snippets")).filter(
      (name) => name.startsWith("headers-") && !["headers-app.conf", "headers-panel.conf", "headers-staff.conf"].includes(name),
    );
    expect(others).toEqual(expect.arrayContaining(["headers-admin.conf", "headers-api.conf", "headers-common.conf", "headers-customer.conf"]));
    for (const name of others) {
      const text = snippet(name);
      expect(text, name).not.toContain("headers-staff.conf");
      expect(text, name).not.toContain("camera=(self)");
    }
    expect(policy(snippet("headers-customer.conf"))["camera"]).toBe("()");
  });

  it("needs no change of the content policy: the picture is a stream, not a file that is fetched", () => {
    // `default-src 'none'` with no media-src stays as it was; the video element is given the stream itself.
    for (const name of ["headers-app.conf", "headers-panel.conf"]) {
      const csp = /^add_header Content-Security-Policy "([^"]*)" always;/m.exec(snippet(name))?.[1] ?? "";
      expect(csp, name).toContain("default-src 'none'");
      expect(csp, name).not.toContain("media-src");
      expect(csp, name).not.toContain("unsafe-inline");
    }
    const field = readFileSync(resolve(SRC, "shared", "stock", "ScanField.tsx"), "utf8");
    expect(field).toContain("element.srcObject = opened");
    expect(field).not.toContain("createObjectURL");
  });
});

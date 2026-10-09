import { readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { catalogs, compareCatalogs, placeholdersOf, translate } from "../../i18n/catalog";
import { ruNetwork } from "../../i18n/network/ru";
import { uzNetwork } from "../../i18n/network/uz";
import { ru } from "../../i18n/ru";
import type { MessageKey } from "../../i18n/types";
import { uz } from "../../i18n/uz";
import { definedTokens, readStyleSheet, styleProblems, usedTokens } from "../../testing/styleGuard";
import "./messages";

/**
 * What holds the network's module to the rules every module follows (docs/09-development-plan/EXPANSION.md):
 * its text in two languages and in its own catalog, its style sheet made of the design tokens, and its
 * code outside the first load.
 */

const SRC = resolve(import.meta.dirname, "..", "..");

function sources(directory: string): { name: string; text: string }[] {
  return readdirSync(directory, { withFileTypes: true, recursive: true })
    .filter((entry) => entry.isFile() && /\.tsx?$/.test(entry.name) && !entry.name.includes(".test."))
    .map((entry) => {
      const path = resolve(entry.parentPath, entry.name);
      return { name: path.slice(SRC.length + 1).replaceAll("\\", "/"), text: readFileSync(path, "utf8") };
    });
}

describe("the network's text", () => {
  it("has the same keys, kinds and placeholders in Uzbek and in Russian", () => {
    expect(compareCatalogs(uzNetwork, ruNetwork)).toEqual({ missingInSecond: [], missingInFirst: [], kindMismatch: [], placeholderMismatch: [] });
    // The check sees a difference when there is one.
    const short = Object.fromEntries(Object.entries(ruNetwork).filter(([key]) => key !== "net.waiting"));
    expect(compareCatalogs(uzNetwork, short).missingInSecond).toEqual(["net.waiting"]);
    expect(compareCatalogs(uzNetwork, { ...ruNetwork, "net.recon.weOwe": "без суммы" }).placeholderMismatch).toEqual(["net.recon.weOwe"]);
  });

  it("resolves in both languages with its parameters, and no message is left untranslated", () => {
    for (const lang of ["uz", "ru"] as const) {
      for (const key of Object.keys(uzNetwork) as MessageKey[]) {
        const message = catalogs[lang][key];
        const template = typeof message === "string" ? message : (Object.values(message)[0] ?? "");
        const params = Object.fromEntries(placeholdersOf(template).map((name) => [name, 3]));
        expect(translate(lang, key, typeof message === "string" ? params : { ...params, count: 3 })).not.toMatch(/[{}]/);
      }
    }
    // Russian is written in Cyrillic and Uzbek in Latin: a message copied across would show here.
    const cyrillic = /\p{Script=Cyrillic}/u;
    for (const [key, message] of Object.entries(uzNetwork)) {
      expect(cyrillic.test(JSON.stringify(message)), key).toBe(false);
    }
    const latinOnly = Object.entries(ruNetwork).filter(([, message]) => !cyrillic.test(JSON.stringify(message)));
    // The one message that is the same in every language: a time and who.
    expect(latinOnly.map(([key]) => key)).toEqual(["net.history.line"]);
  });

  it("says the things that must be said in so many words", () => {
    expect(uzNetwork["net.waiting"]).toBe("Sizdan javob kutilmoqda");
    expect([uzNetwork["net.partner.removed"], ruNetwork["net.partner.removed"]]).toEqual(["Hamkor o'chirilgan", "Партнёр удалён"]);
    expect([uzNetwork["net.recon.differ"], ruNetwork["net.recon.differ"]]).toEqual(["Daftarlar farq qiladi", "Книги расходятся"]);
    expect([uzNetwork["net.by.own"], ruNetwork["net.by.own"], uzNetwork["net.by.partner"], ruNetwork["net.by.partner"]]).toEqual(["biz", "мы", "hamkor", "партнёр"]);
    // The button that confirms a delivery names both things it does, in both languages.
    expect(uzNetwork["net.note.confirm.submit"]).toMatch(/omborga kirim.*nasiya/);
    expect(ruNetwork["net.note.confirm.submit"]).toMatch(/оприходован.*в долг/);
  });

  it("is in its own catalog: no key of it is in the first load's, except the section's name", () => {
    expect(Object.keys(uzNetwork).filter((key) => key in uz || key in ru)).toEqual([]);
    expect("nav.network" in uz && "nav.network" in ru).toBe(true);
    expect("nav.network" in uzNetwork).toBe(false);
    expect([uz["nav.network"], ru["nav.network"]]).toEqual(["Hamkorlar", "Партнёры"]);
  });

  it("is named by nothing outside the network's module, and every key of it is used", () => {
    const keys = Object.keys(uzNetwork);
    const all = sources(SRC);
    const offenders = all
      .filter((source) => !/^(shared\/network|i18n\/network)\//.test(source.name))
      .flatMap((file) => keys.filter((key) => file.text.includes(`"${key}"`)).map((key) => `${file.name}: ${key}`));
    expect(offenders).toEqual([]);
    const own = all.filter((source) => source.name.startsWith("shared/network/")).map((source) => source.text).join("\n");
    expect(keys.filter((key) => !own.includes(`"${key}"`))).toEqual([]);
  });
});

describe("the network's code", () => {
  const IMPORT = /^import (?!type\b)[^;]*?from\s+["'][^"']*\/network\/[^"']*["'];/gm;

  it("is fetched on demand: nothing outside it imports it, but for the one lazy import and the route names", () => {
    const importers = sources(SRC)
      .filter((source) => !source.name.startsWith("shared/network/"))
      .flatMap((file) => [...file.text.matchAll(IMPORT)].map((match) => `${file.name}: ${match[0]}`));
    expect(importers).toEqual([]);
    const app = sources(SRC).find((source) => source.name === "shared/StaffApp.tsx")?.text ?? "";
    expect(app).toContain('lazy(() => import("./network/NetworkScreens"))');
    // Would a plain import be noticed? This is what the check looks for.
    expect([...'import { HomeScreen } from "./network/LinkScreens";'.matchAll(IMPORT)]).toHaveLength(1);
  });

  it("has no inline style anywhere: the page's policy refuses them", () => {
    const files = sources(resolve(SRC, "shared", "network"));
    expect(files.length).toBeGreaterThan(5);
    for (const file of files) {
      expect(file.text, file.name).not.toMatch(/\bstyle=\{/);
      expect(file.text, file.name).not.toMatch(/\.style\./);
    }
    expect(/\bstyle=\{/.test("<p style={{ color: 1 }} />")).toBe(true);
  });

  it("gives nobody outside the application a way to a delivery note: it is printed, never linked", () => {
    const text = sources(resolve(SRC, "shared", "network"))
      .map((file) => file.text)
      .join("\n");
    expect(text).toContain("window.print()");
    expect(text).not.toMatch(/https?:\/\//);
    expect(text).not.toMatch(/location\.origin|share\(|createObjectURL/);
  });
});

describe("the network's style sheet", () => {
  const css = readStyleSheet("shared", "network", "network.css");
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

  it("has what a printed note needs, and is refused for a color, an unknown variable or a removed outline", () => {
    expect(css).toMatch(/@media print \{\s*\.net-note \{/);
    expect(styleProblems(css + ".net-code { color: #c00; }", tokens)).toEqual(["color written out: #c00"]);
    expect(styleProblems(css + ".net-code { color: var(--qd-net); }", tokens)).toEqual(["unknown variable: --qd-net"]);
    expect(styleProblems(css + ".net-code { outline: none; }", tokens)).toEqual(["outline changed"]);
  });
});

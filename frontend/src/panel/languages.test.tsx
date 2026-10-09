// @vitest-environment jsdom
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { loadLanguage } from "../i18n/catalog";
import { I18nProvider } from "../i18n/I18nProvider";
import { LANGUAGES, type Language } from "../i18n/types";
import { createApi } from "../shared/api";
import { fakeServer, ok, SHOP_BASE, SHOP_ID } from "../testing/fakeServer";
import { backoffice } from "./backoffice";
import { TelegramLogin, WIDGET_LANGUAGE } from "./TelegramLogin";

afterEach(cleanup);

/**
 * The two places of the web panel where a language is not simply the reader's: the names of permissions,
 * which the server sends in the languages it has them in, and Telegram's own login button, which speaks
 * Telegram's languages.
 */
describe("the names of permissions", () => {
  const CATALOGUE = {
    groups: [
      {
        key: "ledger",
        label: { uz: "Qarz va to'lovlar", "uz-Cyrl": "Қарз ва тўловлар", ru: "Долги и оплаты", tg: "Қарз ва пардохтҳо", kaa: "", en: "Debts and payments", de: "Schulden" },
        permissions: [
          // A permission another module added: the server has it in Uzbek and Russian only.
          { key: "cash.record", label: { uz: "Kassaga yozish", ru: "Записывать в кассу" }, roles: ["owner"], fixed: false },
        ],
      },
    ],
  };

  async function read() {
    const server = fakeServer((sent) => (sent.path === `${SHOP_BASE}/permissions` ? ok(CATALOGUE) : ok({})));
    const api = createApi({ fetch: server.fetch, auth: { kind: "bearer", token: "test-session" } }).shop(SHOP_ID);
    const groups = await backoffice(api).permissionCatalogue();
    if (groups === null || groups[0] === undefined) {
      throw new Error("no catalogue was read");
    }
    return groups[0];
  }

  it("are kept in every language the server sent, and in no other", async () => {
    const group = await read();
    expect(group.label).toEqual({
      uz: "Qarz va to'lovlar",
      "uz-Cyrl": "Қарз ва тўловлар",
      ru: "Долги и оплаты",
      tg: "Қарз ва пардохтҳо",
      en: "Debts and payments",
    });
    // An empty name is no name, and a language the product does not have is not kept.
    expect(Object.keys(group.label)).not.toContain("kaa");
    expect(Object.keys(group.label)).not.toContain("de");
  });

  it("read Uzbek for a reader whose language the server did not name the permission in", async () => {
    const group = await read();
    const name = (language: Language, label: typeof group.label) => label[language] ?? label.uz;
    expect(LANGUAGES.map((language) => name(language, group.label))).toEqual([
      "Qarz va to'lovlar",
      "Қарз ва тўловлар",
      "Долги и оплаты",
      "Қарз ва пардохтҳо",
      "Qarz va to'lovlar",
      "Debts and payments",
    ]);
    const added = group.permissions[0]?.label ?? { uz: "", ru: "" };
    expect(LANGUAGES.map((language) => name(language, added))).toEqual([
      "Kassaga yozish",
      "Kassaga yozish",
      "Записывать в кассу",
      "Kassaga yozish",
      "Kassaga yozish",
      "Kassaga yozish",
    ]);
  });
});

describe("Telegram's login button", () => {
  it("is asked for in a language Telegram's widget has, for each of the six", () => {
    expect(Object.keys(WIDGET_LANGUAGE).sort()).toEqual([...LANGUAGES].sort());
    expect(WIDGET_LANGUAGE).toEqual({ uz: "uz", "uz-Cyrl": "uz", ru: "ru", tg: "uz", kaa: "uz", en: "en" });
  });

  it.each([
    ["uz-Cyrl", "uz"],
    ["tg", "uz"],
    ["kaa", "uz"],
    ["en", "en"],
    ["ru", "ru"],
  ] as const)("is written in %s as Telegram's %s", async (language, widget) => {
    await loadLanguage(language);
    const { container } = render(
      <I18nProvider initialLanguage={language}>
        <TelegramLogin botUsername="qd_test_bot" language={language} />
      </I18nProvider>,
    );
    expect(container.querySelector("script")?.getAttribute("data-lang")).toBe(widget);
  });
});

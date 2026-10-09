// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { formatCustomerCount, formatDueStatus, formatFullDate, formatMoney } from "../shared/format";
import { StaffApp } from "../shared/StaffApp";
import { enPluralCategory, loadLanguage, translate } from "./catalog";
import { LANGUAGE_STORAGE_KEY } from "./detect";
import { DIRECTIONS, LANGUAGES, type Language, type Message, type MessageKey } from "./types";

/**
 * Six languages, of which only Uzbek is part of the first load. This file reaches the other five the
 * way the application does, by fetching them, and proves what a reader is shown while a language is on
 * its way, when it cannot be fetched, and when it lacks a message.
 */
type Catalog = typeof import("./catalog");
type Texts = Record<string, Message>;

/** A catalog module as an entry point first has it: nothing fetched, nothing put there beforehand. */
async function freshCatalog(): Promise<Catalog> {
  vi.resetModules();
  return import("./catalog");
}

const key = (name: string) => name as MessageKey;

afterEach(cleanup);

describe("what is loaded", () => {
  it("starts with Uzbek alone and reads Uzbek for a language that has not arrived", async () => {
    const catalog = await freshCatalog();
    expect(LANGUAGES.map((language) => catalog.hasLanguage(language))).toEqual([true, false, false, false, false, false]);
    for (const language of LANGUAGES) {
      expect(catalog.translate(language, "nav.customers")).toBe("Mijozlar");
      expect(catalog.translate(language, "customers.count", { count: 3 })).toBe("3 ta mijoz");
    }
  });

  it.each([
    ["ru", "Клиенты", "5 клиентов"],
    ["tg", "Мизоҷон", "5 мизоҷ"],
    ["kaa", "Qarıydarlar", "5 qarıydar"],
    ["en", "Customers", "5 customers"],
    ["uz-Cyrl", "Мижозлар", "5 та мижоз"],
  ] as const)("fetches %s when it is asked for, and no other language with it", async (language, customers, five) => {
    const catalog = await freshCatalog();
    await catalog.loadLanguage(language);
    expect(catalog.hasLanguage(language)).toBe(true);
    expect(catalog.translate(language, "nav.customers")).toBe(customers);
    expect(catalog.translate(language, "customers.count", { count: 5 })).toBe(five);
    for (const other of LANGUAGES) {
      if (other !== language && other !== "uz") {
        expect(catalog.hasLanguage(other), other).toBe(false);
        expect(catalog.translate(other, "nav.customers"), other).toBe("Mijozlar");
      }
    }
  });

  it("names the Uzbek catalog alone in the module every entry point loads", () => {
    const source = readFileSync(resolve(import.meta.dirname, "catalog.ts"), "utf8");
    const staticImports = [...source.matchAll(/^import (?!type\b)[^;]*?from "([^"]+)";/gms)].map((found) => found[1]);
    expect(staticImports.sort()).toEqual(["./types", "./uz"]);
    for (const language of ["ru", "tg", "kaa", "en"]) {
      expect(source).toContain(`import("./${language}")`);
    }
    expect(source).toContain('import("./uzCyrillic")');
    // Would a language slipped into the first load be noticed? This is how the check reads an import.
    expect([...'import { ru } from "./ru";'.matchAll(/^import (?!type\b)[^;]*?from "([^"]+)";/gms)].map((found) => found[1])).toEqual(["./ru"]);
  });
});

describe("a message a language does not have", () => {
  const uz: Texts = {
    "t.hello": "Salom, {name}",
    "t.days": { other: "{count} kun qoldi" },
    "t.only": "Faqat o'zbekcha",
  };

  it("is read in Uzbek, with its values filled in: never a key, never nothing, never an error", async () => {
    const catalog = await freshCatalog();
    catalog.addMessages({ uz, tg: () => Promise.resolve({ "t.hello": "Салом, {name}" }), en: () => Promise.resolve({}) });
    await catalog.loadLanguage("tg");
    await catalog.loadLanguage("en");
    expect(catalog.translate("tg", key("t.hello"), { name: "Ali" })).toBe("Салом, Ali");
    expect(catalog.translate("tg", key("t.only"))).toBe("Faqat o'zbekcha");
    expect(catalog.translate("tg", key("t.days"), { count: 3 })).toBe("3 kun qoldi");
    expect(catalog.translate("en", key("t.hello"), { name: "Ali" })).toBe("Salom, Ali");
    // A catalog that has no part at all for a language (other work adds Uzbek and Russian only).
    expect(catalog.translate("kaa", key("t.hello"), { name: "Ali" })).toBe("Salom, Ali");
  });

  it("is written in Cyrillic for Uzbek Cyrillic, values left as they were typed", async () => {
    const catalog = await freshCatalog();
    catalog.addMessages({ uz });
    await catalog.loadLanguage("uz-Cyrl");
    expect(catalog.translate("uz-Cyrl", key("t.hello"), { name: "Ali Valiyev" })).toBe("Салом, Ali Valiyev");
    expect(catalog.translate("uz-Cyrl", key("t.days"), { count: 3 })).toBe("3 кун қолди");
    expect(catalog.translate("uz-Cyrl", key("t.only"))).toBe("Фақат ўзбекча");
  });

  it("names each language in its own script in every language, Uzbek Cyrillic included", async () => {
    const catalog = await freshCatalog();
    const names = ["O'zbekcha", "Ўзбекча", "Русский", "Тоҷикӣ", "Qaraqalpaqsha", "English"];
    for (const language of LANGUAGES) {
      await catalog.loadLanguage(language);
      expect(LANGUAGES.map((code) => catalog.translate(language, `lang.${code}`)), language).toEqual(names);
    }
  });

  it("is read in Uzbek when the language has the wrong kind of entry for it", async () => {
    const catalog = await freshCatalog();
    catalog.addMessages({
      uz,
      tg: () => Promise.resolve({ "t.days": "{count} рӯз монд", "t.hello": { other: "Салом, {name}" } }),
    });
    await catalog.loadLanguage("tg");
    expect(catalog.translate("tg", key("t.days"), { count: 3 })).toBe("3 kun qoldi");
    expect(catalog.translate("tg", key("t.hello"), { name: "Ali" })).toBe("Salom, Ali");
  });

  it("still fails loudly for a key that no catalog has: that is a mistake in the code, not a missing translation", async () => {
    const catalog = await freshCatalog();
    await catalog.loadLanguage("en");
    for (const language of LANGUAGES) {
      expect(() => catalog.translate(language, key("t.nowhere"))).toThrow('message "t.nowhere" is not loaded');
      expect(() => catalog.translate(language, "money.uzs")).toThrow('needs the parameter "amount"');
    }
  });
});

describe("a language that cannot be fetched", () => {
  it("leaves the reader in Uzbek, says so to the caller, and is asked for again the next time", async () => {
    const catalog = await freshCatalog();
    let attempts = 0;
    catalog.addMessages({
      uz: { "t.hello": "Salom" },
      kaa: () => {
        attempts += 1;
        return attempts === 1 ? Promise.reject(new Error("offline")) : Promise.resolve({ "t.hello": "Sálem" });
      },
    });
    await expect(catalog.loadLanguage("kaa")).rejects.toThrow('the text of "kaa" could not be fetched');
    expect(catalog.hasLanguage("kaa")).toBe(false);
    expect(catalog.translate("kaa", key("t.hello"))).toBe("Salom");
    // What did arrive is kept: the main catalog is in Karakalpak already.
    expect(catalog.translate("kaa", "nav.customers")).toBe("Qarıydarlar");
    await catalog.loadLanguage("kaa");
    expect(attempts).toBe(2);
    expect(catalog.translate("kaa", key("t.hello"))).toBe("Sálem");
  });

  it("never fails the screen that waits for it", async () => {
    const catalog = await freshCatalog();
    catalog.setActiveLanguage("tg");
    catalog.addMessages({ uz: { "t.hello": "Salom" }, tg: () => Promise.reject(new Error("offline")) });
    await expect(catalog.languageSettled()).resolves.toBeUndefined();
    await expect(catalog.withMessages(Promise.resolve("the screen"))).resolves.toBe("the screen");
    expect(catalog.translate("tg", key("t.hello"))).toBe("Salom");
  });
});

describe("a catalog added after the start", () => {
  it("fetches its text for the language in use at once, and tells whoever listens", async () => {
    const catalog = await freshCatalog();
    catalog.setActiveLanguage("en");
    await catalog.loadLanguage("en");
    const told = vi.fn();
    const before = catalog.catalogVersion();
    catalog.subscribe(told);
    let fetched = 0;
    catalog.addMessages({
      uz: { "t.late": "Kech keldi" },
      en: () => {
        fetched += 1;
        return Promise.resolve({ "t.late": "Came late" });
      },
      ru: () => {
        throw new Error("Russian is not the language in use and must not be fetched");
      },
    });
    expect(catalog.translate("en", key("t.late"))).toBe("Kech keldi");
    await catalog.withMessages(Promise.resolve(null));
    expect(fetched).toBe(1);
    expect(catalog.translate("en", key("t.late"))).toBe("Came late");
    expect(told).toHaveBeenCalled();
    expect(catalog.catalogVersion()).toBeGreaterThan(before);
  });

  it("takes text that is handed over with it without fetching anything", async () => {
    const catalog = await freshCatalog();
    catalog.addMessages({ uz: { "t.both": "Ikkalasi" }, ru: { "t.both": "Оба" } });
    expect(catalog.translate("ru", key("t.both"))).toBe("Оба");
    expect(catalog.translate("tg", key("t.both"))).toBe("Ikkalasi");
  });
});

describe("plural forms", () => {
  it.each([
    [0, "0 customers", "0 days overdue"],
    [1, "1 customer", "1 day overdue"],
    [2, "2 customers", "2 days overdue"],
    [11, "11 customers", "11 days overdue"],
    [21, "21 customers", "21 days overdue"],
    [101, "101 customers", "101 days overdue"],
  ])("English has two: %i", async (count, customers, overdue) => {
    await loadLanguage("en");
    expect(translate("en", "customers.count", { count })).toBe(customers);
    expect(translate("en", "overdue.days", { count })).toBe(overdue);
    expect(enPluralCategory(count)).toBe(new Intl.PluralRules("en").select(count));
  });

  it.each([1, 2, 5, 11, 21, 101])("Tajik, Karakalpak and Uzbek Cyrillic have one: %i", async (count) => {
    await Promise.all([loadLanguage("tg"), loadLanguage("kaa"), loadLanguage("uz-Cyrl")]);
    expect(translate("tg", "customers.count", { count })).toBe(`${count} мизоҷ`);
    expect(translate("kaa", "customers.count", { count })).toBe(`${count} qarıydar`);
    expect(translate("uz-Cyrl", "customers.count", { count })).toBe(`${count} та мижоз`);
  });
});

describe("money, dates and counts in each language", () => {
  const DAY = new Date("2026-10-06T07:00:00Z");
  const plain = (text: string) => text.replaceAll(String.fromCharCode(160), " ");

  beforeEach(async () => {
    await Promise.all(LANGUAGES.map((language) => loadLanguage(language)));
  });

  it("writes so'm with the same digits and each language's own word for it", () => {
    expect(LANGUAGES.map((language) => plain(formatMoney(1250000, language)))).toEqual([
      "1 250 000 so'm",
      "1 250 000 сўм",
      "1 250 000 сум",
      "1 250 000 сӯм",
      "1 250 000 swm",
      "1 250 000 soum",
    ]);
  });

  it("writes dollars the same way in every language: the sign after, two decimals, never a word", () => {
    expect(new Set(LANGUAGES.map((language) => formatMoney(125050, language, "USD")))).toEqual(new Set(["1 250.50 $"]));
  });

  it("writes a date with the month's name in the language", () => {
    expect(LANGUAGES.map((language) => formatFullDate(DAY, language))).toEqual([
      "2026-yil 6-oktabr",
      "2026-йил 6-октябрь",
      "6 октября 2026 г.",
      "6 октябри 2026",
      "2026-jıl 6-oktyabr",
      "6 October 2026",
    ]);
  });

  it("counts customers and days late in the language", () => {
    expect(LANGUAGES.map((language) => formatCustomerCount(1, language))).toEqual([
      "1 ta mijoz",
      "1 та мижоз",
      "1 клиент",
      "1 мизоҷ",
      "1 qarıydar",
      "1 customer",
    ]);
    const due = new Date("2026-10-03T07:00:00Z");
    expect(LANGUAGES.map((language) => formatDueStatus(due, DAY, language))).toEqual([
      "3 kun kechikkan",
      "3 кун кечиккан",
      "Просрочено на 3 дня",
      "3 рӯз дер шудааст",
      "3 kún keshikken",
      "3 days overdue",
    ]);
  });
});

describe("the language picker", () => {
  const picker = () => screen.getByRole<HTMLSelectElement>("combobox");
  const heading = () => screen.getByRole("heading", { level: 1 }).textContent;

  function renderApp(language: Language = "uz") {
    return render(<StaffApp entryKey="entry.app" session={{ role: "seller", shopName: "Baraka savdo" }} initialLanguage={language} />);
  }

  beforeEach(() => {
    window.location.hash = "";
    window.localStorage.clear();
    document.documentElement.lang = "";
    document.documentElement.dir = "";
  });

  it("is one labelled control with the six languages, each named in itself and marked as itself", () => {
    renderApp();
    const control = screen.getByRole<HTMLSelectElement>("combobox", { name: "Til" });
    expect(control.tagName).toBe("SELECT");
    expect(control.disabled).toBe(false);
    expect(control.tabIndex).toBeGreaterThanOrEqual(0);
    expect(document.querySelector(`label[for="${control.id}"]`)?.textContent).toBe("Til");
    expect(within(control).getAllByRole("option").map((option) => [option.getAttribute("value"), option.getAttribute("lang"), option.textContent])).toEqual([
      ["uz", "uz", "O'zbekcha"],
      ["uz-Cyrl", "uz-Cyrl", "Ўзбекча"],
      ["ru", "ru", "Русский"],
      ["tg", "tg", "Тоҷикӣ"],
      ["kaa", "kaa", "Qaraqalpaqsha"],
      ["en", "en", "English"],
    ]);
    expect(control.value).toBe("uz");
    // Inside the banner, so that it is found where a header's controls are looked for.
    expect(within(screen.getByRole("banner")).getByRole("combobox")).toBe(control);
  });

  it.each([
    ["uz-Cyrl", "Умумий кўриниш", "Тил"],
    ["tg", "Намуди умумӣ", "Забон"],
    ["kaa", "Ulıwma kórinis", "Til"],
    ["en", "Overview", "Language"],
  ] as const)("changes to %s once its text has arrived, sets lang and dir, and remembers the choice", async (language, title, label) => {
    renderApp();
    expect(document.documentElement.lang).toBe("uz");
    fireEvent.change(picker(), { target: { value: language } });
    await waitFor(() => expect(document.documentElement.lang).toBe(language));
    expect(document.documentElement.dir).toBe("ltr");
    expect(heading()).toBe(title);
    expect(screen.getByRole<HTMLSelectElement>("combobox", { name: label }).value).toBe(language);
    expect(window.localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe(language);
    expect(screen.queryByText("Mijozlar")).toBeNull();
  });

  it("writes every language left to right", () => {
    expect(Object.keys(DIRECTIONS).sort()).toEqual([...LANGUAGES].sort());
    expect(new Set(Object.values(DIRECTIONS))).toEqual(new Set(["ltr"]));
  });

  it("starts in the language it is given and marks the page so", async () => {
    await loadLanguage("en");
    renderApp("en");
    expect(heading()).toBe("Overview");
    expect(document.documentElement.lang).toBe("en");
    expect(picker().value).toBe("en");
    // Starting in a language is not choosing it: nothing is stored until the person picks.
    expect(window.localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBeNull();
  });

  it("ignores a value that is not one of the six", () => {
    renderApp();
    const control = picker();
    act(() => {
      const stray = document.createElement("option");
      stray.value = "kk";
      control.append(stray);
    });
    fireEvent.change(control, { target: { value: "kk" } });
    expect(document.documentElement.lang).toBe("uz");
    expect(window.localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBeNull();
  });
});

describe("a language chosen while it cannot be fetched", () => {
  it("leaves the interface and the stored choice as they were, and works the next time", async () => {
    vi.resetModules();
    const catalog = await import("./catalog");
    const { I18nProvider, useI18n } = await import("./I18nProvider");
    let attempts = 0;
    catalog.addMessages({
      uz: { "t.hello": "Salom" },
      tg: () => {
        attempts += 1;
        return attempts === 1 ? Promise.reject(new Error("offline")) : Promise.resolve({ "t.hello": "Салом" });
      },
    });
    window.localStorage.clear();
    function Probe() {
      const { language, setLanguage, t } = useI18n();
      return (
        <button type="button" onClick={() => setLanguage("tg")}>
          {language}: {t(key("t.hello"))}
        </button>
      );
    }
    render(
      <I18nProvider initialLanguage="uz">
        <Probe />
      </I18nProvider>,
    );
    const button = screen.getByRole("button");
    fireEvent.click(button);
    await waitFor(() => expect(attempts).toBe(1));
    await act(async () => {
      await Promise.resolve();
    });
    expect(button.textContent).toBe("uz: Salom");
    expect(window.localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBeNull();
    fireEvent.click(button);
    await waitFor(() => expect(button.textContent).toBe("tg: Салом"));
    expect(window.localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe("tg");
    expect(document.documentElement.lang).toBe("tg");
  });
});

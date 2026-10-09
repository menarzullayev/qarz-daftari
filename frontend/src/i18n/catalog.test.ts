import { describe, expect, it } from "vitest";

import {
  catalogs,
  compareCatalogs,
  isSameCatalogShape,
  placeholdersOf,
  ruPluralCategory,
  translate,
} from "./catalog";
import { ru } from "./ru";
import { LANGUAGES, type Message, type MessageKey, type RuCatalog } from "./types";
import { uz } from "./uz";

function without<T extends object, K extends keyof T>(source: T, key: K): Omit<T, K> {
  return Object.fromEntries(Object.entries(source).filter(([name]) => name !== key)) as Omit<T, K>;
}

describe("catalog key parity (NFR-007)", () => {
  it("has the same keys, kinds, and placeholders in Uzbek and Russian", () => {
    expect(compareCatalogs(uz, ru)).toEqual({
      missingInSecond: [],
      missingInFirst: [],
      kindMismatch: [],
      placeholderMismatch: [],
    });
    expect(isSameCatalogShape(compareCatalogs(uz, ru))).toBe(true);
  });

  it("reports a key missing from the second catalog", () => {
    const withoutKey = without(ru, "nav.customers");
    const difference = compareCatalogs(uz, withoutKey);
    expect(difference.missingInSecond).toEqual(["nav.customers"]);
    expect(isSameCatalogShape(difference)).toBe(false);
  });

  it("reports an extra key in the second catalog", () => {
    const difference = compareCatalogs(uz, { ...ru, "nav.unknown": "Лишний" });
    expect(difference.missingInFirst).toEqual(["nav.unknown"]);
    expect(isSameCatalogShape(difference)).toBe(false);
  });

  it("reports a plural entry translated as a plain string", () => {
    const difference = compareCatalogs(uz, { ...ru, "customers.count": "{count} клиентов" });
    expect(difference.kindMismatch).toEqual(["customers.count"]);
  });

  it("reports placeholders that differ, including between plural forms", () => {
    const renamed: Record<string, Message> = { ...ru, "money.uzs": "{sum} сум" };
    expect(compareCatalogs(uz, renamed).placeholderMismatch).toEqual(["money.uzs"]);

    const dropped: Record<string, Message> = {
      ...ru,
      "customers.count": { one: "{count} клиент", few: "{count} клиента", many: "много клиентов" },
    };
    expect(compareCatalogs(uz, dropped).placeholderMismatch).toEqual(["customers.count"]);
  });

  it("fails the type check for a missing or an extra key", () => {
    const withoutKey = without(ru, "nav.customers");
    // @ts-expect-error a catalog without "nav.customers" is not a complete Russian catalog
    const missing: RuCatalog = withoutKey;
    // @ts-expect-error a key that Uzbek does not have is rejected
    const extra: RuCatalog = { ...ru, "nav.unknown": "Лишний" };
    // @ts-expect-error a plural entry cannot be a plain string
    const flattened: RuCatalog = { ...ru, "customers.count": "{count} клиентов" };
    // @ts-expect-error an unknown key cannot be requested
    const unknownKey: MessageKey = "nav.unknown";
    expect([missing, extra, flattened, unknownKey]).toHaveLength(4);
  });
});

describe("Uzbek copy", () => {
  it("uses only the plain apostrophe, never a look-alike", () => {
    const lookAlikes = /[`‘’ʻʼ´]/;
    const offenders = Object.entries(uz)
      .filter(([, message]) => lookAlikes.test(JSON.stringify(message)))
      .map(([key]) => key);
    expect(offenders).toEqual([]);
    expect(lookAlikes.test("so’m")).toBe(true);
  });

  it("has no Cyrillic letters except the names of the languages, each written in its own script", () => {
    const offenders = Object.entries(uz)
      .filter(([key, message]) => !key.startsWith("lang.") && /\p{Script=Cyrillic}/u.test(JSON.stringify(message)))
      .map(([key]) => key);
    expect(offenders).toEqual([]);
    expect(Object.keys(uz).filter((key) => key.startsWith("lang.")).sort()).toEqual(LANGUAGES.map((code) => `lang.${code}`).sort());
  });
});

describe("translate", () => {
  it("returns the message in the requested language", () => {
    expect(translate("uz", "nav.customers")).toBe("Mijozlar");
    expect(translate("ru", "nav.customers")).toBe("Клиенты");
  });

  it("fills {name} placeholders", () => {
    expect(translate("uz", "money.uzs", { amount: "45 000" })).toBe("45 000 so'm");
    expect(translate("ru", "date.dayMonthYear", { day: 6, month: "октября", year: 2026 })).toBe(
      "6 октября 2026 г.",
    );
  });

  it("throws when a parameter is missing instead of printing the placeholder", () => {
    expect(() => translate("uz", "money.uzs")).toThrow('message "money.uzs" needs the parameter "amount"');
    expect(() => translate("ru", "date.dayMonth", { day: 6 })).toThrow('needs the parameter "month"');
  });

  it("throws when a plural message gets no numeric count", () => {
    expect(() => translate("ru", "customers.count")).toThrow('needs a numeric "count"');
    expect(() => translate("uz", "customers.count", { count: "5" })).toThrow('needs a numeric "count"');
  });

  it("resolves every key in both languages when given its parameters", () => {
    for (const lang of ["uz", "ru"] as const) {
      for (const key of Object.keys(catalogs[lang]) as MessageKey[]) {
        const message = catalogs[lang][key];
        const template = typeof message === "string" ? message : (Object.values(message)[0] ?? "");
        const params = Object.fromEntries(placeholdersOf(template).map((name) => [name, 3]));
        const text = translate(lang, key, typeof message === "string" ? params : { ...params, count: 3 });
        expect(text).not.toMatch(/[{}]/);
        expect(text.length).toBeGreaterThan(0);
      }
    }
  });
});

describe("Russian plurals", () => {
  it.each([
    [1, "one", "1 клиент", "Просрочено на 1 день"],
    [2, "few", "2 клиента", "Просрочено на 2 дня"],
    [5, "many", "5 клиентов", "Просрочено на 5 дней"],
    [11, "many", "11 клиентов", "Просрочено на 11 дней"],
    [21, "one", "21 клиент", "Просрочено на 21 день"],
    [22, "few", "22 клиента", "Просрочено на 22 дня"],
    [25, "many", "25 клиентов", "Просрочено на 25 дней"],
    [101, "one", "101 клиент", "Просрочено на 101 день"],
    [111, "many", "111 клиентов", "Просрочено на 111 дней"],
  ] as const)("%i is %s", (count, category, customers, overdue) => {
    expect(ruPluralCategory(count)).toBe(category);
    expect(translate("ru", "customers.count", { count })).toBe(customers);
    expect(translate("ru", "overdue.days", { count })).toBe(overdue);
  });

  it.each([0, 4, 12, 13, 14, 20, 24, 100, 104, 112, 1001, 1011])("agrees with CLDR for %i", (count) => {
    expect(ruPluralCategory(count)).toBe(new Intl.PluralRules("ru").select(count));
  });

  it("does not treat 11, 12, or 111 like 1 and 2", () => {
    expect(ruPluralCategory(11)).not.toBe("one");
    expect(ruPluralCategory(12)).not.toBe("few");
    expect(translate("ru", "customers.count", { count: 111 })).not.toBe("111 клиент");
  });

  it("rejects a count that is not a finite number", () => {
    expect(() => ruPluralCategory(Number.NaN)).toThrow(RangeError);
  });
});

describe("Uzbek plurals", () => {
  it.each([1, 2, 5, 11, 21, 101])("keeps one form for %i", (count) => {
    expect(translate("uz", "customers.count", { count })).toBe(`${count} ta mijoz`);
    expect(translate("uz", "overdue.days", { count })).toBe(`${count} kun kechikkan`);
  });
});

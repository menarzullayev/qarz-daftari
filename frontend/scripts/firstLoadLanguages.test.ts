import { describe, expect, it } from "vitest";

import { CYRILLIC_RULES, found, judge, markersOf } from "./firstLoadLanguages.ts";

const uz = {
  "a.one": "Mijozning qarzi to'liq to'langan",
  "a.two": "Bugun to'lanishi kerak bo'lgan summa",
  "a.three": "Do'kon egasiga murojaat qiling va kuting",
  "a.name": "Telegram orqali kirish",
  "a.count": { other: "{count} ta mijoz ro'yxatda turibdi" },
};
const ru = {
  "a.one": "Долг клиента оплачен полностью, спасибо",
  "a.two": "Сумма, которую нужно оплатить сегодня",
  "a.three": "Обратитесь к владельцу магазина и ждите",
  "a.name": "Telegram orqali kirish",
  "a.count": { one: "{count} клиент", few: "{count} клиента", many: "{count} клиентов" },
};

describe("the sentences a language is recognised by", () => {
  it("are long, plain, and in no other catalog", () => {
    expect(markersOf(ru, [uz])).toEqual([
      "Долг клиента оплачен полностью, спасибо",
      "Сумма, которую нужно оплатить сегодня",
      "Обратитесь к владельцу магазина и ждите",
    ]);
    // Short text, text with a place to fill in, text with a quote, and text another catalog has too.
    expect(markersOf({ short: "Клиенты", place: "{count} клиентов в этом списке сейчас", same: "Telegram orqali — nasiya hisobi uchun" }, [
      { same: "Telegram orqali — nasiya hisobi uchun" },
    ])).toEqual([]);
    expect(markersOf(uz, [ru])).toEqual([]);
    expect(markersOf({ ...uz, plain: "Hisobot tayyorlanmoqda, biroz kuting" }, [ru])).toEqual(["Hisobot tayyorlanmoqda, biroz kuting"]);
  });

  it("are found in a script as they are, or written with escapes", () => {
    const markers = markersOf(ru, [uz]);
    expect(found(`const a={x:"${markers[0]}",y:\`${markers[1]}\`}`, markers)).toBe(2);
    const escaped = [...(markers[2] ?? "")].map((char) => (char.charCodeAt(0) > 126 ? `\\u${char.charCodeAt(0).toString(16).padStart(4, "0").toUpperCase()}` : char)).join("");
    expect(found(`const a="${escaped}"`, markers)).toBe(1);
    expect(found('const a="Mijozlar"', markers)).toBe(0);
  });
});

describe("the verdict on a build", () => {
  const russian = markersOf(ru, [uz]);
  const uzbek = ["Hisobot tayyorlanmoqda, biroz kuting", "Sozlamalar saqlandi, davom eting", "Bugun hech kim qarz olmadi hali"];
  const languages = { ru: russian, "uz-Cyrl": CYRILLIC_RULES };
  const firstLoad = `const uz=["${uzbek.join('","')}"]`;
  const russianFile = `export const ru=["${russian.join('","')}"]`;
  const rulesFile = `const words=["${CYRILLIC_RULES.join('","')}"]`;

  it("passes when the first load holds Uzbek and every other language is a file of its own", () => {
    expect(judge([firstLoad], [russianFile, rulesFile], languages, uzbek)).toEqual({ inFirstLoad: [], nowhere: [], uzbekThere: true });
  });

  it("names a language whose text is in the first load", () => {
    expect(judge([firstLoad, russianFile], [rulesFile], languages, uzbek).inFirstLoad).toEqual(["ru"]);
    expect(judge([firstLoad + russianFile + rulesFile], [], languages, uzbek).inFirstLoad).toEqual(["ru", "uz-Cyrl"]);
  });

  it("does not take one shared sentence for a whole catalog", () => {
    const six = [...russian, "Отчёт готовится, подождите немного", "Настройки сохранены, продолжайте", "Сегодня никто не брал в долг"];
    const file = `export const ru=["${six.join('","')}"]`;
    expect(judge([`${firstLoad};const one="${six[0]}"`], [file, rulesFile], { ...languages, ru: six }, uzbek).inFirstLoad).toEqual([]);
    expect(judge([`${firstLoad};const two=["${six[0]}","${six[1]}"]`], [file, rulesFile], { ...languages, ru: six }, uzbek).inFirstLoad).toEqual(["ru"]);
  });

  it("names a language whose text is nowhere, since then it could not have been caught", () => {
    expect(judge([firstLoad], [rulesFile], languages, uzbek).nowhere).toEqual(["ru"]);
    expect(judge([firstLoad], [russianFile, rulesFile], { ...languages, tg: [] }, uzbek).nowhere).toEqual(["tg"]);
  });

  it("says when the Uzbek text is not in the first load", () => {
    expect(judge(["const nothing=1"], [russianFile, rulesFile], languages, uzbek).uzbekThere).toBe(false);
  });
});

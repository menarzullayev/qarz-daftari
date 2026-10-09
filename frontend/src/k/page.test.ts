// @vitest-environment jsdom
import { readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { cyrillicOf, fileText } from "../../scripts/kCyrillic.ts";
import { ACCOUNT_PATH, type Fetch, loadAccount, readAccount, readToken, TOKEN_HEADER } from "./account";
import {
  CATALOGS,
  dayOfDate,
  dayOfInstant,
  dollars,
  LANGUAGES,
  type MessageKey,
  money,
  normalizeLanguage,
  say,
  wording,
} from "./messages";
import { knownLanguage, LANGUAGE_STORAGE_KEY, startPage } from "./page";

const TOKEN = "Zm9vYmFyLXNoYXJlLXRva2VuLTAxMjM0NTY3ODktYWJ";
const SOURCE = resolve(import.meta.dirname);

const ACCOUNT = {
  shop_name: "Baraka do'koni",
  shop_phone: "+998901234567",
  first_name: "Ali",
  lang: "uz",
  balance: 30000,
  overdue: { amount: 10000, due_today: 5000 },
  expires_at: "2027-01-04T07:00:00+00:00",
  entries: [
    { kind: "payment", amount: 20000, created_at: "2026-10-05T04:30:00+00:00", promised_date: null, reversed: false, lines: [] },
    {
      kind: "credit",
      amount: 50000,
      created_at: "2026-10-01T19:30:00+00:00",
      promised_date: "2026-10-08",
      reversed: false,
      lines: [{ name: "Un", qty: "2.5", unit: "kg", unit_price: 20000, line_total: 50000 }],
    },
    { kind: "credit", amount: 7000, created_at: "2026-09-30T05:00:00+00:00", promised_date: "2026-10-07", reversed: true, lines: [] },
    { kind: "reversal", amount: 7000, created_at: "2026-09-30T06:00:00+00:00", promised_date: null, reversed: false, lines: [] },
  ],
  entries_total: 4,
};

type Sent = { input: string; init: RequestInit };

function server(reply: () => Response | Promise<Response>) {
  const sent: Sent[] = [];
  const fetch: Fetch = async (input, init) => {
    sent.push({ input, init });
    return reply();
  };
  return { fetch, sent };
}

const json = (body: unknown, status = 200) => () =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const offline = () => Promise.reject(new TypeError("Failed to fetch"));

function memory(initial: Record<string, string> = {}) {
  const kept = new Map(Object.entries(initial));
  return {
    kept,
    getItem: (key: string) => kept.get(key) ?? null,
    setItem: (key: string, value: string) => void kept.set(key, value),
  };
}

async function open(
  fetch: Fetch,
  options: { hash?: string; storage?: ReturnType<typeof memory> | null; browserLanguages?: string[] } = {},
) {
  const root = document.createElement("div");
  document.body.append(root);
  await startPage({
    root,
    hash: options.hash ?? `#${TOKEN}`,
    fetch,
    storage: options.storage === undefined ? memory() : options.storage,
    browserLanguages: options.browserLanguages ?? [],
  });
  return root;
}

const button = (root: HTMLElement, name: string) =>
  [...root.querySelectorAll("button")].find((candidate) => candidate.textContent === name);

/** Chooses a language in the page's picker, as a reader does. The page is drawn again after it. */
function choose(root: HTMLElement, code: string): void {
  const picker = root.querySelector("select");
  if (picker === null) {
    throw new Error("the page has no language picker");
  }
  picker.value = code;
  picker.dispatchEvent(new Event("change"));
}

afterEach(() => {
  document.body.replaceChildren();
  document.documentElement.lang = "";
});

describe("the secret", () => {
  it("is read from the fragment and from nowhere else", () => {
    expect(readToken(`#${TOKEN}`)).toBe(TOKEN);
    expect(readToken(TOKEN)).toBe(TOKEN);
  });

  it.each(["", "#", "#short", `#${TOKEN}x`, `#${TOKEN.slice(1)}`, `#${TOKEN.slice(0, 42)}/`, `#${TOKEN.slice(0, 42)}=`, `#/k/${TOKEN}`, `#?t=${TOKEN}`])(
    "is not read from %j",
    (hash) => {
      expect(readToken(hash)).toBeNull();
    },
  );

  it("is sent once, in a header, without cookies, and never in the address", async () => {
    const api = server(json(ACCOUNT));
    await open(api.fetch);
    expect(api.sent).toHaveLength(1);
    const { input, init } = api.sent[0] as Sent;
    expect(input).toBe(ACCOUNT_PATH);
    expect(input).not.toContain(TOKEN);
    expect(init.method).toBe("GET");
    expect(init.headers).toEqual({ Accept: "application/json", [TOKEN_HEADER]: TOKEN });
    expect(init.credentials).toBe("omit");
    expect(init.cache).toBe("no-store");
    expect(init.referrerPolicy).toBe("no-referrer");
    expect(init.body).toBeUndefined();
  });

  it("asks the server nothing when the address holds no secret", async () => {
    for (const hash of ["", "#", "#not-a-secret", `#${TOKEN}extra`]) {
      const api = server(json(ACCOUNT));
      const root = await open(api.fetch, { hash });
      expect(api.sent).toHaveLength(0);
      expect(root.querySelector("[role=alert]")?.textContent).toContain("Havola ishlamayapti");
      root.remove();
    }
  });

  it("is written to no storage and shown nowhere on the page", async () => {
    const storage = memory();
    const root = await open(server(json(ACCOUNT)).fetch, { storage });
    choose(root, "ru");
    expect([...storage.kept.values()].join(" ")).not.toContain(TOKEN);
    expect(root.innerHTML).not.toContain(TOKEN);
    expect(JSON.stringify({ ...window.localStorage, ...window.sessionStorage })).not.toContain(TOKEN);
  });
});

describe("what the page shows", () => {
  it("shows the shop, the first name, the balance and the entries with their dates", async () => {
    const root = await open(server(json(ACCOUNT)).fetch);
    const text = root.textContent ?? "";
    expect(root.querySelector(".shop__name")?.textContent).toBe("Baraka do'koni");
    expect(root.querySelector(".shop__phone a")?.getAttribute("href")).toBe("tel:+998901234567");
    expect(root.querySelector("h1")?.textContent).toBe("Ali, bu sizning hisobingiz");
    expect(root.querySelector(".summary__amount")?.textContent).toBe("30 000 so'm");
    expect(text).toContain("Shundan muddati o'tgani: 10 000 so'm");
    expect(text).toContain("Bugun to'lanishi kerak: 5 000 so'm");

    const entries = [...root.querySelectorAll(".entry")].map((entry) => entry.textContent);
    expect(entries).toHaveLength(4);
    expect(entries[0]).toBe("To'lov20 000 so'm2026-yil 5-oktabr");
    // 19:30 UTC on the 1st is half past midnight on the 2nd in Tashkent.
    expect(entries[1]).toBe(
      "Nasiya50 000 so'm2026-yil 2-oktabrTo'lash muddati: 2026-yil 8-oktabrUn — 2.5 kg × 20 000 so'm = 50 000 so'm",
    );
    expect(entries[2]).toContain("Bekor qilingan");
    expect(root.querySelectorAll(".entry--reversed")).toHaveLength(1);
    expect(entries[3]).toContain("Bekor qilish yozuvi");
    expect(text).toContain("Havola 2027-yil 4-yanvar gacha amal qiladi.");
    expect(text).toContain("Bu sahifa faqat o'qish uchun.");
    expect(text).not.toContain("ta yozuv ko'rsatilgan");
    expect(document.title).toBe("Mening qarzim");
  });

  it("has nothing to change but the language: the page can only be read", async () => {
    const root = await open(server(json(ACCOUNT)).fetch);
    expect(root.querySelectorAll("button")).toHaveLength(0);
    expect([...root.querySelectorAll("select option")].map((node) => node.textContent)).toEqual([
      "O'zbekcha",
      "Ўзбекча",
      "Русский",
      "Тоҷикӣ",
      "Qaraqalpaqsha",
      "English",
    ]);
    expect(root.querySelectorAll("select")).toHaveLength(1);
    expect(root.querySelectorAll("input, textarea, form")).toHaveLength(0);
    expect([...root.querySelectorAll("a")].map((node) => node.getAttribute("href"))).toEqual(["tel:+998901234567"]);
  });

  it.each([
    [0, "Qarzingiz yo'q", null],
    [-15000, "15 000 so'm", "Siz haqdorsiz (oldindan to'lov)"],
  ])("says what a balance of %d means", async (balance, amount, label) => {
    const root = await open(server(json({ ...ACCOUNT, balance, overdue: { amount: 0, due_today: 0 } })).fetch);
    expect(root.querySelector(".summary__amount")?.textContent).toBe(amount);
    expect(root.querySelector(".summary__label")?.textContent ?? null).toBe(label);
    expect(root.querySelector(".summary__overdue")).toBeNull();
    expect(root.querySelector(".summary__due")).toBeNull();
    // An advance is said by its label and the amount itself, never by a minus sign.
    expect(root.querySelector(".summary")?.textContent).not.toMatch(/[-−]\s?15/);
  });

  it("leaves out the phone when the shop gave none, and the name when there is none", async () => {
    const root = await open(server(json({ ...ACCOUNT, shop_phone: null, first_name: "" })).fetch);
    expect(root.querySelector(".shop__phone")).toBeNull();
    expect(root.querySelector("a")).toBeNull();
    expect(root.querySelector("h1")?.textContent).toBe("Bu sizning hisobingiz");
  });

  it("says so when there are more entries than it shows, and when there are none", async () => {
    const more = await open(server(json({ ...ACCOUNT, entries_total: 250 })).fetch);
    expect(more.textContent).toContain("Oxirgi 4 ta yozuv ko'rsatilgan, jami 250 ta.");
    const none = await open(server(json({ ...ACCOUNT, entries: [], entries_total: 0 })).fetch);
    expect(none.textContent).toContain("Hozircha yozuv yo'q.");
  });

  it("never treats what the server sends as markup", async () => {
    const hostile = '<img src=x onerror="window.pwned=1"><script>window.pwned=1</script>';
    const root = await open(
      server(
        json({
          ...ACCOUNT,
          shop_name: hostile,
          shop_phone: "javascript:alert(1)",
          first_name: hostile,
          entries: [{ ...ACCOUNT.entries[1], kind: hostile, lines: [{ name: hostile, qty: hostile, unit: hostile, unit_price: 1, line_total: 1 }] }],
          entries_total: 1,
        }),
      ).fetch,
    );
    expect(root.querySelectorAll("img, script, iframe")).toHaveLength(0);
    expect(root.querySelector(".shop__name")?.textContent).toBe(hostile);
    // What is not a phone number is shown as text and is not a link to anything.
    expect(root.querySelector(".shop__phone a")?.hasAttribute("href")).toBe(false);
    expect((window as unknown as { pwned?: number }).pwned).toBeUndefined();
    // A kind the page does not know is "an entry", never the server's own word.
    expect(root.querySelector(".entry__kind")?.textContent).toBe("Yozuv");
  });
});

describe("when there is no account to show", () => {
  it("says the link does not work for a 404, and offers nothing to retry", async () => {
    const api = server(json({ error: { code: "NOT_FOUND", message: "Topilmadi.", fields: {} } }, 404));
    const root = await open(api.fetch);
    const alert = root.querySelector("[role=alert]");
    expect(alert?.querySelector("h1")?.textContent).toBe("Havola ishlamayapti");
    expect(alert?.textContent).toContain("Do'kondan yangi havola so'rang.");
    expect(button(root, "Qayta urinish")).toBeUndefined();
    expect(root.querySelector(".summary")).toBeNull();
  });

  it("says to wait when the address is limited, and asks again on request", async () => {
    let answers = 0;
    const api = server(() => (answers++ === 0 ? json({}, 429)() : json(ACCOUNT)()));
    const root = await open(api.fetch);
    expect(root.querySelector("[role=alert] h1")?.textContent).toBe("Juda ko'p urinish");
    button(root, "Qayta urinish")?.click();
    await expect.poll(() => root.querySelector(".summary__amount")?.textContent).toBe("30 000 so'm");
    expect(api.sent).toHaveLength(2);
  });

  it.each([
    ["the request does not arrive", offline],
    ["the server fails", json({}, 503)],
    ["the answer is not JSON", () => new Response("<html>", { status: 200 })],
    ["the balance is not a whole number", json({ ...ACCOUNT, balance: "30000" })],
    ["the balance is a fraction", json({ ...ACCOUNT, balance: 30000.5 })],
    ["an entry is missing its amount", json({ ...ACCOUNT, entries: [{ kind: "credit" }] })],
    ["the answer is a list", json([ACCOUNT])],
  ])("shows no account and offers to try again when %s", async (_what, reply) => {
    const root = await open(server(reply).fetch);
    expect(root.querySelector("[role=alert] h1")?.textContent).toBe("Ulanib bo'lmadi");
    expect(button(root, "Qayta urinish")).toBeDefined();
    expect(root.querySelector(".summary")).toBeNull();
    expect(root.textContent).not.toContain("so'm");
  });

  it("never throws: every failure is an outcome", async () => {
    expect(await loadAccount(offline, TOKEN)).toEqual({ status: "offline" });
    expect(await loadAccount(server(json({}, 404)).fetch, TOKEN)).toEqual({ status: "gone" });
    expect(await loadAccount(server(json({}, 429)).fetch, TOKEN)).toEqual({ status: "limited" });
    expect(() => readAccount({ ...ACCOUNT, entries_total: null })).toThrow();
  });
});

describe("the language", () => {
  it.each([
    ["uz-Cyrl", "Ali, бу сизнинг ҳисобингиз", "30 000 сўм", "Менинг қарзим"],
    ["en", "Ali, this is your account", "30 000 soum", "My debt"],
  ])("is drawn in %s from the first word to the last", async (lang, greeting, amount, title) => {
    const root = await open(server(json({ ...ACCOUNT, lang })).fetch);
    expect(root.querySelector("h1")?.textContent).toBe(greeting);
    expect(root.querySelector(".summary__amount")?.textContent?.replaceAll(String.fromCharCode(160), " ")).toBe(amount);
    expect(document.documentElement.lang).toBe(lang);
    expect(document.documentElement.dir).toBe("ltr");
    expect(document.title).toBe(title);
    expect(root.querySelector("select")?.value).toBe(lang);
  });

  it.each(["tg", "kaa"])("is drawn in %s when the shop keeps that language for the customer", async (lang) => {
    const root = await open(server(json({ ...ACCOUNT, lang })).fetch);
    expect(document.documentElement.lang).toBe(lang);
    expect(root.querySelector("select")?.value).toBe(lang);
    expect(root.querySelector("h1")?.textContent).toContain("Ali");
    expect(root.querySelector("h1")?.textContent).not.toBe("Ali, bu sizning hisobingiz");
  });

  it("is Uzbek for a language the page does not have", async () => {
    const root = await open(server(json({ ...ACCOUNT, lang: "kk" })).fetch);
    expect(root.querySelector("h1")?.textContent).toBe("Ali, bu sizning hisobingiz");
    expect(document.documentElement.lang).toBe("uz");
  });

  it("names the picker for those who do not see it, and each language in its own language", async () => {
    const root = await open(server(json(ACCOUNT)).fetch);
    const picker = root.querySelector("select");
    const label = root.querySelector("label");
    expect(label?.textContent).toBe("Til");
    expect(label?.htmlFor).toBe(picker?.id);
    expect(picker?.id).not.toBe("");
    expect([...root.querySelectorAll("option")].map((option) => [option.value, option.lang])).toEqual(
      LANGUAGES.map((code) => [code, code]),
    );
    choose(root, "en");
    expect(root.querySelector("label")?.textContent).toBe("Language");
  });

  it("keeps the keyboard on the picker after the page is drawn again in the language chosen", async () => {
    const root = await open(server(json(ACCOUNT)).fetch);
    document.body.append(root);
    const before = root.querySelector("select");
    before?.focus();
    choose(root, "tg");
    const after = root.querySelector("select");
    expect(after).not.toBe(before);
    expect(document.activeElement).toBe(after);
    expect(after?.value).toBe("tg");
  });

  it("is the one the shop keeps for the customer when the reader has chosen none", async () => {
    const root = await open(server(json({ ...ACCOUNT, lang: "ru" })).fetch);
    expect(root.querySelector("h1")?.textContent).toBe("Ali, это ваш счёт");
    expect(root.querySelector(".summary__amount")?.textContent).toBe("30 000 сум");
    expect(root.textContent).toContain("Ссылка действует до 4 января 2027 г.");
    expect(document.documentElement.lang).toBe("ru");
    expect(document.title).toBe("Мой долг");
  });

  it("is the reader's own choice before that, and the browser's before the shop's", async () => {
    const stored = await open(server(json({ ...ACCOUNT, lang: "ru" })).fetch, {
      storage: memory({ [LANGUAGE_STORAGE_KEY]: "uz" }),
      browserLanguages: ["ru-RU"],
    });
    expect(stored.querySelector("h1")?.textContent).toBe("Ali, bu sizning hisobingiz");
    const browser = await open(server(json(ACCOUNT)).fetch, { browserLanguages: ["de-DE", "ru-RU"] });
    expect(browser.querySelector("h1")?.textContent).toBe("Ali, это ваш счёт");
    expect(knownLanguage(null, ["de-DE", "fr"])).toBeNull();
    expect(knownLanguage(null, ["de", "uz-Cyrl-UZ", "ru"])).toBe("uz-Cyrl");
    expect(knownLanguage(null, ["tg-TJ"])).toBe("tg");
    expect(knownLanguage(memory({ [LANGUAGE_STORAGE_KEY]: "kaa" }), ["en-US"])).toBe("kaa");
  });

  it("changes on request without asking the server again, and is remembered", async () => {
    const storage = memory();
    const api = server(json(ACCOUNT));
    const root = await open(api.fetch, { storage });
    choose(root, "ru");
    expect(root.querySelector("h1")?.textContent).toBe("Ali, это ваш счёт");
    expect(root.querySelector("select")?.value).toBe("ru");
    expect(root.querySelector("option:checked")?.textContent).toBe("Русский");
    expect(storage.kept.get(LANGUAGE_STORAGE_KEY)).toBe("ru");
    choose(root, "uz");
    expect(root.querySelector("h1")?.textContent).toBe("Ali, bu sizning hisobingiz");
    expect(api.sent).toHaveLength(1);
  });

  it("still changes when the browser refuses storage", async () => {
    const refusing = {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
    };
    const root = document.createElement("div");
    await startPage({ root, hash: `#${TOKEN}`, fetch: server(json(ACCOUNT)).fetch, storage: refusing, browserLanguages: [] });
    choose(root, "ru");
    expect(root.querySelector("h1")?.textContent).toBe("Ali, это ваш счёт");
  });

  it("says a refusal in the reader's language too", async () => {
    const root = await open(server(json({}, 404)).fetch, { browserLanguages: ["ru"] });
    expect(root.querySelector("[role=alert] h1")?.textContent).toBe("Ссылка не работает");
  });
});

describe("the page's text", () => {
  const places = (template: string) => [...template.matchAll(/\{(\w+)\}/g)].map((match) => match[1]).sort();
  const KEYS = Object.keys(CATALOGS.uz) as MessageKey[];

  it("has every message in Uzbek and Russian, with the same places to fill in", () => {
    expect(Object.keys(CATALOGS.ru).sort()).toEqual(Object.keys(CATALOGS.uz).sort());
    for (const key of KEYS) {
      expect(CATALOGS.ru[key]?.trim(), key).toBeTruthy();
      expect(places(CATALOGS.ru[key] ?? ""), key).toEqual(places(CATALOGS.uz[key] ?? ""));
    }
    expect(LANGUAGES).toEqual(["uz", "uz-Cyrl", "ru", "tg", "kaa", "en"]);
  });

  it.each(["tg", "kaa", "en"] as const)("holds what %s has to Uzbek: no other key, the same places, no empty text", (language) => {
    for (const [key, text] of Object.entries(CATALOGS[language])) {
      expect(KEYS, key).toContain(key);
      expect(text.trim(), key).not.toBe("");
      expect(places(text), key).toEqual(places(CATALOGS.uz[key as MessageKey] ?? ""));
    }
  });

  it.each(LANGUAGES)("says every message in %s, never nothing and never the key", (language) => {
    for (const key of KEYS) {
      const text = wording(language, key);
      expect(text.trim(), key).not.toBe("");
      expect(text, key).not.toBe(key);
      expect(places(text), key).toEqual(places(CATALOGS.uz[key] ?? ""));
    }
  });

  it("reads Uzbek for a message that a language does not have yet", () => {
    const tajik = CATALOGS.tg as Partial<Record<MessageKey, string>>;
    const kept = tajik["balance.none"];
    expect(kept).not.toBe(CATALOGS.uz["balance.none"]);
    delete tajik["balance.none"];
    try {
      expect(say("tg", "balance.none")).toBe("Qarzingiz yo'q");
      expect(say("tg", "balance.owed")).toBe(CATALOGS.tg["balance.owed"]);
    } finally {
      tajik["balance.none"] = kept as string;
    }
    expect(say("tg", "balance.none")).toBe(kept);
  });

  it("carries Uzbek Cyrillic exactly as the rules write it from today's Uzbek text", () => {
    // The file is written by `npm run i18n:k`; this fails when a message in it is no longer what the
    // rules give, or was edited by hand.
    for (const [key, text] of Object.entries(CATALOGS["uz-Cyrl"])) {
      expect(text, key).toBe(cyrillicOf(CATALOGS.uz as Record<string, string>)[key]);
    }
    expect(readFileSync(resolve(SOURCE, "uzCyrl.ts"), "utf8")).toBe(fileText(CATALOGS.uz as Record<string, string>));
    // Would a stale message be noticed? A changed Uzbek text gives another file.
    expect(fileText({ ...CATALOGS.uz, "page.title": "Mening hisobim" })).not.toBe(fileText(CATALOGS.uz as Record<string, string>));
    expect(cyrillicOf({ "page.title": "Mening hisobim", "lang.en": "English", "lang.choose": "Til" })).toEqual({
      "page.title": "Менинг ҳисобим",
      "lang.en": "English",
      "lang.choose": "Тил",
    });
  });

  it("writes Uzbek Cyrillic from the Uzbek text, and names each language in its own script", () => {
    expect(say("uz-Cyrl", "page.title")).toBe("Менинг қарзим");
    expect(say("uz-Cyrl", "greeting", { name: "Ali" })).toBe("Ali, бу сизнинг ҳисобингиз");
    expect(say("uz-Cyrl", "month.10")).toBe("октябрь");
    expect(LANGUAGES.map((code) => say("uz-Cyrl", `lang.${code}`))).toEqual(LANGUAGES.map((code) => say("uz", `lang.${code}`)));
    expect(say("uz-Cyrl", "lang.choose")).toBe("Тил");
    for (const language of LANGUAGES) {
      expect(LANGUAGES.map((code) => say(language, `lang.${code}`))).toEqual([
        "O'zbekcha",
        "Ўзбекча",
        "Русский",
        "Тоҷикӣ",
        "Qaraqalpaqsha",
        "English",
      ]);
    }
  });

  it("writes money and dates as each language does, with the same digits everywhere", () => {
    expect(LANGUAGES.map((language) => money(language, 1250000).replaceAll(String.fromCharCode(160), " "))).toEqual([
      "1 250 000 so'm",
      "1 250 000 сўм",
      "1 250 000 сум",
      "1 250 000 сӯм",
      "1 250 000 swm",
      "1 250 000 soum",
    ]);
    expect(LANGUAGES.map((language) => dayOfDate(language, "2026-10-06"))).toEqual([
      "2026-yil 6-oktabr",
      "2026-йил 6-октябрь",
      "6 октября 2026 г.",
      say("tg", "date", { year: 2026, day: 6, month: say("tg", "month.10") }),
      say("kaa", "date", { year: 2026, day: 6, month: say("kaa", "month.10") }),
      "6 October 2026",
    ]);
    for (const language of ["tg", "kaa"] as const) {
      expect(dayOfDate(language, "2026-10-06")).toMatch(/2026/);
      expect(dayOfDate(language, "2026-10-06")).not.toMatch(/oktabr|октября|October|[{}]/);
    }
  });

  it("fails loudly when a place is left empty", () => {
    expect(() => say("uz", "greeting")).toThrow('message "greeting" needs the parameter "name"');
  });

  it("writes money, days and dates the way the rest of the service does", () => {
    expect(money("uz", 1250000)).toBe("1 250 000 so'm");
    expect(money("ru", 500)).toBe("500 сум");
    expect(() => money("uz", 1.5)).toThrow(RangeError);
    expect(dayOfInstant("uz", "2026-12-31T19:00:00+00:00")).toBe("2027-yil 1-yanvar");
    expect(dayOfInstant("ru", "2026-12-31T18:59:59+00:00")).toBe("31 декабря 2026 г.");
    expect(dayOfInstant("uz", "yesterday")).toBe("yesterday");
    expect(dayOfDate("ru", "2026-10-08")).toBe("8 октября 2026 г.");
    expect(dayOfDate("uz", "2026-13-08")).toBe("2026-13-08");
    expect([normalizeLanguage("ru-RU"), normalizeLanguage("UZ"), normalizeLanguage("de"), normalizeLanguage(null)]).toEqual([
      "ru",
      "uz",
      null,
      null,
    ]);
  });
});

describe("US dollars beside so'm", () => {
  const plain = (text: string | null | undefined) => (text ?? "").replace(/\u00a0/g, " ");
  const DOLLAR_ENTRY = { kind: "credit", amount: 125050, created_at: "2026-10-06T04:30:00+00:00", promised_date: "2026-10-20", reversed: false, lines: [], currency: "USD" };
  const IN_DOLLARS = {
    ...ACCOUNT,
    usd: { balance: 125050, overdue: { amount: 5000, due_today: 1250 } },
    entries: [DOLLAR_ENTRY, ...ACCOUNT.entries],
    entries_total: 5,
  };
  const amounts = (root: HTMLElement) => [...root.querySelectorAll(".summary__amount")].map((amount) => plain(amount.textContent));
  const everyText = (root: HTMLElement) => [...root.querySelectorAll("*")].map((node) => plain(node.textContent));

  it("writes cents as dollars with two decimals, thousands apart and the sign after, by whole division", () => {
    expect([0, 1, 10, 99, 100, 1250, 1999, 125050, 100000000].map((cents) => plain(dollars(cents)))).toEqual([
      "0.00 $",
      "0.01 $",
      "0.10 $",
      "0.99 $",
      "1.00 $",
      "12.50 $",
      "19.99 $",
      "1 250.50 $",
      "1 000 000.00 $",
    ]);
    expect(dollars(125050)).toBe("1\u00a0250.50\u00a0$");
    expect(plain(dollars(-125050))).toBe("-1 250.50 $");
    for (const cents of [12.5, Number.NaN, Number.POSITIVE_INFINITY, 2 ** 53]) {
      expect(() => dollars(cents), String(cents)).toThrow(RangeError);
    }
  });

  it("reads the dollar figures and the currency of an entry, and nothing of either when they are absent", () => {
    const account = readAccount(IN_DOLLARS);
    expect(account.usd).toEqual({ balance: 125050, overdue: 5000, dueToday: 1250 });
    expect(account.entries.map((entry) => entry.currency)).toEqual(["USD", undefined, undefined, undefined, undefined]);
    const without = readAccount(ACCOUNT);
    expect("usd" in without).toBe(false);
    expect(without.entries.some((entry) => "currency" in entry)).toBe(false);
  });

  it("shows the so'm debt and the dollar debt as two amounts, with what is late and due today in each", async () => {
    const root = await open(server(json(IN_DOLLARS)).fetch);
    const text = plain(root.textContent);
    expect(amounts(root)).toEqual(["30 000 so'm", "1 250.50 $"]);
    expect(root.querySelectorAll(".summary__label")).toHaveLength(1);
    expect([...root.querySelectorAll(".summary__overdue")].map((line) => plain(line.textContent))).toEqual([
      "Shundan muddati o'tgani: 10 000 so'm",
      "Shundan muddati o'tgani: 50.00 $",
    ]);
    expect([...root.querySelectorAll(".summary__due")].map((line) => plain(line.textContent))).toEqual([
      "Bugun to'lanishi kerak: 5 000 so'm",
      "Bugun to'lanishi kerak: 12.50 $",
    ]);
    // The entry in dollars carries its sign; the so'm entries are as they were.
    const entries = [...root.querySelectorAll(".entry")].map((entry) => plain(entry.textContent));
    expect(entries[0]).toBe("Nasiya1 250.50 $2026-yil 6-oktabrTo'lash muddati: 2026-yil 20-oktabr");
    expect(entries[1]).toBe("To'lov20 000 so'm2026-yil 5-oktabr");
    // Nothing on the page is a sum of the two: not 30 000 + 125 050, not 30 000 + 1 250.50.
    for (const sum of ["155 050", "155050", "31 250", "31250"]) {
      expect(text, sum).not.toContain(sum);
    }
    // Each amount stands in an element of its own: no element holds a so'm figure and "$" as one number.
    expect(everyText(root).filter((value) => /^[\d .]+ (so'm|\$)$/.test(value)).sort()).toEqual(
      ["1 250.50 $", "1 250.50 $", "20 000 so'm", "30 000 so'm", "50 000 so'm", "7 000 so'm", "7 000 so'm"].sort(),
    );
  });

  it("is the same in Russian, where so'm have their own word and dollars the same sign", async () => {
    const root = await open(server(json({ ...IN_DOLLARS, lang: "ru" })).fetch);
    expect(amounts(root)).toEqual(["30 000 сум", "1 250.50 $"]);
    expect(plain(root.textContent)).toContain("Из них просрочено: 50.00 $");
    expect(plain(root.textContent)).toContain("Оплатить сегодня: 12.50 $");
  });

  it("shows the dollar debt alone when only dollars are owed", async () => {
    const root = await open(
      server(json({ ...IN_DOLLARS, balance: 0, overdue: { amount: 0, due_today: 0 }, entries: [DOLLAR_ENTRY], entries_total: 1 })).fetch,
    );
    expect(amounts(root)).toEqual(["1 250.50 $"]);
    expect(root.querySelector(".summary__label")?.textContent).toBe("Qarzingiz");
    expect(plain(root.querySelector(".summary")?.textContent)).not.toContain("so'm");
    expect(root.textContent).not.toContain("Qarzingiz yo'q");
  });

  it("shows the so'm debt alone when no dollars are owed, and \"no debt\" only when both are nothing", async () => {
    const none = { balance: 0, overdue: { amount: 0, due_today: 0 } };
    const soms = await open(server(json({ ...ACCOUNT, usd: none })).fetch);
    expect(amounts(soms)).toEqual(["30 000 so'm"]);
    expect(soms.querySelector(".summary")?.textContent).not.toContain("$");
    document.body.replaceChildren();

    const clear = await open(server(json({ ...ACCOUNT, ...none, usd: none })).fetch);
    expect(amounts(clear)).toEqual(["Qarzingiz yo'q"]);
    document.body.replaceChildren();

    // Nothing in so'm is not "no debt" while dollars are owed.
    const dollarsOnly = await open(server(json({ ...ACCOUNT, ...none, usd: { ...none, balance: 1 } })).fetch);
    expect(amounts(dollarsOnly)).toEqual(["0.01 $"]);
  });

  it("says nothing about dollars for a shop without them: no \"$\" anywhere on the page", async () => {
    const root = await open(server(json(ACCOUNT)).fetch);
    expect(root.textContent).not.toContain("$");
    expect(root.textContent?.toLowerCase()).not.toContain("dollar");
    expect(root.textContent).not.toContain("USD");
    expect(amounts(root)).toEqual(["30 000 so'm"]);
    expect(root.querySelectorAll(".summary__overdue, .summary__due")).toHaveLength(2);
  });

  it.each([
    ["usd is null", { usd: null }],
    ["usd is a number", { usd: 125050 }],
    ["usd is a list", { usd: [] }],
    ["the dollar balance is missing", { usd: { overdue: { amount: 0, due_today: 0 } } }],
    ["the dollar balance is a fraction", { usd: { balance: 1250.5, overdue: { amount: 0, due_today: 0 } } }],
    ["the dollar balance is text", { usd: { balance: "1250", overdue: { amount: 0, due_today: 0 } } }],
    ["the dollar overdue is missing", { usd: { balance: 1250 } }],
    ["the dollar due today is missing", { usd: { balance: 1250, overdue: { amount: 0 } } }],
    ["an entry is in a currency the page does not know", { entries: [{ ...DOLLAR_ENTRY, currency: "EUR" }] }],
    ["an entry's currency is null", { entries: [{ ...DOLLAR_ENTRY, currency: null }] }],
  ])("treats an answer where %s as any malformed answer: nothing of it is shown", async (_what, wrong) => {
    expect(() => readAccount({ ...ACCOUNT, ...wrong })).toThrow();
    expect(await loadAccount(server(json({ ...ACCOUNT, ...wrong })).fetch, TOKEN)).toEqual({ status: "offline" });
    const root = await open(server(json({ ...ACCOUNT, ...wrong })).fetch);
    expect(root.querySelector(".summary")).toBeNull();
    expect(root.querySelector("[role=alert]")).not.toBeNull();
    expect(root.textContent).not.toContain("so'm");
    expect(root.textContent).not.toContain("$");
  });
});

describe("the page stays apart from the staff application", () => {
  const files = ["main.ts", "page.ts", "account.ts", "messages.ts", "uz.ts", "uzCyrl.ts", "tg.ts", "kaa.ts", "en.ts"];

  it("imports nothing but its own modules and the design tokens", () => {
    const imports = files.flatMap((name) =>
      [...readFileSync(resolve(SOURCE, name), "utf8").matchAll(/^import .*?["']([^"']+)["'];?$/gms)].map((match) => match[1]),
    );
    expect([...new Set(imports)].sort()).toEqual([
      "../shared/tokens.css",
      "./account",
      "./en",
      "./k.css",
      "./kaa",
      "./messages",
      "./page",
      "./tg",
      "./uz",
      "./uzCyrl",
    ]);
    // Every file of the page is in the list above: a new one cannot bring the staff application in unseen.
    expect(readdirSync(SOURCE).filter((name) => name.endsWith(".ts") && !name.endsWith(".test.ts")).sort()).toEqual([...files].sort());
  });

  it("never writes markup from text, and reaches no other host", () => {
    for (const name of files) {
      const source = readFileSync(resolve(SOURCE, name), "utf8");
      expect(source, name).not.toMatch(/innerHTML|outerHTML|insertAdjacentHTML|document\.write|eval\(|new Function/);
      expect(source, name).not.toMatch(/https?:\/\//);
    }
  });

  it("the page itself asks not to be indexed or named as a referrer, and loads one script of its own", () => {
    const html = readFileSync(resolve(SOURCE, "..", "..", "k", "index.html"), "utf8");
    expect(html).toContain('<meta name="robots" content="noindex, nofollow, noarchive" />');
    expect(html).toContain('<meta name="referrer" content="no-referrer" />');
    expect([...html.matchAll(/<script\b[^>]*>/g)].map((match) => match[0])).toEqual([
      '<script type="module" src="/src/k/main.ts">',
    ]);
    expect(html).not.toMatch(/https?:\/\//);
    expect(html).not.toMatch(/\sstyle=/);
  });
});

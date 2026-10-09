// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { ACCOUNT_PATH, type Fetch, loadAccount, readAccount, readToken, TOKEN_HEADER } from "./account";
import { CATALOGS, dayOfDate, dayOfInstant, LANGUAGES, type MessageKey, money, normalizeLanguage, say } from "./messages";
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
    button(root, "Русский")?.click();
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

  it("has nothing to press but the two languages: the page can only be read", async () => {
    const root = await open(server(json(ACCOUNT)).fetch);
    expect([...root.querySelectorAll("button")].map((node) => node.textContent)).toEqual(["O'zbekcha", "Русский"]);
    expect(root.querySelectorAll("input, textarea, select, form")).toHaveLength(0);
    expect([...root.querySelectorAll("a")].map((node) => node.getAttribute("href"))).toEqual(["tel:+998901234567"]);
  });

  it.each([
    [0, "Qarzingiz yo'q", null],
    [-15000, "15 000 so'm", "Ortiqcha to'lovingiz"],
  ])("says what a balance of %d means", async (balance, amount, label) => {
    const root = await open(server(json({ ...ACCOUNT, balance, overdue: { amount: 0, due_today: 0 } })).fetch);
    expect(root.querySelector(".summary__amount")?.textContent).toBe(amount);
    expect(root.querySelector(".summary__label")?.textContent ?? null).toBe(label);
    expect(root.querySelector(".summary__overdue")).toBeNull();
    expect(root.querySelector(".summary__due")).toBeNull();
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
    const browser = await open(server(json(ACCOUNT)).fetch, { browserLanguages: ["en-US", "ru-RU"] });
    expect(browser.querySelector("h1")?.textContent).toBe("Ali, это ваш счёт");
    expect(knownLanguage(null, ["en-US", "de"])).toBeNull();
  });

  it("changes on request without asking the server again, and is remembered", async () => {
    const storage = memory();
    const api = server(json(ACCOUNT));
    const root = await open(api.fetch, { storage });
    button(root, "Русский")?.click();
    expect(root.querySelector("h1")?.textContent).toBe("Ali, это ваш счёт");
    expect(root.querySelector("[aria-pressed=true]")?.textContent).toBe("Русский");
    expect(storage.kept.get(LANGUAGE_STORAGE_KEY)).toBe("ru");
    button(root, "O'zbekcha")?.click();
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
    button(root, "Русский")?.click();
    expect(root.querySelector("h1")?.textContent).toBe("Ali, это ваш счёт");
  });

  it("says a refusal in the reader's language too", async () => {
    const root = await open(server(json({}, 404)).fetch, { browserLanguages: ["ru"] });
    expect(root.querySelector("[role=alert] h1")?.textContent).toBe("Ссылка не работает");
  });
});

describe("the page's text", () => {
  it("has every message in both languages, with the same places to fill in", () => {
    const places = (template: string) => [...template.matchAll(/\{(\w+)\}/g)].map((match) => match[1]).sort();
    expect(Object.keys(CATALOGS.ru).sort()).toEqual(Object.keys(CATALOGS.uz).sort());
    for (const key of Object.keys(CATALOGS.uz) as MessageKey[]) {
      expect(CATALOGS.ru[key].trim(), key).not.toBe("");
      expect(places(CATALOGS.ru[key]), key).toEqual(places(CATALOGS.uz[key]));
    }
    expect(LANGUAGES).toEqual(["uz", "ru"]);
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
    expect([normalizeLanguage("ru-RU"), normalizeLanguage("UZ"), normalizeLanguage("en"), normalizeLanguage(null)]).toEqual([
      "ru",
      "uz",
      null,
      null,
    ]);
  });
});

describe("the page stays apart from the staff application", () => {
  const files = ["main.ts", "page.ts", "account.ts", "messages.ts"];

  it("imports nothing but its own modules and the design tokens", () => {
    const imports = files.flatMap((name) =>
      [...readFileSync(resolve(SOURCE, name), "utf8").matchAll(/^import .*?["']([^"']+)["'];?$/gms)].map((match) => match[1]),
    );
    expect([...new Set(imports)].sort()).toEqual(["../shared/tokens.css", "./account", "./k.css", "./messages", "./page"]);
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

import { readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { ruAdmin } from "../i18n/admin/ru";
import { uzAdmin } from "../i18n/admin/uz";
import { catalogs, compareCatalogs, placeholdersOf, translate } from "../i18n/catalog";
import type { MessageKey } from "../i18n/types";
import { uz } from "../i18n/uz";
import { AUDIT_GROUPS, detailText } from "./AuditScreen";
import "./messages";
import {
  actorName,
  CARD_LABEL_MAX,
  cardProblems,
  cardRows,
  CARDS_MAX,
  cardTag,
  CHANGE_REFUSALS,
  cleanReason,
  dateInRange,
  dateRange,
  isCode,
  isUuid,
  offeredActions,
  parseSetting,
  SETTING_RULES,
  settingText,
  shortId,
} from "./rules";
import { STATES } from "./ShopsScreen";
import { ADMIN, adminApi, auditBody, authBody, CSRF, ok, OTPAUTH, platformBody, refusal, SHOP_ID, shopBody, shopDetailBody } from "./testing";

const TODAY = { year: 2026, month: 10, day: 6 };
const sub = (state: string, storedState = state) => ({ state, storedState, trialEnds: null, paidThrough: null, priorState: null });

describe("reasons and codes", () => {
  it("takes a reason of 3 to 500 characters once white space is tidied", () => {
    expect(cleanReason("  Egasi   so'radi ")).toBe("Egasi so'radi");
    expect(cleanReason("ab")).toBeNull();
    expect(cleanReason("   ")).toBeNull();
    expect(cleanReason("a".repeat(500))).toHaveLength(500);
    expect(cleanReason("a".repeat(501))).toBeNull();
  });

  it("takes a code of exactly six digits", () => {
    expect(["123456", "000000"].map(isCode)).toEqual([true, true]);
    expect(["", "12345", "1234567", "12345a", " 123456", "１２３４５６"].map(isCode)).toEqual([false, false, false, false, false, false]);
  });
});

describe("platform settings, by type and range", () => {
  it("knows the eight settings of the platform", () => {
    expect(Object.keys(SETTING_RULES).sort()).toEqual(
      ["online_pay_on", "payment_cards", "permissions_on", "price_uzs", "review_group", "sms_monthly_quota", "sms_on", "trial_days", "trial_on"].sort(),
    );
  });

  it.each([
    ["trial_days", "1", 1],
    ["trial_days", "365", 365],
    ["price_uzs", "100 000", 100000],
    ["price_uzs", "10000000", 10000000],
    ["sms_monthly_quota", "0", 0],
    ["review_group", "-1001234567890", -1001234567890],
    ["review_group", "  ", null],
    ["trial_on", "false", false],
  ] as const)("reads %s %j", (key, text, value) => {
    expect(parseSetting(SETTING_RULES[key] as never, text)).toEqual({ ok: true, value });
  });

  it.each([
    ["trial_days", "0"],
    ["trial_days", "366"],
    ["trial_days", "30.5"],
    ["trial_days", ""],
    ["trial_days", "-1"],
    ["price_uzs", "999"],
    ["price_uzs", "10000001"],
    ["price_uzs", "1e5"],
    ["sms_monthly_quota", "100001"],
    ["review_group", "1001234567890"],
    ["review_group", "0"],
    ["review_group", "-10000000000000000"],
    ["review_group", "-12.5"],
    ["trial_on", "yes"],
  ] as const)("refuses %s %j", (key, text) => {
    expect(parseSetting(SETTING_RULES[key] as never, text)).toEqual({ ok: false });
  });
});

describe("what may be done to a subscription", () => {
  it("offers only the way out of suspension while a shop is suspended", () => {
    expect(offeredActions(sub("suspended"))).toEqual(["unsuspend"]);
  });

  it("offers a trial unless the shop is paying, its end only during one, and never unsuspension otherwise", () => {
    expect(offeredActions(sub("trial"))).toEqual(["trial", "endTrial", "paidThrough", "suspend"]);
    expect(offeredActions(sub("limited"))).toEqual(["trial", "paidThrough", "suspend"]);
    expect(offeredActions(sub("active"))).toEqual(["paidThrough", "suspend"]);
    // A trial that has run out but is not yet reviewed: limited today, still written as a trial.
    expect(offeredActions(sub("limited", "trial"))).toEqual(["trial", "endTrial", "paidThrough", "suspend"]);
  });

  it("bounds a trial's end to today through a year, and a paid day to yesterday through three years", () => {
    expect(dateRange("trial", TODAY)).toEqual({ min: TODAY, max: { year: 2027, month: 10, day: 6 } });
    expect(dateRange("paidThrough", TODAY)).toEqual({ min: { year: 2026, month: 10, day: 5 }, max: { year: 2029, month: 10, day: 8 } });
    expect(["endTrial", "suspend", "unsuspend"].map((action) => dateRange(action as never, TODAY))).toEqual([null, null, null]);
  });

  it("takes a day inside the range and nothing else", () => {
    const range = dateRange("trial", TODAY);
    if (range === null) {
      throw new Error("a trial has a range");
    }
    expect(dateInRange("2026-10-06", range)).toBe("2026-10-06");
    expect(dateInRange(" 2027-10-06 ", range)).toBe("2027-10-06");
    expect(["2026-10-05", "2027-10-07", "", "06.10.2026", "2026-02-30"].map((text) => dateInRange(text, range))).toEqual([
      null,
      null,
      null,
      null,
      null,
    ]);
  });

  it("recognises a shop identifier and shortens any identifier to six characters", () => {
    expect(isUuid(SHOP_ID)).toBe(true);
    expect(["", "42", `${SHOP_ID}x`, "5a0c6d3e00004000800000000000aaaa"].map(isUuid)).toEqual([false, false, false, false]);
    expect(shortId("77777777-7777-4777-8777-777777a1b2c3")).toBe("a1b2c3");
  });

  it("names who acted: an administrator by the code, a review-group administrator by the Telegram identifier", () => {
    const group = (id: number) => `group ${id}`;
    expect(actorName("77777777-7777-4777-8777-777777a1b2c3", null, group, "—")).toBe("a1b2c3");
    expect(actorName(null, 700123456, group, "—")).toBe("group 700123456");
    expect(actorName(null, null, group, "—")).toBe("—");
    // The server sets exactly one; should it ever send both, the administrator's account is what is shown.
    expect(actorName("77777777-7777-4777-8777-777777a1b2c3", 700123456, group, "—")).toBe("a1b2c3");
  });
});

describe("the administrator's API", () => {
  const KEY = "key-0000-0001";

  it("keeps every call under /api/admin/v1, and writes with the CSRF token", async () => {
    const { server, api } = adminApi((sent) => {
      if (sent.path === `${ADMIN}/auth`) {
        return ok(authBody());
      }
      if (sent.path === `${ADMIN}/auth/enrolment`) {
        return ok({ enrolled: true, otpauth_uri: OTPAUTH }, 201);
      }
      if (sent.path === `${ADMIN}/settings`) {
        return ok(platformBody());
      }
      if (sent.path === `${ADMIN}/shops/${SHOP_ID}` && sent.method === "GET") {
        return ok(shopDetailBody());
      }
      if (sent.path.startsWith(`${ADMIN}/shops/`)) {
        return ok(shopBody());
      }
      return ok({ items: [], next_cursor: null });
    });
    await api.readAuth();
    await api.enrol(KEY);
    await api.openSession("123456");
    await api.closeSession();
    await api.listShops({ q: "baraka", state: "trial", cursor: "c1" });
    await api.readShop(SHOP_ID);
    await api.changeSubscription(SHOP_ID, "trial", { reason: "Sabab", date: "2026-10-20" }, KEY);
    await api.changeSubscription(SHOP_ID, "endTrial", { reason: "Sabab", date: null }, KEY);
    await api.changeSubscription(SHOP_ID, "paidThrough", { reason: "Sabab", date: "2026-11-06" }, KEY);
    await api.changeSubscription(SHOP_ID, "suspend", { reason: "Sabab", date: null }, KEY);
    await api.changeSubscription(SHOP_ID, "unsuspend", { reason: "Sabab", date: null }, KEY);
    await api.readSettings();
    await api.updateSettings({ trial_days: 14 }, null, null, KEY);
    await api.updateSettings({ price_uzs: 120000 }, "123456", "Narx oshdi", KEY);
    await api.listAudit({ shopId: SHOP_ID, action: "subscription", cursor: "c2" });

    expect(server.sent.every((sent) => sent.path.startsWith(`${ADMIN}/`))).toBe(true);
    expect(server.writes().every((sent) => sent.headers["X-CSRF-Token"] === CSRF)).toBe(true);
    expect(server.sent.filter((sent) => sent.method === "GET").every((sent) => sent.headers["X-CSRF-Token"] === undefined)).toBe(true);
    expect(server.writes().map((sent) => [sent.method, sent.path.slice(ADMIN.length), sent.body, sent.headers["Idempotency-Key"]])).toEqual([
      ["POST", "/auth/enrolment", undefined, KEY],
      ["POST", "/auth/session", { code: "123456" }, undefined],
      ["DELETE", "/auth/session", undefined, undefined],
      ["POST", `/shops/${SHOP_ID}/trial`, { reason: "Sabab", trial_ends: "2026-10-20" }, KEY],
      ["POST", `/shops/${SHOP_ID}/trial/end`, { reason: "Sabab" }, KEY],
      ["POST", `/shops/${SHOP_ID}/paid-through`, { reason: "Sabab", paid_through: "2026-11-06" }, KEY],
      ["POST", `/shops/${SHOP_ID}/suspend`, { reason: "Sabab" }, KEY],
      ["POST", `/shops/${SHOP_ID}/unsuspend`, { reason: "Sabab" }, KEY],
      ["PATCH", "/settings", { changes: { trial_days: 14 } }, KEY],
      ["PATCH", "/settings", { changes: { price_uzs: 120000 }, code: "123456", reason: "Narx oshdi" }, KEY],
    ]);
    const reads = server.sent.filter((sent) => sent.method === "GET");
    expect(reads.find((sent) => sent.path === `${ADMIN}/shops`)?.query).toEqual({ q: "baraka", state: "trial", cursor: "c1" });
    expect(reads.find((sent) => sent.path === `${ADMIN}/audit`)?.query).toEqual({ shop_id: SHOP_ID, action: "subscription", cursor: "c2" });
  });

  it("refuses a change that sets a date when none is given, before anything is sent", () => {
    const { server, api } = adminApi(() => ok(shopBody()));
    expect(() => api.changeSubscription(SHOP_ID, "trial", { reason: "Sabab", date: null }, KEY)).toThrow(RangeError);
    expect(server.sent).toHaveLength(0);
  });

  it("reads a shop with its subscription, counts, receipts and changes, and nothing of its customers", async () => {
    const { api } = adminApi(() => ok(shopDetailBody()));
    const shop = await api.readShop(SHOP_ID);
    expect(Object.keys(shop).sort()).toEqual(
      ["changes", "createdAt", "customerCount", "deletionDue", "id", "lang", "name", "ownerTgId", "receipts", "staffCount", "status", "subscription"].sort(),
    );
    expect(shop.subscription).toEqual({ state: "trial", storedState: "trial", trialEnds: "2026-10-20", paidThrough: null, priorState: null });
    expect(shop.receipts[0]).toMatchObject({ statedAmount: 100000, status: "approved", months: 1 });
    expect(shop.changes[0]).toMatchObject({ action: "subscription.trial_set", reason: "Egasi so'radi" });
  });

  it("drops anything about customers that a response might carry: no field is read that is not named", async () => {
    const leaked = shopDetailBody({ customers: [{ display_name: "Ali Valiyev", balance: 120000 }], entries: [{ amount: 1 }] });
    const { api } = adminApi(() => ok(leaked));
    expect(JSON.stringify(await api.readShop(SHOP_ID))).not.toContain("Ali Valiyev");
  });

  it("reads the secret of an enrolment once, and null from a repeated request", async () => {
    const first = adminApi(() => ok({ enrolled: true, otpauth_uri: OTPAUTH }, 201));
    expect(await first.api.enrol(KEY)).toEqual({ otpauthUri: OTPAUTH });
    const again = adminApi(() => ok({ enrolled: true, otpauth_uri: null }, 201));
    expect(await again.api.enrol(KEY)).toEqual({ otpauthUri: null });
  });

  it("refuses answers that are not the contract", async () => {
    await expect(adminApi(() => ok({ ...authBody(), elevated: "yes" })).api.readAuth()).rejects.toMatchObject({ code: "BAD_RESPONSE" });
    await expect(adminApi(() => ok(shopDetailBody({ staff_count: 1.5 }))).api.readShop(SHOP_ID)).rejects.toMatchObject({ code: "BAD_RESPONSE" });
    await expect(adminApi(() => ok(platformBody({}, { trial_days: 30.5 }))).api.readSettings()).rejects.toMatchObject({ code: "BAD_RESPONSE" });
  });

  it("carries the server's refusal with its reason", async () => {
    const { api } = adminApi(() => refusal(409, "SUBSCRIPTION_CHANGE_REFUSED", "Bo'lmaydi.", { reason: "paid" }));
    await expect(api.changeSubscription(SHOP_ID, "trial", { reason: "Sabab", date: "2026-10-20" }, KEY)).rejects.toMatchObject({
      code: "SUBSCRIPTION_CHANGE_REFUSED",
      fields: { reason: "paid" },
    });
  });
});

describe("the audit's detail", () => {
  it("is shown as the server recorded it, key by key", () => {
    expect(detailText({})).toBe("—");
    expect(detailText({ expires_at: "2026-10-06T15:00:00+00:00" })).toBe("expires_at: 2026-10-06T15:00:00+00:00");
    expect(detailText({ before: 30, after: 14 })).toBe("before: 30; after: 14");
    expect(detailText(auditBody().detail)).toContain('after: {"state":"trial","trial_ends":"2026-10-20"');
  });
});

describe("the cards to pay to, as the server checks them (domain/platform_settings.py)", () => {
  const HUMO = { number: "8600123456789012", label: "Humo · Anorbank" };
  const UZCARD = { number: "5614681234567890", label: "Uzcard · Kapitalbank" };
  const rule = SETTING_RULES["payment_cards"] as never;
  const parse = (rows: unknown) => parseSetting(rule, JSON.stringify(rows));
  const many = (count: number) => Array.from({ length: count }, (_, place) => ({ number: `86001234567890${String(place).padStart(2, "0")}`, label: `Karta ${place}` }));

  it("stores the rows in order, numbers without spaces and names trimmed", () => {
    expect(parse([{ number: " 8600 1234 5678 9012 ".trim(), label: "  Humo · Anorbank " }, UZCARD])).toEqual({ ok: true, value: [HUMO, UZCARD] });
    expect(parse([UZCARD, HUMO])).toEqual({ ok: true, value: [UZCARD, HUMO] });
  });

  it("reads no rows as an empty list, which clears the cards", () => {
    expect(parse([])).toEqual({ ok: true, value: [] });
    expect(parseSetting(rule, "")).toEqual({ ok: true, value: [] });
  });

  it.each([
    ["fifteen digits", "860012345678901"],
    ["seventeen digits", "86001234567890123"],
    ["dashes", "8600-1234-5678-9012"],
    ["letters", "86001234567890ab"],
    ["nothing", ""],
    ["digits a card does not carry", "٨٦٠٠١٢٣٤٥٦٧٨٩٠١٢"],
    ["full-width digits", "８６００１２３４５６７８９０１２"],
  ])("refuses a number of %s", (_, number) => {
    expect(cardProblems([HUMO, { number, label: "Humo" }])).toEqual([null, "number"]);
    expect(parse([HUMO, { number, label: "Humo" }])).toEqual({ ok: false });
  });

  it("refuses a name that is empty or longer than forty characters, and takes one of exactly forty", () => {
    expect(CARD_LABEL_MAX).toBe(40);
    for (const label of ["", "   ", "x".repeat(41)]) {
      expect(cardProblems([{ number: HUMO.number, label }])).toEqual(["label"]);
      expect(parse([{ number: HUMO.number, label }])).toEqual({ ok: false });
    }
    expect(parse([{ number: HUMO.number, label: ` ${"x".repeat(40)} ` }])).toEqual({ ok: true, value: [{ number: HUMO.number, label: "x".repeat(40) }] });
    // Counted as the server counts: by character, not by UTF-16 unit.
    expect(parse([{ number: HUMO.number, label: "😀".repeat(40) }])).toMatchObject({ ok: true });
    expect(parse([{ number: HUMO.number, label: "😀".repeat(41) }])).toEqual({ ok: false });
  });

  it("refuses the same number twice, however it is spaced, and marks the later row", () => {
    const twice = [HUMO, UZCARD, { number: "8600 1234 5678 9012", label: "Boshqa nom" }];
    expect(cardProblems(twice)).toEqual([null, null, "duplicate"]);
    expect(parse(twice)).toEqual({ ok: false });
    // The same name on two cards is allowed.
    expect(parse([HUMO, { ...UZCARD, label: HUMO.label }])).toMatchObject({ ok: true });
  });

  it("takes ten cards and refuses eleven", () => {
    expect(CARDS_MAX).toBe(10);
    expect(parse(many(10))).toMatchObject({ ok: true });
    expect(cardProblems(many(11)).every((problem) => problem === null)).toBe(true);
    expect(parse(many(11))).toEqual({ ok: false });
  });

  it("reads rows from the text of the field and nothing from anything else", () => {
    expect(cardRows(settingText([HUMO, UZCARD]))).toEqual([HUMO, UZCARD]);
    expect(cardRows("")).toEqual([]);
    expect(cardRows("not json")).toEqual([]);
    expect(cardRows('"8600123456789012"')).toEqual([]);
    expect(cardRows('[{"number": 8600, "label": null}, "x", null]')).toEqual([
      { number: "", label: "" },
      { number: "", label: "" },
      { number: "", label: "" },
    ]);
    expect(settingText([])).toBe("[]");
    expect(settingText(null)).toBe("");
  });

  it("names a card by its name and last four digits", () => {
    expect(cardTag(HUMO)).toBe("Humo · Anorbank ··9012");
    expect(cardTag(HUMO)).not.toContain("8600");
  });
});

describe("the administrator's catalog", () => {
  it("has the same keys, kinds and placeholders in both languages, and shares none with the main catalog", () => {
    expect(compareCatalogs(uzAdmin, ruAdmin)).toEqual({ missingInSecond: [], missingInFirst: [], kindMismatch: [], placeholderMismatch: [] });
    expect(Object.keys(uzAdmin).filter((key) => key in uz)).toEqual([]);
    const without = Object.fromEntries(Object.entries(ruAdmin).filter(([key]) => key !== "door.title"));
    expect(compareCatalogs(uzAdmin, without).missingInSecond).toEqual(["door.title"]);
  });

  it("resolves every message in both languages", () => {
    for (const lang of ["uz", "ru"] as const) {
      for (const key of Object.keys(uzAdmin) as MessageKey[]) {
        const message = catalogs[lang][key];
        const template = typeof message === "string" ? message : (Object.values(message)[0] ?? "");
        const params = Object.fromEntries(placeholdersOf(template).map((name) => [name, 3]));
        expect(translate(lang, key, typeof message === "string" ? params : { ...params, count: 3 })).not.toMatch(/[{}]/);
      }
    }
  });

  it("uses only the plain apostrophe and no Cyrillic in Uzbek", () => {
    const entries = Object.entries(uzAdmin);
    expect(entries.filter(([, message]) => /[`‘’ʻʼ´]/.test(JSON.stringify(message))).map(([key]) => key)).toEqual([]);
    expect(entries.filter(([, message]) => /\p{Script=Cyrillic}/u.test(JSON.stringify(message))).map(([key]) => key)).toEqual([]);
  });

  it("has a word for every state, refusal, setting and audit group the screens name by the server's word", () => {
    const keys = [
      ...STATES.map((state) => `admin.state.${state}`),
      ...CHANGE_REFUSALS.map((reason) => `admin.refused.${reason}`),
      ...Object.keys(SETTING_RULES).map((key) => `admin.setting.${key}`),
      ...AUDIT_GROUPS.map((group) => `admin.audit.group.${group}`),
    ];
    expect(keys.filter((key) => !(key in uzAdmin))).toEqual([]);
  });

  it("names no administration in what a stranger reads before the server has said who they are", () => {
    for (const lang of ["uz", "ru"] as const) {
      for (const key of Object.keys(uzAdmin).filter((name) => name.startsWith("door.signIn.")) as MessageKey[]) {
        expect(translate(lang, key), key).not.toMatch(/admin|platforma|boshqaruv|админ|платформ|управлен/i);
      }
    }
  });
});

describe("the administrator's entry keeps to itself", () => {
  const SRC = resolve(import.meta.dirname, "..");
  const sources = (folder: string) =>
    readdirSync(resolve(SRC, folder), { recursive: true, encoding: "utf8" })
      .map((name) => name.replaceAll("\\", "/"))
      .filter((name) => /\.(ts|tsx)$/.test(name) && !/\.test\.tsx?$/.test(name))
      .map((name) => ({ name: `${folder}/${name}`, text: readFileSync(resolve(SRC, folder, name), "utf8") }));
  const imports = (text: string) =>
    [...text.matchAll(/^\s*import\s+(?!type\b)[^"']*?["']([^"']+)["']/gm), ...text.matchAll(/import\(\s*["']([^"']+)["']\s*\)/g)].map((match) => match[1] ?? "");
  const others = [...sources("app"), ...sources("shared"), ...sources("panel"), ...sources("testing"), ...sources("i18n").filter((file) => !file.name.startsWith("i18n/admin/"))];
  const admin = sources("admin").filter((file) => file.name !== "admin/testing.tsx");

  it("is imported by neither the Mini App, the shared code nor the staff panel", () => {
    expect(others.map((file) => file.name)).toContain("panel/PanelRoot.tsx");
    const offenders = others.filter((file) => imports(file.text).some((specifier) => /(^|\/)admin(\/|$)/.test(specifier))).map((file) => file.name);
    expect(offenders).toEqual([]);
  });

  it("keeps its text to itself: none of its keys is named outside it", () => {
    const keys = Object.keys(uzAdmin);
    const offenders = others.flatMap((file) => keys.filter((key) => file.text.includes(`"${key}"`)).map((key) => `${file.name}: ${key}`));
    expect(offenders).toEqual([]);
  });

  /**
   * The one place a shop's customers may appear on the administrator's side (REQ-059): the module that
   * reads them under an open support access, and the two screens that show what it read.
   */
  const SUPPORT_SCREENS = ["admin/support/customers.ts", "admin/support/CustomersScreen.tsx", "admin/support/CustomerScreen.tsx"];
  const CUSTOMER_WORDS =
    /displayName|display_name|customerName|customer_name|\bbalance\b|creditLimit|listCustomers|readCustomer|reading\.customer|supportCustomers|CustomerDetail\b/;

  it("never reaches the shop's own API or the customer's: no call outside the admin API but sign-in", () => {
    expect(admin.length).toBeGreaterThan(12);
    for (const file of admin) {
      expect(file.text, file.name).not.toMatch(/createApi\(|\.shop\(|\.account\(|\/api\/v1\/shops|\/api\/v1\/me\b/);
    }
    const paths = admin.flatMap((file) => [...file.text.matchAll(/["'`](\/api\/[^"'`$]*)/g)].map((match) => match[1]));
    expect([...new Set(paths)]).toEqual(["/api/admin/v1"]);
  });

  it("names a shop's customers, entries or balances nowhere but on the support-access screens", () => {
    const elsewhere = admin.filter((file) => !SUPPORT_SCREENS.includes(file.name));
    expect(elsewhere).toHaveLength(admin.length - SUPPORT_SCREENS.length);
    expect(elsewhere.map((file) => file.name)).toEqual(expect.arrayContaining(["admin/adminApi.ts", "admin/ShopScreen.tsx", "admin/SupportSection.tsx", "admin/SupportAccessScreen.tsx", "admin/AuditScreen.tsx", "admin/AdminApp.tsx"]));
    expect(elsewhere.filter((file) => CUSTOMER_WORDS.test(file.text)).map((file) => file.name)).toEqual([]);
    // The rule has teeth: it does find the three files that are allowed to name a customer.
    expect(admin.filter((file) => CUSTOMER_WORDS.test(file.text)).map((file) => file.name).sort()).toEqual([...SUPPORT_SCREENS].sort());
  });

  it("reads a shop's customers in one module, which the two support-access screens alone use, and only the routes open them", () => {
    const using = (pattern: RegExp) => admin.filter((file) => imports(file.text).some((specifier) => pattern.test(specifier))).map((file) => file.name).sort();
    // `send` is the only way to a call that the admin API does not build itself.
    expect(admin.filter((file) => /\.send\(/.test(file.text)).map((file) => file.name)).toEqual(["admin/support/customers.ts"]);
    expect(using(/(^|\/)customers$/)).toEqual(["admin/support/CustomerScreen.tsx", "admin/support/CustomersScreen.tsx"]);
    expect(using(/(^|\/)CustomerScreen$/)).toEqual(["admin/AdminApp.tsx"]);
    // The customer's page borrows the list's two notices; nothing else borrows anything from either.
    expect(using(/(^|\/)CustomersScreen$/)).toEqual(["admin/AdminApp.tsx", "admin/support/CustomerScreen.tsx"]);
    // Nothing of them is reachable from the shared code or the other two entries.
    expect(others.filter((file) => /admin\/support|supportCustomers/.test(file.text)).map((file) => file.name)).toEqual([]);
  });

  it("stores nothing in the browser", () => {
    expect(admin.filter((file) => /localStorage|sessionStorage|indexedDB|document\.cookie/.test(file.text)).map((file) => file.name)).toEqual([]);
  });
});

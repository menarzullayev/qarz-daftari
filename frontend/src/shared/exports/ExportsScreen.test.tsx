// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { catalogs, compareCatalogs, placeholdersOf, translate } from "../../i18n/catalog";
import { ruExports } from "../../i18n/exports/ru";
import { uzExports } from "../../i18n/exports/uz";
import type { MessageKey } from "../../i18n/types";
import { uz } from "../../i18n/uz";
import { deferred, EXPORT_ID, exportBody, fakeServer, ok, refusal, type Reply, type Sent, SHOP_BASE, SHOP_ID } from "../../testing/fakeServer";
import { renderScreen } from "../../testing/renderScreen";
import { createApi } from "../api";
import type { Role } from "../navigation";
import type { ShopMode } from "../workspace/shopMode";
import { DAILY_LIMIT, EXPORT_STATES, exportsOf, exportState, isUnfinished, RETENTION_DAYS } from "./exportsApi";
import ExportsScreen, { POLL_MS } from "./ExportsScreen";

beforeEach(() => {
  window.location.hash = "";
  window.localStorage.clear();
  window.sessionStorage.clear();
});
afterEach(cleanup);

const PATH = `${SHOP_BASE}/exports`;
const LINK = { url: "/files/abc.def", expires_at: "2026-10-06T07:05:00+00:00" };
const OTHER_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbc";
const OTHER_MEMBER = "33333333-3333-4333-8333-3333333d4e5f";
const queued = (overrides: Record<string, unknown> = {}) =>
  exportBody({ status: "queued", finished_at: null, rows: null, available: false, available_until: null, ...overrides });
/** How long the tests wait between reads of the list, and how long they watch to see that none follows. */
const FAST = 15;
const QUIET = 90;
const pause = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

function shop(items: unknown[] = [], onOther: (sent: Sent) => Reply | null = () => null) {
  const held = { items, list: null as Reply | null };
  const server = fakeServer((sent) => {
    const special = onOther(sent);
    if (special !== null) {
      return special;
    }
    if (sent.path === PATH && sent.method === "GET") {
      return held.list ?? ok({ items: held.items });
    }
    if (sent.path === PATH) {
      const made = queued({ id: OTHER_ID, created_at: "2026-10-06T07:00:00+00:00" });
      held.items = [made, ...held.items];
      return ok(made, 201);
    }
    return ok(LINK);
  });
  return { ...server, held };
}
const lists = (server: ReturnType<typeof shop>) => server.sent.filter((sent) => sent.method === "GET" && sent.path === PATH);
const show = (server: ReturnType<typeof shop>, options: { role?: Role; shopMode?: ShopMode | null; language?: "uz" | "ru"; pollMs?: number } = {}) =>
  renderScreen(<ExportsScreen pollMs={options.pollMs ?? FAST} />, {
    fetch: server.fetch,
    role: options.role ?? "manager",
    shopMode: options.shopMode ?? null,
    language: options.language ?? "uz",
  });
const rows = () => within(screen.getByRole("list", { name: "Oxirgi eksportlar" })).getAllByRole("listitem");
const rowText = (index: number) => [...(rows()[index]?.querySelectorAll("p") ?? [])].map((part) => part.textContent);

describe("the export API", () => {
  const api = (server: ReturnType<typeof fakeServer>) => exportsOf(createApi({ fetch: server.fetch, auth: { kind: "bearer", token: "t" } }).shop(SHOP_ID));
  const KEY = "key-0000-0001";

  it("asks for an export with a key and no body, lists the jobs, and asks for a link by the job", async () => {
    const server = fakeServer((sent) => (sent.path.endsWith("/download") ? ok(LINK) : sent.method === "GET" ? ok({ items: [exportBody()] }) : ok(queued(), 201)));
    const client = api(server);
    expect(await client.request(KEY)).toMatchObject({ id: EXPORT_ID, status: "queued", rows: null, available: false, availableUntil: null });
    expect(await client.list()).toEqual([
      {
        id: EXPORT_ID,
        status: "done",
        error: null,
        requestedBy: "33333333-3333-4333-8333-333333333333",
        createdAt: "2026-10-06T05:58:00+00:00",
        finishedAt: "2026-10-06T06:00:00+00:00",
        rows: 1234,
        available: true,
        availableUntil: "2026-10-13T06:00:00+00:00",
      },
    ]);
    expect(await client.link(EXPORT_ID)).toEqual({ url: "/files/abc.def", expiresAt: "2026-10-06T07:05:00+00:00" });
    expect(server.sent.map((sent) => [sent.method, sent.path, sent.body, sent.headers["Idempotency-Key"]])).toEqual([
      ["POST", PATH, undefined, KEY],
      ["GET", PATH, undefined, undefined],
      ["GET", `${PATH}/${EXPORT_ID}/download`, undefined, undefined],
    ]);
  });

  it("refuses answers that are not the contract", async () => {
    for (const wrong of [{ rows: 1.5 }, { available: "yes" }, { id: 7 }, { created_at: null }]) {
      await expect(api(fakeServer(() => ok({ items: [exportBody(wrong)] }))).list()).rejects.toMatchObject({ code: "BAD_RESPONSE" });
    }
    await expect(api(fakeServer(() => ok({ url: null, expires_at: "x" }))).link(EXPORT_ID)).rejects.toMatchObject({ code: "BAD_RESPONSE" });
  });

  it("calls a finished export whose file is gone expired, and knows which jobs are still on their way", () => {
    const job = (status: string, available: boolean) => ({ status, available }) as never;
    expect(exportState(job("done", true))).toBe("done");
    expect(exportState(job("done", false))).toBe("expired");
    expect(["queued", "running", "failed"].map((status) => exportState(job(status, false)))).toEqual(["queued", "running", "failed"]);
    expect(exportState(job("paused", false))).toBeNull();
    expect(["queued", "running", "done", "failed", "paused"].map((status) => isUnfinished(job(status, false)))).toEqual([true, true, false, false, false]);
  });

  it("repeats the server's limits: five a day, kept seven days, and a calm five seconds between reads", () => {
    expect([DAILY_LIMIT, RETENTION_DAYS, POLL_MS]).toEqual([5, 7, 5000]);
  });
});

describe("what an export is", () => {
  it("says what the file holds, sheet by sheet, and that it carries customers' names and phones", async () => {
    show(shop());
    const section = screen.getByRole("region", { name: "Eksport" });
    expect(within(section).getAllByRole("listitem").map((item) => item.textContent?.split(" — ")[0])).toEqual(["Xulosa", "Mijozlar", "Daftar", "Muddatlar", "Tovarlar"]);
    expect(within(section).getByText(/faylda mijozlarning ismlari va telefon raqamlari bor/)).toBeTruthy();
    expect(within(section).getByText("Fayl 7 kun saqlanadi, so'ng o'chiriladi. Bir kunda ko'pi bilan 5 marta eksport so'rash mumkin.")).toBeTruthy();
    expect(await screen.findByText("Hali eksport so'ralmagan.")).toBeTruthy();
  });

  it("says the same in Russian", async () => {
    show(shop([exportBody()]), { language: "ru" });
    expect(screen.getByText(/в файле есть имена и номера телефонов клиентов/)).toBeTruthy();
    expect(await screen.findByText("1234 строки · Файл хранится до 13 октября 2026 г., 11:00, затем удаляется.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Получить ссылку для скачивания" })).toBeTruthy();
  });

  it("keeps import, the other half of the section, as a placeholder under its own heading", () => {
    show(shop());
    expect(within(screen.getByRole("region", { name: "Import" })).getByText("Bu bo'lim tez orada tayyor bo'ladi.")).toBeTruthy();
  });
});

describe("asking for an export", () => {
  it("sends one request with a key however often the button is pressed, then reads the list again", async () => {
    const held = deferred<ReturnType<typeof ok>>();
    const server = shop([], (sent) => (sent.method === "POST" ? held.promise : null));
    show(server);
    await screen.findByText("Hali eksport so'ralmagan.");
    const button = screen.getByRole("button", { name: "Eksport so'rash" }) as HTMLButtonElement;
    fireEvent.click(button);
    fireEvent.click(button);
    await waitFor(() => expect(button.disabled).toBe(true));
    expect(button.textContent).toBe("Saqlanmoqda…");
    expect(server.writes()).toHaveLength(1);
    expect(server.writes()[0]).toMatchObject({ method: "POST", path: PATH });
    expect(server.writes()[0]?.body).toBeUndefined();
    expect(server.writes()[0]?.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
    server.held.items = [queued()];
    held.resolve(ok(queued(), 201));
    expect((await screen.findByRole("status")).textContent).toBe("So'rov qabul qilindi. Fayl tayyor bo'lgach, shu ro'yxatda ko'rinadi.");
    await waitFor(() => expect(rowText(0)).toEqual(["2026-yil 6-oktabr, 10:58Navbatda", "Siz", "Holat o'zi yangilanib turadi."]));
    expect(button.disabled).toBe(false);
  });

  it.each([
    ["in_progress", "Avvalgi eksport hali tayyorlanmoqda. U tugagach, yangisini so'rang."],
    ["daily_limit", "Bugungi eksport soni tugadi (kuniga 5 ta). Ertaga qayta so'rang."],
  ])("words the refusal %s, and reads the list again because it is behind", async (reason, text) => {
    const server = shop([], (sent) => (sent.method === "POST" ? refusal(409, "EXPORT_NOT_ALLOWED", "Eksport hozir mumkin emas.", { reason }) : null));
    show(server);
    await screen.findByText("Hali eksport so'ralmagan.");
    fireEvent.click(screen.getByRole("button", { name: "Eksport so'rash" }));
    expect((await screen.findByRole("alert")).textContent).toBe(text);
    await waitFor(() => expect(lists(server)).toHaveLength(2));
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("shows the server's own words for a refusal it has no words for, and for an unknown reason", async () => {
    let reply: Reply = refusal(409, "EXPORT_NOT_ALLOWED", "Eksport hozir mumkin emas.", { reason: "moon_phase" });
    const server = shop([], (sent) => (sent.method === "POST" ? reply : null));
    show(server);
    await screen.findByText("Hali eksport so'ralmagan.");
    fireEvent.click(screen.getByRole("button", { name: "Eksport so'rash" }));
    expect((await screen.findByRole("alert")).textContent).toBe("Eksport hozir mumkin emas.");
    reply = refusal(403, "SHOP_SUSPENDED", "Do'kon to'xtatilgan.");
    fireEvent.click(screen.getByRole("button", { name: "Eksport so'rash" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe("Do'kon to'xtatilgan."));
  });

  it("resends the same key after a failure, and a new one for the next export", async () => {
    let fail = true;
    const server = shop([], (sent) => (sent.method === "POST" && fail ? "offline" : null));
    show(server);
    await screen.findByText("Hali eksport so'ralmagan.");
    const button = screen.getByRole("button", { name: "Eksport so'rash" });
    fireEvent.click(button);
    expect((await screen.findByRole("alert")).textContent).toContain("Serverga ulanib bo'lmadi");
    fail = false;
    fireEvent.click(button);
    await screen.findByRole("status");
    fireEvent.click(button);
    await waitFor(() => expect(server.writes()).toHaveLength(3));
    const keys = server.writes().map((sent) => sent.headers["Idempotency-Key"]);
    expect(keys[1]).toBe(keys[0]);
    expect(keys[2]).not.toBe(keys[0]);
  });
});

describe("the list of exports", () => {
  it("says each state: waiting, being made, ready with its rows and how long it is kept, failed, expired", async () => {
    show(
      shop([
        queued({ id: "j1" }),
        queued({ id: "j2", status: "running", requested_by: OTHER_MEMBER }),
        exportBody({ id: "j3" }),
        exportBody({ id: "j4", status: "failed", error: "timeout", rows: null, available: false, available_until: null }),
        exportBody({ id: "j5", available: false, available_until: null }),
      ]),
      { pollMs: 60_000 },
    );
    await waitFor(() => expect(rows()).toHaveLength(5));
    expect(rowText(0)).toEqual(["2026-yil 6-oktabr, 10:58Navbatda", "Siz", "Holat o'zi yangilanib turadi."]);
    expect(rowText(1)).toEqual(["2026-yil 6-oktabr, 10:58Tayyorlanmoqda", "Xodim · 3d4e5f", "Holat o'zi yangilanib turadi."]);
    expect(rowText(2).slice(0, 3)).toEqual([
      "2026-yil 6-oktabr, 10:58Tayyor",
      "Siz",
      "1234 qator · Fayl 2026-yil 13-oktabr, 11:00 gacha saqlanadi, so'ng o'chiriladi.",
    ]);
    expect(rowText(3)).toEqual(["2026-yil 6-oktabr, 10:58Tayyorlanmadi", "Siz", "Tayyorlash juda uzoq davom etdi va to'xtatildi. Qayta so'rang."]);
    expect(rowText(4)).toEqual(["2026-yil 6-oktabr, 10:58Fayl muddati o'tgan", "Siz", "Fayl o'chirilgan. Kerak bo'lsa, yangi eksport so'rang."]);
  });

  it.each([
    ["interrupted", "Tayyorlash uzilib qoldi. Qayta so'rang."],
    ["timeout", "Tayyorlash juda uzoq davom etdi va to'xtatildi. Qayta so'rang."],
    ["file_store", "Faylni saqlab bo'lmadi. Birozdan so'ng qayta so'rang."],
    ["internal", "Tayyorlashda xato yuz berdi. Qayta so'rang."],
    ["cosmic_ray", "Fayl tayyorlanmadi. Qayta so'rang."],
    [null, "Fayl tayyorlanmadi. Qayta so'rang."],
  ])("words the failure %s", async (error, text) => {
    show(shop([exportBody({ status: "failed", error, rows: null, available: false, available_until: null })]));
    await waitFor(() => expect(rowText(0)[2]).toBe(text));
  });

  it("shows a state it does not know under the server's own word, with nothing to download", async () => {
    show(shop([exportBody({ status: "paused", available: false, available_until: null })]));
    await waitFor(() => expect(rowText(0)).toEqual(["2026-yil 6-oktabr, 10:58paused", "Siz"]));
    expect(screen.queryByRole("button", { name: "Yuklab olish havolasini olish" })).toBeNull();
  });

  it("offers the download for a ready export alone", async () => {
    show(
      shop([
        queued({ id: "j1" }),
        queued({ id: "j2", status: "running" }),
        exportBody({ id: "j3" }),
        exportBody({ id: "j4", status: "failed", error: "internal", available: false }),
        exportBody({ id: "j5", available: false, available_until: null }),
      ]),
      { pollMs: 60_000 },
    );
    await waitFor(() => expect(rows()).toHaveLength(5));
    expect(rows().map((row) => within(row).queryAllByRole("button").length)).toEqual([0, 0, 1, 0, 0]);
  });

  it("shows the server's refusal with a retry when the list cannot be read", async () => {
    const server = shop();
    server.held.list = refusal(403, "SHOP_SUSPENDED", "Do'kon to'xtatilgan.");
    show(server);
    expect((await screen.findByRole("alert")).textContent).toContain("Do'kon to'xtatilgan.");
    server.held.list = null;
    fireEvent.click(screen.getByRole("button", { name: "Qayta urinish" }));
    expect(await screen.findByText("Hali eksport so'ralmagan.")).toBeTruthy();
  });
});

describe("reading the list again while an export is being made", () => {
  it("asks once when nothing is unfinished", async () => {
    const server = shop([exportBody(), exportBody({ id: "j2", status: "failed", error: "internal", available: false })]);
    show(server);
    await waitFor(() => expect(rows()).toHaveLength(2));
    await pause(QUIET);
    expect(lists(server)).toHaveLength(1);
  });

  it.each(["queued", "running"])("asks again and again while one is %s, and stops once it is finished", async (status) => {
    const server = shop([queued({ status })]);
    show(server);
    await waitFor(() => expect(lists(server).length).toBeGreaterThanOrEqual(3));
    expect(screen.queryByText("Yuklanmoqda…")).toBeNull();
    server.held.items = [exportBody()];
    await screen.findByRole("button", { name: "Yuklab olish havolasini olish" });
    const asked = lists(server).length;
    await pause(QUIET);
    expect(lists(server)).toHaveLength(asked);
  });

  it("waits the interval between two reads", async () => {
    const server = shop([queued()]);
    show(server, { pollMs: 400 });
    await waitFor(() => expect(rows()).toHaveLength(1));
    await pause(QUIET);
    expect(lists(server)).toHaveLength(1);
  });

  it("stops when the screen is left, even with a read due", async () => {
    const server = shop([queued()]);
    const view = show(server);
    await waitFor(() => expect(lists(server).length).toBeGreaterThanOrEqual(2));
    view.unmount();
    const asked = lists(server).length;
    await pause(QUIET);
    expect(lists(server)).toHaveLength(asked);
  });

  it("keeps the list on the screen when a later read fails, says so, and stops until asked", async () => {
    const server = shop([queued()]);
    show(server);
    await waitFor(() => expect(rows()).toHaveLength(1));
    server.held.list = "offline";
    expect((await screen.findByRole("alert")).textContent).toContain("Holatni yangilab bo'lmadi.");
    expect(rows()).toHaveLength(1);
    const asked = lists(server).length;
    await pause(QUIET);
    expect(lists(server)).toHaveLength(asked);
    server.held.list = null;
    server.held.items = [exportBody()];
    fireEvent.click(screen.getByRole("button", { name: "Yangilash" }));
    await screen.findByRole("button", { name: "Yuklab olish havolasini olish" });
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("downloading an export", () => {
  it("asks for a link first, then shows it as a link the person opens: nothing opens by itself", async () => {
    const opened = vi.spyOn(window, "open").mockImplementation(() => null);
    const server = shop([exportBody()]);
    show(server);
    fireEvent.click(await screen.findByRole("button", { name: "Yuklab olish havolasini olish" }));
    const link = await screen.findByRole("link", { name: "Faylni yuklab olish" });
    expect(server.sent.at(-1)).toMatchObject({ method: "GET", path: `${PATH}/${EXPORT_ID}/download` });
    expect(server.sent.at(-1)?.headers["Idempotency-Key"]).toBeUndefined();
    expect(link.getAttribute("href")).toBe("/files/abc.def");
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
    expect(screen.getByText("Havola 5 daqiqa, 2026-yil 6-oktabr, 12:05 gacha amal qiladi. Ochilmasa, yangisini oling.")).toBeTruthy();
    expect(opened).not.toHaveBeenCalled();
    opened.mockRestore();
    // The link is a credential: it is in the page, and nowhere the browser keeps things.
    expect(JSON.stringify({ ...window.localStorage, ...window.sessionStorage })).not.toContain("abc.def");
    expect(window.location.href).not.toContain("abc.def");
    const before = server.sent.length;
    fireEvent.click(screen.getByRole("button", { name: "Yangi havola olish" }));
    await waitFor(() => expect(server.sent).toHaveLength(before + 1));
  });

  it("shows no link before one is asked for", async () => {
    const server = shop([exportBody()]);
    show(server);
    await screen.findByRole("button", { name: "Yuklab olish havolasini olish" });
    expect(screen.queryByRole("link", { name: "Faylni yuklab olish" })).toBeNull();
    expect(server.sent.filter((sent) => sent.path.endsWith("/download"))).toEqual([]);
  });

  it.each([
    ["queued", "Fayl hali navbatda. Tayyor bo'lishini kuting."],
    ["running", "Fayl hali tayyorlanmoqda. Tayyor bo'lishini kuting."],
    ["failed", "Bu eksport tayyorlanmadi. Yangisini so'rang."],
    ["expired", "Fayl muddati o'tgan va o'chirilgan. Yangi eksport so'rang."],
  ])("words the refusal of a file that is %s, and reads the list again", async (status, text) => {
    const server = shop([exportBody()], (sent) =>
      sent.path.endsWith("/download") ? refusal(409, "EXPORT_NOT_READY", "Fayl tayyor emas.", { status }) : null,
    );
    show(server);
    fireEvent.click(await screen.findByRole("button", { name: "Yuklab olish havolasini olish" }));
    expect((await screen.findByRole("alert")).textContent).toBe(text);
    expect(screen.queryByRole("link", { name: "Faylni yuklab olish" })).toBeNull();
    await waitFor(() => expect(lists(server)).toHaveLength(2));
  });

  it("shows the server's words for another refusal, and for a status it does not know", async () => {
    let reply: Reply = refusal(404, "NOT_FOUND", "Topilmadi.");
    const server = shop([exportBody()], (sent) => (sent.path.endsWith("/download") ? reply : null));
    show(server);
    fireEvent.click(await screen.findByRole("button", { name: "Yuklab olish havolasini olish" }));
    expect((await screen.findByRole("alert")).textContent).toBe("Topilmadi.");
    expect(lists(server)).toHaveLength(1);
    reply = refusal(409, "EXPORT_NOT_READY", "Fayl tayyor emas.", { status: "melted" });
    fireEvent.click(screen.getByRole("button", { name: "Yuklab olish havolasini olish" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe("Fayl tayyor emas."));
  });
});

describe("who sees the export", () => {
  it("gives a seller the not-found screen and asks nothing", async () => {
    const server = shop([exportBody()]);
    show(server, { role: "seller" });
    expect(screen.getByText("Bunday sahifa yo'q yoki sizda unga ruxsat yo'q.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Eksport so'rash" })).toBeNull();
    await pause(30);
    expect(server.sent).toEqual([]);
  });

  it("tells a manager of a suspended shop that only the owner exports there, and asks nothing", async () => {
    const server = shop([exportBody()]);
    show(server, { role: "manager", shopMode: "suspended" });
    expect(screen.getByText("Do'kon to'xtatilgan. Bu holatda eksportni faqat do'kon egasi oladi.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Eksport so'rash" })).toBeNull();
    expect(screen.queryByText(/telefon raqamlari/)).toBeNull();
    await pause(30);
    expect(server.sent).toEqual([]);
  });

  it("gives the owner of a suspended shop the whole screen", async () => {
    const server = shop([exportBody()]);
    show(server, { role: "owner", shopMode: "suspended" });
    expect(await screen.findByRole("button", { name: "Yuklab olish havolasini olish" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Eksport so'rash" })).toBeTruthy();
    expect(screen.queryByText("Do'kon to'xtatilgan. Bu holatda eksportni faqat do'kon egasi oladi.")).toBeNull();
  });

  it.each(["limited", null] as const)("gives a manager the whole screen while the shop is %s", async (shopMode) => {
    show(shop([exportBody()]), { role: "manager", shopMode });
    expect(await screen.findByRole("button", { name: "Yuklab olish havolasini olish" })).toBeTruthy();
  });
});

describe("the export screen's catalog", () => {
  it("has the same keys, kinds and placeholders in both languages, and shares none with the main catalog", () => {
    expect(compareCatalogs(uzExports, ruExports)).toEqual({ missingInSecond: [], missingInFirst: [], kindMismatch: [], placeholderMismatch: [] });
    expect(Object.keys(uzExports).filter((key) => key in uz)).toEqual([]);
    const without = Object.fromEntries(Object.entries(ruExports).filter(([key]) => key !== "exports.request"));
    expect(compareCatalogs(uzExports, without).missingInSecond).toEqual(["exports.request"]);
    expect(compareCatalogs(uzExports, { ...ruExports, "exports.rows": "{count}" }).kindMismatch).toEqual(["exports.rows"]);
  });

  it("resolves every message in both languages", () => {
    for (const lang of ["uz", "ru"] as const) {
      for (const key of Object.keys(uzExports) as MessageKey[]) {
        const message = catalogs[lang][key];
        const template = typeof message === "string" ? message : (Object.values(message)[0] ?? "");
        const params = Object.fromEntries(placeholdersOf(template).map((name) => [name, 3]));
        expect(translate(lang, key, typeof message === "string" ? params : { ...params, count: 3 })).not.toMatch(/[{}]/);
      }
    }
  });

  it("uses only the plain apostrophe and no Cyrillic in Uzbek, and ends no Russian sentence with a date", () => {
    const entries = Object.entries(uzExports);
    expect(entries.filter(([, message]) => /[`‘’ʻʼ´]/.test(JSON.stringify(message))).map(([key]) => key)).toEqual([]);
    expect(entries.filter(([, message]) => /\p{Script=Cyrillic}/u.test(JSON.stringify(message))).map(([key]) => key)).toEqual([]);
    // A Russian date ends in "г.": a sentence that ended with one would end in two full stops.
    expect(Object.entries(ruExports).filter(([, message]) => typeof message === "string" && /\{date\}[.!?]$/.test(message)).map(([key]) => key)).toEqual([]);
  });

  it("has a word for every state, failure and refusal the screen names by the server's word", () => {
    const keys = [
      ...EXPORT_STATES.map((state) => `exports.state.${state}`),
      ...["interrupted", "timeout", "file_store", "internal", "other"].map((error) => `exports.failed.${error}`),
      ...["queued", "running", "failed", "expired"].map((status) => `exports.notReady.${status}`),
      ...["in_progress", "daily_limit"].map((reason) => `exports.refused.${reason}`),
      ...["summary", "customers", "ledger", "promises", "goods"].map((sheet) => `exports.sheet.${sheet}`),
    ];
    expect(keys.filter((key) => !(key in uzExports))).toEqual([]);
  });
});

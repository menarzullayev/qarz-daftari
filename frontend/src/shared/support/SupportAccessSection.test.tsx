// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { catalogs, compareCatalogs, placeholdersOf, translate } from "../../i18n/catalog";
import { ruSupport } from "../../i18n/support/ru";
import { uzSupport } from "../../i18n/support/uz";
import type { MessageKey } from "../../i18n/types";
import { uz } from "../../i18n/uz";
import { ACCESS_ID, deferred, fakeServer, NOON, ok, refusal, type Reply, type Sent, SHOP_BASE, SHOP_ID, supportAccessBody } from "../../testing/fakeServer";
import { renderScreen } from "../../testing/renderScreen";
import { createApi } from "../api";
import type { Role } from "../navigation";
import SupportAccessSection from "./SupportAccessSection";
import { adminCode, isOpen, supportOf } from "./supportApi";

afterEach(cleanup);

const PATH = `${SHOP_BASE}/support-access`;
const END = `${PATH}/${ACCESS_ID}/end`;
const closed = (by: string | null, overrides: Record<string, unknown> = {}) =>
  supportAccessBody({ id: `closed-${by}`, state: "closed", closed_at: "2026-10-05T06:30:00+00:00", closed_by: by, starts_at: "2026-10-05T06:00:00+00:00", ends_at: "2026-10-05T08:00:00+00:00", ...overrides });
const expired = supportAccessBody({ id: "expired", state: "expired", starts_at: "2026-10-04T06:00:00+00:00", ends_at: "2026-10-04T07:00:00+00:00" });

function shop(items: unknown[] = [], onOther: (sent: Sent) => Reply | null = () => null) {
  const held = { items };
  const server = fakeServer((sent) => {
    const special = onOther(sent);
    if (special !== null) {
      return special;
    }
    if (sent.method === "GET") {
      return ok({ items: held.items, next_cursor: null });
    }
    held.items = held.items.map((item) =>
      (item as { id: string }).id === ACCESS_ID ? { ...(item as object), state: "closed", closed_at: "2026-10-06T07:00:00+00:00", closed_by: "owner" } : item,
    );
    return ok(held.items[0]);
  });
  return { ...server, held };
}
const show = (server: ReturnType<typeof shop>, options: { role?: Role; language?: "uz" | "ru"; onChanged?: () => void; now?: Date } = {}) =>
  renderScreen(<SupportAccessSection onChanged={options.onChanged} />, {
    fetch: server.fetch,
    role: options.role ?? "owner",
    language: options.language ?? "uz",
    ...(options.now ? { now: options.now } : {}),
  });
const history = () => within(screen.getByRole("list", { name: "Kirishlar tarixi" })).getAllByRole("listitem").map((row) => [...row.querySelectorAll("p")].map((part) => part.textContent));

describe("the owner's support-access API", () => {
  const api = (server: ReturnType<typeof fakeServer>) => supportOf(createApi({ fetch: server.fetch, auth: { kind: "bearer", token: "t" } }).shop(SHOP_ID));

  it("lists a page at a time and ends one access with a key and no body", async () => {
    const server = fakeServer((sent) => (sent.method === "GET" ? ok({ items: [supportAccessBody()], next_cursor: "c2" }) : ok(closed("owner", { id: ACCESS_ID }))));
    const client = api(server);
    expect(await client.list(null)).toEqual({
      items: [
        {
          id: ACCESS_ID,
          adminId: "dddddddd-dddd-4ddd-8ddd-dddddda1b2c3",
          reason: "Egasi yordam so'radi",
          state: "active",
          startsAt: "2026-10-06T06:00:00+00:00",
          endsAt: "2026-10-06T08:00:00+00:00",
          closedAt: null,
          closedBy: null,
        },
      ],
      nextCursor: "c2",
    });
    await client.list("c2");
    expect(await client.end(ACCESS_ID, "key-0000-0001")).toMatchObject({ id: ACCESS_ID, state: "closed", closedBy: "owner" });
    expect(server.sent.map((sent) => [sent.method, sent.path, sent.query, sent.body, sent.headers["Idempotency-Key"]])).toEqual([
      ["GET", PATH, {}, undefined, undefined],
      ["GET", PATH, { cursor: "c2" }, undefined, undefined],
      ["POST", END, {}, undefined, "key-0000-0001"],
    ]);
  });

  it("refuses answers that are not the contract", async () => {
    for (const wrong of [{ reason: null }, { ends_at: 7 }, { admin_id: undefined }]) {
      await expect(api(fakeServer(() => ok({ items: [supportAccessBody(wrong)], next_cursor: null }))).list(null)).rejects.toMatchObject({ code: "BAD_RESPONSE" });
    }
  });

  it("calls an access open only while the server says active and its time has not run out", () => {
    const access = (state: string, endsAt = "2026-10-06T08:00:00+00:00") => ({ state, endsAt });
    expect(isOpen(access("active"), NOON)).toBe(true);
    expect(isOpen(access("active"), new Date("2026-10-06T08:00:00Z"))).toBe(false);
    expect(isOpen(access("active"), new Date("2026-10-06T07:59:59Z"))).toBe(true);
    expect(["expired", "closed", "", "ACTIVE"].map((state) => isOpen(access(state), NOON))).toEqual([false, false, false, false]);
    expect(isOpen(access("active", "soon"), NOON)).toBe(false);
    expect(adminCode("dddddddd-dddd-4ddd-8ddd-dddddda1b2c3")).toBe("a1b2c3");
  });
});

describe("support access under the shop's settings", () => {
  it.each(["manager", "seller"] as const)("shows a %s nothing and asks nothing", async (role) => {
    const server = shop([supportAccessBody()]);
    const view = show(server, { role });
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(view.container.textContent).toBe("");
    expect(server.sent).toEqual([]);
  });

  it("says that nobody can read the shop now when no access is open, and that none ever was", async () => {
    show(shop());
    expect(await screen.findByText("Hozir hech bir administrator do'kon ma'lumotlarini ko'ra olmaydi.")).toBeTruthy();
    expect(screen.getByText("Administratorlar bu do'konga hali kirmagan.")).toBeTruthy();
    expect(screen.getByText(/har bir ko'rishi faoliyat jurnaliga yoziladi/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Kirishni tugatish" })).toBeNull();
  });

  it("says who can read the shop now, why and until when, and offers to end it", async () => {
    show(shop([supportAccessBody()]));
    expect(await screen.findByText("Hozir administrator (a1b2c3) do'kon ma'lumotlarini ko'ra oladi. Kirish 2026-yil 6-oktabr, 13:00 da tugaydi.")).toBeTruthy();
    expect(screen.getByText("Ko'rsatilgan sabab: Egasi yordam so'radi")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Kirishni tugatish" })).toBeTruthy();
    expect(screen.queryByText("Hozir hech bir administrator do'kon ma'lumotlarini ko'ra olmaydi.")).toBeNull();
  });

  it("offers nothing to end once the time of an access the server called active has run out", async () => {
    show(shop([supportAccessBody()]), { now: new Date("2026-10-06T08:00:01Z") });
    expect(await screen.findByText("Hozir hech bir administrator do'kon ma'lumotlarini ko'ra olmaydi.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Kirishni tugatish" })).toBeNull();
    expect(history()[0]?.[0]).toBe("a1b2c3Muddati tugagan");
  });

  it("lists every access with who, why, from when to when, and how it ended", async () => {
    show(shop([supportAccessBody(), closed("owner"), closed("admin"), closed("robot"), expired]));
    await screen.findByRole("list", { name: "Kirishlar tarixi" });
    expect(history()).toEqual([
      ["a1b2c3Hozir ochiq", "Egasi yordam so'radi", "2026-yil 6-oktabr, 11:00 — 2026-yil 6-oktabr, 13:00"],
      ["a1b2c3Egasi tugatgan · 2026-yil 5-oktabr, 11:30", "Egasi yordam so'radi", "2026-yil 5-oktabr, 11:00 — 2026-yil 5-oktabr, 13:00"],
      ["a1b2c3Administrator yopgan · 2026-yil 5-oktabr, 11:30", "Egasi yordam so'radi", "2026-yil 5-oktabr, 11:00 — 2026-yil 5-oktabr, 13:00"],
      ["a1b2c3Yopilgan · 2026-yil 5-oktabr, 11:30", "Egasi yordam so'radi", "2026-yil 5-oktabr, 11:00 — 2026-yil 5-oktabr, 13:00"],
      ["a1b2c3Muddati tugagan", "Egasi yordam so'radi", "2026-yil 4-oktabr, 11:00 — 2026-yil 4-oktabr, 12:00"],
    ]);
    expect(screen.getAllByRole("button", { name: "Kirishni tugatish" })).toHaveLength(1);
  });

  it("asks before ending, sends nothing on no, and one request with a key on yes", async () => {
    const onChanged = vi.fn();
    const held = deferred<ReturnType<typeof ok>>();
    const server = shop([supportAccessBody()], (sent) => (sent.method === "POST" ? held.promise : null));
    show(server, { onChanged });
    fireEvent.click(await screen.findByRole("button", { name: "Kirishni tugatish" }));
    expect(screen.getByText("Administratorning (a1b2c3) kirishi hozir tugatilsinmi? U do'kon ma'lumotlarini boshqa ko'ra olmaydi.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Yo'q" }));
    expect(server.writes()).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: "Kirishni tugatish" }));
    const yes = screen.getByRole("button", { name: "Ha, tugatilsin" }) as HTMLButtonElement;
    fireEvent.click(yes);
    fireEvent.click(yes);
    await waitFor(() => expect(yes.disabled).toBe(true));
    expect(server.writes()).toHaveLength(1);
    expect(server.writes()[0]).toMatchObject({ method: "POST", path: END });
    expect(server.writes()[0]?.body).toBeUndefined();
    expect(server.writes()[0]?.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
    expect(onChanged).not.toHaveBeenCalled();
    server.held.items = [closed("owner", { id: ACCESS_ID })];
    held.resolve(ok(closed("owner", { id: ACCESS_ID })));
    expect(await screen.findByText("Kirish tugatildi.")).toBeTruthy();
    expect(await screen.findByText("Hozir hech bir administrator do'kon ma'lumotlarini ko'ra olmaydi.")).toBeTruthy();
    expect(onChanged).toHaveBeenCalledTimes(1);
    expect(server.sent.filter((sent) => sent.method === "GET")).toHaveLength(2);
  });

  it("keeps the question after a failure, and a retry resends the same key", async () => {
    let fail = true;
    const server = shop([supportAccessBody()], (sent) => (sent.method === "POST" && fail ? "offline" : null));
    show(server);
    fireEvent.click(await screen.findByRole("button", { name: "Kirishni tugatish" }));
    fireEvent.click(screen.getByRole("button", { name: "Ha, tugatilsin" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Serverga ulanib bo'lmadi");
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Ha, tugatilsin" }));
    expect(await screen.findByText("Kirish tugatildi.")).toBeTruthy();
    const keys = server.writes().map((sent) => sent.headers["Idempotency-Key"]);
    expect(keys).toHaveLength(2);
    expect(keys[1]).toBe(keys[0]);
  });

  it("reads the list again when the access was no longer open, and keeps the server's words on the screen", async () => {
    const onChanged = vi.fn();
    const server = shop([supportAccessBody()], (sent) => (sent.method === "POST" ? refusal(409, "SUPPORT_ACCESS_NOT_OPEN", "Bu kirish allaqachon yopilgan.") : null));
    show(server, { onChanged });
    fireEvent.click(await screen.findByRole("button", { name: "Kirishni tugatish" }));
    server.held.items = [closed("admin", { id: ACCESS_ID })];
    fireEvent.click(screen.getByRole("button", { name: "Ha, tugatilsin" }));
    expect(await screen.findByText("Hozir hech bir administrator do'kon ma'lumotlarini ko'ra olmaydi.")).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toBe("Bu kirish allaqachon yopilgan.");
    expect(screen.queryByText("Kirish tugatildi.")).toBeNull();
    expect(onChanged).toHaveBeenCalledTimes(1);
  });

  it("reads the next page with the cursor, and shows a failure with a retry", async () => {
    let fail = true;
    const server = shop([], (sent) =>
      sent.method !== "GET" ? null : fail ? "offline" : sent.query["cursor"] === "c2" ? ok({ items: [expired], next_cursor: null }) : ok({ items: [closed("owner")], next_cursor: "c2" }),
    );
    show(server);
    expect((await screen.findByRole("alert")).textContent).toContain("Serverga ulanib bo'lmadi");
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Qayta urinish" }));
    fireEvent.click(await screen.findByRole("button", { name: "Yana ko'rsatish" }));
    await waitFor(() => expect(history()).toHaveLength(2));
    expect(server.sent.at(-1)?.query).toEqual({ cursor: "c2" });
    expect(screen.queryByRole("button", { name: "Yana ko'rsatish" })).toBeNull();
  });

  it("says the same in Russian", async () => {
    show(shop([supportAccessBody()]), { language: "ru" });
    expect(await screen.findByText("Сейчас администратор (a1b2c3) может видеть данные магазина. Доступ закончится 6 октября 2026 г., 13:00, не позже.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Завершить доступ" })).toBeTruthy();
  });
});

describe("the catalog of the owner's support access", () => {
  it("has the same keys, kinds and placeholders in both languages, and shares none with the main catalog", () => {
    expect(compareCatalogs(uzSupport, ruSupport)).toEqual({ missingInSecond: [], missingInFirst: [], kindMismatch: [], placeholderMismatch: [] });
    expect(Object.keys(uzSupport).filter((key) => key in uz)).toEqual([]);
    const without = Object.fromEntries(Object.entries(ruSupport).filter(([key]) => key !== "support.end"));
    expect(compareCatalogs(uzSupport, without).missingInSecond).toEqual(["support.end"]);
  });

  it("resolves every message in both languages", () => {
    for (const lang of ["uz", "ru"] as const) {
      for (const key of Object.keys(uzSupport) as MessageKey[]) {
        const message = catalogs[lang][key];
        const template = typeof message === "string" ? message : (Object.values(message)[0] ?? "");
        const params = Object.fromEntries(placeholdersOf(template).map((name) => [name, 3]));
        expect(translate(lang, key, typeof message === "string" ? params : { ...params, count: 3 })).not.toMatch(/[{}]/);
      }
    }
  });

  it("uses only the plain apostrophe and no Cyrillic in Uzbek, and ends no Russian sentence with a date", () => {
    const entries = Object.entries(uzSupport);
    expect(entries.filter(([, message]) => /[`‘’ʻʼ´]/.test(JSON.stringify(message))).map(([key]) => key)).toEqual([]);
    expect(entries.filter(([, message]) => /\p{Script=Cyrillic}/u.test(JSON.stringify(message))).map(([key]) => key)).toEqual([]);
    expect(Object.entries(ruSupport).filter(([, message]) => /\{date\}[.!?]$/.test(message)).map(([key]) => key)).toEqual([]);
  });
});

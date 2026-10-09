// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { I18nProvider } from "../i18n/I18nProvider";
import { createApi } from "../shared/api";
import { fakeServer, ok, refusal, SHOP_ID } from "../testing/fakeServer";
import { backoffice } from "./backoffice";
import { readLoginData, signInPanel, signOutPanel } from "./signIn";
import { takeLoginReturn } from "./loginReturn";
import { authUrl, TelegramLogin, WIDGET_SRC } from "./TelegramLogin";
import { CSRF, MANAGER_ID } from "./testing";

afterEach(cleanup);

const LOGIN = { id: 123456789, first_name: "Ali", username: "ali", auth_date: 1791270000, hash: "ab12" };

describe("the widget's data", () => {
  it("is passed on whole: the signature covers every field", () => {
    expect(readLoginData({ ...LOGIN, photo_url: "https://t.me/i/userpic/1.jpg", new_field: "x" })).toEqual({
      ...LOGIN,
      photo_url: "https://t.me/i/userpic/1.jpg",
      new_field: "x",
    });
    expect(readLoginData({ ...LOGIN, last_name: null })).toEqual(LOGIN);
  });

  it.each([
    ["nothing", undefined],
    ["text", "id=1&hash=ab"],
    ["a list", [LOGIN]],
    ["no signature", { id: 1, auth_date: 1 }],
    ["an empty signature", { ...LOGIN, hash: "" }],
    ["no identifier", { auth_date: 1, hash: "ab" }],
    ["no date", { id: 1, hash: "ab" }],
    ["a nested value", { ...LOGIN, user: { id: 1 } }],
    ["a number that is not one", { ...LOGIN, id: Number.NaN }],
  ])("is refused before anything is sent when it is %s", (_name, raw) => {
    expect(readLoginData(raw)).toBeNull();
  });
});

describe("signing in to the panel", () => {
  it("posts the data with credentials, so the browser keeps the cookie, and answers the CSRF token", async () => {
    const credentials: (string | undefined)[] = [];
    const server = fakeServer(() => ok({ csrf_token: CSRF, expires_at: "2026-10-21T07:00:00+00:00" }));
    const auth = await signInPanel((input, init) => {
      credentials.push(init.credentials);
      return server.fetch(input, init);
    }, LOGIN);
    expect(auth).toEqual({ kind: "cookie", csrfToken: CSRF });
    expect(server.sent).toHaveLength(1);
    expect(server.sent[0]).toMatchObject({ method: "POST", path: "/api/v1/auth/telegram-login", body: LOGIN, query: {} });
    expect(credentials).toEqual(["same-origin"]);
    expect(server.sent[0]?.headers["Authorization"]).toBeUndefined();
    expect(server.sent[0]?.headers["X-CSRF-Token"]).toBeUndefined();
  });

  it("fails when the answer carries no CSRF token: a session that cannot write is not a session", async () => {
    const server = fakeServer(() => ok({ expires_at: "2026-10-21T07:00:00+00:00" }));
    await expect(signInPanel(server.fetch, LOGIN)).rejects.toMatchObject({ code: "BAD_RESPONSE" });
  });

  it("fails with the server's refusal", async () => {
    const server = fakeServer(() => refusal(401, "UNAUTHENTICATED", "Avval tizimga kiring."));
    await expect(signInPanel(server.fetch, LOGIN)).rejects.toMatchObject({ status: 401, serverMessage: "Avval tizimga kiring." });
  });

  it("signs out with the CSRF token, as every write of a cookie session must", async () => {
    const server = fakeServer(() => ({ status: 200, body: null }));
    await signOutPanel(server.fetch, { kind: "cookie", csrfToken: CSRF });
    expect(server.sent[0]).toMatchObject({ method: "POST", path: "/api/v1/auth/sign-out" });
    expect(server.sent[0]?.headers["X-CSRF-Token"]).toBe(CSRF);
  });
});

describe("the CSRF token on the panel's calls", () => {
  const office = (kind: "cookie" | "bearer") => {
    const server = fakeServer((sent) => (sent.method === "GET" ? ok({ items: [], pending: null }) : ok({ id: MANAGER_ID, role: "seller", status: "active", expires_at: "x", token: null, from_membership: "a", to_membership: "b", deletion_due: null })));
    const auth = kind === "cookie" ? ({ kind, csrfToken: CSRF } as const) : ({ kind, token: "session-token" } as const);
    return { server, calls: backoffice(createApi({ fetch: server.fetch, auth }).shop(SHOP_ID)) };
  };
  const KEY = "key-0000-0001";
  const writes = (calls: ReturnType<typeof backoffice>) => [
    calls.invite("seller", KEY),
    calls.cancelInvitation("ab".repeat(32), KEY),
    calls.updateMember(MANAGER_ID, { role: "seller" }, KEY),
    calls.removeMember(MANAGER_ID, KEY),
    calls.startTransfer(MANAGER_ID, KEY),
    calls.cancelTransfer(KEY),
    calls.answerTransfer("accept", KEY),
    calls.answerTransfer("decline", KEY),
    calls.requestDeletion("Baraka savdo", KEY),
    calls.cancelDeletion(KEY),
  ];

  it("is sent with every write of a cookie session, with the idempotency key, and with no read", async () => {
    const { server, calls } = office("cookie");
    await Promise.all(writes(calls));
    await Promise.all([calls.listStaff(), calls.listInvitations(), calls.readTransfer(), calls.listActivity({}), calls.readDeletion().catch(() => null)]);
    expect(server.writes()).toHaveLength(10);
    expect(server.writes().map((sent) => sent.method).sort()).toEqual(
      ["DELETE", "DELETE", "DELETE", "DELETE", "PATCH", "POST", "POST", "POST", "POST", "POST"],
    );
    for (const sent of server.writes()) {
      expect(sent.headers["X-CSRF-Token"], `${sent.method} ${sent.path}`).toBe(CSRF);
      expect(sent.headers["Idempotency-Key"], `${sent.method} ${sent.path}`).toBe(KEY);
      expect(sent.headers["Authorization"]).toBeUndefined();
    }
    const reads = server.sent.filter((sent) => sent.method === "GET");
    expect(reads).toHaveLength(5);
    expect(reads.every((sent) => sent.headers["X-CSRF-Token"] === undefined)).toBe(true);
    // Neither proof of the session ever travels in an address.
    expect(server.sent.every((sent) => !JSON.stringify([sent.path, sent.query]).includes(CSRF))).toBe(true);
  });

  it("is never sent by the Mini App's bearer session, whose calls carry the token header instead", async () => {
    const { server, calls } = office("bearer");
    await Promise.all(writes(calls));
    await calls.listStaff();
    expect(server.sent).toHaveLength(11);
    for (const sent of server.sent) {
      expect(sent.headers["X-CSRF-Token"]).toBeUndefined();
      expect(sent.headers["Authorization"]).toBe("Bearer session-token");
    }
  });

  it("refuses a response that is not the contract instead of showing it", async () => {
    const server = fakeServer(() => ok({ items: [{ id: MANAGER_ID, role: "admin", status: "active" }] }));
    const calls = backoffice(createApi({ fetch: server.fetch, auth: { kind: "cookie", csrfToken: CSRF } }).shop(SHOP_ID));
    await expect(calls.listStaff()).rejects.toMatchObject({ code: "BAD_RESPONSE" });
  });
});

describe("Telegram's sign-in button", () => {
  const widget = () =>
    render(
      <I18nProvider initialLanguage="ru">
        <TelegramLogin botUsername="qarz_daftari_bot" language="ru" />
      </I18nProvider>,
    );
  const scripts = () => [...document.querySelectorAll("script")];

  it("adds Telegram's own script, from telegram.org over HTTPS, for the build's bot", () => {
    widget();
    expect(scripts()).toHaveLength(1);
    const script = scripts()[0] as HTMLScriptElement;
    const url = new URL(script.src);
    expect([url.protocol, url.hostname, url.pathname]).toEqual(["https:", "telegram.org", "/js/telegram-widget.js"]);
    expect(script.src).toBe(WIDGET_SRC);
    expect(script.async).toBe(true);
    expect(script.getAttribute("data-telegram-login")).toBe("qarz_daftari_bot");
    expect(script.getAttribute("data-lang")).toBe("ru");
    // Redirect mode. A callback in `data-onauth` is what Telegram's script would build with eval().
    expect(script.getAttribute("data-auth-url")).toBe(authUrl());
    expect(script.hasAttribute("data-onauth")).toBe(false);
    expect(script.hasAttribute("data-onunauth")).toBe(false);
    // The widget is asked for nothing beyond identity: no permission to write to the person.
    expect(script.hasAttribute("data-request-access")).toBe(false);
    expect(screen.getByRole("group", { name: "Вход через Telegram" }).contains(script)).toBe(true);
  });

  it("sends Telegram back to this page without its query string and fragment, and leaves no function on the page", () => {
    expect(authUrl({ origin: "https://qarz.example.uz", pathname: "/panel/" })).toBe("https://qarz.example.uz/panel/");
    const before = Object.keys(window);
    const view = widget();
    expect(Object.keys(window)).toEqual(before);
    view.unmount();
    expect(scripts()).toHaveLength(0);
  });

  it("replaces the button, not adds a second one, when the language changes", () => {
    const view = widget();
    view.rerender(
      <I18nProvider initialLanguage="ru">
        <TelegramLogin botUsername="qarz_daftari_bot" language="uz" />
      </I18nProvider>,
    );
    expect(scripts().map((script) => script.getAttribute("data-lang"))).toEqual(["uz"]);
  });

  it("names the frame Telegram's script puts in the button's place, which comes with no title", async () => {
    const view = widget();
    const host = screen.getByRole("group", { name: "Вход через Telegram" });
    // What Telegram's script does once it has loaded: a frame of its own in the place of the script.
    const frame = document.createElement("iframe");
    frame.id = "telegram-login-qarz_daftari_bot";
    expect(frame.title).toBe("");
    host.append(frame);
    await waitFor(() => expect(frame.title).toBe("Вход через Telegram"));
    expect(screen.getByTitle("Вход через Telegram")).toBe(frame);

    // A frame the script puts there later (it replaces its own) is named as well, in the language of the day.
    view.rerender(
      <I18nProvider initialLanguage="ru">
        <TelegramLogin botUsername="qarz_daftari_bot" language="ru" />
      </I18nProvider>,
    );
    const second = document.createElement("iframe");
    host.append(second);
    await waitFor(() => expect(second.title).toBe("Вход через Telegram"));
  });

  it("names no frame but the button's own", async () => {
    widget();
    const elsewhere = document.createElement("iframe");
    document.body.append(elsewhere);
    const host = screen.getByRole("group", { name: "Вход через Telegram" });
    const own = document.createElement("iframe");
    host.append(own);
    await waitFor(() => expect(own.title).not.toBe(""));
    expect(elsewhere.title).toBe("");
    elsewhere.remove();
  });

  it("says so when the script cannot be loaded", async () => {
    widget();
    scripts()[0]?.dispatchEvent(new Event("error"));
    expect((await screen.findByRole("alert")).textContent).toBe("Кнопка входа Telegram не загрузилась. Проверьте интернет и обновите страницу.");
  });
});

describe("coming back from Telegram", () => {
  const SITE = "https://qarz.example.uz";
  const SIGNED = "id=123456789&first_name=Ali&username=ali&auth_date=1791270000&hash=ab12";

  function page(search: string, referrer: string, hash = "") {
    const replaced: unknown[][] = [];
    return {
      replaced,
      location: { origin: SITE, pathname: "/panel/", search, hash },
      history: { state: null, replaceState: (...args: unknown[]) => void replaced.push(args) },
      document: { referrer },
    };
  }

  it("takes the signed fields, every one as text, and removes them from the address in the same history entry", () => {
    const loaded = page(`?${SIGNED}`, `${SITE}/panel/`);
    expect(takeLoginReturn(loaded)).toEqual({
      status: "returned",
      data: { id: "123456789", first_name: "Ali", username: "ali", auth_date: "1791270000", hash: "ab12" },
    });
    expect(loaded.replaced).toEqual([[null, "", "/panel/"]]);
  });

  it("keeps a fragment that is a route, and decodes what Telegram encoded", () => {
    const loaded = page("?id=1&first_name=Ali%20Vali&photo_url=https%3A%2F%2Ft.me%2Fi%2F1.jpg&auth_date=2&hash=ab", `${SITE}/admin/`, "#/shops");
    expect(takeLoginReturn(loaded)).toMatchObject({ data: { first_name: "Ali Vali", photo_url: "https://t.me/i/1.jpg" } });
    expect(loaded.replaced).toEqual([[null, "", "/panel/#/shops"]]);
  });

  it("leaves an ordinary address alone", () => {
    for (const search of ["", "?shop=1", "?id=1&auth_date=2"]) {
      const loaded = page(search, `${SITE}/panel/`);
      expect(takeLoginReturn(loaded)).toEqual({ status: "none" });
      expect(loaded.replaced).toEqual([]);
    }
  });

  it.each([
    ["another site", "https://evil.example/"],
    ["a look-alike of this site", `${SITE}.evil.example/panel/`],
    ["this site without TLS", "http://qarz.example.uz/panel/"],
    ["nowhere: a link in a message, a typed address", ""],
    ["something that is no address", "not a url"],
  ])("refuses fields that arrive from %s, and still removes them from the address", (_name, referrer) => {
    const loaded = page(`?${SIGNED}`, referrer);
    expect(takeLoginReturn(loaded)).toEqual({ status: "refused" });
    expect(loaded.replaced).toEqual([[null, "", "/panel/"]]);
  });

  it.each([
    ["no identifier", "auth_date=2&hash=ab"],
    ["no date", "id=1&hash=ab"],
    ["an empty signature", "id=1&auth_date=2&hash="],
    ["a field given twice", "id=1&id=2&auth_date=2&hash=ab"],
  ])("refuses fields with %s", (_name, query) => {
    const loaded = page(`?${query}`, `${SITE}/panel/`);
    expect(takeLoginReturn(loaded)).toEqual({ status: "refused" });
    expect(loaded.replaced).toHaveLength(1);
  });

  it("works on the real window: the address bar loses the query string and history gains no entry", () => {
    const entries = window.history.length;
    window.history.pushState(null, "", `/panel/?${SIGNED}#/reports`);
    const pushed = window.history.length;
    // jsdom's referrer is empty, which is a link from nowhere.
    expect(takeLoginReturn()).toEqual({ status: "refused" });
    expect(window.location.search).toBe("");
    expect(window.location.href).not.toContain("hash=");
    expect(window.location.hash).toBe("#/reports");
    expect(window.history.length).toBe(pushed);
    expect(pushed).toBe(entries + 1);
    window.history.replaceState(null, "", "/");
  });
});

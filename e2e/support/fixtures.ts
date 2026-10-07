import { type APIRequestContext, type BrowserContext, test as base, expect, type Page } from "@playwright/test";

import { Chat } from "./chat.ts";
import { stack } from "./stack.ts";
import { loginWidgetFields, type Person, webAppInitData } from "./telegram.ts";

/**
 * Stand-in for `https://telegram.org/js/telegram-web-app.js`, which the Mini App page loads. Like the
 * real bridge it reads the launch data Telegram puts in the fragment (`#tgWebAppData=...`), keeps it in
 * `sessionStorage` so that it is still there when the page is loaded again after the fragment changed,
 * and offers it as `window.Telegram.WebApp.initData`. The request for the real script never leaves the
 * machine.
 */
const BRIDGE_STAND_IN = `(function () {
  var KEPT = "__telegram__initParams";
  var launch = {};
  new URLSearchParams(window.location.hash.slice(1)).forEach(function (value, key) { launch[key] = value; });
  try {
    var kept = JSON.parse(window.sessionStorage.getItem(KEPT) || "{}");
    for (var key in kept) { if (!(key in launch)) { launch[key] = kept[key]; } }
    window.sessionStorage.setItem(KEPT, JSON.stringify(launch));
  } catch (error) {}
  var initData = launch.tgWebAppData || "";
  var unsafe = {};
  try {
    var user = new URLSearchParams(initData).get("user");
    if (user) { unsafe.user = JSON.parse(user); }
  } catch (error) { unsafe = {}; }
  window.Telegram = { WebApp: {
    initData: initData, initDataUnsafe: unsafe, themeParams: {}, version: "8.0", platform: "e2e",
    ready: function () {}, expand: function () {}, onEvent: function () {}
  } };
})();`;

/**
 * Stand-in for `https://telegram.org/js/telegram-widget.js`, in the widget's redirect mode
 * (`data-auth-url`). The real script draws a frame from oauth.telegram.org and, once the person has
 * confirmed in Telegram, sends the browser to the auth URL with the signed fields added to the query
 * string. This one draws a button; pressing it asks the test for the signed fields (the part Telegram
 * plays) and then leaves for the auth URL in exactly that way. It uses no eval and nothing inline, so
 * it runs under the page's real Content-Security-Policy; it proves nothing about Telegram's own script.
 */
const WIDGET_STAND_IN = `(function () {
  var script = document.currentScript;
  var authUrl = script.getAttribute("data-auth-url");
  var button = document.createElement("button");
  button.type = "button";
  button.className = "button";
  button.textContent = "Log in with Telegram (stand-in for " + script.getAttribute("data-telegram-login") + ")";
  button.addEventListener("click", function () {
    window.__e2eTelegramConfirm().then(function (user) {
      if (!authUrl) { throw new Error("the page gave the widget no data-auth-url"); }
      var link = document.createElement("a");
      link.href = authUrl;
      var target = link.href;
      target += target.indexOf("?") >= 0 ? "&" : "?";
      var pairs = [];
      for (var key in user) { pairs.push(key + "=" + encodeURIComponent(user[key])); }
      window.location.href = target + pairs.join("&");
    });
  });
  script.parentNode.insertBefore(button, script);
})();`;

/** What the suite watches for on every page of every journey (hardening, seen from the browser). */
export type Watch = {
  /** `securitypolicyviolation` events, and console messages about the policy. */
  cspViolations: string[];
  /** API answers without the proxy's `X-Request-Id`. */
  missingRequestId: string[];
  /** Requests to anywhere but the stack; they are refused, and listed here. */
  outsideRequests: string[];
  /** How many API answers were looked at: the check above means something only when this is not zero. */
  apiAnswers: number;
};

type Fixtures = {
  /** Fails the test afterwards on any violation, unless the test says it provokes them. */
  watch: Watch;
  expectViolations: boolean;
  chat: Chat;
  api: APIRequestContext;
  /** Another browser, for another person: nothing is shared with the first (cookies, storage). */
  secondContext: BrowserContext;
};

const confirming = new WeakMap<Page, Person>();
/** The signed fields "Telegram" last gave each page, so that a test can look for them where they must not be. */
const confirmed = new WeakMap<Page, Record<string, string>>();

async function prepare(context: BrowserContext, watch: Watch): Promise<void> {
  await context.exposeBinding("__e2eCspViolation", (_source, detail: string) => {
    watch.cspViolations.push(detail);
  });
  await context.exposeBinding("__e2eTelegramConfirm", ({ page }) => {
    const person = confirming.get(page);
    if (!person) {
      throw new Error("nobody is confirming a sign-in on this page");
    }
    const fields = loginWidgetFields(stack.botToken, person);
    confirmed.set(page, fields);
    return fields;
  });
  await context.addInitScript(() => {
    document.addEventListener("securitypolicyviolation", (event) => {
      const report = (window as unknown as { __e2eCspViolation: (detail: string) => void }).__e2eCspViolation;
      report(`${event.violatedDirective} blocked ${event.blockedURI || "inline"} on ${window.location.pathname}`);
    });
  });
  context.on("console", (message) => {
    if (message.type() === "error" && /Content Security Policy/i.test(message.text())) {
      watch.cspViolations.push(`console: ${message.text()}`);
    }
  });
  context.on("response", (response) => {
    const url = new URL(response.url());
    if (url.origin === stack.baseURL && url.pathname.startsWith("/api/")) {
      watch.apiAnswers += 1;
      if (!/^[0-9a-f]{32}$/.test(response.headers()["x-request-id"] ?? "")) {
        watch.missingRequestId.push(`${response.request().method()} ${url.pathname} -> ${response.status()}`);
      }
    }
  });
  await context.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.origin === stack.baseURL) {
      await route.continue();
    } else if (url.origin === "https://telegram.org" && url.pathname === "/js/telegram-web-app.js") {
      await route.fulfill({ contentType: "application/javascript", body: BRIDGE_STAND_IN });
    } else if (url.origin === "https://telegram.org" && url.pathname === "/js/telegram-widget.js") {
      await route.fulfill({ contentType: "application/javascript", body: WIDGET_STAND_IN });
    } else {
      watch.outsideRequests.push(route.request().url());
      await route.abort();
    }
  });
}

export const test = base.extend<Fixtures>({
  expectViolations: [false, { option: true }],

  watch: [
    async ({ context, expectViolations }, use) => {
      const watch: Watch = { cspViolations: [], missingRequestId: [], outsideRequests: [], apiAnswers: 0 };
      await prepare(context, watch);
      await use(watch);
      if (!expectViolations) {
        expect(watch.cspViolations, "Content-Security-Policy violations").toEqual([]);
        expect(watch.outsideRequests, "requests that would have left this machine").toEqual([]);
      }
      expect(watch.missingRequestId, "API answers without X-Request-Id").toEqual([]);
    },
    { auto: true },
  ],

  secondContext: async ({ browser, watch }, use) => {
    const context = await browser.newContext({ baseURL: stack.baseURL, ignoreHTTPSErrors: true });
    await prepare(context, watch);
    await use(context);
    await context.close();
  },

  api: async ({ playwright }, use) => {
    const api = await playwright.request.newContext({ ignoreHTTPSErrors: true });
    await use(api);
    await api.dispose();
  },

  chat: async ({ api }, use) => {
    await use(new Chat(api));
  },
});

export { expect };

export const PHONE = { width: 390, height: 844 };
export const DESKTOP = { width: 1366, height: 900 };

/**
 * Opens the Mini App as Telegram opens it for this person: at `/app/` with freshly signed launch data in
 * the fragment. The page signs in with it on its own.
 */
export async function openMiniApp(page: Page, person: Person): Promise<string> {
  const initData = webAppInitData(stack.botToken, person);
  await page.setViewportSize(PHONE);
  // A new document every time: a change of the fragment alone would not load the page again.
  await page.goto("about:blank");
  await page.goto(`/app/#tgWebAppData=${encodeURIComponent(initData)}&tgWebAppVersion=8.0`);
  return initData;
}

/**
 * Signs in to the panel (`/panel/`) or the administrators' entry (`/admin/`) as this person: opens the
 * page, presses the Login widget's button, and lets "Telegram" confirm. The page does the rest itself.
 */
export async function signInOnWeb(page: Page, person: Person, entry: "/panel/" | "/admin/"): Promise<Record<string, string>> {
  confirming.set(page, person);
  await page.setViewportSize(DESKTOP);
  await page.goto(entry);
  // Pressing the button leaves the page and comes back to it; the page that loads then signs in.
  const signedIn = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/v1/auth/telegram-login");
  await page.getByRole("button", { name: /Log in with Telegram \(stand-in/ }).click();
  await signedIn;
  return confirmed.get(page) ?? {};
}

/** Moves inside the single-page application without loading the document again. */
export async function goTo(page: Page, path: string): Promise<void> {
  await page.evaluate((target) => {
    window.location.hash = `#${target}`;
  }, path);
}

import { expect, signInOnWeb, test } from "../support/fixtures.ts";
import { lit, serviceLog, sqlValue, stack } from "../support/stack.ts";
import { loginWidgetFields, newPerson } from "../support/telegram.ts";

/**
 * Hardening, seen from the browser. Two of its three parts run inside every other journey: the `watch`
 * fixture fails any test in which the browser reports a Content-Security-Policy violation, in which an
 * API answer lacks the proxy's `X-Request-Id`, or in which a page asks for something outside the stack.
 * Here: the proof that those checks can fail, the rest of the answers, and the body limit.
 */

test.describe("the checks that watch every journey can fail", () => {
  test.use({ expectViolations: true });

  for (const entry of ["/panel/", "/admin/", "/app/"] as const) {
    test(`${entry}: eval and inline script are refused by the page's policy, and the suite sees it`, async ({ page, watch }) => {
      await page.goto(entry);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      expect(watch.cspViolations).toEqual([]);

      // Code built from text, which is what Telegram's widget did in its callback mode and why
      // 'unsafe-eval' used to be allowed. (A timer given text compiles it later, as the page itself would;
      // an eval() typed here would run with the debugger's exemption from the policy.)
      await page.evaluate(() => {
        window.setTimeout("window.__evalRan = true;", 0);
      });
      await page.evaluate(() => {
        const script = document.createElement("script");
        script.textContent = "window.__inlineRan = true;";
        document.head.append(script);
      });
      await page.evaluate(() => new Promise((done) => window.setTimeout(done, 50)));
      expect(await page.evaluate(() => ["__evalRan" in window, "__inlineRan" in window])).toEqual([false, false]);

      await expect.poll(() => watch.cspViolations.filter((violation) => violation.startsWith("script-src")).length).toBeGreaterThanOrEqual(2);
      expect(watch.cspViolations.join("\n")).toContain("eval");
      expect(watch.cspViolations.join("\n")).toContain("inline");
    });
  }

  test("a request to anywhere but the stack is refused and reported", async ({ page, watch }) => {
    await page.goto("/panel/");
    await page.evaluate(() => {
      const image = new Image();
      image.src = "https://example.org/pixel.png";
    });
    await expect.poll(() => watch.cspViolations.some((violation) => violation.startsWith("img-src"))).toBe(true);
  });
});

test("every API answer carries the proxy's request id: success, refusal, not found, too large", async ({ page, watch, chat }) => {
  const owner = newPerson("Olim");
  await chat.openShop(owner, `Sarlavha ${owner.id}`);
  await signInOnWeb(page, owner, "/panel/");
  await expect(page.locator("main dl dd").first()).toHaveText("0 so'm");
  await page.waitForLoadState("networkidle");
  const loaded = watch.apiAnswers;
  expect(loaded).toBeGreaterThan(2);

  const answers = await page.evaluate(async () => {
    const seen: { what: string; status: number; requestId: string | null; code: unknown }[] = [];
    const ask = async (what: string, path: string, init?: RequestInit) => {
      const answer = await fetch(path, init);
      const body: unknown = await answer.json().catch(() => null);
      const code = (body as { error?: { code?: unknown } } | null)?.error?.code ?? null;
      seen.push({ what, status: answer.status, requestId: answer.headers.get("X-Request-Id"), code });
    };
    await ask("a read with the session", "/api/v1/me");
    await ask("a read without one", "/api/v1/me", { credentials: "omit" });
    await ask("a write without the CSRF token", "/api/v1/me", { method: "PATCH", headers: { "Content-Type": "application/json" }, body: '{"lang":"ru"}' });
    await ask("an address nobody routes", "/api/v1/no-such-thing");
    await ask("another shop", "/api/v1/shops/00000000-0000-4000-8000-000000000000/customers");
    // One byte over the megabyte the proxy and the application allow on an ordinary route.
    await ask("a body over the limit", "/api/v1/me", { method: "PATCH", headers: { "Content-Type": "application/json" }, body: "x".repeat(1024 * 1024 + 1) });
    return seen;
  });

  expect(answers.map((answer) => [answer.what, answer.status, answer.code])).toEqual([
    ["a read with the session", 200, null],
    ["a read without one", 401, "UNAUTHENTICATED"],
    ["a write without the CSRF token", 401, "UNAUTHENTICATED"],
    ["an address nobody routes", 404, "NOT_FOUND"],
    ["another shop", 404, "NOT_FOUND"],
    ["a body over the limit", 413, "BODY_TOO_LARGE"],
  ]);
  for (const answer of answers) {
    expect(answer.requestId, answer.what).toMatch(/^[0-9a-f]{32}$/);
  }
  expect(new Set(answers.map((answer) => answer.requestId)).size).toBe(answers.length);
  expect(watch.apiAnswers).toBeGreaterThanOrEqual(loaded + answers.length);
  expect(watch.missingRequestId).toEqual([]);
});

test("what Telegram sent the browser back with is left nowhere: not in the address, the history or the logs", async ({ page }) => {
  const owner = newPerson("Olim");
  const fields = await signInOnWeb(page, owner, "/panel/");
  await expect(page.getByRole("heading", { level: 1, name: "Do'kon yo'q" })).toBeVisible();
  const signature = fields["hash"] ?? "";
  expect(signature).toMatch(/^[0-9a-f]{64}$/);

  expect(page.url()).toBe(`${stack.baseURL}/panel/`);
  // The CSRF token is in memory only, the session in a cookie no script can read.
  const kept = await page.evaluate(() => JSON.stringify([{ ...window.localStorage }, { ...window.sessionStorage }, document.cookie]));
  expect(kept).not.toContain(signature);
  expect(kept).toBe('[{},{},""]');
  expect((await page.context().cookies()).map((cookie) => [cookie.name, cookie.httpOnly, cookie.secure, cookie.sameSite, cookie.path])).toEqual([
    ["qd_session", true, true, "Lax", "/api"],
  ]);

  // One step back is the sign-in page as it was before Telegram: no entry of the history has the fields.
  await page.goBack();
  expect(page.url()).toBe(`${stack.baseURL}/panel/`);
  await page.goForward();
  expect(page.url()).toBe(`${stack.baseURL}/panel/`);

  // The proxy logged the visit without its query string; the application never saw one.
  const proxyLog = serviceLog("proxy");
  expect(proxyLog).toContain('"path":"/api/v1/auth/telegram-login"');
  expect(proxyLog).not.toContain(signature);
  expect(proxyLog).not.toContain("auth_date");
  const apiLog = serviceLog("api");
  expect(apiLog).toContain("/api/v1/auth/telegram-login");
  expect(apiLog).not.toContain(signature);
});

test("signed fields in a link that did not come from this site's own sign-in page are not taken", async ({ page, watch }) => {
  // Somebody else's valid, unused fields, sent as a link in a message: followed, it would sign the
  // reader in to the sender's account.
  const sender = newPerson("Begona");
  const fields = loginWidgetFields(stack.botToken, sender);
  await page.goto(`/panel/?${new URLSearchParams(fields).toString()}`);

  await expect(page.getByRole("heading", { level: 1, name: "Panelga kirish" })).toBeVisible();
  await expect(page.getByRole("alert")).toHaveText("Telegram ma'lumotlari qabul qilinmadi. Kirish tugmasini qayta bosing.");
  expect(page.url()).toBe(`${stack.baseURL}/panel/`);
  // Nothing was sent to the server, and nobody was signed in.
  expect(watch.apiAnswers).toBe(0);
  expect(await page.context().cookies()).toEqual([]);
  expect(sqlValue(`SELECT count(*) FROM app_user WHERE tg_id = ${lit(sender.id)}`)).toBe("0");
});

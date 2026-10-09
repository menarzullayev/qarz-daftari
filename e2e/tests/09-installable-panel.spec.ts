import { expect, goTo, signInOnWeb, test } from "../support/fixtures.ts";
import { stack } from "../support/stack.ts";
import { newPerson } from "../support/telegram.ts";

/**
 * The web panel as an installable application (module B of the expansion of 2026-10-09): its manifest,
 * its service worker, and what it does without a connection, in the real browser through the proxy.
 */

// A browser refuses to fetch a service worker's script from a host whose certificate it cannot verify,
// whatever the page was allowed (`ignoreHTTPSErrors` is about pages). The stack's certificate is
// self-signed, so in the other journeys the panel's worker is never installed; this browser is started
// to accept the certificate everywhere, as a real one accepts a real certificate.
test.use({ launchOptions: { args: ["--ignore-certificate-errors"] } });

test("the panel is installable: its worker keeps the shell and nothing else, and the panel opens without a connection", async ({ page, chat, context }) => {
  const owner = newPerson("Olim");
  await chat.openShop(owner, `Oflayn ${owner.id}`);
  await signInOnWeb(page, owner, "/panel/");
  await expect(page.locator("main dl dd").first()).toHaveText("0 so'm");

  await test.step("the page names a manifest the browser accepts, and a worker controls the panel and nothing above it", async () => {
    await expect(page.locator('link[rel="manifest"]')).toHaveAttribute("href", "/panel/manifest.webmanifest");
    const manifest = await page.request.get("/panel/manifest.webmanifest");
    expect(manifest.headers()["content-type"]).toContain("application/manifest+json");
    expect(await manifest.json()).toMatchObject({ display: "standalone", start_url: "/panel/", scope: "/panel/" });
    const scope = await page.evaluate(async () => (await navigator.serviceWorker.ready).scope);
    expect(scope).toBe(`${stack.baseURL}/panel/`);
    const worker = await page.request.get("/panel/sw.js");
    expect(worker.headers()["cache-control"]).toBe("no-cache");
    expect(worker.headers()["service-worker-allowed"]).toBeUndefined();
  });

  await test.step("what the worker keeps: the panel's page and built files, and nothing a person's data is in", async () => {
    // Opening a screen makes the page ask the API; none of it may end up kept.
    await goTo(page, "/customers");
    await page.waitForLoadState("networkidle");
    const kept = await page.evaluate(async () => {
      const paths: string[] = [];
      for (const name of await caches.keys()) {
        const cache = await caches.open(name);
        for (const request of await cache.keys()) {
          const url = new URL(request.url);
          paths.push(url.pathname + url.search);
        }
      }
      return paths;
    });
    expect(kept).toContain("/panel/");
    expect(kept.length).toBeGreaterThan(3);
    expect(kept.filter((path) => path !== "/panel/" && !/^\/assets\/[A-Za-z0-9._-]+\.(?:js|css)$/.test(path))).toEqual([]);
  });

  await test.step("without a connection the panel still opens, and says that there is none", async () => {
    await context.setOffline(true);
    await page.goto("/panel/");
    await expect(page.getByRole("heading", { level: 1, name: "Panelga kirish" })).toBeVisible();
    await expect(page.getByRole("alert")).toContainText("Internet aloqasi yo'q");
    // The sign-in button needs Telegram's script, which cannot be fetched: it is not offered.
    await expect(page.getByRole("button", { name: /Log in with Telegram/ })).toHaveCount(0);
    await context.setOffline(false);
    await expect(page.getByRole("alert")).toHaveCount(0);
  });

  await test.step("the other entries are not the worker's: nothing controls them", async () => {
    for (const entry of ["/admin/", "/app/", "/k/"]) {
      await page.goto(entry);
      await expect(page.locator("#root")).not.toBeEmpty();
      expect(await page.evaluate(() => navigator.serviceWorker.controller), entry).toBeNull();
      await expect(page.locator('link[rel="manifest"]'), entry).toHaveCount(0);
    }
  });
});

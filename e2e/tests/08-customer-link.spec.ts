import { createHash } from "node:crypto";

import { expect, goTo, signInOnWeb, test } from "../support/fixtures.ts";
import { lit, serviceLog, sql, sqlValue, stack } from "../support/stack.ts";
import { newPerson, personWithId, totpCode } from "../support/telegram.ts";

/**
 * Module B of the expansion of 2026-10-09: a customer's secret read-only link with its page at /k/, in
 * the real browser, through the proxy, under its policies; the `watch` fixture fails the journey on any
 * Content-Security-Policy violation. The installable panel is the next file.
 */

const SWITCH = "customer_links_on";
const NOT_A_LINK = "A".repeat(43);

const switchedOn = () => sql(`SELECT value::text FROM platform_setting WHERE key = ${lit(SWITCH)}`)[0]?.[0] === "true";

/** The next allow-listed identifier nobody has enrolled a second factor for: it can be enrolled once. */
function freshAdministrator() {
  const enrolled = new Set(sql("SELECT u.tg_id::text FROM admin_account a JOIN app_user u ON u.id = a.user_id").map((row) => row[0]));
  const id = stack.adminTgIds.find((candidate) => !enrolled.has(String(candidate)));
  if (id === undefined) {
    throw new Error("every allow-listed administrator of this stack is enrolled already: start a new stack (e2e/stack.sh down, up)");
  }
  return personWithId(id, "Admin");
}

test("a customer without Telegram reads their debt through a secret link, until the shop ends it", async ({ page, chat, secondContext }) => {
  const owner = newPerson("Olim");
  const shopName = `Havola ${owner.id}`;
  const shopId = await chat.openShop(owner, shopName);
  await chat.entry(owner, "Ali 45000");
  const customerId = sqlValue(`SELECT id::text FROM customer WHERE shop_id = ${lit(shopId)}`);
  const title = "Mijoz uchun havola (Telegramsiz)";
  const shares = () =>
    sql(`SELECT encode(token_hash, 'hex'), (revoked_at IS NULL)::text FROM customer_share WHERE shop_id = ${lit(shopId)} ORDER BY created_at`);
  const recorded = () =>
    sql(`SELECT action, actor_kind FROM activity WHERE shop_id = ${lit(shopId)} AND action LIKE 'customer.share%' ORDER BY at, id`);

  // A stack keeps its settings from one run of the suite to the next; a fresh one has the switch off.
  if (!switchedOn()) {
    await test.step("while the switch is off there is nothing: no section for the owner, no route for anyone", async () => {
      await signInOnWeb(page, owner, "/panel/");
      await goTo(page, `/customers/${customerId}`);
      await expect(page.getByRole("region", { name: "Telegramga ulash" })).toBeVisible();
      await page.waitForLoadState("networkidle");
      await expect(page.getByRole("heading", { name: title })).toHaveCount(0);
      await expect(page.getByText("havola (Telegramsiz)")).toHaveCount(0);

      const unknown = await page.request.get("/api/v1/no-such-route");
      for (const path of ["/api/v1/customer-share", `/api/v1/shops/${shopId}/customers/${customerId}/share`, `/api/v1/shops/${shopId}/share-contact`]) {
        const answer = await page.request.get(path, { headers: { "X-Share-Token": NOT_A_LINK } });
        expect(answer.status(), path).toBe(404);
        expect(await answer.json(), path).toEqual(await unknown.json());
      }
      expect(shares()).toEqual([]);
    });

    await test.step("an administrator turns the switch on, with a code of the second factor", async () => {
      const admin = freshAdministrator();
      await signInOnWeb(page, admin, "/admin/");
      await page.getByRole("button", { name: "Maxfiy kalit yaratish" }).click();
      const uri = await page.getByText(/^otpauth:\/\/totp\//).innerText();
      await page.getByLabel("6 xonali kod").fill(totpCode(uri));
      await page.getByRole("button", { name: "Tasdiqlash" }).click();
      await expect(page.getByRole("heading", { level: 1, name: "Do'konlar" })).toBeVisible();

      await goTo(page, "/settings");
      const box = page.getByLabel("Mijoz uchun havola va QR kod (Telegramsiz) yoqilgan");
      await expect(box).not.toBeChecked();
      await box.check();
      // A code is accepted once, and the one of this half-minute opened the session: the next one.
      await page.getByLabel("Autentifikator kodi").fill(totpCode(uri, new Date(Date.now() + 30_000)));
      await page.getByRole("button", { name: "Saqlash" }).click();
      await expect(page.getByText("Sozlamalar saqlandi. O'zgarganlari:")).toBeVisible();
      expect(switchedOn()).toBe(true);
    });
  }

  const link = await test.step("the owner makes the customer's link: shown once, with its QR code, and only its hash is kept", async () => {
    await signInOnWeb(page, owner, "/panel/");
    await goTo(page, `/customers/${customerId}`);
    const section = page.getByRole("region", { name: title });
    await expect(section.getByText("Bu mijoz uchun havola yo'q.")).toBeVisible();
    await section.getByRole("button", { name: "Havola yaratish" }).click();
    const text = await section.getByText(/\/k\/#[A-Za-z0-9_-]{43}$/).innerText();
    await expect(section.getByRole("img", { name: "Havolaning QR kodi" })).toBeVisible();
    await expect(section.getByRole("button", { name: "Chop etish" })).toBeVisible();
    expect(new URL(text).origin).toBe(stack.baseURL);
    const token = new URL(text).hash.slice(1);
    expect(shares()).toEqual([[createHash("sha256").update(token).digest("hex"), "true"]]);
    // The secret is nowhere the page could leave it.
    expect(await page.evaluate(() => JSON.stringify([{ ...window.localStorage }, { ...window.sessionStorage }, window.location.href]))).not.toContain(token);
    return text;
  });
  const token = new URL(link).hash.slice(1);

  // Another browser: nobody is signed in to anything in it.
  const visitor = await secondContext.newPage();
  const asked: string[] = [];
  visitor.on("request", (request) => {
    if (new URL(request.url()).pathname.startsWith("/api/")) {
      asked.push(`${request.method()} ${request.url()} ${request.headers()["cookie"] === undefined ? "no cookie" : "cookie"}`);
    }
  });

  await test.step("whoever holds the link reads the account without signing in, and nothing else", async () => {
    await visitor.goto(link);
    await expect(visitor.getByRole("heading", { level: 1 })).toHaveText("Ali, bu sizning hisobingiz");
    await expect(visitor.locator(".shop__name")).toHaveText(shopName);
    await expect(visitor.locator(".summary__amount")).toHaveText("45 000 so'm");
    await expect(visitor.locator(".entry")).toHaveCount(1);
    await expect(visitor.locator(".entry")).toContainText("Nasiya");
    await expect(visitor.locator(".entry")).toContainText("To'lash muddati:");
    await expect(visitor.getByText("Bu sahifa faqat o'qish uchun.")).toBeVisible();
    // Nothing to press but the two languages, and nothing of the staff's application.
    await expect(visitor.getByRole("button")).toHaveText(["O'zbekcha", "Русский"]);
    await expect(visitor.getByRole("navigation", { name: "Asosiy menyu" })).toHaveCount(0);

    // One request, to one address that holds no secret, without a cookie.
    expect(asked).toEqual([`GET ${stack.baseURL}/api/v1/customer-share no cookie`]);
    expect(await visitor.evaluate(() => document.cookie)).toBe("");
    expect(await visitor.evaluate(() => navigator.serviceWorker.controller)).toBeNull();
    // The secret reached no log: not the proxy's, not the application's.
    expect(serviceLog("proxy")).not.toContain(token);
    expect(serviceLog("api")).not.toContain(token);
    expect(recorded()).toEqual([
      ["customer.share_created", "staff"],
      ["customer.share_opened", "customer"],
    ]);
  });

  await test.step("the page's headers: not indexed, not kept, no referrer, this origin only", async () => {
    const answer = await visitor.request.get("/k/");
    const headers = answer.headers();
    expect(headers["referrer-policy"]).toBe("no-referrer");
    expect(headers["x-robots-tag"]).toContain("noindex");
    expect(headers["cache-control"]).toBe("no-store");
    expect(headers["content-security-policy"]).not.toMatch(/https?:|\*/);
  });

  await test.step("the reader changes the language, and the server is not asked again", async () => {
    await visitor.getByRole("button", { name: "Русский" }).click();
    await expect(visitor.getByRole("heading", { level: 1 })).toHaveText("Ali, это ваш счёт");
    await expect(visitor.locator(".summary__amount")).toHaveText("45 000 сум");
    expect(asked).toHaveLength(1);
  });

  await test.step("a secret nobody was given opens nothing", async () => {
    // A new document: a change of the fragment alone would not load the page again.
    await visitor.goto("about:blank");
    await visitor.goto(`/k/#${NOT_A_LINK}`);
    await expect(visitor.getByRole("alert").getByRole("heading", { level: 1 })).toHaveText("Ссылка не работает");
    await expect(visitor.locator(".summary")).toHaveCount(0);
  });

  await test.step("the shop ends the link, and it stops working at once", async () => {
    const section = page.getByRole("region", { name: title });
    await section.getByRole("button", { name: "Havolani yashirish" }).click();
    await expect(section.getByText(/^Havola amalda:/)).toBeVisible();
    await expect(section.getByText(/^Oxirgi marta ochilgan:/)).toBeVisible();
    await section.getByRole("button", { name: "Havolani bekor qilish" }).click();
    await section.getByRole("button", { name: "Ha, bekor qilish" }).click();
    await expect(section.getByText("Havola bekor qilindi.")).toBeVisible();
    expect(shares().map((row) => row[1])).toEqual(["false"]);

    await visitor.goto("about:blank");
    await visitor.goto(link);
    await expect(visitor.getByRole("alert").getByRole("heading", { level: 1 })).toHaveText("Ссылка не работает");
    await expect(visitor.locator(".summary")).toHaveCount(0);
    expect(recorded().at(-1)).toEqual(["customer.share_revoked", "staff"]);
  });
});

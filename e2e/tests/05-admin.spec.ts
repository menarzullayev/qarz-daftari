import { outbox } from "../support/chat.ts";
import { expect, goTo, openMiniApp, signInOnWeb, test } from "../support/fixtures.ts";
import { BRAND_NAME, lit, sql, sqlValue, stack } from "../support/stack.ts";
import { newPerson, personWithId, totpCode } from "../support/telegram.ts";

/** Words that would tell a stranger what this address is. */
const ADMIN_WORDS = /admin|platforma|boshqaruv|ikkinchi omil|autentifikator|do'konlar|audit/i;

/** The next allow-listed identifier nobody has enrolled a second factor for: it can be enrolled once. */
function freshAdministrator() {
  const enrolled = new Set(sql("SELECT u.tg_id::text FROM admin_account a JOIN app_user u ON u.id = a.user_id").map((row) => row[0]));
  const id = stack.adminTgIds.find((candidate) => !enrolled.has(String(candidate)));
  if (id === undefined) {
    throw new Error("every allow-listed administrator of this stack is enrolled already: start a new stack (e2e/stack.sh down, up)");
  }
  return personWithId(id, "Admin");
}

test("somebody who is not an administrator finds nothing at the administrators' address", async ({ page }) => {
  const stranger = newPerson("Begona");
  await signInOnWeb(page, stranger, "/admin/");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Sahifa topilmadi");
  await expect(page.getByText("Bunday sahifa yo'q yoki sizda unga ruxsat yo'q.")).toBeVisible();
  await expect(page.getByRole("navigation")).toHaveCount(0);
  expect(await page.locator("body").innerText()).not.toMatch(ADMIN_WORDS);
  // Any address under it is the same nothing.
  await goTo(page, "/shops");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Sahifa topilmadi");
  expect(sqlValue(`SELECT count(*) FROM admin_account a JOIN app_user u ON u.id = a.user_id WHERE u.tg_id = ${lit(stranger.id)}`)).toBe("0");
});

test("an administrator enrols the second factor, suspends a shop with a reason, and its staff are refused writes", async ({ page, chat, secondContext }) => {
  const owner = newPerson("Olim");
  const shopName = `Tekshiruv ${owner.id}`;
  const shopId = await chat.openShop(owner, shopName);
  const admin = freshAdministrator();
  const reason = "To'lov bo'yicha tekshiruv";

  // The shop's owner is at work before anything happens to the shop.
  const staff = await secondContext.newPage();
  await openMiniApp(staff, owner);
  await expect(staff.getByRole("heading", { level: 1, name: "Umumiy ko'rinish" })).toBeVisible();

  await test.step("Telegram sign-in leads to the second factor, not into the panel", async () => {
    await signInOnWeb(page, admin, "/admin/");
    await expect(page.getByRole("heading", { level: 1, name: "Ikkinchi omil" })).toBeVisible();
    await expect(page.getByRole("navigation")).toHaveCount(0);
  });

  await test.step("enrolment: the secret is shown once, and the code computed from it opens the session", async () => {
    await page.getByRole("button", { name: "Maxfiy kalit yaratish" }).click();
    const uri = await page.getByText(/^otpauth:\/\/totp\//).innerText();
    expect(new URL(uri).searchParams.get("issuer")).toBe(BRAND_NAME);
    await page.getByLabel("6 xonali kod").fill(totpCode(uri));
    await page.getByRole("button", { name: "Tasdiqlash" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Do'konlar" })).toBeVisible();
    expect(
      sql(`SELECT a.status, (a.confirmed_at IS NOT NULL)::text FROM admin_account a JOIN app_user u ON u.id = a.user_id WHERE u.tg_id = ${lit(admin.id)}`),
    ).toEqual([["active", "true"]]);
    // The secret is nowhere the page could leave it.
    const secret = new URL(uri).searchParams.get("secret") ?? "";
    expect(await page.evaluate(() => JSON.stringify([{ ...window.localStorage }, { ...window.sessionStorage }]))).not.toContain(secret);
    expect(await page.locator("body").innerText()).not.toContain(secret);
  });

  await test.step("the shops list finds the shop", async () => {
    await page.getByLabel("Do'kon nomi bo'yicha qidirish").fill(shopName);
    await page.getByRole("button", { name: "Qidirish" }).click();
    const rows = page.getByRole("table", { name: "Do'konlar" }).getByRole("row");
    await expect(rows).toHaveCount(2);
    await expect(rows.nth(1)).toContainText("Sinov muddati");
    await rows.nth(1).getByRole("link", { name: shopName }).click();
    await expect(page.getByRole("heading", { level: 2, name: shopName })).toBeVisible();
    await expect(page.locator("dt", { hasText: "Do'kon holati" }).locator("xpath=following-sibling::dd[1]")).toHaveText("Ishlamoqda");
  });

  await test.step("suspend it, giving the reason", async () => {
    await page.getByRole("button", { name: "Do'konni to'xtatish" }).click();
    await page.getByLabel("Sabab", { exact: true }).fill(reason);
    await page.getByRole("button", { name: "Davom etish" }).click();
    await expect(page.getByText(`${shopName}: «Do'konni to'xtatish» bajarilsinmi?`)).toBeVisible();
    await expect(page.getByText(`Sabab: ${reason}`)).toBeVisible();
    await page.getByRole("button", { name: "Ha, bajarilsin" }).click();
    const subscription = page.getByRole("region", { name: "Obuna", exact: true });
    await expect(subscription.locator("dt", { hasText: "Obuna holati" }).locator("xpath=following-sibling::dd[1]")).toContainText("To'xtatilgan");
    await expect(subscription.getByRole("button", { name: "To'xtatishni bekor qilish" })).toBeVisible();
    expect(sql(`SELECT state, prior_state FROM subscription WHERE shop_id = ${lit(shopId)}`)).toEqual([["suspended", "trial"]]);
    expect(sql(`SELECT action, reason FROM admin_audit WHERE target_shop = ${lit(shopId)} AND reason IS NOT NULL`)).toEqual([
      ["subscription.suspended", reason],
    ]);
    // The owner would be told, with the reason.
    expect(outbox(owner).at(-1)?.text).toContain(reason);
  });

  await test.step("the shop's staff, already at work, are refused a write, and the screen says why", async () => {
    await goTo(staff, "/customers/new");
    await staff.getByLabel("Ism", { exact: true }).fill("Yangi mijoz");
    await staff.getByRole("button", { name: "Yangi mijoz" }).click();
    await expect(staff.getByRole("alert")).toContainText("to'xtatilgan");
    expect(sqlValue(`SELECT count(*) FROM customer WHERE shop_id = ${lit(shopId)}`)).toBe("0");
  });
});

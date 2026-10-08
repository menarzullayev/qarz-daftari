import { expect, openMiniApp, signInOnWeb, test } from "../support/fixtures.ts";
import { lit, sql } from "../support/stack.ts";
import { newPerson } from "../support/telegram.ts";

const NAV = "Asosiy menyu";

test("a person signed in on a phone and on a desktop signs out everywhere from the phone", async ({ page, chat, secondContext }) => {
  const owner = newPerson("Olim");
  const shopName = `Chiqish ${owner.id}`;
  await chat.openShop(owner, shopName);
  /** The person's sessions, oldest first: the kind, and whether it is still open. */
  const sessions = () =>
    sql(
      `SELECT s.kind, (s.revoked_at IS NULL)::text FROM user_session s JOIN app_user u ON u.id = s.user_id
       WHERE u.tg_id = ${lit(owner.id)} ORDER BY s.created_at, s.id`,
    );

  await test.step("the panel on a desktop", async () => {
    await signInOnWeb(page, owner, "/panel/");
    await expect(page.getByRole("banner").locator("strong")).toHaveText(shopName);
    await expect(page.getByRole("heading", { level: 1, name: "Umumiy ko'rinish" })).toBeVisible();
  });

  const phone = await secondContext.newPage();
  await test.step("and the Mini App on a phone: two sessions of one person", async () => {
    await openMiniApp(phone, owner);
    await expect(phone.getByRole("banner").locator("strong")).toHaveText(shopName);
    expect(sessions()).toEqual([
      ["web", "true"],
      ["webapp", "true"],
    ]);
  });

  await test.step("the button asks first, and nothing has ended yet", async () => {
    await phone.getByRole("button", { name: "Barcha qurilmalarda chiqish" }).click();
    await expect(phone.getByText(/Hisobingiz barcha qurilmalarda yopiladi/)).toBeVisible();
    expect(sessions().map(([, open]) => open)).toEqual(["true", "true"]);
  });

  await test.step("yes: the phone is signed out and says why, and both sessions are ended on the server", async () => {
    await phone.getByRole("button", { name: "Ha, chiqish" }).click();
    await expect(phone.getByRole("heading", { level: 1, name: "Kirish talab qilinadi" })).toBeVisible();
    await expect(phone.getByRole("status")).toHaveText("Barcha qurilmalarda hisobingizdan chiqdingiz.");
    expect(sessions()).toEqual([
      ["web", "false"],
      ["webapp", "false"],
    ]);
  });

  await test.step("the panel, still open on the desktop, is refused at its next request", async () => {
    await page.getByRole("navigation", { name: NAV }).getByRole("link", { name: "Mijozlar" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Panelga kirish" })).toBeVisible();
    await expect(page.getByRole("status")).toHaveText("Sessiya tugadi. Davom etish uchun qayta kiring.");
  });

  await test.step("loading the Mini App again with the same launch does not bring the session back", async () => {
    await phone.reload();
    await expect(phone.getByRole("heading", { level: 1, name: "Kirish talab qilinadi" })).toBeVisible();
    expect(sessions().map(([, open]) => open)).toEqual(["false", "false"]);
  });
});

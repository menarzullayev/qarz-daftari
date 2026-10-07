import { outbox } from "../support/chat.ts";
import { expect, goTo, openMiniApp, signInOnWeb, test } from "../support/fixtures.ts";
import { lit, sql } from "../support/stack.ts";
import { newPerson } from "../support/telegram.ts";

const NAV = "Asosiy menyu";

test("the owner invites a seller, who sees the workspace but not the owner's sections", async ({ page, chat, secondContext }) => {
  const owner = newPerson("Olim");
  const seller = newPerson("Sevara");
  const shopName = `Savdo ${owner.id}`;
  const shopId = await chat.openShop(owner, shopName);

  const token = await test.step("the owner makes an invitation link for a seller in the panel", async () => {
    await signInOnWeb(page, owner, "/panel/");
    await page.getByRole("navigation", { name: NAV }).getByRole("link", { name: "Xodimlar" }).click();
    await expect(page.getByRole("table", { name: "Xodimlar ro'yxati" }).getByRole("row")).toHaveCount(2);
    await page.getByLabel("Yangi xodim roli").selectOption({ label: "Sotuvchi" });
    await page.getByRole("button", { name: "Taklif havolasini yaratish" }).click();
    const invite = page.getByRole("region", { name: "Xodim taklif qilish" });
    await expect(invite.getByText("Sotuvchi uchun taklif havolasi")).toBeVisible();
    const link = await invite.getByText(/^https:\/\/t\.me\//).innerText();
    const match = /^https:\/\/t\.me\/[A-Za-z0-9_]+\?start=(s_[A-Za-z0-9_-]+)$/.exec(link);
    expect(match, `an invitation link, got ${link}`).not.toBeNull();
    await expect(page.getByRole("table", { name: "Ochiq takliflar" }).getByRole("rowheader")).toHaveText(["Sotuvchi"]);
    expect(sql(`SELECT kind, role, status FROM invitation WHERE shop_id = ${lit(shopId)}`)).toEqual([["staff", "seller", "issued"]]);
    return match?.[1] ?? "";
  });

  await test.step("the invited person follows the link: Telegram opens the bot's chat with /start", async () => {
    const answers = await chat.say(seller, `/start ${token}`);
    expect(answers.map((message) => message.text).join("\n")).toContain(shopName);
    expect(
      sql(`SELECT m.role, m.status FROM membership m JOIN app_user u ON u.id = m.user_id WHERE m.shop_id = ${lit(shopId)} AND u.tg_id = ${lit(seller.id)}`),
    ).toEqual([["seller", "active"]]);
    expect(sql(`SELECT status FROM invitation WHERE shop_id = ${lit(shopId)}`)).toEqual([["used"]]);
    // The link works once.
    const again = await chat.say(newPerson("Begona"), `/start ${token}`);
    expect(again).toHaveLength(1);
    expect(sql(`SELECT count(*) FROM membership WHERE shop_id = ${lit(shopId)}`)).toEqual([["2"]]);
    expect(outbox(seller).length).toBeGreaterThan(0);
  });

  await test.step("the seller's Mini App: the shop's workspace, with a seller's sections only", async () => {
    const sellerPage = await secondContext.newPage();
    await openMiniApp(sellerPage, seller);
    await expect(sellerPage.getByRole("banner").locator("strong")).toHaveText(shopName);
    await expect(sellerPage.getByRole("heading", { level: 1, name: "Umumiy ko'rinish" })).toBeVisible();
    await expect(sellerPage.getByRole("navigation", { name: NAV }).getByRole("link")).toHaveText([
      "Umumiy ko'rinish",
      "Mijozlar",
      "Yangi yozuv",
      "Katalog",
    ]);
    for (const path of ["/staff", "/activity", "/subscription", "/shop-settings", "/reports", "/import-export"]) {
      await goTo(sellerPage, path);
      await expect(sellerPage.getByRole("heading", { level: 1 }), path).toHaveText("Sahifa topilmadi");
      await expect(sellerPage.getByText("Bunday sahifa yo'q yoki sizda unga ruxsat yo'q.")).toBeVisible();
    }
    // What a seller may do works: the customers list answers, empty.
    await goTo(sellerPage, "/customers");
    await expect(sellerPage.getByText("Hali mijoz yo'q. Birinchi mijozni qo'shing.")).toBeVisible();
  });

  await test.step("the owner's staff list now shows the seller, and no open invitation", async () => {
    await page.getByRole("navigation", { name: NAV }).getByRole("link", { name: "Umumiy ko'rinish" }).click();
    await page.getByRole("navigation", { name: NAV }).getByRole("link", { name: "Xodimlar" }).click();
    const staff = page.getByRole("table", { name: "Xodimlar ro'yxati" });
    await expect(staff.getByRole("rowheader")).toHaveCount(2);
    await expect(staff.getByRole("rowheader").filter({ hasText: "Sotuvchi" })).toHaveCount(1);
    await expect(page.getByText("Ochiq taklif yo'q.")).toBeVisible();
  });
});

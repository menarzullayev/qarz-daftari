import { outbox } from "../support/chat.ts";
import { inDays, uzDate } from "../support/dates.ts";
import { expect, goTo, openMiniApp, test } from "../support/fixtures.ts";
import { lit, sql, sqlValue } from "../support/stack.ts";
import { newPerson } from "../support/telegram.ts";

test("a customer is linked with consent, sees the debt, and asks to move a date; the shop accepts", async ({ page, chat, secondContext }) => {
  const owner = newPerson("Olim");
  const customer = newPerson("Ali");
  const shopName = `Mahalla ${owner.id}`;
  const shopId = await chat.openShop(owner, shopName);
  await chat.entry(owner, "Ali 45000");
  const customerId = sqlValue(`SELECT id::text FROM customer WHERE shop_id = ${lit(shopId)}`);
  // A shop's default: thirty days from the sale.
  const promised = inDays(30);
  const requested = inDays(40);
  const linksOf = () =>
    sql(
      `SELECT l.status, l.consent_text_v::text, (l.consent_at IS NOT NULL)::text FROM customer_link l JOIN app_user u ON u.id = l.user_id
       WHERE l.customer_id = ${lit(customerId)} AND u.tg_id = ${lit(customer.id)}`,
    );

  const start = await test.step("the shop makes the customer's personal link", async () => {
    await openMiniApp(page, owner);
    await goTo(page, `/customers/${customerId}`);
    const section = page.getByRole("region", { name: "Telegramga ulash" });
    await expect(section.getByText("Mijoz hali Telegramga ulanmagan.")).toBeVisible();
    await section.getByRole("button", { name: "Shaxsiy havola yaratish" }).click();
    const link = await section.getByText(/^https:\/\/t\.me\//).innerText();
    const match = /^https:\/\/t\.me\/[A-Za-z0-9_]+\?start=(c_[A-Za-z0-9_-]+)$/.exec(link);
    expect(match, `a personal link, got ${link}`).not.toBeNull();
    return match?.[1] ?? "";
  });

  await test.step("the customer follows it: the bot says what is stored and asks; nothing is linked before the answer", async () => {
    const asked = await chat.say(customer, `/start ${start}`);
    expect(asked).toHaveLength(1);
    expect(asked[0]?.text).toContain(`${shopName} do'koni`);
    expect(asked[0]?.text).toContain("Rozimisiz?");
    expect(linksOf()).toEqual([]);
    const agree = asked[0]?.buttons.find((button) => button.data.includes(":ok:"));
    expect(agree?.text).toContain("Roziman");
    const answer = await chat.press(customer, agree ?? { text: "", data: "" });
    expect(answer.at(-1)?.text).toContain(`«${shopName}» do'konidagi hisobingizga ulandingiz`);
    expect(linksOf()).toEqual([["active", expect.stringMatching(/^[1-9]/), "true"]]);
  });

  const mine = await secondContext.newPage();
  await test.step("the customer's own page: the shop, the debt, the entry and its promised date", async () => {
    await openMiniApp(mine, customer);
    await expect(mine.getByRole("heading", { level: 1, name: "Mening qarzlarim" })).toBeVisible();
    await expect(mine.getByRole("heading", { level: 2, name: shopName })).toBeVisible();
    await expect(mine.locator(".balance strong")).toHaveText("45 000 so'm");
    const entry = mine.getByRole("region", { name: "Yozuvlar" }).getByRole("listitem");
    await expect(entry).toHaveCount(1);
    await expect(entry).toContainText(`To'lash va'dasi: ${uzDate(promised)}`);
    // Their own payment history (DEC-066): nothing has fallen due yet, and the page says so.
    await expect(mine.getByRole("region", { name: "To'lov tarixingiz" })).toContainText(
      "Hali to'lash muddati kelgan qarzingiz bo'lmagan.",
    );
    // A customer has no staff workspace: no navigation.
    await expect(mine.getByRole("navigation", { name: "Asosiy menyu" })).toHaveCount(0);
  });

  await test.step("the customer asks for a later date", async () => {
    const entry = mine.getByRole("region", { name: "Yozuvlar" }).getByRole("listitem");
    await entry.getByRole("button", { name: "Muddatni kechroq so'rash" }).click();
    await entry.getByLabel("Qaysi kungacha to'laysiz?").fill(requested);
    await entry.getByLabel("Sabab (ixtiyoriy)").fill("Oylik kechikdi");
    await entry.getByRole("button", { name: "So'rovni yuborish" }).click();
    await expect(entry).toContainText(`Muddatni ${uzDate(requested)} ga ko'chirish so'ralgan. Do'kon javobi kutilmoqda.`);
    // Until the shop answers, the date stands.
    await expect(entry).toContainText(`To'lash va'dasi: ${uzDate(promised)}`);
    expect(sql(`SELECT requested_date::text, reason, status FROM date_change_request WHERE shop_id = ${lit(shopId)}`)).toEqual([
      [requested, "Oylik kechikdi", "open"],
    ]);
    expect(sql(`SELECT promised_date::text FROM open_debt WHERE customer_id = ${lit(customerId)}`)).toEqual([[promised]]);
    // The shop's people would be told in the chat.
    expect(outbox(owner).at(-1)?.text).toContain("Ali");
  });

  await test.step("the shop accepts it on the staff side", async () => {
    await goTo(page, "/disputes");
    await page.getByRole("link", { name: "Muddat so'rovlari" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Muddat so'rovlari" })).toBeVisible();
    const row = page.getByRole("listitem").filter({ hasText: "Ali" });
    await expect(row).toContainText(`Hozirgi muddat: ${uzDate(promised)}. So'ralgan muddat: ${uzDate(requested)}.`);
    await expect(row).toContainText("Mijoz sababi: Oylik kechikdi");
    await row.getByRole("button", { name: "Qabul qilish" }).click();
    await row.getByRole("button", { name: "Ha, ko'chirilsin" }).click();
    await expect(page.getByText("Ochiq so'rov yo'q.")).toBeVisible();
    expect(sql(`SELECT status, (decided_by IS NOT NULL)::text FROM date_change_request WHERE shop_id = ${lit(shopId)}`)).toEqual([["accepted", "true"]]);
    expect(sql(`SELECT promised_date::text FROM open_debt WHERE customer_id = ${lit(customerId)}`)).toEqual([[requested]]);
    expect(sql(`SELECT promised_date::text FROM promise WHERE shop_id = ${lit(shopId)} ORDER BY created_at`)).toEqual([[promised], [requested]]);
    expect(outbox(customer).at(-1)?.text).toContain(shopName);
  });

  await test.step("the customer's page shows the new date", async () => {
    await openMiniApp(mine, customer);
    // The entry now carries its dates' history as a list of its own.
    const entry = mine.getByRole("region", { name: "Yozuvlar" }).locator("li.row");
    await expect(entry).toContainText(`To'lash va'dasi: ${uzDate(requested)}`);
    await expect(entry).toContainText(`Do'kon so'rovga rozi bo'ldi: muddat ${uzDate(requested)} ga ko'chirildi.`);
    await expect(mine.locator(".balance strong")).toHaveText("45 000 so'm");
  });
});

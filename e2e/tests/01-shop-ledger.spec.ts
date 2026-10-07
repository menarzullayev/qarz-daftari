import { outbox } from "../support/chat.ts";
import { expect, goTo, openMiniApp, test } from "../support/fixtures.ts";
import { lit, sql, sqlValue } from "../support/stack.ts";
import { newPerson } from "../support/telegram.ts";
import { inDays, uzDate } from "../support/dates.ts";

/** What the ledger holds for one customer: every entry in order, and the balance they add up to. */
function ledger(customerId: string) {
  const entries = sql(
    `SELECT e.kind, e.amount::text, COALESCE(p.promised_date::text, ''), (e.reverses_id IS NOT NULL)::text
     FROM ledger_entry e
     LEFT JOIN LATERAL (SELECT promised_date FROM promise WHERE entry_id = e.id ORDER BY created_at DESC LIMIT 1) p ON true
     WHERE e.customer_id = ${lit(customerId)} ORDER BY e.seq`,
  );
  return entries.map(([kind, amount, promised, reversal]) => ({ kind, amount: Number(amount), promised, reversal: reversal === "true" }));
}

test("a new person opens a shop, sells on credit, takes a payment, and reverses an entry", async ({ page, chat }) => {
  const owner = newPerson("Olim");
  const shopName = `Baraka ${owner.id}`;
  const promised = inDays(13);

  await test.step("somebody Telegram has never shown us opens the Mini App: no shop yet", async () => {
    await openMiniApp(page, owner);
    await expect(page.getByRole("heading", { level: 1, name: "Do'kon yo'q" })).toBeVisible();
    expect(sqlValue(`SELECT count(*) FROM app_user WHERE tg_id = ${lit(owner.id)}`)).toBe("1");
  });

  const shopId = await test.step("they open a shop in the chat, as the screen tells them to", async () => {
    const id = await chat.openShop(owner, shopName);
    expect(outbox(owner).at(-1)?.text).toContain(`«${shopName}» do'koni ochildi`);
    expect(sql(`SELECT role, status FROM membership WHERE shop_id = ${lit(id)}`)).toEqual([["owner", "active"]]);
    return id;
  });

  await test.step("the Mini App now opens on the shop's empty overview", async () => {
    await openMiniApp(page, owner);
    await expect(page.getByRole("banner").locator("strong")).toHaveText(shopName);
    await expect(page.getByRole("heading", { level: 1, name: "Umumiy ko'rinish" })).toBeVisible();
    await expect(page.getByText("Hozircha hech kim qarzdor emas.")).toBeVisible();
  });

  const customerId = await test.step("add a customer", async () => {
    await page.getByRole("navigation", { name: "Asosiy menyu" }).getByRole("link", { name: "Mijozlar" }).click();
    await page.getByRole("link", { name: "Yangi mijoz" }).click();
    await page.getByLabel("Ism", { exact: true }).fill("Ali Valiyev");
    await page.getByRole("button", { name: "Yangi mijoz" }).click();
    await expect(page.getByRole("heading", { level: 2, name: "Ali Valiyev" })).toBeVisible();
    const rows = sql(`SELECT id::text, display_name, status FROM customer WHERE shop_id = ${lit(shopId)}`);
    expect(rows).toEqual([[expect.any(String), "Ali Valiyev", "active"]]);
    expect(page.url()).toContain(`#/customers/${rows[0]?.[0]}`);
    return rows[0]?.[0] ?? "";
  });

  await test.step("a credit sale of 150 000 with a promised date", async () => {
    await page.getByRole("link", { name: "Nasiya yozish" }).click();
    await page.getByLabel("Summa, so'm").fill("150000");
    await page.getByRole("radio", { name: "Sanani tanlash" }).check();
    await page.getByRole("textbox", { name: "Sanani tanlash" }).fill(promised);
    await page.getByRole("button", { name: "Nasiyani yozish" }).click();
    const saved = page.getByRole("status");
    await expect(saved).toContainText("Ali Valiyev: 150 000 so'm nasiya yozildi.");
    await expect(saved).toContainText("Yangi qarz: 150 000 so'm");
    await expect(saved).toContainText(`To'lash va'dasi: ${uzDate(promised)}`);
    expect(ledger(customerId)).toEqual([{ kind: "credit", amount: 150_000, promised, reversal: false }]);
  });

  await test.step("a payment of 50 000", async () => {
    await page.getByRole("link", { name: "Mijoz sahifasi" }).click();
    await page.getByRole("link", { name: "To'lov qabul qilish" }).click();
    await page.getByLabel("Summa, so'm").fill("50000");
    await page.getByRole("button", { name: "To'lovni yozish" }).click();
    const saved = page.getByRole("status");
    await expect(saved).toContainText("Ali Valiyev: 50 000 so'm to'lov qabul qilindi.");
    await expect(saved).toContainText("Yangi qarz: 100 000 so'm");
    expect(ledger(customerId).map((entry) => [entry.kind, entry.amount])).toEqual([
      ["credit", 150_000],
      ["payment", 50_000],
    ]);
  });

  await test.step("the customer's balance and the overview's totals say 100 000", async () => {
    await page.getByRole("link", { name: "Mijoz sahifasi" }).click();
    await expect(page.locator(".balance strong")).toHaveText("100 000 so'm");
    const entries = page.getByRole("region", { name: "Yozuvlar" }).getByRole("listitem");
    await expect(entries).toHaveCount(2);
    await expect(entries.nth(0)).toContainText("50 000 so'm");
    await expect(entries.nth(1)).toContainText("150 000 so'm");
    await expect(entries.nth(1)).toContainText(`To'lash va'dasi: ${uzDate(promised)}`);

    await goTo(page, "/");
    const totals = page.locator("main dl");
    await expect(totals.locator("dd").nth(0)).toHaveText("100 000 so'm");
    await expect(totals.locator("dd").nth(1)).toHaveText("1 ta mijoz");
    await expect(page.getByRole("region", { name: "Qarzdorlar" }).getByRole("link")).toContainText(["100 000 so'm"]);
    expect(sql(`SELECT customer_id::text, remaining::text FROM open_debt WHERE shop_id = ${lit(shopId)}`)).toEqual([[customerId, "100000"]]);
  });

  await test.step("the payment is reversed: the totals follow, and nothing is erased", async () => {
    await page.getByRole("region", { name: "Qarzdorlar" }).getByRole("link", { name: /Ali Valiyev/ }).click();
    const entries = page.getByRole("region", { name: "Yozuvlar" }).getByRole("listitem");
    await entries.filter({ hasText: "To'lov" }).getByRole("button", { name: "Yozuvni bekor qilish" }).click();
    await page.getByRole("button", { name: "Ha, bekor qilinsin" }).click();
    await expect(page.locator(".balance strong")).toHaveText("150 000 so'm");
    expect(ledger(customerId).map((entry) => [entry.kind, entry.amount, entry.reversal])).toEqual([
      ["credit", 150_000, false],
      ["payment", 50_000, false],
      ["reversal", 50_000, true],
    ]);
    await goTo(page, "/");
    await expect(page.locator("main dl dd").nth(0)).toHaveText("150 000 so'm");
    expect(sql(`SELECT remaining::text FROM open_debt WHERE shop_id = ${lit(shopId)}`)).toEqual([["150000"]]);
  });
});

test("the Mini App is loaded again inside Telegram with the same launch data", async ({ page, chat }) => {
  const owner = newPerson("Sardor");
  const shopName = `Qayta ${owner.id}`;
  await chat.openShop(owner, shopName);
  await openMiniApp(page, owner);
  await page.getByRole("navigation", { name: "Asosiy menyu" }).getByRole("link", { name: "Mijozlar" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Mijozlar" })).toBeVisible();

  // Telegram's "Reload Page", or a web view the phone restored: the page loads again and Telegram's
  // bridge hands it the same signed launch data a second time. The server accepts that data once.
  await page.reload();
  await expect(page.getByRole("heading", { level: 1, name: "Mijozlar" })).toBeVisible();
  await expect(page.getByRole("banner").locator("strong")).toHaveText(shopName);
  await expect(page.getByText("Hali mijoz yo'q. Birinchi mijozni qo'shing.")).toBeVisible();
  // One launch, one session: the second load did not sign in again.
  expect(
    sqlValue(`SELECT count(*) FROM user_session s JOIN app_user u ON u.id = s.user_id WHERE u.tg_id = ${lit(owner.id)} AND s.kind = 'webapp'`),
  ).toBe("1");
});

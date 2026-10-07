import { thisMonth, uzDate, inDays } from "../support/dates.ts";
import type { Download } from "@playwright/test";

import { expect, signInOnWeb, test } from "../support/fixtures.ts";
import { lit, sql, waitForRows } from "../support/stack.ts";
import { newPerson } from "../support/telegram.ts";

const NAV = "Asosiy menyu";

test("the panel: customers table, this month's report, and an export the worker writes", async ({ page, chat }) => {
  // The worker writes exports on its 30-second schedule.
  test.setTimeout(100_000);
  const owner = newPerson("Olim");
  const shopName = `Hisob ${owner.id}`;
  const shopId = await chat.openShop(owner, shopName);
  // The book is filled the quick way the product offers: one line in the chat is one entry.
  await chat.entry(owner, "Ali 45000");
  await chat.entry(owner, "Sobir 120000");
  await chat.entry(owner, "Ali -15000");

  await test.step("sign in on a desktop screen: side navigation and the shop's overview", async () => {
    await signInOnWeb(page, owner, "/panel/");
    await expect(page.getByRole("banner").locator("strong")).toHaveText(shopName);
    await expect(page.getByRole("heading", { level: 1, name: "Umumiy ko'rinish" })).toBeVisible();
    await expect(page.locator("main dl dd").nth(0)).toHaveText("150 000 so'm");
    // The address the browser came back to carried Telegram's signed fields; none of it is left.
    expect(new URL(page.url()).search).toBe("");
    expect(page.url()).not.toContain("hash=");
  });

  await test.step("the customers table", async () => {
    await page.getByRole("navigation", { name: NAV }).getByRole("link", { name: "Mijozlar" }).click();
    const table = page.getByRole("table", { name: "Mijozlar ro'yxati" });
    await expect(table.getByRole("row")).toHaveCount(3);
    await expect(table.getByRole("row").filter({ hasText: "Ali" })).toContainText("30 000 so'm");
    await expect(table.getByRole("row").filter({ hasText: "Sobir" })).toContainText("120 000 so'm");
    expect(
      sql(`SELECT c.display_name, COALESCE(sum(d.remaining), 0)::text FROM customer c LEFT JOIN open_debt d ON d.customer_id = c.id
           WHERE c.shop_id = ${lit(shopId)} GROUP BY 1 ORDER BY 1`),
    ).toEqual([
      ["Ali", "30000"],
      ["Sobir", "120000"],
    ]);
  });

  await test.step("the report for the current month", async () => {
    await page.getByRole("navigation", { name: NAV }).getByRole("link", { name: "Hisobotlar" }).click();
    await expect(page.getByRole("button", { name: "Shu oy" })).toHaveAttribute("aria-pressed", "true");
    const today = inDays(0);
    const period = page.getByRole("region", { name: `${uzDate(`${thisMonth()}-01`)} — ${uzDate(today)}` });
    const fact = (term: string) => period.locator("dt", { hasText: term }).locator("xpath=following-sibling::dd[1]");
    await expect(fact("Davr boshidagi qarz")).toHaveText("0 so'm");
    await expect(fact("Davr oxiridagi qarz")).toHaveText("150 000 so'm");
    await expect(fact("Berilgan nasiya")).toHaveText("165 000 so'm");
    await expect(fact("Qaytarilgan (to'lovlar)")).toHaveText("15 000 so'm");
    await expect(fact("Yangi mijozlar")).toHaveText("2 ta mijoz");
    await expect(period.getByText("0 so'm + 165 000 so'm + 0 so'm − 15 000 so'm = 150 000 so'm")).toBeVisible();
    expect(
      sql(`SELECT kind, sum(amount)::text, count(*)::text FROM ledger_entry WHERE shop_id = ${lit(shopId)} GROUP BY kind ORDER BY kind`),
    ).toEqual([
      ["credit", "165000", "2"],
      ["payment", "15000", "1"],
    ]);
  });

  await test.step("an export is asked for, written by the worker, and downloaded as a real .xlsx", async () => {
    await page.getByRole("navigation", { name: NAV }).getByRole("link", { name: "Import va eksport" }).click();
    await page.getByRole("button", { name: "Eksport so'rash" }).click();
    await expect(page.getByRole("status").filter({ hasText: "So'rov qabul qilindi." })).toBeVisible();
    const row = page.getByRole("table", { name: "Oxirgi eksportlar" }).getByRole("row").nth(1);
    await expect(row).toContainText("Siz");

    const job = await waitForRows(
      `SELECT status, COALESCE(error, ''), row_count::text, (file_id IS NOT NULL)::text FROM export_job WHERE shop_id = ${lit(shopId)}`,
      (rows) => rows[0]?.[0] === "done" || rows[0]?.[0] === "failed",
      60_000,
    );
    expect(job).toEqual([["done", "", expect.stringMatching(/^[1-9]\d*$/), "true"]]);
    // The screen learns of it by itself, at its own calm interval.
    await expect(row).toContainText("Tayyor", { timeout: 15_000 });

    await row.getByRole("button", { name: "Yuklab olish havolasini olish" }).click();
    const link = row.getByRole("link", { name: "Faylni yuklab olish" });
    const href = await link.getAttribute("href");
    expect(href).toMatch(/^(https:\/\/127\.0\.0\.1:\d+)?\/files\//);

    // The link needs no session: it is opened here without cookies, as from another program.
    const answer = await page.request.get(href ?? "", { headers: { Cookie: "" } });
    expect(answer.status()).toBe(200);
    expect(answer.headers()["content-disposition"]).toMatch(/^attachment; filename/);
    expect(answer.headers()["content-type"]).toBe("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet");
    expect(answer.headers()["x-request-id"]).toMatch(/^[0-9a-f]{32}$/);
    const file = await answer.body();
    // An .xlsx is a zip archive: local file header signature, and the workbook part named inside.
    expect(file.subarray(0, 4)).toEqual(Buffer.from([0x50, 0x4b, 0x03, 0x04]));
    expect(file.includes(Buffer.from("xl/workbook.xml"))).toBe(true);
    expect(file.includes(Buffer.from("[Content_Types].xml"))).toBe(true);

    // And in the browser: following the link saves a file instead of showing a page.
    // The link opens in a new tab, which the browser turns into a download at once.
    const downloaded = new Promise<Download>((resolve) => {
      page.context().on("page", (opened) => opened.on("download", resolve));
      page.on("download", resolve);
    });
    await link.click();
    expect((await downloaded).suggestedFilename()).toMatch(/\.xlsx$/);
  });
});

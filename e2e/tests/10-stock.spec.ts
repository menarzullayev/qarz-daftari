import { expect, goTo, signInOnWeb, test } from "../support/fixtures.ts";
import { lit, sql, sqlValue, stack } from "../support/stack.ts";
import { newPerson, personWithId, totpCode } from "../support/telegram.ts";

/**
 * Module I of the expansion of 2026-10-09: the stock, a purchase receipt and a supplier's account, in
 * the web panel, in the real browser, through the proxy and under its policies; the `watch` fixture
 * fails the journey on any Content-Security-Policy violation. Before this journey nothing of the
 * stock's screens had been drawn by a browser: it is their proof.
 */

const SWITCH = "stock_on";
const NAV = "Asosiy menyu";
/** A real EAN-13 with a right check digit. */
const EAN = "4006381333931";

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

test("the stock: a receipt on credit from a supplier, a barcode, a sale that takes goods out, and the report", async ({ page, chat }) => {
  // Three sign-ins, one of them an administrator's after a pause for the proxy's sign-in limit.
  test.setTimeout(150_000);
  const owner = newPerson("Olim");
  const shopName = `Ombor ${owner.id}`;
  const shopId = await chat.openShop(owner, shopName);
  // A customer to sell to, made the quick way the product offers.
  await chat.entry(owner, "Ali 45000");
  const customerId = sqlValue(`SELECT id::text FROM customer WHERE shop_id = ${lit(shopId)}`);
  const supplierName = `Baraka ulgurji ${owner.id}`;
  const itemName = `Shakar ${owner.id}`;

  const nav = () => page.getByRole("navigation", { name: NAV });
  /** The value beside a name in a list of facts or figures. */
  const fact = (term: string) => page.locator("dt", { hasText: term }).locator("xpath=following-sibling::dd[1]");
  const movements = () =>
    sql(`SELECT kind, qty::float8::text, on_hand_after::float8::text FROM stock_movement WHERE shop_id = ${lit(shopId)} ORDER BY item_seq`);
  const onHand = () => sql(`SELECT on_hand::float8::text, cost_value::text FROM stock_level WHERE shop_id = ${lit(shopId)}`);

  // A stack keeps its settings from one run of the suite to the next; a fresh one has the switch off.
  if (!switchedOn()) {
    await test.step("while the switch is off there is nothing: no section for the owner, no route for anyone", async () => {
      await signInOnWeb(page, owner, "/panel/");
      await expect(page.getByRole("heading", { level: 1, name: "Umumiy ko'rinish" })).toBeVisible();
      await page.waitForLoadState("networkidle");
      await expect(nav().getByRole("link", { name: "Katalog", exact: true })).toBeVisible();
      await expect(nav().getByRole("link", { name: "Ombor", exact: true })).toHaveCount(0);
      await expect(nav().getByRole("link", { name: "Ombor hujjatlari", exact: true })).toHaveCount(0);
      await expect(nav().getByRole("link", { name: "Ta'minotchilar", exact: true })).toHaveCount(0);
      // The stock's address is an unknown route of the application too.
      await goTo(page, "/stock");
      await expect(page.getByRole("heading", { level: 1, name: "Sahifa topilmadi" })).toBeVisible();

      const unknown = await page.request.get("/api/v1/no-such-route");
      for (const path of [`/api/v1/shops/${shopId}/stock/settings`, `/api/v1/shops/${shopId}/stock/items`, `/api/v1/shops/${shopId}/suppliers`]) {
        const answer = await page.request.get(path);
        expect(answer.status(), path).toBe(404);
        expect(await answer.json(), path).toEqual(await unknown.json());
      }
    });

    await test.step("an administrator turns the switch on, with a code of the second factor", async () => {
      const admin = freshAdministrator();
      // Signing in, the administrators' status, enrolling and passing the factor all count against the
      // proxy's sign-in limit by address (30 a minute, burst 10), and the journeys before this one have
      // used most of it from this address. Twenty seconds refill the burst.
      await page.waitForTimeout(20_000);
      await signInOnWeb(page, admin, "/admin/");
      await page.getByRole("button", { name: "Maxfiy kalit yaratish" }).click();
      const uri = await page.getByText(/^otpauth:\/\/totp\//).innerText();
      await page.getByLabel("6 xonali kod").fill(totpCode(uri));
      await page.getByRole("button", { name: "Tasdiqlash" }).click();
      await expect(page.getByRole("heading", { level: 1, name: "Do'konlar" })).toBeVisible();

      await goTo(page, "/settings");
      const box = page.getByLabel("Ombor, kirim va ta'minotchilar yoqilgan");
      await expect(box).not.toBeChecked();
      await box.check();
      // A code is accepted once, and the one of this half-minute opened the session: the next one.
      await page.getByLabel("Autentifikator kodi").fill(totpCode(uri, new Date(Date.now() + 30_000)));
      await page.getByRole("button", { name: "Saqlash" }).click();
      await expect(page.getByText("Sozlamalar saqlandi. O'zgarganlari:")).toBeVisible();
      expect(switchedOn()).toBe(true);
    });
  }

  await test.step("with the switch on the owner has the stock, its documents and the suppliers", async () => {
    // Signing in again reads the person's shops again, and with them what the platform has switched on.
    await signInOnWeb(page, owner, "/panel/");
    await expect(nav().getByRole("link", { name: "Ombor", exact: true })).toBeVisible();
    await expect(nav().getByRole("link", { name: "Ombor hujjatlari", exact: true })).toBeVisible();
    await expect(nav().getByRole("link", { name: "Ta'minotchilar", exact: true })).toBeVisible();
    const settings = await page.request.get(`/api/v1/shops/${shopId}/stock/settings`);
    expect(settings.status()).toBe(200);
  });

  await test.step("the owner adds a supplier", async () => {
    await nav().getByRole("link", { name: "Ta'minotchilar", exact: true }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Ta'minotchilar" })).toBeVisible();
    await page.getByRole("button", { name: "Ta'minotchi qo'shish" }).click();
    await page.getByRole("textbox", { name: "Ta'minotchi", exact: true }).fill(supplierName);
    await page.getByRole("button", { name: "Saqlash" }).click();
    const table = page.getByRole("table", { name: "Ta'minotchilar" });
    await expect(table.getByRole("link", { name: supplierName })).toBeVisible();
    await expect(table.getByRole("row").filter({ hasText: supplierName })).toContainText("Qarz yo'q");
    expect(sql(`SELECT name, status FROM supplier WHERE shop_id = ${lit(shopId)}`)).toEqual([[supplierName, "active"]]);
  });

  await test.step("a receipt on credit with a good met for the first time: ten kilograms at 10 000", async () => {
    await nav().getByRole("link", { name: "Ombor", exact: true }).click();
    await page.getByRole("link", { name: "Tez kirim" }).click();
    await expect(page.getByRole("heading", { level: 2, name: "Kirim" })).toBeVisible();
    await page.getByLabel("Ta'minotchi", { exact: true }).selectOption({ label: supplierName });

    await page.getByRole("button", { name: "Yangi tovar qo'shish" }).click();
    await page.getByLabel("Nomi", { exact: true }).fill(itemName);
    await page.getByLabel("O'lchov birligi").selectOption("kg");
    await page.getByLabel("Sotish narxi, so'm").fill("15000");
    await page.getByLabel("Shtrix-kod (ixtiyoriy)").fill(EAN);
    await page.getByRole("button", { name: "Kirimga qo'shish" }).click();

    await page.getByLabel("Miqdor (kg)").fill("10");
    await page.getByLabel("Bir birlik narxi").fill("10000");
    await page.getByLabel("Qarzga olindi").check();
    await page.getByRole("button", { name: "O'tkazish" }).click();

    await expect(page.getByRole("status").filter({ hasText: /Kirim № \d+ o'tkazildi\./ })).toBeVisible();
    await expect(fact("Jami")).toHaveText("100 000 so'm");
    await expect(fact("Ta'minotchiga qarz bo'lib qoldi")).toHaveText("100 000 so'm");

    // The books: one movement in, the level it left, what is owed, and nothing that does not add up.
    expect(movements()).toEqual([["receipt", "10", "10"]]);
    expect(onHand()).toEqual([["10", "100000"]]);
    expect(sql(`SELECT kind, status, total::text, paid::text FROM stock_document WHERE shop_id = ${lit(shopId)}`)).toEqual([
      ["receipt", "posted", "100000", "0"],
    ]);
    expect(sql(`SELECT currency, balance::text FROM supplier_balance WHERE shop_id = ${lit(shopId)}`)).toEqual([["UZS", "100000"]]);
    expect(sql(`SELECT code FROM catalog_barcode WHERE shop_id = ${lit(shopId)}`)).toEqual([[EAN]]);
    expect(sqlValue(`SELECT count(*)::text FROM stock_level_mismatches(${lit(shopId)}::uuid)`)).toBe("0");
  });

  await test.step("the stock shows the item with what is on hand and, to the owner, what it cost", async () => {
    await nav().getByRole("link", { name: "Ombor", exact: true }).click();
    const table = page.getByRole("table", { name: "Ombordagi tovarlar" });
    await expect(table.getByRole("columnheader", { name: "O'rtacha tannarx" })).toBeVisible();
    const row = table.getByRole("row").filter({ hasText: itemName });
    await expect(row).toContainText("10 kg");
    await expect(row).toContainText("15 000 so'm");
    await expect(row).toContainText("10 000 so'm");
    await expect(row).toContainText("100 000 so'm");
  });

  await test.step("the supplier's account shows what the shop owes", async () => {
    await nav().getByRole("link", { name: "Ta'minotchilar", exact: true }).click();
    const row = page.getByRole("table", { name: "Ta'minotchilar" }).getByRole("row").filter({ hasText: supplierName });
    await expect(row).toContainText("Qarzimiz: 100 000 so'm");
  });

  await test.step("a barcode typed fast and ended with Enter, as a scanner does, finds the item", async () => {
    await nav().getByRole("link", { name: "Ombor", exact: true }).click();
    const search = page.getByLabel("Shtrix-kodni skanerlang yoki nom yozing");
    await search.click();
    await search.pressSequentially(EAN, { delay: 5 });
    await search.press("Enter");
    const found = page.getByRole("status").filter({ hasText: itemName });
    await expect(found.getByRole("link", { name: itemName })).toBeVisible();
    await expect(found).toContainText("10 kg");
  });

  await test.step("a sale on credit with a goods line takes two and a half kilograms out", async () => {
    await goTo(page, `/customers/${customerId}/credit`);
    await page.getByRole("button", { name: "Tovarlar bilan yozish" }).click();
    await page.getByRole("list", { name: "Katalogdagi tovarlar" }).getByRole("button", { name: itemName }).click();
    await page.getByLabel(`${itemName}: miqdor`).fill("2,5");
    await page.getByRole("button", { name: "Nasiyani yozish" }).click();
    // 2.5 kg at 15 000 so'm.
    await expect(page.getByRole("status").filter({ hasText: /37\s500 so'm nasiya yozildi\./ })).toBeVisible();

    expect(movements()).toEqual([
      ["receipt", "10", "10"],
      ["sale", "-2.5", "7.5"],
    ]);
    expect(onHand()).toEqual([["7.5", "75000"]]);
    expect(sqlValue(`SELECT count(*)::text FROM stock_level_mismatches(${lit(shopId)}::uuid)`)).toBe("0");

    await nav().getByRole("link", { name: "Ombor", exact: true }).click();
    const row = page.getByRole("table", { name: "Ombordagi tovarlar" }).getByRole("row").filter({ hasText: itemName });
    await expect(row).toContainText("7,5 kg");
  });

  await test.step("the report values what is left at cost", async () => {
    await page.getByRole("link", { name: "Ombor hisoboti" }).click();
    await expect(fact("Ombor tannarxi, so'mda olinganlari")).toHaveText("75 000 so'm");
    await expect(fact("Hisobdagi tovarlar")).toHaveText("1");
    // 7.5 kg at the selling price of 15 000 so'm.
    await expect(fact("Sotish narxida qiymati")).toHaveText("112 500 so'm");
  });

  await test.step("the proxy lets the staff's page use the camera, and no page outside the staff's", async () => {
    const panel = (await page.request.get("/panel/")).headers()["permissions-policy"] ?? "";
    expect(panel).toContain("camera=(self)");
    expect(panel).toContain("microphone=()");
    const customerPage = (await page.request.get("/k/")).headers()["permissions-policy"] ?? "";
    expect(customerPage).toContain("camera=()");
    expect(customerPage).not.toContain("camera=(self)");
  });
});

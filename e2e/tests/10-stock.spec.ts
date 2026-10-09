import { DESKTOP, expect, expectNoSidewaysScroll, goTo, PHONE, signInOnWeb, TABLET, test } from "../support/fixtures.ts";
import { lit, sql, sqlValue, stack } from "../support/stack.ts";
import { newPerson, personWithId, totpCode } from "../support/telegram.ts";

/**
 * Module I of the expansion of 2026-10-09: the stock, a purchase receipt and a supplier's account, in
 * the web panel, in the real browser, through the proxy and under its policies; the `watch` fixture
 * fails the journey on any Content-Security-Policy violation. Before this journey nothing of the
 * stock's screens had been drawn by a browser: it is their proof.
 *
 * It ends with a sale for cash, without a customer: the goods leave the stock, the money is in the cash
 * book, and the sale taken back undoes both. For that the cash book is switched on as well.
 */

const SWITCH = "stock_on";
/** The cash book's switch: with it on, a sale for cash is also an entry of income. */
const CASH_BOOK = "cash_book_on";
const SWITCH_LABELS = [
  [SWITCH, "Ombor, kirim va ta'minotchilar yoqilgan"],
  [CASH_BOOK, "Kassa (kirim va chiqim daftari) yoqilgan"],
] as const;
const NAV = "Asosiy menyu";
/** A real EAN-13 with a right check digit. */
const EAN = "4006381333931";

const isOn = (key: string) => sql(`SELECT value::text FROM platform_setting WHERE key = ${lit(key)}`)[0]?.[0] === "true";
const switchedOn = () => isOn(SWITCH);

/** The next allow-listed identifier nobody has enrolled a second factor for: it can be enrolled once. */
function freshAdministrator() {
  const enrolled = new Set(sql("SELECT u.tg_id::text FROM admin_account a JOIN app_user u ON u.id = a.user_id").map((row) => row[0]));
  const id = stack.adminTgIds.find((candidate) => !enrolled.has(String(candidate)));
  if (id === undefined) {
    throw new Error("every allow-listed administrator of this stack is enrolled already: start a new stack (e2e/stack.sh down, up)");
  }
  return personWithId(id, "Admin");
}

test("the stock: a receipt on credit from a supplier, a barcode, a sale that takes goods out, the report, and a sale for cash", async ({ page, chat }) => {
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
      for (const path of [
        `/api/v1/shops/${shopId}/stock/settings`,
        `/api/v1/shops/${shopId}/stock/items`,
        `/api/v1/shops/${shopId}/stock/sales`,
        `/api/v1/shops/${shopId}/suppliers`,
      ]) {
        const answer = await page.request.get(path);
        expect(answer.status(), path).toBe(404);
        expect(await answer.json(), path).toEqual(await unknown.json());
      }
    });
  }

  // The stock's switch and the cash book's: both off on a fresh stack; on one that an earlier run of the
  // suite left, whichever is still off.
  const off = SWITCH_LABELS.filter(([key]) => !isOn(key));
  if (off.length > 0) {
    await test.step("an administrator turns the stock on, and the cash book with it, with a code of the second factor", async () => {
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
      for (const [, label] of off) {
        const box = page.getByLabel(label);
        await expect(box).not.toBeChecked();
        await box.check();
      }
      // A code is accepted once, and the one of this half-minute opened the session: the next one.
      await page.getByLabel("Autentifikator kodi").fill(totpCode(uri, new Date(Date.now() + 30_000)));
      await page.getByRole("button", { name: "Saqlash" }).click();
      await expect(page.getByText("Sozlamalar saqlandi. O'zgarganlari:")).toBeVisible();
      expect(SWITCH_LABELS.map(([key]) => isOn(key))).toEqual([true, true]);
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
    // The supplier is searched for by name among all of them, not picked from a first page: typed into
    // the field, found by the server, and chosen from the list.
    const whose = page.getByRole("combobox", { name: "Ta'minotchi", exact: true });
    await whose.fill(supplierName);
    await page.getByRole("listbox", { name: "Ta'minotchi" }).getByRole("option", { name: supplierName }).click();
    await expect(whose).toHaveValue(supplierName);

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

  const cashBook = () =>
    sql(
      `SELECT e.direction, e.method, e.amount::text, c.name, e.note, (e.cancelled_at IS NOT NULL)::text
         FROM cash_entry e JOIN cash_category c ON c.id = e.category_id WHERE e.shop_id = ${lit(shopId)} ORDER BY e.created_at`,
    );

  await test.step("a sale for cash, without a customer, takes a kilogram and a half out and puts its money in the cash book", async () => {
    await nav().getByRole("link", { name: "Ombor", exact: true }).click();
    await page.getByRole("link", { name: "Naqd savdo", exact: true }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Naqd savdo" })).toBeVisible();
    // The barcode as a scanner types it: the item is a line at once, one of it at its own price.
    const find = page.getByLabel("Tovar: shtrix-kodni skanerlang yoki nom yozing");
    await find.click();
    await find.pressSequentially(EAN, { delay: 5 });
    await find.press("Enter");
    const qty = page.getByLabel(`«${itemName}»: miqdor (kg)`);
    await expect(qty).toHaveValue("1");
    await expect(page.getByLabel(`«${itemName}»: bir birlik narxi, so'm`)).toHaveValue("15000");
    // The same code again adds to the line: a tenth of a kilogram for a weighed good.
    await find.pressSequentially(EAN, { delay: 5 });
    await find.press("Enter");
    await expect(qty).toHaveValue("1,1");
    await page.getByRole("button", { name: `«${itemName}»: ko'paytirish` }).click();
    await expect(qty).toHaveValue("1,2");
    await qty.fill("1,5");
    await expect(page.locator(".sale-total")).toHaveText("Jami 22 500 so'm");
    // The shop keeps a cash book, and the form says where the money goes.
    await expect(page.getByText("Savdo kassaga kirim bo'lib yoziladi.")).toBeVisible();

    // The form with a line in it is no wider than a phone's, a tablet's or a desktop's window.
    const window = page.viewportSize();
    for (const [name, size] of [["phone", PHONE], ["tablet", TABLET], ["desktop", DESKTOP]] as const) {
      await page.setViewportSize(size);
      await expectNoSidewaysScroll(page, `/panel/#/stock/sale with a line at ${name} width (${size.width}px)`);
    }
    if (window) {
      await page.setViewportSize(window);
    }

    await page.getByRole("button", { name: "Sotish" }).click();
    await expect(page.getByRole("status").filter({ hasText: "Naqd savdo № 1 yozildi." })).toBeVisible();
    await expect(page.getByRole("status").filter({ hasText: "Naqd savdo № 1 yozildi." })).toContainText("22 500 so'm");
    await expect(page.getByText("Kassaga kirim bo'lib yozildi.")).toBeVisible();
    // The owner sees what the goods cost: a kilogram and a half bought at 10 000.
    await expect(fact("Tannarx")).toHaveText("15 000 so'm");
    await expect(fact("Foyda")).toHaveText("7 500 so'm");
    await expect(fact("Kim sotdi")).toHaveText("Men");

    expect(movements()).toEqual([
      ["receipt", "10", "10"],
      ["sale", "-2.5", "7.5"],
      ["sale", "-1.5", "6"],
    ]);
    expect(onHand()).toEqual([["6", "60000"]]);
    expect(sql(`SELECT kind, status, number::text, total::text, paid::text, method FROM stock_document WHERE shop_id = ${lit(shopId)} AND kind = 'sale'`)).toEqual([
      ["sale", "posted", "1", "22500", "22500", "cash"],
    ]);
    // The money: one entry of income, under the stock's own category, that says which sale it is.
    expect(cashBook()).toEqual([["income", "cash", "22500", "Ombor: naqd savdo", "Naqd savdo № 1", "false"]]);
    // No customer and no debt: the customer's account is what the sale on credit left.
    expect(sqlValue(`SELECT count(*)::text FROM ledger_entry WHERE shop_id = ${lit(shopId)}`)).toBe("2");
    expect(sqlValue(`SELECT count(*)::text FROM stock_level_mismatches(${lit(shopId)}::uuid)`)).toBe("0");
  });

  await test.step("the sale is in the day's list, in the cash book, among the item's movements and in the report", async () => {
    await page.getByRole("link", { name: "Naqd savdolar", exact: true }).click();
    const list = page.getByRole("table", { name: "Naqd savdolar" });
    const row = list.getByRole("row").filter({ hasText: "Naqd savdo № 1" });
    await expect(row).toContainText(`${itemName} 1,5 kg`);
    await expect(row).toContainText("22 500 so'm");
    await expect(row).toContainText("Men");
    await expect(fact("Savdolar soni")).toHaveText("1");
    await expect(fact("Jami tushum")).toHaveText("22 500 so'm");

    await nav().getByRole("link", { name: "Kassa", exact: true }).click();
    const entries = page.getByRole("list", { name: "Yozuvlar" }).or(page.getByRole("table", { name: "Yozuvlar" }));
    await expect(entries).toContainText("Naqd savdo № 1");
    await expect(entries).toContainText("Ombor: naqd savdo");
    await expect(entries).toContainText("22 500 so'm");

    // An item's movements name the sale and lead to its own page, not to the documents'.
    await nav().getByRole("link", { name: "Ombor", exact: true }).click();
    await page.getByRole("table", { name: "Ombordagi tovarlar" }).getByRole("link", { name: itemName }).click();
    const moved = page.getByRole("table", { name: "Harakatlar" }).getByRole("row").filter({ hasText: "Naqd savdo № 1" });
    await expect(moved).toContainText("-1,5 kg");
    await moved.getByRole("link", { name: "Naqd savdo № 1" }).click();
    await expect(page.getByRole("heading", { level: 2, name: /Naqd savdo № 1/ })).toBeVisible();

    await goTo(page, "/stock/report");
    // On credit and for cash together: 2.5 + 1.5 kg for 37 500 + 22 500, bought at 10 000 a kilogram.
    const sold = page.getByRole("table", { name: "30 kun ichida sotilganlar" }).getByRole("row").filter({ hasText: itemName });
    await expect(sold.getByRole("cell")).toHaveText(["4 kg", "60 000 so'm", "40 000 so'm", "20 000 so'm", "1,5 kg", "22 500 so'm"]);
    await expect(fact("Savdolar soni")).toHaveText("1");
    await expect(fact("Jami tushum")).toHaveText("22 500 so'm");
  });

  await test.step("the sale taken back, with a reason: the goods are in the stock again and the cash entry is cancelled", async () => {
    await goTo(page, "/stock/sales");
    await page.getByRole("table", { name: "Naqd savdolar" }).getByRole("link", { name: "Naqd savdo № 1" }).click();
    await page.getByRole("button", { name: "Savdoni bekor qilish" }).click();
    const form = page.getByRole("group", { name: "Nima uchun bekor qilinmoqda?" });
    // No reason, no request.
    await form.getByRole("button", { name: "Savdoni bekor qilish" }).click();
    await expect(page.getByText("Sababni yozing: 1 dan 200 tagacha belgi.")).toBeVisible();
    expect(sqlValue(`SELECT status FROM stock_document WHERE shop_id = ${lit(shopId)} AND kind = 'sale'`)).toBe("posted");
    await form.getByRole("textbox", { name: "Nima uchun bekor qilinmoqda?" }).fill("xato urilgan");
    await form.getByRole("button", { name: "Savdoni bekor qilish" }).click();
    await expect(page.getByRole("status").filter({ hasText: "Savdo bekor qilindi." })).toBeVisible();
    await expect(page.getByRole("heading", { level: 2, name: /Naqd savdo № 1/ })).toContainText("Bekor qilingan");
    await expect(page.getByRole("button", { name: "Savdoni bekor qilish" })).toHaveCount(0);

    expect(onHand()).toEqual([["7.5", "75000"]]);
    expect(sql(`SELECT status, cancel_reason FROM stock_document WHERE shop_id = ${lit(shopId)} AND kind = 'sale'`)).toEqual([["cancelled", "xato urilgan"]]);
    expect(cashBook()).toEqual([["income", "cash", "22500", "Ombor: naqd savdo", "Naqd savdo № 1", "true"]]);
    expect(sqlValue(`SELECT count(*)::text FROM stock_level_mismatches(${lit(shopId)}::uuid)`)).toBe("0");
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

import { expect, goTo, signInOnWeb, test } from "../support/fixtures.ts";
import { lit, sql, sqlValue, stack } from "../support/stack.ts";
import { newPerson, personWithId, totpCode } from "../support/telegram.ts";

/**
 * Module J of the expansion of 2026-10-09: the network between shops, in the web panel, in two real
 * browsers at once, through the proxy and under its policies. Two shops link by an invitation code; the
 * buyer orders, the supplier accepts and issues a delivery note, the buyer confirms it, and BOTH shops'
 * books take it in that one step; a payment is recorded by one side and confirmed by the other; a third
 * shop sees nothing of it. The screens were written against a described contract: this journey is the
 * proof that they and the server mean the same thing.
 */

const NAV = "Asosiy menyu";
const SWITCHES = [
  ["stock_on", "Ombor, kirim va ta'minotchilar yoqilgan"],
  ["network_on", "Do'konlar orasidagi tarmoq (hamkorlar, buyurtmalar, yuk xatlari) yoqilgan"],
] as const;
const CONFIRM = "Tasdiqlash: tovar omborga kirim qilinadi, ta'minotchi daftariga nasiya savdo yoziladi";

const isOn = (key: string) => sql(`SELECT value::text FROM platform_setting WHERE key = ${lit(key)}`)[0]?.[0] === "true";

/** The next allow-listed identifier nobody has enrolled a second factor for: it can be enrolled once. */
function freshAdministrator() {
  const enrolled = new Set(sql("SELECT u.tg_id::text FROM admin_account a JOIN app_user u ON u.id = a.user_id").map((row) => row[0]));
  const id = stack.adminTgIds.find((candidate) => !enrolled.has(String(candidate)));
  if (id === undefined) {
    throw new Error("every allow-listed administrator of this stack is enrolled already: start a new stack (e2e/stack.sh down, up)");
  }
  return personWithId(id, "Admin");
}

test("the network: two shops link by a code, an order is delivered and confirmed into both books, a payment is confirmed, and a third shop sees nothing", async ({
  page,
  secondContext,
  chat,
}) => {
  test.setTimeout(150_000);
  const buyer = newPerson("Xaridor");
  const supplier = newPerson("Taminotchi");
  const stranger = newPerson("Begona");
  const buyerShop = await chat.openShop(buyer, `Ziyo market ${buyer.id}`);
  const supplierShop = await chat.openShop(supplier, `Baraka ulgurji ${supplier.id}`);
  const strangerShop = await chat.openShop(stranger, `Uchinchi ${stranger.id}`);
  const goodsName = `Guruch ${supplier.id}`;
  const other = await secondContext.newPage();

  const nav = (of: typeof page) => of.getByRole("navigation", { name: NAV });
  const rowsOf = (table: string, shop: string) => sqlValue(`SELECT count(*)::text FROM ${table} WHERE shop_id = ${lit(shop)}`);

  if (SWITCHES.some(([key]) => !isOn(key))) {
    await test.step("an administrator turns the network on (it needs the stock's switch as well)", async () => {
      const admin = freshAdministrator();
      // The proxy's sign-in limit by address: the journeys before this one have used most of its burst.
      await page.waitForTimeout(15_000);
      await signInOnWeb(page, admin, "/admin/");
      await page.getByRole("button", { name: "Maxfiy kalit yaratish" }).click();
      const uri = await page.getByText(/^otpauth:\/\/totp\//).innerText();
      await page.getByLabel("6 xonali kod").fill(totpCode(uri));
      await page.getByRole("button", { name: "Tasdiqlash" }).click();
      await expect(page.getByRole("heading", { level: 1, name: "Do'konlar" })).toBeVisible();
      await goTo(page, "/settings");
      for (const [, label] of SWITCHES) {
        await page.getByLabel(label).check();
      }
      // A code is accepted once, and the one of this half-minute opened the session: the next one.
      await page.getByLabel("Autentifikator kodi").fill(totpCode(uri, new Date(Date.now() + 30_000)));
      await page.getByRole("button", { name: "Saqlash" }).click();
      await expect(page.getByText("Sozlamalar saqlandi. O'zgarganlari:")).toBeVisible();
      expect(SWITCHES.map(([key]) => isOn(key))).toEqual([true, true]);
    });
  }

  await test.step("the supplier takes goods into its stock, and makes an invitation code", async () => {
    await signInOnWeb(other, supplier, "/panel/");
    await nav(other).getByRole("link", { name: "Ombor", exact: true }).click();
    await other.getByRole("link", { name: "Tez kirim" }).click();
    await other.getByRole("button", { name: "Yangi tovar qo'shish" }).click();
    await other.getByLabel("Nomi", { exact: true }).fill(goodsName);
    await other.getByLabel("O'lchov birligi").selectOption("kg");
    await other.getByLabel("Sotish narxi, so'm").fill("12000");
    await other.getByRole("button", { name: "Kirimga qo'shish" }).click();
    await other.getByLabel("Miqdor (kg)").fill("50");
    await other.getByLabel("Bir birlik narxi").fill("9000");
    await other.getByRole("button", { name: "O'tkazish" }).click();
    await expect(other.getByRole("status").filter({ hasText: /Kirim № \d+ o'tkazildi\./ })).toBeVisible();

    await nav(other).getByRole("link", { name: "Hamkorlar", exact: true }).click();
    await expect(other.getByText("Hali hech bir do'kon bilan ulanmagansiz.", { exact: false })).toBeVisible();
    await other.getByRole("button", { name: "Taklif kodi yaratish" }).click();
    await other.getByRole("button", { name: "Biz ta'minotchimiz" }).click();
    await other.getByRole("button", { name: "Kod yaratish" }).click();
    await expect(other.getByText("Taklif kodi tayyor")).toBeVisible();
  });
  const code = (await other.locator(".net-code").innerText()).trim();
  // Only the code's hash is kept.
  expect(sqlValue(`SELECT count(*)::text FROM network_invite WHERE shop_id = ${lit(supplierShop)} AND used_at IS NULL`)).toBe("1");

  await test.step("the buyer presents the code, and the supplier accepts the request", async () => {
    await signInOnWeb(page, buyer, "/panel/");
    await nav(page).getByRole("link", { name: "Hamkorlar", exact: true }).click();
    await page.getByRole("button", { name: "Kod bilan ulanish" }).click();
    await page.getByLabel("Hamkor bergan kod").fill(code);
    await page.getByRole("button", { name: "So'rov yuborish" }).click();
    await expect(page.getByText("So'rov yuborildi.", { exact: false })).toBeVisible();

    // The supplier's overview is read again: the request is there to answer.
    await goTo(other, "/network/payments");
    await goTo(other, "/network");
    await other.getByRole("button", { name: "Qabul qilish", exact: true }).click();
    await other.getByRole("group", { name: "Qabul qilish" }).getByRole("button", { name: "Qabul qilish", exact: true }).click();
    await expect
      .poll(() => sql(`SELECT shop_id::text, role, state FROM network_link WHERE shop_id IN (${lit(buyerShop)}, ${lit(supplierShop)}) ORDER BY role`))
      .toEqual([
        [buyerShop, "buyer", "active"],
        [supplierShop, "supplier", "active"],
      ]);
  });

  await test.step("the buyer writes an order for ten kilograms and sends it", async () => {
    await goTo(page, "/network/orders/new");
    await page.getByLabel("1-qator: tovar nomi").fill(goodsName);
    await page.getByLabel("Miqdor", { exact: true }).fill("10");
    await page.getByLabel("Birlik", { exact: true }).selectOption("kg");
    await page.getByRole("button", { name: "Ta'minotchiga yuborish" }).click();
    await expect(page.getByRole("heading", { name: "Buyurtma № 1" })).toBeVisible();
    // Saved and then sent: one order, and no draft left behind.
    expect(rowsOf("network_order", buyerShop)).toBe("1");
    expect(rowsOf("network_order_draft", buyerShop)).toBe("0");
  });

  await test.step("the supplier accepts it at 12 000 a kilogram from its own stock, and issues the delivery note", async () => {
    await goTo(other, "/network/orders/in");
    await other.getByRole("link", { name: "Buyurtma № 1" }).click();
    await other.getByRole("button", { name: "Buyurtmani qabul qilish" }).click();
    await other.getByLabel("Bir birlik narxi").fill("12000");
    await other.getByRole("button", { name: "O'z tovarimizni tanlash" }).click();
    const finder = other.getByLabel("Tovar: shtrix-kodni skanerlang yoki nom yozing");
    await finder.fill(goodsName);
    await finder.press("Enter");
    await other.getByRole("list", { name: "Topilgan tovarlar" }).getByRole("button", { name: goodsName }).click();
    await other.getByRole("button", { name: "Qabul qilish va javobni yuborish" }).click();
    await other.getByRole("button", { name: "Yetkazish: yuk xati yozish" }).click();
    await other.getByRole("button", { name: "Yuk xatini yozish" }).click();
    await expect(other.getByRole("heading", { name: "Yuk xati № 1" })).toBeVisible();
    // Issued, and in nobody's books yet.
    expect(sql(`SELECT status, total::text FROM network_note WHERE shop_id = ${lit(buyerShop)}`)).toEqual([["issued", "120000"]]);
    expect(rowsOf("stock_document", buyerShop)).toBe("0");
    expect(rowsOf("ledger_entry", supplierShop)).toBe("0");
  });

  await test.step("the buyer confirms what arrived: its stock and its debt, the supplier's stock and its credit sale, in one step", async () => {
    await goTo(page, "/network/notes/waiting");
    await page.getByRole("link", { name: "Yuk xati № 1" }).click();
    await page.getByRole("button", { name: "Yukni qabul qilish" }).click();
    await page.getByRole("button", { name: "Yangi tovar sifatida qo'shish" }).click();
    await page.getByLabel("Sotish narxi, so'm").fill("15000");
    await page.getByRole("button", { name: CONFIRM }).click();
    await expect(page.getByText("Yuk qabul qilindi: omborga kirim yozildi.")).toBeVisible();
    await expect(page.getByRole("link", { name: "Ombor hujjatini ochish" })).toBeVisible();

    // The buyer: a posted receipt of this note, ten kilograms in, 120 000 owed to the supplier.
    expect(sql(`SELECT kind, status, total::text, paid::text, (origin_ref IS NOT NULL)::text FROM stock_document WHERE shop_id = ${lit(buyerShop)}`)).toEqual([
      ["receipt", "posted", "120000", "0", "true"],
    ]);
    expect(sql(`SELECT kind, qty::float8::text FROM stock_movement WHERE shop_id = ${lit(buyerShop)}`)).toEqual([["receipt", "10"]]);
    expect(sql(`SELECT currency, balance::text FROM supplier_balance WHERE shop_id = ${lit(buyerShop)}`)).toEqual([["UZS", "120000"]]);
    // The supplier: ten of its fifty kilograms out, and a credit sale of 120 000 to the buyer's account.
    expect(sql(`SELECT kind, qty::float8::text, on_hand_after::float8::text FROM stock_movement WHERE shop_id = ${lit(supplierShop)} ORDER BY item_seq`)).toEqual([
      ["receipt", "50", "50"],
      ["sale", "-10", "40"],
    ]);
    expect(sql(`SELECT kind, amount::text FROM ledger_entry WHERE shop_id = ${lit(supplierShop)}`)).toEqual([["credit", "120000"]]);
    expect(sql(`SELECT status FROM network_note WHERE shop_id IN (${lit(buyerShop)}, ${lit(supplierShop)})`)).toEqual([["received"], ["received"]]);
    for (const shop of [buyerShop, supplierShop]) {
      expect(sqlValue(`SELECT count(*)::text FROM stock_level_mismatches(${lit(shop)}::uuid)`)).toBe("0");
      expect(sqlValue(`SELECT count(*)::text FROM open_debt_mismatches(${lit(shop)}::uuid)`)).toBe("0");
    }
  });

  await test.step("the buyer records a payment of 50 000, and it reaches the supplier's books when the supplier confirms", async () => {
    await goTo(page, "/network/payments");
    await page.getByRole("button", { name: "To'lov yozish", exact: true }).click();
    await page.getByLabel("To'lov summasi").fill("50000");
    await page.getByRole("button", { name: "To'lovni yozish", exact: true }).click();
    await expect(page.getByText("Hamkorning tasdig'i kutilmoqda").first()).toBeVisible();
    // In the buyer's books at once; not in the supplier's.
    expect(sql(`SELECT currency, balance::text FROM supplier_balance WHERE shop_id = ${lit(buyerShop)}`)).toEqual([["UZS", "70000"]]);
    expect(sql(`SELECT kind FROM ledger_entry WHERE shop_id = ${lit(supplierShop)} ORDER BY seq`)).toEqual([["credit"]]);

    await goTo(other, "/network/payments");
    await other.getByRole("button", { name: "To'lovni tasdiqlash", exact: true }).click();
    await other.getByRole("group", { name: "To'lovni tasdiqlash" }).getByRole("button", { name: "To'lovni tasdiqlash", exact: true }).click();
    await expect.poll(() => sql(`SELECT kind, amount::text FROM ledger_entry WHERE shop_id = ${lit(supplierShop)} ORDER BY seq`)).toEqual([
      ["credit", "120000"],
      ["payment", "50000"],
    ]);
    expect(sql(`SELECT DISTINCT status FROM network_payment WHERE shop_id IN (${lit(buyerShop)}, ${lit(supplierShop)})`)).toEqual([["confirmed"]]);
  });

  await test.step("a third shop sees nothing of it, on its screens or through the API", async () => {
    const orderId = sqlValue(`SELECT id::text FROM network_order WHERE shop_id = ${lit(buyerShop)}`);
    // The second browser forgets the supplier: no session, and nothing kept of its shop.
    await other.evaluate(() => {
      window.localStorage.clear();
      window.sessionStorage.clear();
    });
    await secondContext.clearCookies();
    await signInOnWeb(other, stranger, "/panel/");
    await nav(other).getByRole("link", { name: "Hamkorlar", exact: true }).click();
    await expect(other.getByText("Hali hech bir do'kon bilan ulanmagansiz.", { exact: false })).toBeVisible();
    await expect(other.getByText("Hozircha sizdan hech narsa kutilmayapti.")).toBeVisible();
    const own = await other.request.get(`/api/v1/shops/${strangerShop}/network`);
    expect(own.status()).toBe(200);
    expect((await own.json()).links).toEqual([]);
    expect((await other.request.get(`/api/v1/shops/${strangerShop}/network/orders/${orderId}`)).status()).toBe(404);
    for (const shop of [buyerShop, supplierShop]) {
      expect([403, 404]).toContain((await other.request.get(`/api/v1/shops/${shop}/network`)).status());
      expect([403, 404]).toContain((await other.request.get(`/api/v1/shops/${shop}/network/orders/${orderId}`)).status());
    }
    for (const table of ["network_link", "network_order", "network_note", "network_payment", "network_event"]) {
      expect(rowsOf(table, strangerShop), table).toBe("0");
    }
  });
});

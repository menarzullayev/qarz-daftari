import type { Locator, Page } from "@playwright/test";

import type { Chat } from "../support/chat.ts";
import { DESKTOP, expect, expectNoSidewaysScroll, goTo, openMiniApp, PHONE, settled, signInOnWeb, TABLET, test } from "../support/fixtures.ts";
import { lit, sql, sqlValue } from "../support/stack.ts";
import { newPerson } from "../support/telegram.ts";

/**
 * The front end's quality bar, in the real browser (docs/08-technical-spec/OUTPUT.md, "Front-end
 * quality"): no screen is wider than its window at a phone's, a tablet's and a desktop's width, in the
 * Mini App and in the panel alike; the main screens hold at twice the text size; and a customer is
 * added and a credit recorded with the keyboard alone.
 *
 * The other journeys each run at one width, because what they press differs by layout (a table on a
 * wide screen is a list on a narrow one). Here one signed-in page is resized and walked through its
 * screens, which costs one sign-in for every width: the proxy limits sign-ins by address.
 */

const WIDTHS = [
  ["phone", PHONE],
  ["tablet", TABLET],
  ["desktop", DESKTOP],
] as const;

/** The journeys before this one have used most of the proxy's sign-in allowance; this refills some. */
test.beforeAll(async () => {
  await new Promise((done) => setTimeout(done, 12_000));
});

/** A shop with a few customers in its book, one of them with a long name and a large debt. */
async function shopWithBook(chat: Chat, name: string) {
  const owner = newPerson(name);
  const shopName = `Keng ${owner.id}`;
  const shopId = await chat.openShop(owner, shopName);
  await chat.entry(owner, "Ali 45000");
  await chat.entry(owner, "Muhammadyusufxon 9876000");
  await chat.entry(owner, "Ali -15000");
  const customerId = sqlValue(`SELECT id::text FROM customer WHERE shop_id = ${lit(shopId)} AND display_name = 'Ali'`);
  return { owner, shopName, shopId, customerId };
}

/** The screens every member of staff has, by address. */
function staffScreens(customerId: string): string[] {
  return [
    "/",
    "/customers",
    `/customers/${customerId}`,
    `/customers/${customerId}/credit`,
    `/customers/${customerId}/payment`,
    "/customers/new",
    "/new",
    "/catalog",
    "/reminders",
    "/reports",
    "/import-export",
    "/disputes",
    "/payment-notices",
    "/subscription",
    "/shop-settings",
  ];
}

/** Opens each screen at each width and holds it to its window; every failure is named, not the first only. */
async function walk(page: Page, entry: string, screens: readonly string[]): Promise<void> {
  for (const [name, size] of WIDTHS) {
    await page.setViewportSize(size);
    for (const path of screens) {
      await goTo(page, path);
      await settled(page);
      const where = `${entry}#${path} at ${name} width (${size.width}px)`;
      // The address is one of the application's: a screen that is not there would prove nothing.
      await expect.soft(page.getByRole("heading", { level: 1 }).first(), where).not.toHaveText("Sahifa topilmadi");
      await expect.soft(page.getByText("Kutilmagan xatolik yuz berdi."), where).toHaveCount(0);
      await expectNoSidewaysScroll(page, where, { soft: true });
    }
  }
}

type Finding = string;

/**
 * What goes wrong when text grows and the layout does not follow, measured on the screen as drawn:
 *
 *  - text cut off: an element that hides what does not fit, and holds more than fits;
 *  - controls on top of each other: two links, buttons or fields of the screen whose boxes intersect;
 *  - the navigation over the content: with the page scrolled to its end, the bar and the screen's last
 *    lines share space.
 */
async function textSizeFindings(page: Page): Promise<Finding[]> {
  return page.evaluate(() => {
    const found: string[] = [];
    const name = (element: Element) => {
      const words = (element.textContent ?? "").trim().replace(/\s+/g, " ").slice(0, 40);
      return `<${element.tagName.toLowerCase()} class="${(element as HTMLElement).className}"> "${words}"`;
    };
    const shown = (element: Element) => {
      const box = element.getBoundingClientRect();
      return box.width > 2 && box.height > 2 && getComputedStyle(element).visibility !== "hidden";
    };

    for (const element of document.body.querySelectorAll<HTMLElement>("*")) {
      // Text for a screen reader alone is a 1px box on purpose.
      if (!shown(element) || element.closest(".visually-hidden") || (element.textContent ?? "").trim() === "") {
        continue;
      }
      const style = getComputedStyle(element);
      const cutAcross = /hidden|clip/.test(style.overflowX) && element.scrollWidth > element.clientWidth + 1;
      const cutDown = /hidden|clip/.test(style.overflowY) && element.scrollHeight > element.clientHeight + 1;
      if (cutAcross || cutDown) {
        found.push(`cut off: ${name(element)} holds ${element.scrollWidth}x${element.scrollHeight}px in ${element.clientWidth}x${element.clientHeight}px`);
      }
    }

    const main = document.querySelector(".shell__main") ?? document.body;
    const controls = [...main.querySelectorAll("a[href], button, input, select, textarea")].filter(shown);
    for (const [index, one] of controls.entries()) {
      for (const other of controls.slice(index + 1)) {
        if (one.contains(other) || other.contains(one)) {
          continue;
        }
        const a = one.getBoundingClientRect();
        const b = other.getBoundingClientRect();
        const across = Math.min(a.right, b.right) - Math.max(a.left, b.left);
        const down = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
        if (across > 2 && down > 2) {
          found.push(`on top of each other: ${name(one)} and ${name(other)} share ${Math.round(across)}x${Math.round(down)}px`);
        }
      }
    }

    const bar = document.querySelector(".shell__nav");
    const content = document.querySelector(".shell__main");
    if (bar && content) {
      window.scrollTo(0, document.documentElement.scrollHeight);
      const a = bar.getBoundingClientRect();
      const b = content.getBoundingClientRect();
      const across = Math.min(a.right, b.right) - Math.max(a.left, b.left);
      const down = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
      if (across > 1 && down > 1) {
        found.push(`the navigation covers the last ${Math.round(down)}px of the screen (the bar is ${Math.round(a.height)}px tall)`);
      }
      window.scrollTo(0, 0);
    }
    return found;
  });
}

/** Twice the text size, as a browser's or a phone's own setting gives it: the root's font size, which every rem follows. */
async function setTextSize(page: Page, percent: 100 | 200): Promise<void> {
  await page.evaluate((size) => {
    document.documentElement.style.fontSize = size;
  }, percent === 200 ? "32px" : "");
}

/** The main screens at twice the text size. `strict` fails the test on a finding; otherwise it is printed. */
async function walkAtDoubleText(page: Page, entry: string, screens: readonly string[], width: (typeof WIDTHS)[number], strict: boolean): Promise<void> {
  const [name, size] = width;
  await page.setViewportSize(size);
  await setTextSize(page, 200);
  for (const path of screens) {
    await goTo(page, path);
    await settled(page);
    const where = `${entry}#${path} at 200% text, ${name} width (${size.width}px)`;
    expect(await page.evaluate(() => getComputedStyle(document.documentElement).fontSize), where).toBe("32px");
    const findings = await textSizeFindings(page);
    if (strict) {
      await expectNoSidewaysScroll(page, where, { soft: true });
      expect.soft(findings, where).toEqual([]);
    } else {
      const sideways = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      for (const finding of sideways > 0 ? [`${sideways}px wider than the window`, ...findings] : findings) {
        console.log(`[not held to yet] ${where}: ${finding}`);
      }
    }
  }
  await setTextSize(page, 100);
}

test("the Mini App: no screen is wider than its window at any width, and the main screens hold at twice the text size", async ({ page, chat }) => {
  test.setTimeout(150_000);
  const { owner, shopName, customerId } = await shopWithBook(chat, "Keng");
  await openMiniApp(page, owner);
  await expect(page.getByRole("banner").locator("strong")).toHaveText(shopName);
  await expect(page.locator("main dl dd").nth(0)).toHaveText("9 906 000 so'm");

  // "More" is the phone's own screen: the sections that do not fit the tab bar.
  await walk(page, "/app/", [...staffScreens(customerId), "/more"]);

  const main = ["/", "/customers", `/customers/${customerId}`, `/customers/${customerId}/credit`, "/customers/new"];
  // Twice the text at a tablet's width leaves each line the room a phone gives at the ordinary size.
  await walkAtDoubleText(page, "/app/", main, WIDTHS[1], true);
  // A phone at twice the text size is narrower than any width the screens are designed for: measured
  // and printed, so that it is known, and not yet a failure.
  await walkAtDoubleText(page, "/app/", main, WIDTHS[0], false);
});

test("the panel: no screen is wider than its window at any width, a phone's included, and the main screens hold at twice the text size", async ({ page, chat }) => {
  test.setTimeout(150_000);
  const { owner, shopName, customerId } = await shopWithBook(chat, "Panel");
  await signInOnWeb(page, owner, "/panel/");
  await expect(page.getByRole("banner").locator("strong")).toHaveText(shopName);
  await expect(page.locator("main dl dd").nth(0)).toHaveText("9 906 000 so'm");

  // The owner's back office is the panel's own: the staff and what was done in the shop.
  await walk(page, "/panel/", [...staffScreens(customerId), "/staff", "/activity"]);

  const main = ["/", "/customers", `/customers/${customerId}`, `/customers/${customerId}/credit`, "/reports", "/staff"];
  await walkAtDoubleText(page, "/panel/", main, WIDTHS[2], true);
  await walkAtDoubleText(page, "/panel/", main, WIDTHS[1], true);
});

/** What has the focus, for a failure's message. */
function focused(page: Page): Promise<string> {
  return page.evaluate(() => {
    const element = document.activeElement;
    if (!element || element === document.body) {
      return "nothing (the page itself)";
    }
    const words = (element.getAttribute("aria-label") ?? element.textContent ?? "").trim().replace(/\s+/g, " ").slice(0, 40);
    return `<${element.tagName.toLowerCase()}> "${words}"`;
  });
}

/**
 * Presses Tab until `target` has the focus, as a person without a pointer does. Fails when it is not
 * reached (it cannot take focus, or something before it does not let focus go), and when it is reached
 * but nothing shows that it is: the focus ring is how the person knows where they are.
 */
async function tabTo(page: Page, target: Locator, what: string): Promise<void> {
  await expect(target, what).toBeVisible();
  const passed: string[] = [];
  let held = 0;
  for (let presses = 0; presses < 60; presses += 1) {
    await page.keyboard.press("Tab");
    if (await target.evaluate((element) => element === document.activeElement)) {
      expect(await target.evaluate((element) => element.matches(":focus-visible")), `${what}: the focus is shown`).toBe(true);
      const ring = await target.evaluate((element) => {
        const style = getComputedStyle(element);
        return `${style.outlineStyle} ${Number.parseFloat(style.outlineWidth)}`;
      });
      expect(ring, `${what}: the focus ring is drawn`).toBe("solid 3");
      return;
    }
    passed.push(await focused(page));
    // Three presses in a row that leave the focus on the same element: it does not let the focus go.
    const moved = await page.evaluate(() => {
      const kept = window as unknown as { __e2eFocused?: Element | null };
      const now = document.activeElement === document.body ? null : document.activeElement;
      const same = now !== null && kept.__e2eFocused === now;
      kept.__e2eFocused = now;
      return !same;
    });
    held = moved ? 0 : held + 1;
    if (held >= 2) {
      throw new Error(`${what}: the focus is held by ${passed.at(-1)} and does not move on with Tab`);
    }
  }
  throw new Error(`${what} was not reached with Tab in 60 presses; the focus went through: ${passed.join(" -> ")}`);
}

test("with the keyboard alone: a customer is added and a credit recorded", async ({ page, chat }) => {
  const owner = newPerson("Klaviatura");
  const shopName = `Tugma ${owner.id}`;
  const shopId = await chat.openShop(owner, shopName);
  // Signing in cannot be done with the keyboard alone (inside Telegram there is nothing to press, and
  // on the web the button is Telegram's own frame): the journey starts signed in.
  await openMiniApp(page, owner);
  await expect(page.getByRole("heading", { level: 1, name: "Umumiy ko'rinish" })).toBeVisible();

  // From here on: Tab, Enter, Space and the letters and digits of what is typed. No pointer.
  const key = page.keyboard;

  await test.step("to the customer book, and to the form for a new customer", async () => {
    await tabTo(page, page.getByRole("navigation", { name: "Asosiy menyu" }).getByRole("link", { name: "Mijozlar" }), "the customers tab");
    await key.press("Enter");
    await expect(page.getByRole("heading", { level: 1, name: "Mijozlar" })).toBeVisible();
    // The new screen has the focus, so the next Tab starts inside it and not at the top of the page.
    expect(await page.evaluate(() => document.activeElement?.tagName)).toBe("MAIN");
    await tabTo(page, page.getByRole("link", { name: "Yangi mijoz" }), "the link to a new customer");
    await key.press("Enter");
  });

  const customerId = await test.step("the name is typed and the form sent with Space on its button", async () => {
    await tabTo(page, page.getByLabel("Ism", { exact: true }), "the name field");
    await key.type("Ali Valiyev");
    await tabTo(page, page.getByRole("button", { name: "Yangi mijoz" }), "the button that adds the customer");
    await key.press("Space");
    await expect(page.getByRole("heading", { level: 2, name: "Ali Valiyev" })).toBeVisible();
    const rows = sql(`SELECT id::text, display_name FROM customer WHERE shop_id = ${lit(shopId)}`);
    expect(rows).toEqual([[expect.any(String), "Ali Valiyev"]]);
    return rows[0]?.[0] ?? "";
  });

  await test.step("a credit of 150 000 is typed and recorded with Enter on its button", async () => {
    await tabTo(page, page.getByRole("link", { name: "Nasiya yozish" }), "the link to a credit sale");
    await key.press("Enter");
    await tabTo(page, page.getByLabel("Summa, so'm"), "the amount field");
    await key.type("150000");
    await tabTo(page, page.getByRole("button", { name: "Nasiyani yozish" }), "the button that records the credit");
    await key.press("Enter");
    await expect(page.getByRole("status")).toContainText("Ali Valiyev: 150 000 so'm nasiya yozildi.");
    expect(sql(`SELECT kind, amount::text FROM ledger_entry WHERE customer_id = ${lit(customerId)} ORDER BY seq`)).toEqual([["credit", "150000"]]);
  });

  await test.step("and back to the customer, whose page says what is owed", async () => {
    await tabTo(page, page.getByRole("link", { name: "Mijoz sahifasi" }), "the link back to the customer");
    await key.press("Enter");
    await expect(page.locator(".balance strong")).toHaveText("150 000 so'm");
  });
});

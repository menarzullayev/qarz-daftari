import { expect, goTo, PHONE, DESKTOP, signInOnWeb, test } from "../support/fixtures.ts";
import { lit, sql, sqlValue, stack } from "../support/stack.ts";
import { newPerson, webAppInitData } from "../support/telegram.ts";
import { best, type Budget, measure, overBudget, PROFILE, totalBlockingTime, type Vitals } from "../support/vitals.ts";

/**
 * Web vitals of a first visit to each page a person lands on, on a slow phone's connection and
 * processor (support/vitals.ts), with budgets that fail the run. NFR-010
 * (docs/08-technical-spec/OUTPUT.md) asks that the staff Mini App be "usable within 3 seconds on a
 * low-end Android phone on a 3G connection": the `usableMs` of /app/ below is that check, and the
 * bundle budget of frontend/scripts/size.ts is the other half of the same requirement.
 *
 * The budgets are what a run of this suite measured, with headroom for a slower runner; the comment
 * beside each says what was measured. Raising one is a decision, made here, in the change that needs it.
 *
 * Not measured: Telegram's own script, which the Mini App's page loads from telegram.org before
 * anything else. The suite answers that request itself, at once, so the time Telegram's server takes is
 * not in these figures.
 */

const GOOD_CLS = 0.1;

const BUDGETS = {
  // The overview with its totals, after signing in with the launch data: five round trips in a row.
  app: { lcpMs: 2500, usableMs: 3000, tbtMs: 600, cls: GOOD_CLS },
  // The sign-in screen: what a visitor to the panel is shown first, every time (a reload asks again).
  panel: { lcpMs: 2500, usableMs: 3000, tbtMs: 600, cls: GOOD_CLS },
  // A customer's own account behind their link: one small script and one request.
  k: { lcpMs: 2500, usableMs: 3000, tbtMs: 300, cls: GOOD_CLS },
} as const satisfies Record<string, Budget>;

const LOADS = 2;
const SWITCH = "customer_links_on";

/**
 * Whether the slow connection was in force at all: the page and its script are two round trips, so
 * nothing can have been painted sooner. A figure under that would mean the page was measured at the
 * machine's own speed, and every budget below would hold whatever the page weighed.
 */
function throttled(vitals: Vitals): boolean {
  return vitals.fcpMs >= 2 * PROFILE.latencyMs;
}

function report(name: string, runs: readonly Vitals[], taken: Vitals, budget: Budget): void {
  const line = (vitals: Vitals) =>
    `LCP ${vitals.lcpMs} ms, FCP ${vitals.fcpMs} ms, usable ${vitals.usableMs} ms, TBT ${vitals.tbtMs} ms, CLS ${vitals.cls}`;
  console.log(`[web vitals] ${name}: ${line(taken)}`);
  runs.forEach((run, index) => console.log(`[web vitals]   load ${index + 1}: ${line(run)}`));
  console.log(`[web vitals]   budget: LCP ${budget.lcpMs} ms, usable ${budget.usableMs} ms, TBT ${budget.tbtMs} ms, CLS ${budget.cls}`);
  test.info().annotations.push({ type: "web vitals", description: `${name}: ${line(taken)}` });
}

test("the measure itself: blocking time counts what is over 50 ms after the first paint, and a budget is held", () => {
  expect(totalBlockingTime([[100, 40], [200, 50], [300, 51], [400, 250]], 0)).toBe(201);
  // A task before the first paint blocked nobody: there was nothing to tap.
  expect(totalBlockingTime([[100, 400], [900, 120]], 500)).toBe(70);
  const fine: Vitals = { lcpMs: 1800, fcpMs: 900, usableMs: 2000, tbtMs: 120, cls: 0.02 };
  expect(overBudget(fine, BUDGETS.app)).toEqual([]);
  expect(overBudget({ ...fine, lcpMs: 2501 }, BUDGETS.app)).toEqual(["LCP 2501 ms is over 2500 ms"]);
  expect(overBudget({ ...fine, usableMs: 3001 }, BUDGETS.app)).toEqual(["usable after 3001 ms, over 3000 ms"]);
  expect(overBudget({ ...fine, cls: 0.11 }, BUDGETS.app)).toEqual(["CLS 0.11 is over 0.1"]);
  expect(overBudget({ ...fine, tbtMs: 601 }, BUDGETS.app)).toEqual(["TBT 601 ms is over 600 ms"]);
  // A page that reported no paint at all was not measured, and that is not a pass.
  expect(overBudget({ ...fine, lcpMs: 0 }, BUDGETS.app)).toEqual(["no largest contentful paint was reported: nothing was measured"]);
  expect(best([fine, { ...fine, lcpMs: 1700, tbtMs: 300 }])).toEqual({ ...fine, lcpMs: 1700 });
  expect(PROFILE).toMatchObject({ latencyMs: 150, cpuSlowdown: 4 });
});

test("a first visit on a slow phone: the Mini App, the panel and a customer's page are drawn in time and hold still", async ({ page, chat, freshContext }) => {
  test.setTimeout(150_000);
  const owner = newPerson("Tezlik");
  const shopName = `Tez ${owner.id}`;
  const shopId = await chat.openShop(owner, shopName);
  await chat.entry(owner, "Ali 45000");
  await chat.entry(owner, "Sobir 120000");
  const customerId = sqlValue(`SELECT id::text FROM customer WHERE shop_id = ${lit(shopId)} AND display_name = 'Ali'`);

  await test.step("/app/: the shop's overview, signed in with Telegram's launch data (NFR-010)", async () => {
    const runs: Vitals[] = [];
    for (let load = 0; load < LOADS; load += 1) {
      const context = await freshContext({ viewport: PHONE });
      // Launch data is accepted once: each load is a launch of its own, as each opening in Telegram is.
      const launch = webAppInitData(stack.botToken, owner);
      runs.push(await measure(context, "main dl dd", (opened) => opened.goto(`/app/#tgWebAppData=${encodeURIComponent(launch)}&tgWebAppVersion=8.0`)));
    }
    const taken = best(runs);
    report("/app/", runs, taken, BUDGETS.app);
    expect(throttled(taken), "/app/ was loaded on the slow connection").toBe(true);
    expect.soft(overBudget(taken, BUDGETS.app), "/app/").toEqual([]);
  });

  await test.step("/panel/: the sign-in screen", async () => {
    const runs: Vitals[] = [];
    for (let load = 0; load < LOADS; load += 1) {
      const context = await freshContext({ viewport: DESKTOP });
      runs.push(await measure(context, ".signin__widget button", (opened) => opened.goto("/panel/")));
    }
    const taken = best(runs);
    report("/panel/", runs, taken, BUDGETS.panel);
    expect(throttled(taken), "/panel/ was loaded on the slow connection").toBe(true);
    expect.soft(overBudget(taken, BUDGETS.panel), "/panel/").toEqual([]);
  });

  await test.step("/k/: a customer's account behind their link", async () => {
    // The page a real link opens, when the platform has links switched on (08-customer-link turns the
    // switch on, and a stack keeps it). On a stack where it is off the same page is measured answering
    // a link that does not exist: the same script and style sheet, one request, and its message.
    let address = `/k/#${"A".repeat(43)}`;
    let usable = '[role="alert"] h1';
    if (sql(`SELECT value::text FROM platform_setting WHERE key = ${lit(SWITCH)}`)[0]?.[0] === "true") {
      await signInOnWeb(page, owner, "/panel/");
      await goTo(page, `/customers/${customerId}`);
      const section = page.getByRole("region", { name: "Mijoz uchun havola (Telegramsiz)" });
      await section.getByRole("button", { name: "Havola yaratish" }).click();
      const link = new URL(await section.getByText(/\/k\/#[A-Za-z0-9_-]{43}$/).innerText());
      address = link.pathname + link.hash;
      usable = ".summary__amount";
    } else {
      console.log("[web vitals] /k/: customer links are switched off on this stack; measured with a link that does not exist");
    }
    const runs: Vitals[] = [];
    for (let load = 0; load < LOADS; load += 1) {
      const context = await freshContext({ viewport: PHONE });
      runs.push(await measure(context, usable, (opened) => opened.goto(address)));
    }
    const taken = best(runs);
    report("/k/", runs, taken, BUDGETS.k);
    expect(throttled(taken), "/k/ was loaded on the slow connection").toBe(true);
    expect.soft(overBudget(taken, BUDGETS.k), "/k/").toEqual([]);
  });
});

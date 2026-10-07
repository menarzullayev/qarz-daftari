// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { ApiAuth } from "../shared/api";
import type { Role } from "../shared/navigation";
import { StaffRoot } from "../shared/StaffRoot";
import {
  creditSettingsBody,
  CUSTOMER_ID,
  customerBody,
  detailBody,
  fakeServer,
  itemBody,
  linkBody,
  NO_OVERDUE,
  NOON,
  ok,
  refusal,
  remindersBody,
  settingsBody,
  SHOP_BASE,
  SHOP_ID,
  subscriptionBody,
} from "../testing/fakeServer";

/**
 * The web panel's desktop layouts, sign-in and back-office screens must leave the Telegram Mini App as
 * it was (story S15.1). The snapshots next to this file were recorded from the Mini App before that
 * story, at a phone's width; this test renders the same entry again and compares the markup and the
 * requests. A table, a banner, a new section or a new call in the Mini App fails it.
 *
 * To re-record after a deliberate change to the Mini App: `npx vitest run src/app -u`.
 */

const OTHER_SHOP = "5a0c6d3e-0000-4000-8000-00000000bbbb";
const PHONE = 375;
const DESKTOP = 1280;

/** jsdom has neither a layout nor media queries: give it a width and the one query the layouts ask. */
function setWidth(width: number) {
  Object.defineProperty(window, "innerWidth", { configurable: true, value: width });
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: (query: string) => {
      const min = /min-width:\s*(\d+)px/.exec(query);
      return {
        media: query,
        matches: min !== null && width >= Number(min[1]),
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
      };
    },
  });
}

function backend(shops: unknown) {
  return fakeServer((sent) => {
    if (sent.method !== "GET") {
      return refusal(404, "NOT_FOUND", "Topilmadi.");
    }
    switch (sent.path) {
      case "/api/v1/me/shops":
        return ok(shops);
      case "/api/v1/me/accounts":
        return ok({ items: [] });
      case `${SHOP_BASE}/overview`:
        return ok({ outstanding: 120000, debtors: 1, overdue: { amount: 45000, customers: 1 }, due_today: 0 });
      case `${SHOP_BASE}/overview/debtors`:
        return ok({
          items: [{ ...customerBody(), overdue: { amount: 45000, since: "2026-10-01", days: 5, due_today: 0 } }],
          next_cursor: "more",
        });
      case `${SHOP_BASE}/customers`:
        return ok({
          items: [customerBody(), customerBody({ id: "11111111-1111-4111-8111-111111111112", display_name: "Vali", phone: null, balance: 0 })],
          next_cursor: null,
        });
      case `${SHOP_BASE}/customers/${CUSTOMER_ID}`:
        return ok(detailBody({ overdue: NO_OVERDUE }));
      case `${SHOP_BASE}/customers/${CUSTOMER_ID}/link`:
        return ok(linkBody());
      case `${SHOP_BASE}/credit-settings`:
        return ok(creditSettingsBody());
      case `${SHOP_BASE}/catalog`:
        return ok({
          items: [itemBody(), itemBody({ id: "44444444-4444-4444-8444-444444444449", name: "Choy", learned: true })],
          next_cursor: null,
        });
      case SHOP_BASE:
        return ok(settingsBody());
      case `${SHOP_BASE}/subscription`:
        return ok(subscriptionBody());
      case `${SHOP_BASE}/reminders`:
        return ok(remindersBody());
      // Asked by the owner's settings since the support-access story (REQ-059): a deliberate addition.
      case `${SHOP_BASE}/support-access`:
        return ok({ items: [], next_cursor: null });
      default:
        // Anything the Mini App did not ask before this story is refused, and shows in the snapshot.
        return refusal(404, "NOT_FOUND", "Topilmadi.");
    }
  });
}

const bearer = async (): Promise<ApiAuth> => ({ kind: "bearer", token: "session-token" });

async function miniApp(role: Role, hash: string, width: number, shops = 1) {
  setWidth(width);
  window.location.hash = hash;
  const items = [{ shop_id: SHOP_ID, name: "Baraka savdo", role, membership_id: "33333333-3333-4333-8333-333333333333" }];
  if (shops > 1) {
    items.push({ shop_id: OTHER_SHOP, name: "Ziyo market", role: "owner", membership_id: "33333333-3333-4333-8333-333333333334" });
  }
  const server = backend({ items, active_shop: SHOP_ID });
  const view = render(
    <StaffRoot entryKey="entry.app" initialLanguage="uz" connect={bearer} fetch={server.fetch} now={() => NOON} customerPage />,
  );
  await screen.findAllByRole("navigation");
  await waitFor(() => expect(screen.queryByText("Yuklanmoqda…")).toBeNull());
  // One more turn, so that a request started by the last render is in the list.
  await new Promise((resolve) => setTimeout(resolve, 20));
  await waitFor(() => expect(screen.queryByText("Yuklanmoqda…")).toBeNull());
  const result = {
    html: view.container.innerHTML,
    requests: server.sent.map((sent) => `${sent.method} ${sent.path}`).sort(),
  };
  view.unmount();
  return result;
}

beforeEach(() => {
  window.localStorage.clear();
});
afterEach(() => {
  cleanup();
  window.location.hash = "";
});

const CASES: readonly (readonly [Role, string, number])[] = [
  ["owner", "#/", 1],
  ["owner", "#/", 2],
  ["owner", "#/customers", 1],
  ["owner", `#/customers/${CUSTOMER_ID}`, 1],
  ["owner", "#/new", 1],
  ["owner", "#/catalog", 1],
  ["owner", "#/shop-settings", 1],
  ["owner", "#/subscription", 1],
  ["owner", "#/staff", 1],
  ["owner", "#/activity", 1],
  ["owner", "#/more", 1],
  ["manager", "#/", 1],
  ["manager", "#/customers", 1],
  ["manager", "#/catalog", 1],
  ["manager", "#/shop-settings", 1],
  ["seller", "#/", 1],
  ["seller", "#/catalog", 1],
  ["seller", "#/staff", 1],
];

describe("the Telegram Mini App is as it was before the web panel's layouts", () => {
  it.each(CASES)("%s at %s with %i shop(s), 375 px wide", async (role, hash, shops) => {
    const phone = await miniApp(role, hash, PHONE, shops);
    expect(phone.html).toMatchSnapshot("markup");
    expect(phone.requests).toMatchSnapshot("requests");
  });

  it.each(CASES)("%s at %s with %i shop(s): the same on a wide screen, tables are the panel's", async (role, hash, shops) => {
    const phone = await miniApp(role, hash, PHONE, shops);
    const wide = await miniApp(role, hash, DESKTOP, shops);
    expect(wide.html).toBe(phone.html);
    expect(wide.requests).toEqual(phone.requests);
  });

  it("has no table, no sign-out and no Telegram sign-in script anywhere in these screens", async () => {
    for (const [role, hash, shops] of CASES) {
      const { html } = await miniApp(role, hash, PHONE, shops);
      expect(html).not.toContain("<table");
      expect(html).not.toContain("telegram-widget");
    }
    expect(document.querySelector('script[src*="telegram"]')).toBeNull();
  });
});

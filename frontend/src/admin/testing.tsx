import { render } from "@testing-library/react";
import type { ReactElement } from "react";

import { I18nProvider } from "../i18n/I18nProvider";
import type { Language } from "../i18n/types";
import { fakeServer, ok, refusal, type Reply, type Sent } from "../testing/fakeServer";
import { createAdminApi } from "./adminApi";
import type { Who } from "./SupportSection";

/** Test helpers of the administrator's panel; nothing here is part of a build. */

export const CSRF = "csrf-for-tests-only-0000000000000000000000000";
export const ADMIN = "/api/admin/v1";
export const SHOP_ID = "5a0c6d3e-0000-4000-8000-00000000aaaa";
export const OTHER_SHOP = "5a0c6d3e-0000-4000-8000-00000000bbbb";
export const ADMIN_ID = "77777777-7777-4777-8777-777777a1b2c3";
/** A provisioning URI as the server builds one. Not a real secret. */
export const OTPAUTH = "otpauth://totp/Qarz%20Daftari:admin-1?secret=ORSXG5BAMZXXEIDUMVZXI4Y&issuer=Qarz%20Daftari";
export const LOGIN = { id: 123456789, first_name: "Ali", auth_date: 1791270000, hash: "ab12" };
/** Tuesday 6 October 2026, 12:00 in Tashkent. */
export const NOW = new Date("2026-10-06T07:00:00Z");

export const authBody = (overrides: Record<string, unknown> = {}) => ({
  enrolled: true,
  confirmed: true,
  elevated: false,
  expires_at: null,
  locked_until: null,
  ...overrides,
});

export const shopBody = (overrides: Record<string, unknown> = {}, subscription: Record<string, unknown> = {}) => ({
  id: SHOP_ID,
  name: "Baraka savdo",
  status: "active",
  created_at: "2026-09-01T05:00:00+00:00",
  subscription: { state: "trial", stored_state: "trial", trial_ends: "2026-10-20", paid_through: null, prior_state: null, ...subscription },
  owner_tg_id: 123456789,
  staff_count: 3,
  customer_count: 42,
  ...overrides,
});

export const auditBody = (overrides: Record<string, unknown> = {}) => ({
  id: "66666666-6666-4666-8666-666666666661",
  at: "2026-10-05T06:00:00+00:00",
  admin_id: ADMIN_ID,
  actor_tg_id: null,
  action: "subscription.trial_set",
  target_type: "shop",
  target_id: SHOP_ID,
  shop_id: SHOP_ID,
  reason: "Egasi so'radi",
  detail: {
    before: { state: "trial", trial_ends: "2026-10-01", paid_through: null, prior_state: null },
    after: { state: "trial", trial_ends: "2026-10-20", paid_through: null, prior_state: null },
  },
  ...overrides,
});

export const shopDetailBody = (overrides: Record<string, unknown> = {}, subscription: Record<string, unknown> = {}) => ({
  ...shopBody({}, subscription),
  lang: "uz",
  deletion_due: null,
  receipts: [
    {
      id: "55555555-5555-4555-8555-555555555551",
      stated_amount: 100000,
      status: "approved",
      months: 1,
      reject_reason: null,
      created_at: "2026-09-20T05:00:00+00:00",
      decided_at: "2026-09-20T06:00:00+00:00",
    },
  ],
  changes: [auditBody()],
  ...overrides,
});

export const platformBody = (overrides: Record<string, unknown> = {}, settings: Record<string, unknown> = {}) => ({
  settings: {
    trial_on: true,
    trial_days: 30,
    price_uzs: 100000,
    payment_cards: [{ number: "8600123456789012", label: "Humo · Anorbank" }],
    review_group: null,
    sms_on: false,
    sms_monthly_quota: 0,
    online_pay_on: false,
    free_plan_on: false,
    free_plan_customers: 30,
    permissions_on: false,
    customer_links_on: false,
    usd_on: false,
    cash_book_on: false,
    ...settings,
  },
  needs_code: [
    "cash_book_on",
    "customer_links_on",
    "free_plan_on",
    "online_pay_on",
    "payment_cards",
    "permissions_on",
    "price_uzs",
    "review_group",
    "sms_on",
    "trial_on",
    "usd_on",
  ],
  changed: { price_uzs: { by: ADMIN_ID, at: "2026-10-01T05:00:00+00:00" } },
  ...overrides,
});

export const RECEIPT_ID = "55555555-5555-4555-8555-555555555552";
export const OTHER_RECEIPT = "55555555-5555-4555-8555-555555555553";

/** A subscription receipt as the administrator reads one: waiting, sent an hour before `NOW`, with its file. */
export const receiptBody = (overrides: Record<string, unknown> = {}) => ({
  id: RECEIPT_ID,
  shop_id: SHOP_ID,
  shop_name: "Baraka savdo",
  stated_amount: 200000,
  stated_months: 2,
  status: "submitted",
  months: null,
  reject_reason: null,
  created_at: "2026-10-06T06:00:00+00:00",
  decided_at: null,
  decided_by: null,
  decided_by_tg_id: null,
  has_file: true,
  ...overrides,
});

/** GET /receipts/{id}: the receipt with a link to its file and no other receipt carrying the same file. */
export const receiptDetailBody = (overrides: Record<string, unknown> = {}) => ({
  ...receiptBody(),
  file: { url: "/files/receipt.token", expires_at: "2026-10-06T07:05:00+00:00" },
  copies: [],
  ...overrides,
});

export const OTHER_ADMIN = "77777777-7777-4777-8777-777777d4e5f6";
export const ACCESS_ID = "88888888-8888-4888-8888-888888888881";

/** A support access open at `NOW`: from an hour before it until an hour after. */
export const supportBody = (overrides: Record<string, unknown> = {}) => ({
  id: ACCESS_ID,
  shop_id: SHOP_ID,
  shop_name: "Baraka savdo",
  admin_id: ADMIN_ID,
  reason: "Egasi yordam so'radi",
  state: "active",
  starts_at: "2026-10-06T06:00:00+00:00",
  ends_at: "2026-10-06T08:00:00+00:00",
  closed_at: null,
  closed_by: null,
  ...overrides,
});

/** A panel that has not learnt which administrator it serves, and one that has. */
export const NOBODY: Who = { me: null, learn: () => undefined };
export const KNOWN: Who = { me: ADMIN_ID, learn: () => undefined };

/** An administrator's API over a fake server, with the web session's cookie and CSRF token. */
export function adminApi(handler: (sent: Sent, index: number) => Reply) {
  const server = fakeServer(handler);
  return { server, api: createAdminApi({ fetch: server.fetch, auth: { kind: "cookie", csrfToken: CSRF } }) };
}

export function renderAdmin(screen: ReactElement, language: Language = "uz") {
  return render(<I18nProvider initialLanguage={language}>{screen}</I18nProvider>);
}

export { ok, refusal };

/** Table cells as text, row by row, with no-break spaces made plain. */
export function cells(table: HTMLElement): (string | undefined)[][] {
  return [...table.querySelectorAll("tr")].map((row) => [...row.children].map((cell) => cell.textContent?.replace(/\s/g, " ")));
}

import { render } from "@testing-library/react";
import type { ReactElement } from "react";

import { I18nProvider } from "../i18n/I18nProvider";
import type { Language } from "../i18n/types";
import { createApi, type Fetch, type ShopMembership } from "../shared/api";
import type { Role } from "../shared/navigation";
import { WorkspaceProvider } from "../shared/workspace/context";
import { MEMBERSHIP_ID, NOON, SHOP_ID } from "../testing/fakeServer";
import { BOT } from "../testing/renderScreen";
import { OfficeProvider } from "./office";

/** Test helpers of the web panel; nothing here is part of a build. */

export const CSRF = "csrf-for-tests-only-0000000000000000000000000";
export const OWNER_ID = MEMBERSHIP_ID;
export const MANAGER_ID = "33333333-3333-4333-8333-3333333a1b2c";
export const SELLER_ID = "33333333-3333-4333-8333-3333333d4e5f";
export const OTHER_SHOP = "5a0c6d3e-0000-4000-8000-00000000bbbb";

export const memberBody = (id: string, role: Role, status = "active") => ({ id, user_id: `user-${id}`, role, status });

/** GET /shops/{id}/staff: the owner (the signed-in person), an active manager and a suspended seller. */
export const STAFF = [
  memberBody(OWNER_ID, "owner"),
  memberBody(MANAGER_ID, "manager"),
  memberBody(SELLER_ID, "seller", "suspended"),
];

export const INVITATION_ID = "ab".repeat(32);
export const invitationBody = (overrides: Record<string, unknown> = {}) => ({
  id: INVITATION_ID,
  role: "seller",
  expires_at: "2026-10-13T07:00:00+00:00",
  ...overrides,
});

/** An invitation token as the server issues one: 43 URL-safe characters. Not a real credential. */
export const TOKEN = "Zm9yLXRlc3RzLW9ubHktbm90LWEtcmVhbC10b2tlbi0yMjIy";

export const transferBody = (overrides: Record<string, unknown> = {}) => ({
  id: "66666666-6666-4666-8666-666666666666",
  from_membership: OWNER_ID,
  to_membership: MANAGER_ID,
  status: "pending",
  expires_at: "2026-10-08T07:00:00+00:00",
  ...overrides,
});

export const PENDING_DELETION = { status: "deletion_pending", deletion_due: "2026-11-05T07:00:00+00:00" };
export const NO_DELETION = { status: "active", deletion_due: null };

/** jsdom has neither a layout nor media queries: give it a width and answer `(min-width: Npx)` from it. */
export function setWidth(width: number): void {
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

/**
 * Renders one of the panel's parts as the panel would: inside the language, workspace and office
 * contexts, against a fake server, with the panel's cookie session and its CSRF token.
 */
export function renderOffice(
  part: ReactElement,
  options: {
    fetch: Fetch;
    role?: Role;
    language?: Language;
    membershipId?: string | null;
    botUsername?: string | null;
    shopName?: string;
    shops?: readonly ShopMembership[];
    reloadSession?: () => void;
  },
) {
  const api = createApi({ fetch: options.fetch, auth: { kind: "cookie", csrfToken: CSRF } }).shop(SHOP_ID);
  return render(
    <I18nProvider initialLanguage={options.language ?? "uz"}>
      <WorkspaceProvider
        value={{
          api,
          role: options.role ?? "owner",
          membershipId: options.membershipId === undefined ? OWNER_ID : options.membershipId,
          botUsername: options.botUsername === undefined ? BOT : options.botUsername,
          now: () => NOON,
          shopName: options.shopName ?? "Baraka savdo",
          shops: options.shops,
          reloadSession: options.reloadSession,
        }}
      >
        <OfficeProvider>{part}</OfficeProvider>
      </WorkspaceProvider>
    </I18nProvider>,
  );
}

/** The elements Tab stops at, in the order it reaches them. */
export function tabStops(root: HTMLElement): HTMLElement[] {
  const candidates = root.querySelectorAll<HTMLElement>("a[href], button, input, select, textarea, [tabindex]");
  return [...candidates].filter(
    (element) => !element.hasAttribute("disabled") && element.tabIndex >= 0 && element.getAttribute("aria-hidden") !== "true",
  );
}

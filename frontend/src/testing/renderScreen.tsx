import { act, render } from "@testing-library/react";
import type { ReactElement } from "react";

import { I18nProvider } from "../i18n/I18nProvider";
import type { Language } from "../i18n/types";
import { createApi, type Fetch } from "../shared/api";
import type { Role } from "../shared/navigation";
import { WorkspaceProvider } from "../shared/workspace/context";
import { MEMBERSHIP_ID, NOON, SHOP_ID } from "./fakeServer";

/** Renders a screen as StaffApp would: inside the language and workspace contexts, against a fake server. */
export function renderScreen(
  screen: ReactElement,
  options: {
    fetch: Fetch;
    role?: Role;
    language?: Language;
    now?: Date;
    /** The signed-in person's membership; by default the author of `entryBody()`. Null: not known. */
    membershipId?: string | null;
    botUsername?: string | null;
  },
) {
  const api = createApi({ fetch: options.fetch, auth: { kind: "bearer", token: "test-session" } }).shop(SHOP_ID);
  const now = options.now ?? NOON;
  return render(
    <I18nProvider initialLanguage={options.language ?? "uz"}>
      <WorkspaceProvider
        value={{
          api,
          role: options.role ?? "seller",
          membershipId: options.membershipId === undefined ? MEMBERSHIP_ID : options.membershipId,
          botUsername: options.botUsername === undefined ? BOT : options.botUsername,
          now: () => now,
        }}
      >
        {screen}
      </WorkspaceProvider>
    </I18nProvider>,
  );
}

/** The bot the test screens link to. */
export const BOT = "qarz_daftari_bot";

export function go(hash: string) {
  act(() => {
    window.location.hash = hash;
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  });
}

/**
 * Matcher for `getByText` that compares the text exactly. The default matcher collapses white space,
 * so it cannot find an amount: thousands are separated by a no-break space.
 */
export const exact = (text: string) => (_content: string, node: Element | null) =>
  node !== null && node.children.length === 0 && node.textContent === text;

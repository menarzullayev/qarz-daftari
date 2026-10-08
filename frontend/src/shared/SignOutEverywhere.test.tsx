// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { catalogs } from "../i18n/catalog";
import type { Language } from "../i18n/types";
import {
  customerBody,
  fakeServer,
  LINK_ID,
  NO_OVERDUE,
  NOON,
  ok,
  refusal,
  type Reply,
  type Sent,
  SHOP_ID,
} from "../testing/fakeServer";
import type { ApiAuth } from "./api";
import { StaffRoot } from "./StaffRoot";

/**
 * "Sign out everywhere" in the Telegram Mini App (security review, finding 9). The web panel's side of it
 * is in panel/PanelRoot.test.tsx; what the server does is in backend/tests/api/test_auth.py.
 */

const PATH = "/api/v1/auth/sign-out-everywhere";
const ASK = "Barcha qurilmalarda chiqish";
const YES = "Ha, chiqish";
const DONE = "Barcha qurilmalarda hisobingizdan chiqdingiz.";
const STAFF = { items: [{ shop_id: SHOP_ID, name: "Baraka savdo", role: "seller" }], active_shop: SHOP_ID };
const NO_SHOPS = { items: [], active_shop: null };
const ACCOUNTS = [
  { link_id: LINK_ID, shop_name: "Baraka savdo", display_name: "Ali Valiyev", balance: 120000 },
  { link_id: "77777777-7777-4777-8777-777777777778", shop_name: "Oltin don", display_name: "Ali aka", balance: 0 },
];

beforeEach(() => {
  window.location.hash = "";
  window.localStorage.clear();
});
afterEach(cleanup);

const bearer = async (): Promise<ApiAuth> => ({ kind: "bearer", token: "session-token" });
const heading = () => screen.getByRole("heading", { level: 1 }).textContent;
const signOuts = (server: ReturnType<typeof fakeServer>) => server.sent.filter((sent) => sent.path === PATH);

/** A member of staff, or with `accounts` a customer; `answer` is what the server says to the sign-out. */
function backend(shops: unknown, answer: (sent: Sent) => Reply = () => ok(null), accounts: unknown[] = []) {
  return fakeServer((sent) => {
    if (sent.path === PATH) {
      return answer(sent);
    }
    if (sent.path === "/api/v1/me/shops") {
      return ok(shops);
    }
    if (sent.path === "/api/v1/me/accounts") {
      return ok({ items: accounts });
    }
    if (sent.path.endsWith("/overview")) {
      return ok({ outstanding: 120000, debtors: 1, overdue: { amount: 0, customers: 0 }, due_today: 0 });
    }
    if (sent.path.endsWith("/overview/debtors")) {
      return ok({ items: [{ ...customerBody(), overdue: NO_OVERDUE }], next_cursor: null });
    }
    return ok({ items: [], next_cursor: null });
  });
}

function start(server: ReturnType<typeof fakeServer>, language: Language = "uz") {
  return render(
    <StaffRoot entryKey="entry.app" initialLanguage={language} connect={bearer} fetch={server.fetch} now={() => NOON} customerPage />,
  );
}

describe("signing out everywhere from the Mini App", () => {
  it("asks first: the button sends nothing, and the question says what will happen", async () => {
    const server = backend(STAFF);
    start(server);
    fireEvent.click(await screen.findByRole("button", { name: ASK }));
    expect(screen.getByText(/Hisobingiz barcha qurilmalarda yopiladi/)).toBeTruthy();
    expect(screen.getByRole("button", { name: YES })).toBeTruthy();
    expect(screen.queryByRole("button", { name: ASK })).toBeNull();
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(signOuts(server)).toEqual([]);
  });

  it("sends nothing when the answer is no, and offers the button again", async () => {
    const server = backend(STAFF);
    start(server);
    fireEvent.click(await screen.findByRole("button", { name: ASK }));
    fireEvent.click(screen.getByRole("button", { name: "Bekor qilish" }));
    expect(screen.getByRole("button", { name: ASK })).toBeTruthy();
    expect(screen.queryByRole("button", { name: YES })).toBeNull();
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(signOuts(server)).toEqual([]);
    expect(heading()).toBe("Umumiy ko'rinish");
  });

  it("on yes tells the server once, with the session's token, and shows that the person is signed out", async () => {
    const server = backend(STAFF);
    start(server);
    fireEvent.click(await screen.findByRole("button", { name: ASK }));
    fireEvent.click(screen.getByRole("button", { name: YES }));
    await waitFor(() => expect(heading()).toBe("Kirish talab qilinadi"));
    expect(screen.getByRole("status").textContent).toBe(DONE);
    expect(signOuts(server)).toHaveLength(1);
    expect(signOuts(server)[0]).toMatchObject({ method: "POST", body: undefined });
    expect(signOuts(server)[0]?.headers["Authorization"]).toBe("Bearer session-token");
    // Nothing of the shop is on the screen any more, and nothing more is asked of the server.
    expect(screen.queryByText("Jami qarz")).toBeNull();
    const after = server.sent.length;
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(server.sent).toHaveLength(after);
  });

  it("does not send a second request when yes is pressed twice", async () => {
    let release: (reply: Reply) => void = () => undefined;
    const server = backend(STAFF, () => new Promise((resolve) => (release = resolve as (reply: Reply) => void)));
    start(server);
    fireEvent.click(await screen.findByRole("button", { name: ASK }));
    const yes = screen.getByRole("button", { name: YES });
    fireEvent.click(yes);
    fireEvent.click(yes);
    const waiting = await screen.findByRole("button", { name: "Chiqilmoqda…" });
    expect((waiting as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Bekor qilish" }) as HTMLButtonElement).disabled).toBe(true);
    release(ok(null));
    await waitFor(() => expect(heading()).toBe("Kirish talab qilinadi"));
    expect(signOuts(server)).toHaveLength(1);
  });

  it("stays signed in, says why, and can be tried again when the request fails", async () => {
    let offline = true;
    const server = backend(STAFF, () => (offline ? "offline" : ok(null)));
    start(server);
    fireEvent.click(await screen.findByRole("button", { name: ASK }));
    fireEvent.click(screen.getByRole("button", { name: YES }));
    expect((await screen.findByRole("alert")).textContent).toContain("Serverga ulanib bo'lmadi");
    expect(heading()).toBe("Umumiy ko'rinish");
    expect(screen.queryByText(DONE)).toBeNull();
    offline = false;
    fireEvent.click(screen.getByRole("button", { name: YES }));
    await waitFor(() => expect(heading()).toBe("Kirish talab qilinadi"));
    expect(signOuts(server)).toHaveLength(2);
  });

  it("treats a session the server no longer knows as signed out, without claiming it ended the others", async () => {
    const server = backend(STAFF, () => refusal(401, "UNAUTHENTICATED", "Avval tizimga kiring."));
    start(server);
    fireEvent.click(await screen.findByRole("button", { name: ASK }));
    fireEvent.click(screen.getByRole("button", { name: YES }));
    await waitFor(() => expect(heading()).toBe("Kirish talab qilinadi"));
    expect(screen.queryByText(DONE)).toBeNull();
  });

  it("is offered to a person who is only a customer, under their accounts", async () => {
    const server = backend(NO_SHOPS, () => ok(null), ACCOUNTS);
    start(server);
    fireEvent.click(await screen.findByRole("button", { name: ASK }));
    fireEvent.click(screen.getByRole("button", { name: YES }));
    await waitFor(() => expect(heading()).toBe("Kirish talab qilinadi"));
    expect(screen.getByRole("status").textContent).toBe(DONE);
    expect(signOuts(server)).toHaveLength(1);
  });

  it("is offered to a person with no shop and no account, who has a session all the same", async () => {
    const server = backend(NO_SHOPS);
    start(server);
    expect(await screen.findByRole("button", { name: ASK })).toBeTruthy();
    expect(heading()).toBe("Do'kon yo'q");
  });

  it("speaks Russian to a Russian reader, from the catalog", async () => {
    const server = backend(STAFF);
    start(server, "ru");
    fireEvent.click(await screen.findByRole("button", { name: catalogs.ru["session.everywhere.action"] as string }));
    expect(screen.getByText(catalogs.ru["session.everywhere.confirm"] as string)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Да, выйти" }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("Вы вышли на всех устройствах."));
  });
});
